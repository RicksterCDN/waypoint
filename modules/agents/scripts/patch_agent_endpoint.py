"""Patch the agent endpoint to use BotServiceRbac-only authorization.

By default a Foundry hosted agent endpoint is created with TWO authorization
schemes: ``Entra`` (user OBO) and ``BotServiceRbac`` (bot service identity).
The ``Entra`` scheme causes Teams / M365 Copilot to prompt every first-time
user with an OAuth "Open Foundry login" card before the agent can answer.

The product team's `samples/agent-creation-script.ps1` removes the ``Entra``
scheme after creating the agent version so channel delivery flows entirely
through BotServiceRbac (no user consent prompt). This script does the same
PATCH against the live agent so `azd deploy` + `make publish` results in a
working M365 experience with no per-user sign-in step.

Idempotent: re-runs are no-ops if the endpoint is already configured this way.

Required env vars:
    AGENT_NAME          - agent name (e.g. rhodes)
    PROJECT_ENDPOINT    - https://<account>.services.ai.azure.com/api/projects/<project>

Usage:
    python scripts/patch_agent_endpoint.py
"""

from __future__ import annotations

import json
import os
import sys

import requests
from azure.identity import DefaultAzureCredential


API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_FEATURES = "HostedAgents=V1Preview,AgentEndpoints=V1Preview"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT").rstrip("/")

    token = DefaultAzureCredential().get_token(AI_SCOPE).token
    headers = {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": FOUNDRY_FEATURES,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{project_endpoint}/agents/{agent_name}?api-version={API_VERSION}"

    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        # Agent not registered yet (e.g. partial deploy, or this script is being
        # invoked across all of agents/ before every one has actually deployed).
        # Don't fail CI — there's literally nothing to patch.
        print(
            f"::warning::Agent '{agent_name}' not found at {project_endpoint} — "
            "skipping endpoint patch (deploy probably hasn't completed yet)."
        )
        return 0
    r.raise_for_status()
    endpoint = (r.json() or {}).get("agent_endpoint") or {}
    current_schemes = endpoint.get("authorization_schemes") or []
    current_protocols = endpoint.get("protocols") or []
    current_selector = endpoint.get("version_selector") or {}

    scheme_types = [s.get("type") for s in current_schemes]
    # The agent endpoint must satisfy ALL THREE conditions before we can skip:
    #   1. authorization_schemes == ["BotServiceRbac"]   (no Entra OBO prompt)
    #   2. "activity" is in protocols                    (Bot Service POSTs to
    #                                                     /protocols/activityprotocol;
    #                                                     without "activity" the
    #                                                     bot reaches a 404 and
    #                                                     Teams / Web Chat hang).
    #   3. version_selector routes 100% traffic to       (otherwise subsequent
    #      "@latest"                                      `azd deploy` revisions
    #                                                     never serve traffic —
    #                                                     the endpoint stays
    #                                                     pinned to the version
    #                                                     it was first created
    #                                                     with).
    # Earlier versions of this script only checked the schemes; we later added
    # protocols, and now version_selector — each gap was a real outage.
    schemes_ok = scheme_types == ["BotServiceRbac"]
    protocols_ok = "activity" in current_protocols
    rules = current_selector.get("version_selection_rules") or []
    selector_ok = (
        len(rules) == 1
        and rules[0].get("agent_version") == "@latest"
        and rules[0].get("traffic_percentage") == 100
    )
    if schemes_ok and protocols_ok and selector_ok:
        print(
            f"::notice::{agent_name} endpoint already has BotServiceRbac + "
            f"activity protocol + @latest selector — no change."
        )
        return 0

    # Always ensure "activity" is present (otherwise the bot endpoint is dead).
    # Preserve any other declared protocols (e.g. "responses" for OpenAI-style
    # callers used by the smoke test).
    desired_protocols = list(current_protocols)
    if "activity" not in desired_protocols:
        desired_protocols.append("activity")
    if "responses" not in desired_protocols:
        desired_protocols.append("responses")
    protocols = desired_protocols

    body = {
        "agent_endpoint": {
            "protocols": protocols,
            "authorization_schemes": [{"type": "BotServiceRbac"}],
            "version_selector": {
                "version_selection_rules": [
                    {
                        "type": "FixedRatio",
                        "agent_version": "@latest",
                        "traffic_percentage": 100,
                    }
                ]
            },
        }
    }

    print(f"PATCH {url}")
    print(f"body: {json.dumps(body, indent=2)}")

    p = requests.patch(url, headers=headers, json=body, timeout=60)
    if p.status_code >= 300:
        print(f"::error::PATCH failed [{p.status_code}]: {p.text}")
        return 1

    after = (p.json() or {}).get("agent_endpoint") or {}
    print(f"::notice::{agent_name} endpoint patched.")
    print(json.dumps(after, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
