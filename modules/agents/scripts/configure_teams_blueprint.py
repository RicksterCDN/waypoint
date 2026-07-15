"""Configure the Teams Developer Portal backend for an agent blueprint.

After the Bot Service + Teams channel are provisioned (see
`infra/core/publish/bot-channel.bicep`), the agent blueprint also needs to
be linked to that bot in the Teams Developer Portal. This script does the
PUT call that the Foundry "foundry-ai-teammate" C# sample does in
`configure-blueprint-backend.ps1`.

API: PUT https://dev.teams.microsoft.com/api/v1.0/agentblueprints/{blueprintId}/backendConfiguration
Body: { "type": "botBased", "botBased": { "botId": "<blueprintId>" } }

⚠️ This endpoint requires a **user-delegated** token for
`https://dev.teams.microsoft.com`. It will NOT work with a CI service
principal — run it locally after `make publish`.

Required env vars:
    AGENT_NAME          - agent name (e.g. rhodes)
    PROJECT_ENDPOINT    - https://<account>.services.ai.azure.com/api/projects/<project>

Usage:
    python scripts/configure_teams_blueprint.py
"""

from __future__ import annotations

import json
import os
import sys

import requests
from azure.identity import AzureCliCredential


API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
TEAMS_DEV_SCOPE = "https://dev.teams.microsoft.com/.default"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT").rstrip("/")

    # Always use the developer's Azure CLI identity — this API rejects
    # app-only tokens.
    cred = AzureCliCredential()

    ai_token = cred.get_token(AI_SCOPE).token
    agent_url = f"{project_endpoint}/agents/{agent_name}?api-version={API_VERSION}"
    r = requests.get(
        agent_url,
        headers={
            "Authorization": f"Bearer {ai_token}",
            "Foundry-Features": "AgentEndpoints=V1Preview,HostedAgents=V1Preview",
        },
        timeout=30,
    )
    r.raise_for_status()
    agent = r.json()

    latest = (agent.get("versions") or {}).get("latest") or {}
    blueprint_id = (latest.get("blueprint") or {}).get("client_id") \
        or (agent.get("blueprint") or {}).get("client_id")
    if not blueprint_id:
        sys.exit(f"::error::Agent '{agent_name}' has no blueprint.client_id.")

    print(f"Agent:       {agent_name}")
    print(f"Blueprint:   {blueprint_id}")

    try:
        teams_token = cred.get_token(TEAMS_DEV_SCOPE).token
    except Exception as e:
        sys.exit(
            "::error::Could not acquire dev.teams.microsoft.com token. "
            "Run: az login --scope https://dev.teams.microsoft.com/.default\n"
            f"Detail: {e}"
        )

    # Bot ID == blueprint ID (per the official sample).
    body = {"type": "botBased", "botBased": {"botId": blueprint_id}}
    url = (
        "https://dev.teams.microsoft.com/api/v1.0/agentblueprints/"
        f"{blueprint_id}/backendConfiguration"
    )

    print(f"PUT {url}")
    print(f"body: {json.dumps(body, indent=2)}")

    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {teams_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=body,
        timeout=60,
    )

    if resp.status_code >= 300:
        portal_url = f"https://dev.teams.microsoft.com/tools/agent-blueprint/{blueprint_id}"
        print(f"::warning::Teams Developer Portal returned {resp.status_code}: {resp.text}")
        print()
        print("The Teams Developer Portal API does not currently accept tokens from")
        print("Azure CLI for backend configuration writes. Finish manually:")
        print()
        print(f"  1. Open: {portal_url}")
        print("  2. Click 'Configuration' in the left rail")
        print(f"  3. Agent Type: Bot Based")
        print(f"  4. Bot ID:     {blueprint_id}")
        print("  5. Click Save")
        print()
        # Try to open the browser automatically (best-effort).
        try:
            import webbrowser
            if webbrowser.open(portal_url):
                print(f"(Opened {portal_url} in your default browser.)")
        except Exception:
            pass
        # Don't fail the build — this is a Microsoft-side API limitation, not
        # something the caller can fix.
        return 0

    print("::notice::Blueprint backend configured.")
    if resp.text.strip():
        try:
            print(json.dumps(resp.json(), indent=2))
        except ValueError:
            print(resp.text)
    else:
        print("(empty response)")

    blueprint_url = f"https://dev.teams.microsoft.com/tools/agent-blueprint/{blueprint_id}"
    print(f"\nBlueprint config: {blueprint_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
