#!/usr/bin/env python3
"""Idempotent, notebook-free provisioning of the Waypoint "Fabric IQ" AI layer.

This is the IaC replacement for the interactive
`Deploy_EnterpriseLakehouse_VendorModel` notebook's *AI* steps. It uses **only** the
Microsoft Fabric public REST API + the OneLake DFS REST endpoint (no Spark, no
`sempy_labs`, no `notebookutils`), so it runs on any CI runner or from the azd
`postprovision` hook. It:

  1. resolves the workspace + grounding source (a mirrored Azure Database for PostgreSQL
     item by default, or the legacy keystone lakehouse) + its SQL analytics endpoint,
  2. determines each table's typed column schema — for the mirrored operational core the
     schema is the authoritative typed projection declared in ``MIRRORED_TABLE_SCHEMA``
     (kept in lock-step with the API's ``repository._PROJECTED_COLUMNS``); for a lakehouse
     it is discovered by reading each table's Delta transaction log over OneLake (pure HTTPS),
  3. builds/refreshes a **Direct Lake** semantic model (TMSL ``model.bim``) over those
     tables, grounded for AI with model/table/column descriptions,
  4. creates/refreshes and **publishes** a **Fabric Data Agent** wired to that semantic
     model, with steering instructions.

Every create is look-up-before-write and re-runnable: an existing item is updated in
place via ``updateDefinition`` instead of being duplicated.

The heavy REST orchestration lives here; ``provision-fabric-iq.sh`` is a thin wrapper
that gates execution and acquires the Entra tokens this module consumes via env vars.

Consumed environment (all set by the wrapper / deploy workflow):
  FABRIC_TOKEN            Bearer token for https://api.fabric.microsoft.com (required)
  STORAGE_TOKEN          Bearer token for https://storage.azure.com — OneLake reads (required)
  POWERBI_TOKEN          Bearer token for the Power BI API — optional Direct Lake refresh
  FABRIC_API_BASE        Defaults to https://api.fabric.microsoft.com/v1
  ONELAKE_DFS_BASE       Defaults to https://onelake.dfs.fabric.microsoft.com
  WORKSPACE_ID           Fabric workspace GUID (preferred)
  WORKSPACE_NAME         Fabric workspace display name (used if WORKSPACE_ID is empty)
  IQ_SOURCE_TYPE         "mirrored" (default) grounds the model on the mirrored operational
                         Postgres financial core; "lakehouse" grounds it on the keystone corpus
                         Delta tables (legacy behaviour).
  MIRRORED_DATABASE_NAME Mirrored Azure Database for PostgreSQL item display name
                         (required when IQ_SOURCE_TYPE=mirrored; default WaypointMirror)
  MIRRORED_SCHEMA_NAME   SQL schema the mirrored tables surface under (default "_public";
                         the endpoint prefixes the source "public" schema with "_")
  LAKEHOUSE_NAME         Lakehouse display name (required when IQ_SOURCE_TYPE=lakehouse)
  SEMANTIC_MODEL_NAME    Direct Lake model display name (default CaldovaIQ)
  DATA_AGENT_NAME        Data Agent display name (default WaypointDataAgent)
  TABLES                 Optional comma-separated allow-list; default = all managed tables
  AGENT_STRICT           "true" (default) fails the run if the Data Agent step errors
  AGENT_INSTRUCTIONS     Optional override for the Data Agent AI instructions
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
import uuid

FABRIC_API_BASE = os.environ.get("FABRIC_API_BASE", "https://api.fabric.microsoft.com/v1").rstrip("/")
ONELAKE_DFS_BASE = os.environ.get("ONELAKE_DFS_BASE", "https://onelake.dfs.fabric.microsoft.com").rstrip("/")
FABRIC_TOKEN = os.environ.get("FABRIC_TOKEN", "")
STORAGE_TOKEN = os.environ.get("STORAGE_TOKEN", "")
POWERBI_TOKEN = os.environ.get("POWERBI_TOKEN", "")

SEMANTIC_MODEL_NAME = os.environ.get("SEMANTIC_MODEL_NAME") or "CaldovaIQ"
DATA_AGENT_NAME = os.environ.get("DATA_AGENT_NAME") or "WaypointDataAgent"
AGENT_STRICT = (os.environ.get("AGENT_STRICT", "true").lower() == "true")

# Prior display names the semantic model may already exist under in a workspace that was
# provisioned before a rename. On provision we rename such a legacy item IN PLACE (preserving
# its GUID) to SEMANTIC_MODEL_NAME instead of creating a duplicate — this keeps the Data Agent's
# `artifactId` binding and every GUID-based downstream consumer (Forge's Foundry->Fabric
# connection, workflow `semantic_model_id` output) intact across the rename. Override/extend via
# SEMANTIC_MODEL_LEGACY_NAMES (comma-separated); the default carries the historical "WaypointIQ".
SEMANTIC_MODEL_LEGACY_NAMES = [
    n.strip()
    for n in (os.environ.get("SEMANTIC_MODEL_LEGACY_NAMES") or "WaypointIQ").split(",")
    if n.strip() and n.strip() != SEMANTIC_MODEL_NAME
]

# Grounding source. "mirrored" (default) = the mirrored operational Postgres financial core;
# "lakehouse" = the legacy keystone corpus Delta tables.
IQ_SOURCE_TYPE = (os.environ.get("IQ_SOURCE_TYPE") or "mirrored").strip().lower()
MIRRORED_DATABASE_NAME = os.environ.get("MIRRORED_DATABASE_NAME") or "WaypointMirror"
# Azure Database for PostgreSQL mirroring preserves the source schema NAME, but the mirrored SQL
# analytics endpoint materializes the source "public" schema as "_public" (underscore-prefixed):
# "public" is a T-SQL reserved keyword, so Fabric prefixes it with "_" to keep it addressable.
# The Direct Lake partition schemaName MUST therefore be "_public", not "public", or framing fails
# with "cannot access the source Delta table". Verified live via INFORMATION_SCHEMA on the endpoint.
MIRRORED_SCHEMA_NAME = os.environ.get("MIRRORED_SCHEMA_NAME") or "_public"

# Authoritative typed schema of the mirrored operational financial core. This is the Fabric-facing
# projection of the four transactional tables and MUST stay in lock-step with the API's
# `PostgresWaypointRepository` -> `_PROJECTED_COLUMNS` + `_SCHEMA_SQL` (join keys + id + updated_at).
# `jsonb payload` is intentionally excluded (Fabric Mirroring cannot replicate json/jsonb). Types are
# TMSL/Analysis-Services dataTypes: text/keys -> string, date/timestamptz -> dateTime, numeric -> decimal.
# `infra/scripts/test_fabric_iq_schema.py` guards this against drift from the repository projection.
MIRRORED_TABLE_SCHEMA: dict[str, list[tuple[str, str]]] = {
    "suppliers": [
        ("id", "string"),
        ("name", "string"),
        ("status", "string"),
        ("category", "string"),
        ("updated_at", "dateTime"),
    ],
    "invoices": [
        ("id", "string"),
        ("supplier_id", "string"),
        ("scenario_id", "string"),
        ("invoice_number", "string"),
        ("invoice_date", "dateTime"),
        ("due_date", "dateTime"),
        ("status", "string"),
        ("currency", "string"),
        ("total_amount", "decimal"),
        ("updated_at", "dateTime"),
    ],
    "invoice_lines": [
        ("id", "string"),
        ("invoice_id", "string"),
        ("quantity", "decimal"),
        ("unit_price", "decimal"),
        ("amount", "decimal"),
        ("sku", "string"),
        ("purchase_order", "string"),
        ("updated_at", "dateTime"),
    ],
    "reconciliation_findings": [
        ("id", "string"),
        ("invoice_id", "string"),
        ("scenario_id", "string"),
        ("category", "string"),
        ("severity", "string"),
        ("status", "string"),
        ("overpayment_amount", "decimal"),
        ("updated_at", "dateTime"),
    ],
}

DEFAULT_AGENT_INSTRUCTIONS = (
    "You are a contract-manufacturing (CMO) invoice-assurance analyst for Waypoint. Answer "
    "questions using the Direct Lake semantic model over the operational financial core mirrored "
    "from Postgres: invoices, invoice_lines and reconciliation_findings are the core fact/detail "
    "tables and suppliers provides context. Explain invoice leakage and overbilling: quantify money "
    "at risk from reconciliation_findings.overpayment_amount, attribute it to suppliers and invoices, "
    "and cite the finding category, severity and status. Prefer exact business-key lookups "
    "(supplier + invoice number). State the invoice number, supplier and the figures behind every "
    "conclusion, and never expose IP-sensitive or restricted context that the model does not return."
)

# --- Spark/Delta type -> TMSL (Analysis Services) dataType -------------------------------
_TYPE_MAP = {
    "string": "string",
    "long": "int64",
    "integer": "int64",
    "int": "int64",
    "short": "int64",
    "byte": "int64",
    "double": "double",
    "float": "double",
    "boolean": "boolean",
    "binary": "binary",
    "date": "dateTime",
    "timestamp": "dateTime",
    "timestamp_ntz": "dateTime",
}


def log(msg: str) -> None:
    print(f"[fabric-iq] {msg}", flush=True)


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[fabric-iq] ERROR: {msg}", file=sys.stderr, flush=True)
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


def fabric_lro(method: str, path: str, body: dict | None = None,
               query: str = "") -> dict | None:
    """Call a Fabric REST endpoint and resolve the long-running-operation if returned.

    Fabric item create / updateDefinition may return 202 with an ``Operation-Location``
    header we must poll until the operation succeeds.
    """
    url = f"{FABRIC_API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    status, headers, payload = _request(method, url, FABRIC_TOKEN, body)

    if status in (200, 201):
        return payload if isinstance(payload, dict) else None
    if status == 202:
        op_url = headers.get("Operation-Location") or headers.get("Location")
        if not op_url:
            return None
        retry = int(headers.get("Retry-After", "5") or "5")
        for _ in range(60):
            time.sleep(max(retry, 3))
            s, h, p = _request("GET", op_url, FABRIC_TOKEN)
            state = (p or {}).get("status") if isinstance(p, dict) else None
            if state in ("Succeeded", "Completed"):
                # Fetch the operation result when a result endpoint is offered.
                result_url = h.get("Location")
                if result_url:
                    _, _, rp = _request("GET", result_url, FABRIC_TOKEN)
                    return rp if isinstance(rp, dict) else (p if isinstance(p, dict) else None)
                return p if isinstance(p, dict) else None
            if state in ("Failed", "Undefined"):
                fail(f"Fabric operation failed for {method} {path}: {json.dumps(p)[:800]}")
            retry = int(h.get("Retry-After", str(retry)) or retry)
        fail(f"Fabric operation timed out polling for {method} {path}")
    fail(f"Fabric {method} {path} returned {status}: {json.dumps(payload)[:800]}")
    return None


def b64_part(path: str, obj) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    return {
        "path": path,
        "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


# --- Workspace / lakehouse resolution ---------------------------------------------------
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


def resolve_lakehouse(ws_id: str, name: str) -> dict:
    _, _, payload = _request("GET", f"{FABRIC_API_BASE}/workspaces/{ws_id}/lakehouses", FABRIC_TOKEN)
    for lh in (payload or {}).get("value", []):
        if lh.get("displayName") == name:
            return lh
    fail(f"lakehouse '{name}' not found in workspace {ws_id}.")
    return {}


def sql_endpoint(lakehouse: dict) -> tuple[str, str]:
    props = lakehouse.get("properties", {}) or {}
    sep = props.get("sqlEndpointProperties", {}) or {}
    server = sep.get("connectionString", "")
    # For a lakehouse SQL analytics endpoint the database name is the lakehouse name.
    database = lakehouse.get("displayName", "")
    if not server:
        fail("lakehouse SQL analytics endpoint is not provisioned yet (connectionString empty). "
             "Re-run after the SQL endpoint finishes provisioning.")
    return server, database


def resolve_mirrored_database(ws_id: str, name: str) -> dict:
    _, _, payload = _request("GET", f"{FABRIC_API_BASE}/workspaces/{ws_id}/mirroredDatabases", FABRIC_TOKEN)
    for item in (payload or {}).get("value", []):
        if item.get("displayName") == name:
            return item
    fail(f"mirrored database '{name}' not found in workspace {ws_id}. "
         "Provision it first with provision-fabric-mirror.sh (WAYPOINT_FABRIC_MIRROR_ENABLED=true).")
    return {}


def mirrored_sql_endpoint(ws_id: str, item: dict) -> tuple[str, str]:
    """Resolve the (server, database) of a mirrored database's SQL analytics endpoint.

    The list payload may not inline sqlEndpointProperties, so fall back to a GET on the item.
    """
    props = item.get("properties", {}) or {}
    sep = props.get("sqlEndpointProperties", {}) or {}
    server = sep.get("connectionString", "")
    if not server:
        item_id = item.get("id", "")
        _, _, full = _request(
            "GET", f"{FABRIC_API_BASE}/workspaces/{ws_id}/mirroredDatabases/{item_id}", FABRIC_TOKEN)
        sep = ((full or {}).get("properties", {}) or {}).get("sqlEndpointProperties", {}) or {}
        server = sep.get("connectionString", "")
    database = item.get("displayName", "")
    if not server:
        fail("mirrored database SQL analytics endpoint is not provisioned yet (connectionString "
             "empty). Re-run after mirroring has initialized and the SQL endpoint is ready.")
    return server, database


def mirrored_table_schema() -> dict[str, list[dict]]:
    """The authoritative typed projection of the mirrored operational core (no OneLake reads)."""
    return {
        table: [{"name": col, "dataType": dtype, "sourceColumn": col} for col, dtype in cols]
        for table, cols in MIRRORED_TABLE_SCHEMA.items()
    }


def list_tables(ws_id: str, lh_id: str) -> list[str]:
    names: list[str] = []
    url = f"{FABRIC_API_BASE}/workspaces/{ws_id}/lakehouses/{lh_id}/tables"
    while url:
        _, _, payload = _request("GET", url, FABRIC_TOKEN)
        # The Fabric lakehouse "list tables" API returns the array under "data"; some
        # other Fabric list endpoints use "value" — accept either for safety.
        rows = (payload or {}).get("data")
        if rows is None:
            rows = (payload or {}).get("value", [])
        for t in rows:
            if (t.get("type") or "Managed") == "Managed":
                names.append(t["name"])
        token = (payload or {}).get("continuationToken")
        url = f"{FABRIC_API_BASE}/workspaces/{ws_id}/lakehouses/{lh_id}/tables?continuationToken={urllib.parse.quote(token)}" if token else ""
    return names


# --- Delta-log schema discovery over OneLake (pure HTTPS, no Spark) ----------------------
def _dfs_list(ws_id: str, directory: str) -> list[str]:
    url = (f"{ONELAKE_DFS_BASE}/{ws_id}?recursive=false&resource=filesystem"
           f"&directory={urllib.parse.quote(directory)}")
    status, _, payload = _request("GET", url, STORAGE_TOKEN,
                                  extra_headers={"x-ms-version": "2021-08-06"})
    if status != 200 or not isinstance(payload, dict):
        return []
    return [p["name"] for p in payload.get("paths", []) if not p.get("isDirectory")]


def _dfs_read(ws_id: str, path: str) -> str:
    url = f"{ONELAKE_DFS_BASE}/{ws_id}/{urllib.parse.quote(path)}"
    status, _, payload = _request("GET", url, STORAGE_TOKEN,
                                  extra_headers={"x-ms-version": "2021-08-06"})
    if status != 200:
        return ""
    return payload if isinstance(payload, str) else json.dumps(payload)


def table_columns(ws_id: str, lh_id: str, table: str) -> list[dict]:
    """Return [{name, dataType, sourceColumn}] by parsing the Delta log's latest schema."""
    log_dir = f"{lh_id}/Tables/{table}/_delta_log"
    files = _dfs_list(ws_id, log_dir)
    commits = sorted(f for f in files if f.endswith(".json"))
    schema_string = None
    for commit in commits:  # ascending -> last metaData wins (schema evolution)
        body = _dfs_read(ws_id, commit)
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                action = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = action.get("metaData")
            if meta and meta.get("schemaString"):
                schema_string = meta["schemaString"]
    if not schema_string:
        log(f"  ! could not read a Delta schema for '{table}'; skipping it.")
        return []
    try:
        schema = json.loads(schema_string)
    except json.JSONDecodeError:
        return []
    columns = []
    for field in schema.get("fields", []):
        ftype = field.get("type")
        if isinstance(ftype, dict):
            data_type = "string"  # struct/array/map -> unsupported in Direct Lake column
        else:
            base = str(ftype).split("(")[0].strip().lower()
            data_type = _TYPE_MAP.get(base, "string")
        columns.append({
            "name": field["name"],
            "dataType": data_type,
            "sourceColumn": field["name"],
        })
    return columns


# --- TMSL (Direct Lake) model builder ---------------------------------------------------
def _summarize_by(data_type: str) -> str:
    return "sum" if data_type in ("int64", "double", "decimal") else "none"


def build_model_bim(server: str, database: str, tables: dict[str, list[dict]],
                    schema_name: str = "dbo", source_label: str = "Lakehouse Delta table",
                    model_description: str | None = None) -> dict:
    model_tables = []
    for name, columns in tables.items():
        model_tables.append({
            "name": name,
            "description": f"{source_label} '{name}' surfaced via Direct Lake.",
            "columns": [
                {
                    "name": c["name"],
                    "dataType": c["dataType"],
                    "sourceColumn": c["sourceColumn"],
                    "summarizeBy": _summarize_by(c["dataType"]),
                }
                for c in columns
            ],
            "partitions": [
                {
                    "name": name,
                    "mode": "directLake",
                    "source": {
                        "type": "entity",
                        "entityName": name,
                        "schemaName": schema_name,
                        "expressionSource": "DatabaseQuery",
                    },
                }
            ],
        })
    m_expression = (
        "let\n"
        f'    database = Sql.Database("{server}", "{database}")\n'
        "in\n"
        "    database"
    )
    return {
        "name": SEMANTIC_MODEL_NAME,
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "description": (
                model_description
                or (
                    "Caldova IQ — Direct Lake analytics over the contract-manufacturing invoice "
                    "assurance lakehouse. Use to quantify invoice leakage/overbilling, attribute it "
                    "to suppliers and invoices, and analyse reconciliation findings over time."
                )
            ),
            "expressions": [
                {"name": "DatabaseQuery", "kind": "m", "expression": m_expression}
            ],
            "tables": model_tables,
            "annotations": [
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "0"},
                {"name": "PBI_ProTooling", "value": "[\"WaypointFabricIQ\"]"},
            ],
        },
    }


# --- Generic Fabric item helpers --------------------------------------------------------
def find_item(ws_id: str, item_type: str, display_name: str) -> str | None:
    _, _, payload = _request("GET", f"{FABRIC_API_BASE}/workspaces/{ws_id}/{item_type}", FABRIC_TOKEN)
    for item in (payload or {}).get("value", []):
        if item.get("displayName") == display_name:
            return item["id"]
    return None


def rename_item(ws_id: str, item_id: str, new_display_name: str) -> None:
    """Rename an existing Fabric item in place (PATCH displayName), preserving its GUID.

    Uses the generic Items PATCH endpoint (synchronous, not an LRO). The stable item id means
    every GUID-based binding (Data Agent `artifactId`, Forge's Foundry->Fabric connection) is
    unaffected by the rename.
    """
    status, _, payload = _request(
        "PATCH",
        f"{FABRIC_API_BASE}/workspaces/{ws_id}/items/{item_id}",
        FABRIC_TOKEN,
        {"displayName": new_display_name},
    )
    if status not in (200, 201):
        fail(f"Fabric PATCH items/{item_id} (rename -> '{new_display_name}') "
             f"returned {status}: {json.dumps(payload)[:800]}")


def upsert_item(ws_id: str, item_type: str, display_name: str, description: str,
                parts: list[dict], legacy_names: list[str] | None = None) -> str:
    existing = find_item(ws_id, item_type, display_name)
    # Self-heal older workspaces: if the item isn't found under its current name but exists under a
    # known legacy name, rename it in place (GUID preserved) and treat it as the existing item.
    if not existing:
        for legacy in legacy_names or []:
            legacy_id = find_item(ws_id, item_type, legacy)
            if legacy_id:
                log(f"  migrating {item_type[:-1]} '{legacy}' -> '{display_name}' "
                    f"in place ({legacy_id}; GUID preserved)")
                rename_item(ws_id, legacy_id, display_name)
                existing = legacy_id
                break
    definition = {"parts": parts}
    if existing:
        log(f"  updating existing {item_type[:-1]} '{display_name}' ({existing})")
        # Only the definition parts change on a re-run; do NOT set updateMetadata (that would
        # require a .platform part we don't ship, and the displayName/description are immutable here).
        fabric_lro("POST", f"/workspaces/{ws_id}/{item_type}/{existing}/updateDefinition",
                   {"definition": definition})
        return existing
    log(f"  creating {item_type[:-1]} '{display_name}'")
    result = fabric_lro("POST", f"/workspaces/{ws_id}/{item_type}",
                        {"displayName": display_name, "description": description,
                         "definition": definition})
    item_id = (result or {}).get("id") if isinstance(result, dict) else None
    return item_id or find_item(ws_id, item_type, display_name) or ""


# --- Semantic model + Data Agent orchestration ------------------------------------------
def provision_semantic_model(ws_id: str, server: str, database: str,
                             tables: dict[str, list[dict]], schema_name: str = "dbo",
                             source_label: str = "Lakehouse Delta table",
                             model_description: str | None = None) -> str:
    model_bim = build_model_bim(server, database, tables, schema_name=schema_name,
                                source_label=source_label, model_description=model_description)
    pbism = {"version": "4.0", "settings": {}}
    parts = [
        b64_part("definition.pbism", pbism),
        b64_part("model.bim", model_bim),
    ]
    sm_id = upsert_item(ws_id, "semanticModels", SEMANTIC_MODEL_NAME,
                        "Caldova IQ Direct Lake semantic model (provisioned by IaC).", parts,
                        legacy_names=SEMANTIC_MODEL_LEGACY_NAMES)
    log(f"  semantic model id = {sm_id}")
    refresh_semantic_model(ws_id, sm_id)
    return sm_id


def refresh_semantic_model(ws_id: str, sm_id: str) -> None:
    """Best-effort Direct Lake reframe. Non-fatal: Direct Lake also frames on first query."""
    if not POWERBI_TOKEN:
        log("  (no Power BI token; skipping explicit refresh — Direct Lake frames on first query)")
        return
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}/datasets/{sm_id}/refreshes"
    status, _, payload = _request("POST", url, POWERBI_TOKEN, {"type": "full"})
    if status in (200, 202):
        log("  triggered a Direct Lake refresh.")
    else:
        log(f"  (refresh skipped — API returned {status}: {json.dumps(payload)[:200]})")


def provision_data_agent(ws_id: str, sm_id: str) -> str:
    instructions = os.environ.get("AGENT_INSTRUCTIONS") or DEFAULT_AGENT_INSTRUCTIONS
    ds_folder = f"semantic_model-{SEMANTIC_MODEL_NAME}"
    schema = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition"

    data_agent = {"$schema": f"{schema}/dataAgent/2.1.0/schema.json"}
    stage_config = {
        "$schema": f"{schema}/stageConfiguration/1.0.0/schema.json",
        "aiInstructions": instructions,
    }
    datasource = {
        "$schema": f"{schema}/datasource/1.0.0/schema.json",
        "artifactId": sm_id,
        "workspaceId": ws_id,
        "displayName": SEMANTIC_MODEL_NAME,
        "type": "semantic_model",
        "userDescription": "Caldova IQ Direct Lake semantic model over the mirrored operational Postgres financial core.",
        "dataSourceInstructions": (
            "Use for all invoice, supplier, invoice-line and reconciliation-finding questions. "
            "Aggregate reconciliation_findings.overpayment_amount for money at risk; join to "
            "invoices and suppliers via supplier_id / invoice_id."
        ),
    }
    publish_info = {"$schema": f"{schema}/publishInfo/1.0.0/schema.json", "description": ""}

    parts = [
        b64_part("Files/Config/data_agent.json", data_agent),
        b64_part("Files/Config/draft/stage_config.json", stage_config),
        b64_part(f"Files/Config/draft/{ds_folder}/datasource.json", datasource),
        # Including the published/* parts + publish_info.json publishes the agent declaratively.
        b64_part("Files/Config/published/stage_config.json", stage_config),
        b64_part(f"Files/Config/published/{ds_folder}/datasource.json", datasource),
        b64_part("Files/Config/publish_info.json", publish_info),
    ]
    agent_id = upsert_item(ws_id, "dataAgents", DATA_AGENT_NAME,
                           "Caldova IQ Data Agent (provisioned + published by IaC).", parts)
    log(f"  data agent id = {agent_id}")
    return agent_id


def emit_output(key: str, value: str) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


MIRRORED_MODEL_DESCRIPTION = (
    "Caldova IQ — Direct Lake analytics over the operational contract-manufacturing invoice "
    "financial core mirrored from Azure Database for PostgreSQL. Use to quantify invoice "
    "leakage/overbilling from reconciliation findings, attribute it to suppliers and invoices, "
    "and analyse the transactional detail over time."
)


def _resolve_mirrored_source(ws_id: str) -> tuple[str, str, dict[str, list[dict]]]:
    item = resolve_mirrored_database(ws_id, MIRRORED_DATABASE_NAME)
    server, database = mirrored_sql_endpoint(ws_id, item)
    log(f"mirrored db = {MIRRORED_DATABASE_NAME} ({item.get('id', '')})")
    log(f"sql endpoint= {server} / {database} (schema {MIRRORED_SCHEMA_NAME})")
    tables = mirrored_table_schema()
    allow = [t.strip() for t in os.environ.get("TABLES", "").split(",") if t.strip()]
    if allow:
        tables = {t: cols for t, cols in tables.items() if t in allow}
    if not tables:
        fail("no mirrored tables selected — check TABLES / MIRRORED_TABLE_SCHEMA.")
    log(f"tables      = {', '.join(tables)}")
    return server, database, tables


def _resolve_lakehouse_source(ws_id: str) -> tuple[str, str, dict[str, list[dict]]]:
    if not STORAGE_TOKEN:
        fail("STORAGE_TOKEN is required for lakehouse sources (OneLake Delta-log schema reads).")
    lakehouse_name = os.environ.get("LAKEHOUSE_NAME", "").strip()
    if not lakehouse_name:
        fail("LAKEHOUSE_NAME is required when IQ_SOURCE_TYPE=lakehouse.")
    lakehouse = resolve_lakehouse(ws_id, lakehouse_name)
    lh_id = lakehouse["id"]
    server, database = sql_endpoint(lakehouse)
    log(f"lakehouse   = {lakehouse_name} ({lh_id})")
    log(f"sql endpoint= {server} / {database}")

    allow = [t.strip() for t in os.environ.get("TABLES", "").split(",") if t.strip()]
    discovered = list_tables(ws_id, lh_id)
    if allow:
        discovered = [t for t in discovered if t in allow]
    if not discovered:
        msg = ("no managed Delta tables found in the lakehouse — nothing to model. "
               "Ensure the corpus/Tables are written (ledgerfield) before provisioning Fabric IQ.")
        if os.environ.get("IQ_ALLOW_EMPTY", "false").lower() == "true":
            log(f"! {msg}")
            log("IQ_ALLOW_EMPTY=true — skipping this run; re-run after Tables are written (idempotent).")
            raise SystemExit(0)
        fail(msg)
    log(f"tables      = {', '.join(discovered)}")
    tables: dict[str, list[dict]] = {}
    for name in discovered:
        cols = table_columns(ws_id, lh_id, name)
        if cols:
            tables[name] = cols
    if not tables:
        fail("discovered tables but could not read any column schemas from the Delta logs.")
    return server, database, tables


def main() -> None:
    if not FABRIC_TOKEN:
        fail("FABRIC_TOKEN is required.")
    if IQ_SOURCE_TYPE not in ("mirrored", "lakehouse"):
        fail(f"IQ_SOURCE_TYPE must be 'mirrored' or 'lakehouse' (got '{IQ_SOURCE_TYPE}').")

    ws_id = resolve_workspace()
    log(f"workspace   = {ws_id}")
    log(f"source type = {IQ_SOURCE_TYPE}")

    if IQ_SOURCE_TYPE == "mirrored":
        server, database, tables = _resolve_mirrored_source(ws_id)
        schema_name = MIRRORED_SCHEMA_NAME
        source_label = "Mirrored Postgres table"
        model_description: str | None = MIRRORED_MODEL_DESCRIPTION
    else:
        server, database, tables = _resolve_lakehouse_source(ws_id)
        schema_name = "dbo"
        source_label = "Lakehouse Delta table"
        model_description = None

    log("Provisioning Direct Lake semantic model...")
    emit_output("workspace_id", ws_id)
    sm_id = provision_semantic_model(ws_id, server, database, tables, schema_name=schema_name,
                                     source_label=source_label, model_description=model_description)
    emit_output("semantic_model_id", sm_id)
    emit_output("semantic_model_name", SEMANTIC_MODEL_NAME)

    log("Provisioning + publishing Fabric Data Agent...")
    try:
        agent_id = provision_data_agent(ws_id, sm_id)
        emit_output("data_agent_id", agent_id)
        emit_output("data_agent_name", DATA_AGENT_NAME)
    except SystemExit:
        if AGENT_STRICT:
            raise
        log("  ! Data Agent provisioning failed and AGENT_STRICT=false; continuing.")

    log("Done. Fabric IQ is provisioned.")
    log(f"  semantic_model = {SEMANTIC_MODEL_NAME} ({sm_id})")


if __name__ == "__main__":
    main()
