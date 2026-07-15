"""Ensure the ``waypoint-iq`` durable toolbox authenticates to Waypoint with the
Foundry **project managed identity** (the doc-canonical path), not a per-agent
agentic-identity connection.

Why this exists
---------------
The ``waypoint-iq`` toolbox is an OpenAPI toolbox over our first-party Waypoint
REST API. Waypoint validates app-only callers against BOTH a Waypoint app role
(``Waypoint.Read``) AND an allow-list of client app ids
(``APP_MSAL_ALLOWED_APP_IDS``).

The canonical design (see ``docs/WAYPOINTIQ.md`` and ``iqs/waypoint-iq/toolbox.yaml``)
is ``managed_identity`` auth: the Foundry ToolServer calls Waypoint as the
project managed identity, which is granted ``Waypoint.Read`` and is present in
Waypoint's ``APP_MSAL_ALLOWED_APP_IDS``. This is deterministic and stable across
agent redeploys and per-hire identities — unlike the agentic-identity token,
whose minting depends on per-call agent context that has proven fragile.

Prerequisites (one-time, per environment):
  * The Foundry project MI holds the ``Waypoint.Read`` app role on the Waypoint
    API app registration.
  * The Foundry project MI app id is present in Waypoint's
    ``APP_MSAL_ALLOWED_APP_IDS``. Persist this in Waypoint's own IaC so it
    survives Waypoint redeploys.

This script is idempotent: it reads the ``waypoint-iq`` toolbox's current default
version and, if its OpenAPI tool is not already ``managed_identity`` with the
right audience, creates a new version with the auth flipped and promotes it to
``default_version``. It preserves the OpenAPI spec and everything else.

Requirements
------------
* ``az`` CLI logged in to the target tenant/subscription (used for tokens).
* ``FOUNDRY_PROJECT_ENDPOINT`` set, e.g.
  ``https://<account>.services.ai.azure.com/api/projects/<project>``.

The Waypoint ``--audience`` (``api://<app-id>``) is environment-specific; the
default below matches the primary Forge environment. Override it for a different
environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

TOOLBOX_API_VERSION = "v1"
TOOLBOX_FEATURE_HEADER = "Foundry-Features=Toolboxes=V1Preview"
AI_DATAPLANE_RESOURCE = "https://ai.azure.com"

# Primary Forge-environment defaults (override via flags/env for other envs).
DEFAULT_TOOLBOX = "waypoint-iq"
DEFAULT_AUDIENCE = os.environ.get("WAYPOINT_AUDIENCE") or os.environ.get("WAYPOINT_API_SCOPE") or ""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


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


def _openapi_tool(version: dict) -> dict | None:
    for tool in version.get("tools", []):
        if tool.get("type") == "openapi":
            return tool
    return None


def managed_identity_auth(audience: str) -> dict:
    return {"type": "managed_identity", "security_scheme": {"audience": audience}}


def ensure_toolbox_auth(endpoint: str, toolbox: str, audience: str) -> None:
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

    desired_auth = managed_identity_auth(audience)
    current_auth = tool["openapi"].get("auth")
    if current_auth == desired_auth:
        print(
            f"[toolbox] '{toolbox}' v{default_version} already uses managed_identity "
            f"(audience {audience}). Nothing to do."
        )
        return

    print(
        f"[toolbox] '{toolbox}' v{default_version} auth = "
        f"{json.dumps(current_auth)}; flipping to managed_identity -> {audience}."
    )
    tool["openapi"]["auth"] = desired_auth

    new_version_body = {
        "description": version.get("description")
        or "WaypointIQ managed-identity OpenAPI toolbox (project MI, doc-canonical).",
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
    parser.add_argument("--toolbox", default=DEFAULT_TOOLBOX)
    parser.add_argument("--audience", default=os.environ.get("WAYPOINT_AUDIENCE", DEFAULT_AUDIENCE),
                        help="Waypoint app audience api://<app-id> (environment-specific).")
    args = parser.parse_args()

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("::error::FOUNDRY_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 1

    ensure_toolbox_auth(endpoint, args.toolbox, args.audience)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
