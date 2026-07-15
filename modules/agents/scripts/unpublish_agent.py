"""Unpublish a Foundry hosted agent end-to-end.

Removes, in order, anything that keeps the agent visible in the M365 admin
"All agents" registry or Teams "Built for your org" catalog:

  1. Foundry hosted agent             (DELETE /agents/<name>?force=true)
  2. Foundry Application ARM resource (DELETE .../projects/.../applications/<name>)
  3. Teams app catalog entries        (DELETE /v1.0/appCatalogs/teamsApps/<id>)
  4. (Optional) Entra Agent Identity  (DELETE /beta/directory/agentIdentities/<id>)

The Teams catalog step requires delegated AppCatalog.ReadWrite.All. Azure CLI
is not pre-authorized for that scope, so this script uses an interactive
device-code flow against Microsoft Graph. Run it from a workstation, not CI.

Env vars:
    AGENT_NAME        - required, the agent display name (matches catalog name)
    PROJECT_ENDPOINT  - https://<account>.services.ai.azure.com/api/projects/<project>
    SUBSCRIPTION_ID
    RESOURCE_GROUP
    ACCOUNT_NAME
    PROJECT_NAME
    TENANT_ID         - required for the Graph device-code login

Flags:
    --skip-foundry        skip steps 1 & 2
    --skip-catalog        skip step 3
    --agent-identity ID   also delete the named Entra agentIdentity (step 4)
    --dry-run             list what would be deleted, do not delete
"""

from __future__ import annotations

import argparse
import os
import sys

import requests
from azure.identity import DefaultAzureCredential, DeviceCodeCredential


AI_SCOPE = "https://ai.azure.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_DELEGATED = ["AppCatalog.ReadWrite.All", "Directory.AccessAsUser.All"]

FOUNDRY_API_VERSION = "2025-11-15-preview"
ARM_API_VERSION = "2026-01-15-preview"
FOUNDRY_HEADERS = {
    "Foundry-Features": "AgentEndpoints=V1Preview,HostedAgents=V1Preview",
}


def _env(name: str, *, required: bool = True) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _delete_foundry_agent(endpoint: str, name: str, token: str, *, dry: bool) -> None:
    url = f"{endpoint}/agents/{name}?api-version={FOUNDRY_API_VERSION}&force=true"
    print(f"[foundry-agent] DELETE {url}")
    if dry:
        return
    r = requests.delete(
        url, headers={"Authorization": f"Bearer {token}", **FOUNDRY_HEADERS}, timeout=60
    )
    if r.status_code in (200, 202, 204, 404):
        print(f"[foundry-agent] -> {r.status_code}")
    else:
        print(f"[foundry-agent] -> {r.status_code} {r.text}")


def _delete_foundry_application(
    sub: str, rg: str, account: str, project: str, name: str, token: str, *, dry: bool
) -> None:
    url = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project}/applications/{name}?api-version={ARM_API_VERSION}"
    )
    print(f"[foundry-app] DELETE {url}")
    if dry:
        return
    r = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code in (200, 202, 204, 404):
        print(f"[foundry-app] -> {r.status_code}")
    else:
        print(f"[foundry-app] -> {r.status_code} {r.text}")


def _graph(method: str, url: str, token: str, **kw) -> requests.Response:
    return requests.request(
        method, url, headers={"Authorization": f"Bearer {token}"}, timeout=60, **kw
    )


def _delete_catalog_entries(name: str, token: str, *, dry: bool) -> int:
    url = (
        "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps"
        f"?$filter=displayName eq '{name}'&$select=id,displayName,distributionMethod"
    )
    r = _graph("GET", url, token)
    r.raise_for_status()
    entries = r.json().get("value", [])
    print(f"[catalog] found {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} matching {name!r}")
    for e in entries:
        eid = e["id"]
        print(f"[catalog] DELETE teamsApps/{eid} ({e.get('distributionMethod')})")
        if dry:
            continue
        d = _graph(
            "DELETE", f"https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{eid}", token
        )
        print(f"[catalog] -> {d.status_code}{(' ' + d.text) if d.status_code >= 300 else ''}")
    return len(entries)


def _delete_agent_identity(identity_id: str, token: str, *, dry: bool) -> None:
    url = f"https://graph.microsoft.com/beta/directory/agentIdentities/{identity_id}"
    print(f"[agent-id] DELETE {url}")
    if dry:
        return
    d = _graph("DELETE", url, token)
    print(f"[agent-id] -> {d.status_code}{(' ' + d.text) if d.status_code >= 300 else ''}")


def main() -> None:
    p = argparse.ArgumentParser(description="Unpublish a Foundry agent and clean catalog entries.")
    p.add_argument("--skip-foundry", action="store_true")
    p.add_argument("--skip-catalog", action="store_true")
    p.add_argument("--agent-identity", help="Entra agentIdentity GUID to delete")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    name = _env("AGENT_NAME")
    dry = args.dry_run

    if not args.skip_foundry:
        endpoint = _env("PROJECT_ENDPOINT")
        sub = _env("SUBSCRIPTION_ID")
        rg = _env("RESOURCE_GROUP")
        account = _env("ACCOUNT_NAME")
        project = _env("PROJECT_NAME")
        cred = DefaultAzureCredential()
        ai_token = cred.get_token(AI_SCOPE).token
        arm_token = cred.get_token(ARM_SCOPE).token
        _delete_foundry_agent(endpoint, name, ai_token, dry=dry)
        _delete_foundry_application(sub, rg, account, project, name, arm_token, dry=dry)

    if not args.skip_catalog or args.agent_identity:
        tenant = _env("TENANT_ID")
        # Graph Explorer first-party app id is pre-consented in most tenants for
        # delegated scopes. Falls back to interactive device-code flow.
        graph_cred = DeviceCodeCredential(
            tenant_id=tenant,
            client_id="14d82eec-204b-4c2f-b7e8-296a70dab67e",  # Microsoft Graph CLI
        )
        graph_token = graph_cred.get_token(*GRAPH_DELEGATED).token
        if not args.skip_catalog:
            _delete_catalog_entries(name, graph_token, dry=dry)
        if args.agent_identity:
            _delete_agent_identity(args.agent_identity, graph_token, dry=dry)

    print("done.")


if __name__ == "__main__":
    main()
