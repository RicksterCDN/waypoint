"""Ensure the ``waypoint-iq`` durable toolbox authenticates to Waypoint with a
per-agent **agentic identity**, not the Foundry project/account managed identity.

.. note::
   This is a **fallback** path. The canonical, durable auth is
   ``managed_identity`` via the Foundry project MI — see
   ``scripts/ensure_waypoint_iq_managed_identity.py`` and ``docs/WAYPOINTIQ.md``.
   Prefer that path (grant the project MI ``Waypoint.Read`` and add its app id to
   Waypoint's ``APP_MSAL_ALLOWED_APP_IDS``). Only use this agentic-identity
   variant when the project MI cannot be allow-listed in Waypoint; agentic-token
   minting depends on per-call agent context that has proven fragile.

Why this exists
---------------
The ``waypoint-iq`` toolbox is an OpenAPI toolbox over our first-party Waypoint
REST API. Waypoint validates app-only callers against BOTH a Waypoint app role
(``Waypoint.Read``) AND an allow-list of client app ids
(``APP_MSAL_ALLOWED_APP_IDS``). The Foundry project/account managed identity is
NOT on that allow-list, so a ``managed_identity`` toolbox auth 401s from the
hired-teammate (Teams) surface.

The fix is to auth the toolbox's downstream call as the *agent's own*
AgentInstance identity (e.g. the invoice-analyst AgentIdentity), which Seth's
Waypoint config already allow-lists and grants ``Waypoint.Read``. In Foundry that
is a project connection of ``authType=AgenticIdentityToken`` (category
``RemoteTool``) bound to the Waypoint audience; the toolbox's OpenAPI tool then
references that connection instead of ``managed_identity``.

This script is idempotent and does two things:

  1. Ensures the ``waypoint-iq-agentic`` project connection exists with the right
     authType / target / audience (ARM PUT; safe to re-run).
  2. Reads the ``waypoint-iq`` toolbox's current default version and, if its
     OpenAPI tool is not already pointed at the connection, creates a new version
     with the auth flipped and promotes it to ``default_version``.

It deliberately does NOT author the Waypoint OpenAPI spec itself: the toolbox is
externally provisioned (it ships with Waypoint's Foundry integration). We only
read the current version and swap the auth block, preserving everything else.

Requirements
------------
* ``az`` CLI logged in to the target tenant/subscription (used for tokens).
* ``FOUNDRY_PROJECT_ENDPOINT`` set, e.g.
  ``https://<account>.services.ai.azure.com/api/projects/<project>``.

The Waypoint ``--target`` (container URL) and ``--audience`` (``api://<app-id>``)
are environment-specific; the defaults below match the primary Forge environment.
Override them for a different environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

ARM_API_VERSION = "2025-04-01-preview"
TOOLBOX_API_VERSION = "v1"
TOOLBOX_FEATURE_HEADER = "Foundry-Features=Toolboxes=V1Preview"
AI_DATAPLANE_RESOURCE = "https://ai.azure.com"

# Primary Forge-environment defaults (override via flags/env for other envs).
DEFAULT_CONNECTION = "waypoint-iq-agentic"
DEFAULT_TOOLBOX = "waypoint-iq"
DEFAULT_TARGET = "https://api.grayflower-2758f17b.swedencentral.azurecontainerapps.io"
DEFAULT_AUDIENCE = os.environ.get("WAYPOINT_AUDIENCE") or os.environ.get("WAYPOINT_API_SCOPE") or ""


def _run(cmd: list[str], *, input_str: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_str,
        capture_output=True,
        text=True,
    )


def _az_json(args: list[str]) -> object:
    proc = _run(["az", *args])
    if proc.returncode != 0:
        raise RuntimeError(f"az {' '.join(args)} failed:\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def _parse_endpoint(endpoint: str) -> tuple[str, str]:
    """Return (account_name, project_name) from a Foundry project endpoint."""
    host = urlparse(endpoint).hostname or ""
    account = host.split(".")[0]
    path = urlparse(endpoint).path.rstrip("/")
    if "/projects/" not in path:
        raise ValueError(f"Cannot parse project from endpoint path: {path!r}")
    project = path.rsplit("/projects/", 1)[1].split("/")[0]
    if not account or not project:
        raise ValueError(f"Could not derive account/project from {endpoint!r}")
    return account, project


def _resolve_account_scope(account: str) -> tuple[str, str]:
    """Return (subscription_id, resource_group) for the Cognitive Services account."""
    accounts = _az_json(["cognitiveservices", "account", "list", "-o", "json"]) or []
    for acc in accounts:
        if acc.get("name") == account:
            rid = acc["id"]
            # /subscriptions/<sub>/resourceGroups/<rg>/providers/...
            parts = rid.split("/")
            sub = parts[parts.index("subscriptions") + 1]
            rg = parts[parts.index("resourceGroups") + 1]
            return sub, rg
    raise RuntimeError(
        f"Cognitive Services account {account!r} not found in the current "
        "subscription context. Run `az account set --subscription <id>`."
    )


def _arm_rest(method: str, url: str, body: dict | None = None) -> object:
    cmd = ["az", "rest", "--method", method, "--url", url,
           "--headers", "Content-Type=application/json"]
    if body is not None:
        cmd += ["--body", json.dumps(body)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ARM {method} {url} failed:\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def _toolbox_rest(method: str, url: str, body: dict | None = None) -> object:
    cmd = ["az", "rest", "--method", method, "--url", url,
           "--resource", AI_DATAPLANE_RESOURCE,
           "--headers", TOOLBOX_FEATURE_HEADER]
    if body is not None:
        cmd += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"Toolbox {method} {url} failed:\n{proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def ensure_connection(sub: str, rg: str, account: str, project: str,
                      name: str, target: str, audience: str) -> None:
    base = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project}/connections/{name}?api-version={ARM_API_VERSION}"
    )
    desired = {
        "authType": "AgenticIdentityToken",
        "category": "RemoteTool",
        "target": target,
        "audience": audience,
        "isSharedToAll": False,
    }
    try:
        existing = _arm_rest("get", base)
    except RuntimeError:
        existing = None

    if existing:
        props = existing.get("properties", {})
        if (props.get("authType") == desired["authType"]
                and props.get("target") == desired["target"]
                and props.get("audience") == desired["audience"]):
            print(f"[connection] '{name}' already correct (AgenticIdentityToken).")
            return
        print(f"[connection] '{name}' exists but drifted; updating...")
    else:
        print(f"[connection] creating '{name}' (AgenticIdentityToken)...")

    _arm_rest("put", base, {"properties": desired})
    print(f"[connection] '{name}' ensured -> target={target} audience={audience}")


def _openapi_tool(version: dict) -> dict | None:
    for tool in version.get("tools", []):
        if tool.get("type") == "openapi":
            return tool
    return None


def ensure_toolbox_auth(endpoint: str, toolbox: str, connection: str) -> None:
    ep = endpoint.rstrip("/")
    tb = _toolbox_rest("get", f"{ep}/toolboxes/{toolbox}?api-version={TOOLBOX_API_VERSION}")
    if not tb:
        raise RuntimeError(
            f"Toolbox {toolbox!r} not found. It is provisioned with Waypoint's "
            "Foundry integration; this script only re-points its auth."
        )
    default_version = tb.get("default_version")
    if not default_version:
        raise RuntimeError(f"Toolbox {toolbox!r} has no default_version to read.")

    version = _toolbox_rest(
        "get",
        f"{ep}/toolboxes/{toolbox}/versions/{default_version}?api-version={TOOLBOX_API_VERSION}",
    )
    tool = _openapi_tool(version)
    if tool is None:
        raise RuntimeError(
            f"Toolbox {toolbox!r} default version {default_version} has no OpenAPI tool."
        )

    desired_auth = {
        "type": "project_connection",
        "security_scheme": {"project_connection_id": connection},
    }
    current_auth = tool["openapi"].get("auth")
    if current_auth == desired_auth:
        print(
            f"[toolbox] '{toolbox}' v{default_version} already uses connection "
            f"'{connection}'. Nothing to do."
        )
        return

    print(
        f"[toolbox] '{toolbox}' v{default_version} auth = "
        f"{json.dumps(current_auth)}; flipping to project_connection -> '{connection}'."
    )
    tool["openapi"]["auth"] = desired_auth

    new_version_body = {
        "description": version.get("description")
        or "WaypointIQ agentic-identity OpenAPI toolbox (per-agent identity).",
        "tools": version["tools"],
    }
    if version.get("metadata"):
        new_version_body["metadata"] = version["metadata"]

    created = _toolbox_rest(
        "post",
        f"{ep}/toolboxes/{toolbox}/versions?api-version={TOOLBOX_API_VERSION}",
        new_version_body,
    )
    new_version = created.get("version")
    print(f"[toolbox] created version {new_version}.")

    _toolbox_rest(
        "patch",
        f"{ep}/toolboxes/{toolbox}?api-version={TOOLBOX_API_VERSION}",
        {"default_version": new_version},
    )
    print(f"[toolbox] promoted v{new_version} to default_version.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connection", default=DEFAULT_CONNECTION)
    parser.add_argument("--toolbox", default=DEFAULT_TOOLBOX)
    parser.add_argument("--target", default=os.environ.get("WAYPOINT_TARGET", DEFAULT_TARGET),
                        help="Waypoint container app URL (environment-specific).")
    parser.add_argument("--audience", default=os.environ.get("WAYPOINT_AUDIENCE", DEFAULT_AUDIENCE),
                        help="Waypoint app audience api://<app-id> (environment-specific).")
    parser.add_argument("--connection-only", action="store_true",
                        help="Only ensure the connection; skip the toolbox bump.")
    args = parser.parse_args()

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("::error::FOUNDRY_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 1

    account, project = _parse_endpoint(endpoint)
    sub, rg = _resolve_account_scope(account)
    print(f"[scope] account={account} project={project} sub={sub} rg={rg}")

    ensure_connection(sub, rg, account, project,
                      args.connection, args.target, args.audience)

    if not args.connection_only:
        ensure_toolbox_auth(endpoint, args.toolbox, args.connection)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
