#!/usr/bin/env python3
"""Initialize the Forge -> Waypoint Fabric data agent project connection.

This is an explicit, idempotent data-plane operation. It creates (or updates) a
``MicrosoftFabric`` project connection on the Forge Foundry project that targets
the published Waypoint Fabric data agent (WaypointDataAgent). The
operations-data-expert agent binds ``MicrosoftFabricPreviewTool`` to this
connection at runtime (see agents/operations-data-expert/evidence_tools.py).

The data agent is backed by the WaypointIQ Direct Lake semantic model over the
mirrored operational Postgres core only (suppliers, invoices, invoice_lines,
reconciliation_findings). The connection uses AAD auth: the Fabric data agent
tool performs on-behalf-of (OBO) identity passthrough, querying Fabric as the
signed-in user rather than a service principal.

Prerequisite (Waypoint side): the Forge project managed identity must hold at
least Viewer on the Fabric workspace that hosts the data agent. As of this
writing that grant was made by hand for project MI
<foundry-project-managed-identity-object-id> on workspace
<fabric-workspace-id>; codifying it cross-repo is a tracked
follow-up.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DEFAULT_CONNECTION_API_VERSION = "2025-06-01"
DEFAULT_CONNECTION_NAME = "waypoint-data-agent-connection"
DEFAULT_WORKSPACE_ID = "<fabric-workspace-id>"
DEFAULT_DATA_AGENT_ID = "<fabric-data-agent-id>"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default=os.getenv("AZURE_RESOURCE_GROUP", "rg-forge"))
    parser.add_argument("--subscription-id", default=os.getenv("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--ai-account-name", default=os.getenv("AZURE_AI_ACCOUNT_NAME"))
    parser.add_argument("--ai-project-name", default=os.getenv("AZURE_AI_PROJECT_NAME", "ai-project-forge"))
    parser.add_argument(
        "--connection-name",
        default=os.getenv("FABRIC_DATA_AGENT_CONNECTION_NAME", DEFAULT_CONNECTION_NAME),
    )
    parser.add_argument(
        "--fabric-workspace-id",
        default=os.getenv("FABRIC_WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
    )
    parser.add_argument(
        "--fabric-data-agent-id",
        default=os.getenv("FABRIC_DATA_AGENT_ID", DEFAULT_DATA_AGENT_ID),
    )
    parser.add_argument(
        "--connection-api-version",
        default=os.getenv("AZURE_CONNECTION_API_VERSION", DEFAULT_CONNECTION_API_VERSION),
    )
    args = parser.parse_args()

    _hydrate_defaults_from_azure(args)
    _require_args(
        args,
        "subscription_id",
        "resource_group",
        "ai_account_name",
        "ai_project_name",
        "connection_name",
        "fabric_workspace_id",
        "fabric_data_agent_id",
    )

    target = (
        f"{FABRIC_API_BASE}/workspaces/{args.fabric_workspace_id}"
        f"/aiskills/{args.fabric_data_agent_id}"
    )
    connection_uri = (
        f"https://management.azure.com/subscriptions/{args.subscription_id}"
        f"/resourceGroups/{args.resource_group}/providers/Microsoft.CognitiveServices"
        f"/accounts/{args.ai_account_name}/projects/{args.ai_project_name}"
        f"/connections/{args.connection_name}?api-version={args.connection_api_version}"
    )
    body = {
        "properties": {
            "category": "MicrosoftFabric",
            "authType": "AAD",
            "target": target,
            "isSharedToAll": True,
            "metadata": {
                "workspaceId": args.fabric_workspace_id,
                "artifactId": args.fabric_data_agent_id,
                "ApiType": "Azure",
            },
        }
    }

    management_token = _az_token("https://management.azure.com")
    print(
        f"Creating/updating Fabric data agent connection '{args.connection_name}' "
        f"on {args.ai_account_name}/{args.ai_project_name} -> {target}"
    )
    result = _json_request("PUT", connection_uri, management_token, body)
    if isinstance(result, dict):
        props = result.get("properties") or {}
        # The Fabric connection service forces isSharedToAll=false because these
        # connections are per-user/OBO. Surface it so the operator isn't surprised.
        print(
            f"Connection ready (authType={props.get('authType')}, "
            f"isSharedToAll={props.get('isSharedToAll')})."
        )
    print(f"Bind via FABRIC_DATA_AGENT_CONNECTION_NAME={args.connection_name}")
    return 0


def _hydrate_defaults_from_azure(args: argparse.Namespace) -> None:
    if not args.subscription_id:
        args.subscription_id = _az_json(["account", "show", "--query", "id", "-o", "tsv"]).strip()

    if not args.ai_account_name:
        resources = _az_json(["resource", "list", "-g", args.resource_group, "-o", "json"])
        for resource in json.loads(resources):
            if (
                resource.get("type") == "Microsoft.CognitiveServices/accounts"
                and resource.get("kind") == "AIServices"
            ):
                args.ai_account_name = resource.get("name")
                break


def _json_request(
    method: str,
    uri: str,
    token: str,
    body: dict[str, Any] | None = None,
    *,
    allow_not_found: bool = False,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        uri,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None
    except HTTPError as error:
        if allow_not_found and error.code == 404:
            return None
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {method} {uri}: {details}") from error


def _az_token(resource: str) -> str:
    return _az_json(
        ["account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"]
    ).strip()


def _az_json(args: list[str]) -> str:
    return _az(args, capture=True)


def _az(args: list[str], *, capture: bool = False) -> str:
    az_executable = shutil.which("az") or shutil.which("az.cmd")
    if az_executable is None:
        raise RuntimeError("Azure CLI executable 'az' was not found on PATH.")
    process = subprocess.run(
        [az_executable, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"az {' '.join(args)} failed with exit code {process.returncode}: "
            f"{process.stdout}{process.stderr}"
        )
    if process.stderr.strip():
        print(process.stderr.strip(), file=sys.stderr)
    return process.stdout if capture else ""


def _require_args(args: argparse.Namespace, *names: str) -> None:
    missing = [name.replace("_", "-") for name in names if not getattr(args, name)]
    if missing:
        raise SystemExit(f"Missing required values: {', '.join(missing)}")


if __name__ == "__main__":
    raise SystemExit(main())
