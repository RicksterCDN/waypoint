"""Ensure the ``invoice-analyst-tools`` toolbox includes a **read-only** view of
the durable ``waypoint-iq`` OpenAPI toolbox, executed server-side by the Foundry
ToolServer.

Why this exists
---------------
The invoice-analyst hosted agent must answer live invoice-assurance questions
("status of our runs", "tell me about INV-…") from Teams and the Responses
surface. Reaching Waypoint through a *client-side* ``MCPStreamableHTTPTool`` in
the container crashes on Foundry hosted-agent deactivation (anyio cancel-scope
teardown across tasks) → corrupted tool result → "Function failed". Server-side
toolbox tools (executed by the Foundry ToolServer) do not have this problem — the
knowledge-base MCP tool already works this way.

The fix is to fold the Waypoint reads *into the analyst's own toolbox* so they run
server-side. Per ``docs/WAYPOINTIQ.md`` the canonical ``waypoint-iq`` toolbox
remains the single source of truth for the OpenAPI spec; the analyst is a
**read-only** consumer, so we project only its ``GET`` operations into
``invoice-analyst-tools``. The Waypoint OpenAPI tool's auth is set to the
doc-canonical ``managed_identity`` (project MI, granted ``Waypoint.Read`` and
allow-listed in Waypoint) so downstream calls are deterministic across redeploys
and per-hire identities.

This script is idempotent:

  1. Reads the source ``waypoint-iq`` toolbox default version and extracts its
     OpenAPI tool (spec + server URL).
  2. Filters the spec to ``GET`` operations only (read-only projection) and sets
     ``managed_identity`` auth explicitly (not copied from source, so the analyst
     path stays correct even if the standalone toolbox drifts).
  3. Reads the target ``invoice-analyst-tools`` default version. If it already
     contains an equivalent read-only Waypoint tool, it is a no-op. Otherwise it
     creates a new target version = [existing non-Waypoint tools + read-only
     Waypoint tool] and promotes it to ``default_version``.

It deliberately does NOT author the Waypoint OpenAPI spec: that ships with
Waypoint's Foundry integration via the ``waypoint-iq`` toolbox. We read the live
tool and curate it down to reads, so the analyst can never drift from the
canonical contract or expose write operations it isn't authorized for.

Requirements
------------
* ``az`` CLI logged in to the target tenant/subscription (used for tokens).
* ``FOUNDRY_PROJECT_ENDPOINT`` set, e.g.
  ``https://<account>.services.ai.azure.com/api/projects/<project>``.
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

DEFAULT_SOURCE_TOOLBOX = "waypoint-iq"
DEFAULT_TARGET_TOOLBOX = "invoice-analyst-tools"
WAYPOINT_TOOL_NAME = "waypoint_iq"
# Doc-canonical auth: the Foundry ToolServer calls Waypoint as the project
# managed identity (granted Waypoint.Read + allow-listed in Waypoint). This is
# deterministic across redeploys/hires, unlike per-agent agentic-identity tokens.
DEFAULT_AUDIENCE = os.environ.get("WAYPOINT_AUDIENCE") or os.environ.get("WAYPOINT_API_SCOPE") or ""


def managed_identity_auth(audience: str) -> dict:
    return {"type": "managed_identity", "security_scheme": {"audience": audience}}


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


def _get_default_version(ep: str, toolbox: str) -> dict:
    tb = _toolbox_rest("get", f"{ep}/toolboxes/{toolbox}?api-version={TOOLBOX_API_VERSION}")
    if not tb:
        raise RuntimeError(f"Toolbox {toolbox!r} not found.")
    default_version = tb.get("default_version")
    if not default_version:
        raise RuntimeError(f"Toolbox {toolbox!r} has no default_version.")
    version = _toolbox_rest(
        "get",
        f"{ep}/toolboxes/{toolbox}/versions/{default_version}?api-version={TOOLBOX_API_VERSION}",
    )
    version["_default_version"] = default_version
    return version


def _openapi_tool(version: dict) -> dict | None:
    for tool in version.get("tools", []):
        if tool.get("type") == "openapi":
            return tool
    return None


def _read_only_spec(spec: dict) -> dict:
    """Return a copy of ``spec`` with only GET operations retained."""
    ro = dict(spec)
    ro_paths: dict = {}
    for path, methods in (spec.get("paths") or {}).items():
        gets = {m: op for m, op in methods.items() if m.lower() == "get"}
        if gets:
            ro_paths[path] = gets
    ro["paths"] = ro_paths
    return ro


def _op_ids(spec: dict) -> set[str]:
    return {
        op.get("operationId")
        for methods in (spec.get("paths") or {}).values()
        for op in methods.values()
        if isinstance(op, dict) and op.get("operationId")
    }


def build_readonly_waypoint_tool(source_version: dict, audience: str = DEFAULT_AUDIENCE) -> tuple[dict, set[str]]:
    src_tool = _openapi_tool(source_version)
    if src_tool is None:
        raise RuntimeError("Source waypoint-iq toolbox default version has no OpenAPI tool.")
    openapi = src_tool["openapi"]
    ro_spec = _read_only_spec(openapi["spec"])
    op_ids = _op_ids(ro_spec)
    if not op_ids:
        raise RuntimeError("Read-only projection produced zero GET operations.")
    tool = {
        "type": "openapi",
        "openapi": {
            "name": WAYPOINT_TOOL_NAME,
            "spec": ro_spec,
            # Doc-canonical auth: project managed identity (see WAYPOINTIQ.md).
            # Set explicitly (not copied from source) so the analyst path is
            # correct even if the standalone waypoint-iq toolbox drifts.
            "auth": managed_identity_auth(audience),
        },
    }
    return tool, op_ids


def ensure(ep: str, source_toolbox: str, target_toolbox: str, audience: str = DEFAULT_AUDIENCE) -> int:
    source_version = _get_default_version(ep, source_toolbox)
    ro_tool, desired_ops = build_readonly_waypoint_tool(source_version, audience)
    desired_auth = ro_tool["openapi"].get("auth")
    print(
        f"[source] {source_toolbox} v{source_version['_default_version']} -> "
        f"{len(desired_ops)} read-only ops; auth={json.dumps(desired_auth)}"
    )

    target_version = _get_default_version(ep, target_toolbox)
    tv = target_version["_default_version"]
    existing_tools = target_version.get("tools", [])

    existing_wp = None
    other_tools = []
    for tool in existing_tools:
        if (
            tool.get("type") == "openapi"
            and (tool.get("openapi") or {}).get("name") == WAYPOINT_TOOL_NAME
        ):
            existing_wp = tool
        else:
            other_tools.append(tool)

    if existing_wp is not None:
        cur_openapi = existing_wp.get("openapi") or {}
        cur_ops = _op_ids(cur_openapi.get("spec") or {})
        cur_auth = cur_openapi.get("auth")
        if cur_ops == desired_ops and cur_auth == desired_auth:
            print(
                f"[target] {target_toolbox} v{tv} already has a matching read-only "
                f"'{WAYPOINT_TOOL_NAME}' tool ({len(cur_ops)} ops). Nothing to do."
            )
            return 0
        print(
            f"[target] {target_toolbox} v{tv} has a '{WAYPOINT_TOOL_NAME}' tool but it "
            f"drifted (ops {len(cur_ops)}->{len(desired_ops)}, auth match="
            f"{cur_auth == desired_auth}); rebuilding."
        )
    else:
        print(
            f"[target] {target_toolbox} v{tv} is missing '{WAYPOINT_TOOL_NAME}'; adding "
            f"read-only tool ({len(desired_ops)} ops)."
        )

    new_tools = other_tools + [ro_tool]
    new_version_body = {
        "description": target_version.get("description")
        or "invoice-analyst tools (knowledge base + read-only WaypointIQ).",
        "tools": new_tools,
    }
    if target_version.get("metadata"):
        new_version_body["metadata"] = target_version["metadata"]

    created = _toolbox_rest(
        "post",
        f"{ep}/toolboxes/{target_toolbox}/versions?api-version={TOOLBOX_API_VERSION}",
        new_version_body,
    )
    new_version = created.get("version")
    print(f"[target] created version {new_version} with {len(new_tools)} tool(s).")

    _toolbox_rest(
        "patch",
        f"{ep}/toolboxes/{target_toolbox}?api-version={TOOLBOX_API_VERSION}",
        {"default_version": new_version},
    )
    print(f"[target] promoted v{new_version} to default_version.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source-toolbox", default=DEFAULT_SOURCE_TOOLBOX)
    parser.add_argument("--target-toolbox", default=DEFAULT_TARGET_TOOLBOX)
    parser.add_argument(
        "--audience",
        default=os.environ.get("WAYPOINT_AUDIENCE", DEFAULT_AUDIENCE),
        help="Waypoint app audience api://<app-id> for managed_identity auth.",
    )
    args = parser.parse_args()

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("::error::FOUNDRY_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 1

    ep = endpoint.rstrip("/")
    try:
        rc = ensure(ep, args.source_toolbox, args.target_toolbox, args.audience)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print("Done.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
