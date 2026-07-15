"""Ensure a Foundry Project Application resource exists for the agent.

This is the **third** ARM op the Foundry portal performs as part of its
"Publish to Teams / M365 Copilot" flow (after the Bot Service + Teams channel
writes handled by `ensure_bot_service.py`). The resource type is

    Microsoft.CognitiveServices/accounts/projects/applications/<agentName>

Without this resource the agent shows up in the **M365 admin registry** (because
that lives in a different store, populated by the dataplane `/microsoft365/publish`
call in `publish_agent.py`), but it does **not** appear in the Teams "Built for
your org" catalog. Teams reads from this ARM resource.

Idempotent: if the resource already exists with the same `agents[].agentId`
mapping, this is a no-op.

Required env vars:
    AGENT_NAME, ACCOUNT_NAME, PROJECT_NAME, RESOURCE_GROUP, SUBSCRIPTION_ID
"""

from __future__ import annotations

import json
import os
import sys

import requests
from azure.identity import DefaultAzureCredential


ARM_API_VERSION = "2026-01-15-preview"
ARM_SCOPE = "https://management.azure.com/.default"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def main() -> int:
    agent_name = _env("AGENT_NAME")
    account_name = _env("ACCOUNT_NAME")
    project_name = _env("PROJECT_NAME")
    resource_group = _env("RESOURCE_GROUP")
    subscription_id = _env("SUBSCRIPTION_ID")

    token = DefaultAzureCredential().get_token(ARM_SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    base = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.CognitiveServices"
        f"/accounts/{account_name}/projects/{project_name}/applications/{agent_name}"
        f"?api-version={ARM_API_VERSION}"
    )

    expected_agent_id = (
        f"azureml://subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/workspaces/{account_name}@{project_name}@AML/applications/{agent_name}"
        f"/agents/{agent_name}"
    )

    existing = requests.get(base, headers=headers, timeout=30)
    if existing.status_code == 200:
        props = (existing.json() or {}).get("properties") or {}
        agents = props.get("agents") or []
        if agents and agents[0].get("agentId") == expected_agent_id:
            print(
                f"::notice::Foundry application '{agent_name}' already exists and "
                f"points at the agent — no change."
            )
            return 0
        print(
            f"::notice::Foundry application '{agent_name}' exists but agent mapping "
            f"is stale; updating..."
        )

    body = {
        "properties": {
            "displayName": agent_name,
            "isEnabled": True,
            "authorizationPolicy": {"authorizationScheme": "Channels"},
            "agents": [
                {
                    "agentId": expected_agent_id,
                    "agentName": agent_name,
                }
            ],
            "trafficRoutingPolicy": {
                "protocol": "FixedRatio",
                "rules": [
                    {
                        "deploymentId": "",
                        "description": "Default rule routing all traffic to the first deployment",
                        "ruleId": "default",
                        "trafficPercentage": 100,
                    }
                ],
            },
        }
    }

    r = requests.put(base, headers=headers, json=body, timeout=120)
    if not r.ok:
        print(f"::error::PUT applications/{agent_name} failed ({r.status_code}): {r.text}", file=sys.stderr)
        return 1
    out = r.json() or {}
    state = ((out.get("properties") or {}).get("provisioningState"))
    print(f"::notice::Foundry application '{agent_name}' ready (provisioningState={state}).")
    print(
        "::notice::Allow ~5–15 minutes for the Teams 'Built for your org' catalog to refresh."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
