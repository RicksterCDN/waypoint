#!/usr/bin/env python3
"""Idempotent, notebook-free provisioning of the Waypoint FabricIQ *source mirror*.

FabricIQ grounds its Direct Lake semantic model + Data Agent on a zero-ETL, continuously
replicated copy of the operational Azure Database for PostgreSQL financial core. This module
creates that copy with **Fabric Mirroring for Azure Database for PostgreSQL** using only the
Microsoft Fabric public REST API (no notebook, no Spark), so it runs from the azd
``postprovision`` hook or the deploy workflow, exactly like ``provision_fabric_iq.py``.

It:
  1. resolves the Fabric workspace,
  2. ensures a Fabric **connection** to the source PostgreSQL server (created if a
     ``CONNECTION_ID`` is not supplied — an operator can pre-create the connection in the
     portal and pass its GUID if a tenant needs a bespoke connection shape),
  3. creates a **Mirrored Azure Database for PostgreSQL** item whose ``mirroring.json``
     mounts the four financial-core tables (suppliers, invoices, invoice_lines,
     reconciliation_findings), and
  4. **starts mirroring** and polls the mirroring status.

Every step is look-up-before-write and re-runnable: an existing connection / mirrored
database (matched by display name) is reused instead of being duplicated, and starting an
already-running mirror is treated as success.

Consumed environment (set by the wrapper / deploy workflow):
  FABRIC_TOKEN               Bearer token for https://api.fabric.microsoft.com (required)
  FABRIC_API_BASE            Defaults to https://api.fabric.microsoft.com/v1
  WORKSPACE_ID               Fabric workspace GUID (preferred)
  WORKSPACE_NAME             Fabric workspace display name (used if WORKSPACE_ID is empty)
  MIRRORED_DATABASE_NAME     Mirrored item display name (default WaypointMirror)
  SOURCE_SERVER              Source PostgreSQL FQDN, e.g. waypoint-postgres.postgres.database.azure.com (required)
  SOURCE_DATABASE            Source database name (default waypoint)
  SOURCE_SCHEMA              Schema that owns the mirrored tables (default public)
  MIRROR_TABLES              Comma-separated table list (default the four financial-core tables)
  RETENTION_DAYS             Delta retention in days, 1-30 (default 7)
  CONNECTION_ID              Existing Fabric connection GUID to reuse (optional; skips connection create)
  CONNECTION_NAME            Display name for the connection (default Waypoint-Postgres-Mirror)
  CONNECTION_TYPE            Fabric connection type (default AzurePostgreSQL; PostgreSQL for self-hosted)
  CONNECTION_CREATION_METHOD Fabric creation method (default AzurePostgreSQL.Database)
  FABRIC_MIRROR_USER         PostgreSQL mirroring role login (required when creating a connection)
  FABRIC_MIRROR_PASSWORD     PostgreSQL mirroring role password (required when creating a connection)
  MIRROR_STRICT              "true" (default) fails on start/poll errors; "false" warns and continues
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FABRIC_API_BASE = os.environ.get("FABRIC_API_BASE", "https://api.fabric.microsoft.com/v1").rstrip("/")
FABRIC_TOKEN = os.environ.get("FABRIC_TOKEN", "")

MIRRORED_DATABASE_NAME = os.environ.get("MIRRORED_DATABASE_NAME") or "WaypointMirror"
CONNECTION_NAME = os.environ.get("CONNECTION_NAME") or "Waypoint-Postgres-Mirror"
SOURCE_DATABASE = os.environ.get("SOURCE_DATABASE") or "waypoint"
SOURCE_SCHEMA = os.environ.get("SOURCE_SCHEMA") or "public"
DEFAULT_TABLES = "suppliers,invoices,invoice_lines,reconciliation_findings"
MIRROR_STRICT = os.environ.get("MIRROR_STRICT", "true").lower() == "true"


def log(msg: str) -> None:
    print(f"[fabric-mirror] {msg}", flush=True)


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[fabric-mirror] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def _request(method: str, url: str, token: str, body: dict | None = None,
             extra_headers: dict | None = None):
    """Perform an HTTP request, returning (status, headers, parsed_or_text)."""
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8", "replace") if resp.length != 0 else ""
            parsed = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
            return resp.status, dict(resp.headers), parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, dict(exc.headers), parsed


def fabric_lro(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    """Call a Fabric REST endpoint, resolving a long-running operation if one is returned.

    Returns (final_status, payload). Callers decide how to treat non-2xx statuses so that
    idempotent no-ops (e.g. starting an already-running mirror) are not fatal.
    """
    url = f"{FABRIC_API_BASE}{path}"
    status, headers, payload = _request(method, url, FABRIC_TOKEN, body)
    if status in (200, 201):
        return status, payload if isinstance(payload, dict) else None
    if status == 202:
        op_url = headers.get("Operation-Location") or headers.get("Location")
        if not op_url:
            return status, payload if isinstance(payload, dict) else None
        retry = int(headers.get("Retry-After", "5") or "5")
        for _ in range(60):
            time.sleep(max(retry, 3))
            s, h, p = _request("GET", op_url, FABRIC_TOKEN)
            state = (p or {}).get("status") if isinstance(p, dict) else None
            if state in ("Succeeded", "Completed"):
                result_url = h.get("Location")
                if result_url:
                    _, _, rp = _request("GET", result_url, FABRIC_TOKEN)
                    return 200, rp if isinstance(rp, dict) else (p if isinstance(p, dict) else None)
                return 200, p if isinstance(p, dict) else None
            if state in ("Failed", "Undefined"):
                fail(f"Fabric operation failed for {method} {path}: {json.dumps(p)[:800]}")
            retry = int(h.get("Retry-After", str(retry)) or retry)
        fail(f"Fabric operation timed out polling for {method} {path}")
    return status, payload if isinstance(payload, dict) else None


def b64_part(path: str, obj) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    return {
        "path": path,
        "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def resolve_workspace() -> str:
    ws_id = os.environ.get("WORKSPACE_ID", "").strip()
    if ws_id:
        return ws_id
    name = os.environ.get("WORKSPACE_NAME", "").strip()
    if not name:
        fail("neither WORKSPACE_ID nor WORKSPACE_NAME is set.")
    _, _, payload = _request("GET", f"{FABRIC_API_BASE}/workspaces", FABRIC_TOKEN)
    for ws in (payload or {}).get("value", []):
        if ws.get("displayName") == name:
            return ws["id"]
    fail(f"could not resolve a workspace named '{name}'.")
    return ""


def find_connection_by_name(name: str) -> str:
    """Return the GUID of an existing connection matching the display name, or ''."""
    url = f"{FABRIC_API_BASE}/connections"
    while url:
        _, _, payload = _request("GET", url, FABRIC_TOKEN)
        for conn in (payload or {}).get("value", []):
            if conn.get("displayName") == name:
                return conn.get("id", "")
        token = (payload or {}).get("continuationToken")
        url = f"{FABRIC_API_BASE}/connections?continuationToken={urllib.parse.quote(token)}" if token else ""
    return ""


def ensure_connection(server: str) -> str:
    """Reuse a supplied/existing connection or create a cloud connection to the source server."""
    existing = os.environ.get("CONNECTION_ID", "").strip()
    if existing:
        log(f"using supplied connection id = {existing}")
        return existing
    found = find_connection_by_name(CONNECTION_NAME)
    if found:
        log(f"reusing existing connection '{CONNECTION_NAME}' = {found}")
        return found

    user = os.environ.get("FABRIC_MIRROR_USER", "").strip()
    password = os.environ.get("FABRIC_MIRROR_PASSWORD", "").strip()
    if not user or not password:
        fail("creating a connection requires FABRIC_MIRROR_USER and FABRIC_MIRROR_PASSWORD "
             "(or supply an existing CONNECTION_ID).")

    # Azure Database for PostgreSQL uses the "AzurePostgreSQL" connection type with the
    # "AzurePostgreSQL.Database" creation method (verified against
    # /connections/supportedConnectionTypes). A self-hosted server would instead use
    # type "PostgreSQL" / creation method "PostgreSql"; both are overridable.
    conn_type = os.environ.get("CONNECTION_TYPE", "AzurePostgreSQL").strip()
    creation_method = os.environ.get("CONNECTION_CREATION_METHOD", "AzurePostgreSQL.Database").strip()
    body = {
        "connectivityType": "ShareableCloud",
        "displayName": CONNECTION_NAME,
        "connectionDetails": {
            "type": conn_type,
            "creationMethod": creation_method,
            "parameters": [
                {"dataType": "Text", "name": "server", "value": server},
                {"dataType": "Text", "name": "database", "value": SOURCE_DATABASE},
            ],
        },
        "privacyLevel": "Organizational",
        "credentialDetails": {
            "singleSignOnType": "None",
            "connectionEncryption": "Encrypted",
            "skipTestConnection": False,
            "credentials": {
                "credentialType": "Basic",
                "username": user,
                "password": password,
            },
        },
    }
    status, payload = fabric_lro("POST", "/connections", body)
    if status not in (200, 201) or not isinstance(payload, dict) or not payload.get("id"):
        fail("failed to create the Fabric connection to the source PostgreSQL server "
             f"({status}): {json.dumps(payload)[:800]}. Pre-create the connection in the "
             "Fabric portal and re-run with CONNECTION_ID set.")
    conn_id = payload["id"]
    log(f"created connection '{CONNECTION_NAME}' = {conn_id}")
    return conn_id


def find_mirrored_database(ws_id: str, name: str) -> str:
    _, _, payload = _request("GET", f"{FABRIC_API_BASE}/workspaces/{ws_id}/mirroredDatabases", FABRIC_TOKEN)
    for item in (payload or {}).get("value", []):
        if item.get("displayName") == name:
            return item.get("id", "")
    return ""


def mirroring_definition(connection_id: str, tables: list[str], retention_days: int) -> dict:
    mounted = [
        {"source": {"typeProperties": {"schemaName": SOURCE_SCHEMA, "tableName": t}}}
        for t in tables
    ]
    return {
        "properties": {
            "source": {
                "type": "AzurePostgreSql",
                "typeProperties": {
                    "connection": connection_id,
                    "database": SOURCE_DATABASE,
                },
            },
            "target": {
                "type": "MountedRelationalDatabase",
                "typeProperties": {
                    "defaultSchema": SOURCE_SCHEMA,
                    "format": "Delta",
                    "retentionInDays": retention_days,
                },
            },
            "mountedTables": mounted,
        }
    }


def ensure_mirrored_database(ws_id: str, connection_id: str, tables: list[str],
                             retention_days: int) -> str:
    existing = find_mirrored_database(ws_id, MIRRORED_DATABASE_NAME)
    if existing:
        # Changing the mounted table set on a running mirror requires stop + reseed, so an
        # existing item is reused as-is (idempotent). Delete it manually to re-shape the mirror.
        log(f"reusing existing mirrored database '{MIRRORED_DATABASE_NAME}' = {existing}")
        return existing
    definition = mirroring_definition(connection_id, tables, retention_days)
    body = {
        "displayName": MIRRORED_DATABASE_NAME,
        "description": "Waypoint FabricIQ source mirror of the operational financial core (IaC).",
        "definition": {"parts": [b64_part("mirroring.json", definition)]},
    }
    status, payload = fabric_lro("POST", f"/workspaces/{ws_id}/mirroredDatabases", body)
    if status not in (200, 201) or not isinstance(payload, dict) or not payload.get("id"):
        fail(f"failed to create the mirrored database ({status}): {json.dumps(payload)[:800]}")
    item_id = payload["id"]
    log(f"created mirrored database '{MIRRORED_DATABASE_NAME}' = {item_id}")
    return item_id


def ensure_workspace_role_assignment(ws_id: str, principal_id: str, role: str) -> None:
    """Idempotently grant a workspace role to a service principal / managed identity.

    Required for the source PostgreSQL server's system-assigned managed identity (SAMI):
    azure_cdc writes the mirrored Delta tables to OneLake AS the server SAMI, so without a
    write-capable workspace role (Contributor) startMirroring reports 'Running' but every table
    fails with CDC_ERR_SYS_ONELAKE_PERMISSION_DENIED and no data ever lands. Look-up-before-create
    so re-runs are no-ops; best-effort under MIRROR_STRICT=false.
    """
    if not principal_id:
        log("SAMI object id not provided; skipping workspace role grant "
            "(mirroring will fail with CDC_ERR_SYS_ONELAKE_PERMISSION_DENIED if it is not already a member).")
        return
    # Already assigned?
    url = f"{FABRIC_API_BASE}/workspaces/{ws_id}/roleAssignments"
    while url:
        status, _, payload = _request("GET", url, FABRIC_TOKEN)
        if status not in (200, 201) or not isinstance(payload, dict):
            break
        for ra in payload.get("value", []):
            if (ra.get("principal") or {}).get("id") == principal_id:
                log(f"workspace role for SAMI {principal_id} already present ({ra.get('role')}).")
                return
        token = payload.get("continuationToken")
        url = (f"{FABRIC_API_BASE}/workspaces/{ws_id}/roleAssignments"
               f"?continuationToken={urllib.parse.quote(token)}") if token else ""
    body = {"principal": {"id": principal_id, "type": "ServicePrincipal"}, "role": role}
    status, _, payload = _request(
        "POST", f"{FABRIC_API_BASE}/workspaces/{ws_id}/roleAssignments", FABRIC_TOKEN, body)
    if status in (200, 201):
        log(f"granted workspace {role} to source-server SAMI {principal_id}.")
        return
    text = json.dumps(payload)[:400] if isinstance(payload, dict) else str(payload)[:400]
    # 400/409 on an existing assignment is a benign race; anything else is real.
    if status in (400, 409):
        log(f"workspace role grant for SAMI {principal_id} returned {status} (already present?): {text}")
        return
    msg = f"failed to grant workspace {role} to SAMI {principal_id}: {status} {text}"
    if MIRROR_STRICT:
        fail(msg)
    log("WARN: " + msg)


def start_mirroring(ws_id: str, item_id: str) -> None:
    status, payload = fabric_lro("POST", f"/workspaces/{ws_id}/mirroredDatabases/{item_id}/startMirroring")
    if status in (200, 201, 202):
        log("start mirroring accepted.")
        return
    # Creating a mirrored database auto-starts mirroring, so an immediate startMirroring call
    # commonly returns 400 "OperationNotAllowedInCurrentStatus: Initializing"; likewise starting
    # an already-running mirror is a benign no-op. Surface anything else per MIRROR_STRICT.
    text = json.dumps(payload)[:800] if payload is not None else ""
    lowered = text.lower()
    benign = (
        status == 409
        or "running" in lowered
        or "already" in lowered
        or "initializ" in lowered
        or "operationnotallowedincurrentstatus" in lowered
        or "not allowed in current status" in lowered
    )
    if benign:
        log("mirror already initializing/running (start is a no-op).")
        return
    msg = f"startMirroring returned {status}: {text}"
    if MIRROR_STRICT:
        fail(msg)
    log(f"! {msg} (MIRROR_STRICT=false; continuing)")


def _enablement_hint(text: str) -> str:
    return (
        "\n  The source PostgreSQL server is not enabled for Fabric Mirroring.\n"
        "  IaC provisions the SAMI + capacity params, but the azure_cdc preload +\n"
        "  per-database registration is a one-time PORTAL step with no public API:\n"
        "    Azure portal -> the flexible server -> Fabric mirroring -> Get started ->\n"
        "    Prepare -> Restart (wait for 'ready for mirroring'), then re-run deploy.\n"
        "  See docs/fabric-iq.md 'Source-server enablement (one-time)'."
    )


def poll_status(ws_id: str, item_id: str) -> None:
    # getMirroringStatus is a POST action (not a GET), returning {"status": "..."}.
    for _ in range(20):
        status, payload = fabric_lro("POST", f"/workspaces/{ws_id}/mirroredDatabases/{item_id}/getMirroringStatus")
        state = (payload or {}).get("status") if isinstance(payload, dict) else None
        if state:
            log(f"mirroring status = {state}")
            if state in ("Running", "Initialized"):
                return
            if state in ("Stopped", "Failed"):
                detail = json.dumps(payload)[:600]
                msg = f"mirroring entered '{state}': {detail}"
                low = detail.lower()
                if "not ready for mirroring" in low or "select enable" in low or "fabric mirroring blade" in low:
                    msg += _enablement_hint(detail)
                if MIRROR_STRICT:
                    fail(msg)
                log(f"! {msg} (MIRROR_STRICT=false)")
                return
        time.sleep(10)
    log("mirroring status did not reach 'Running' within the poll window "
        "(replication continues asynchronously; check the Fabric portal).")


def emit_output(key: str, value: str) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main() -> None:
    if not FABRIC_TOKEN:
        fail("FABRIC_TOKEN is required.")
    server = os.environ.get("SOURCE_SERVER", "").strip()
    if not server:
        fail("SOURCE_SERVER (source PostgreSQL FQDN) is required.")
    retention_days = int(os.environ.get("RETENTION_DAYS", "7"))
    if not 1 <= retention_days <= 30:
        fail("RETENTION_DAYS must be between 1 and 30.")
    tables = [t.strip() for t in os.environ.get("MIRROR_TABLES", DEFAULT_TABLES).split(",") if t.strip()]
    if not tables:
        fail("MIRROR_TABLES resolved to an empty list.")

    ws_id = resolve_workspace()
    log(f"workspace        = {ws_id}")
    log(f"source           = {server} / {SOURCE_DATABASE} (schema {SOURCE_SCHEMA})")
    log(f"mirrored tables  = {', '.join(tables)}")

    connection_id = ensure_connection(server)
    item_id = ensure_mirrored_database(ws_id, connection_id, tables, retention_days)
    emit_output("mirrored_database_id", item_id)
    emit_output("mirrored_database_name", MIRRORED_DATABASE_NAME)
    emit_output("mirror_connection_id", connection_id)

    # Grant the source server's SAMI a write-capable workspace role BEFORE starting mirroring:
    # azure_cdc writes the Delta tables to OneLake as this identity. Without it the mirror shows
    # 'Running' but every table fails with CDC_ERR_SYS_ONELAKE_PERMISSION_DENIED.
    ensure_workspace_role_assignment(
        ws_id,
        os.environ.get("SOURCE_SERVER_SAMI_OBJECT_ID", "").strip(),
        os.environ.get("SOURCE_SERVER_SAMI_ROLE", "Contributor").strip() or "Contributor",
    )

    log("Starting mirroring...")
    start_mirroring(ws_id, item_id)
    poll_status(ws_id, item_id)
    log("Done. FabricIQ source mirror is provisioned.")


if __name__ == "__main__":
    main()
