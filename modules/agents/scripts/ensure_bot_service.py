"""Ensure a Bot Service + Teams channel exists for a Foundry hosted agent.

Idempotent: if a Bot Service already exists in the resource group that points
to the agent's activity-protocol endpoint, this is a no-op. Otherwise it
deploys `infra/core/publish/bot-channel.bicep` with a fresh unique name and
the agent's `instance_identity.client_id` as the bot's `msaAppId`.

This is the same step the Foundry portal's "Publish to Teams / M365" button
does when no bot exists yet. Wiring it into `make publish` means future agents
get the bot auto-created without anyone touching the portal.

The bot's `msaAppId` is **immutable** on Azure Bot Service, and the resource
goes into a 7-day soft-delete cooldown when removed. We therefore detect the
existing bot rather than trying to re-create one with the same name.

Required env vars:
    AGENT_NAME          - logical agent name (e.g. rhodes)
    PROJECT_ENDPOINT    - https://<account>.services.ai.azure.com/api/projects/<project>
    ACCOUNT_NAME        - Foundry account (cognitive services) resource name
    PROJECT_NAME        - Foundry project name
    RESOURCE_GROUP      - resource group containing the bot service
    SUBSCRIPTION_ID

Usage:
    python scripts/ensure_bot_service.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml
from azure.identity import DefaultAzureCredential


API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
BOT_API_VERSION = "2022-09-15"
REPO_ROOT = Path(__file__).resolve().parents[1]
BICEP_FILE = REPO_ROOT / "infra" / "core" / "publish" / "bot-channel.bicep"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _get(url: str, token: str, extra: dict | None = None) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return requests.get(url, headers=headers, timeout=30)


def _list_bots(subscription_id: str, resource_group: str, arm_token: str) -> list[dict]:
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.BotService/botServices"
        f"?api-version={BOT_API_VERSION}"
    )
    r = _get(url, arm_token)
    r.raise_for_status()
    return (r.json() or {}).get("value") or []


def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT").rstrip("/")
    account_name = _env("ACCOUNT_NAME")
    project_name = _env("PROJECT_NAME")
    resource_group = _env("RESOURCE_GROUP")
    subscription_id = _env("SUBSCRIPTION_ID")

    cred = DefaultAzureCredential()
    ai_token = cred.get_token(AI_SCOPE).token
    arm_token = cred.get_token(ARM_SCOPE).token

    # Read publish.yaml to decide which identity becomes msaAppId.
    # AI Teammate (digital_worker=true) requires the bot to be wired to the
    # **blueprint** app id — that's the audience M365 signs teammate JWTs
    # against. Regular agents use the **instance identity**.
    publish_config = os.environ.get("PUBLISH_CONFIG")
    digital_worker = False
    if publish_config and Path(publish_config).is_file():
        cfg = yaml.safe_load(Path(publish_config).read_text()) or {}
        digital_worker = bool(cfg.get("digital_worker", False))

    # Fetch the agent JSON for its identity client ids.
    agent_url = f"{project_endpoint}/agents/{agent_name}?api-version={API_VERSION}"
    r = _get(agent_url, ai_token, {"Foundry-Features": "HostedAgents=V1Preview,AgentEndpoints=V1Preview"})
    r.raise_for_status()
    agent = r.json() or {}
    latest = (agent.get("versions") or {}).get("latest") or {}
    instance_id = (
        (latest.get("instance_identity") or {}).get("client_id")
        or (agent.get("instance_identity") or {}).get("client_id")
    )
    blueprint_id = (
        (latest.get("blueprint") or {}).get("client_id")
        or (agent.get("blueprint") or {}).get("client_id")
    )
    if not instance_id:
        sys.exit(
            f"::error::Agent '{agent_name}' has no instance_identity.client_id "
            "yet — version may still be provisioning."
        )
    if digital_worker and not blueprint_id:
        sys.exit(
            f"::error::Agent '{agent_name}' has no blueprint.client_id "
            "(required for digital-worker bot wiring)."
        )

    # Per Foundry product team guidance + samples/main.bicep:
    #   * Digital worker (AI Teammate): msaAppId = blueprint client_id.
    #     Additionally, a one-time PUT to the Teams Developer Portal
    #     (`dev.teams.microsoft.com/.../agentblueprints/<bp>/backendConfiguration`,
    #     body: { type: botBased, botBased: { botId: <bp> } }) is required
    #     so Teams knows which Bot Service to route teammate traffic to.
    #     That call lives in `scripts/configure_teams_blueprint.py` and is
    #     invoked from the `make publish` flow.
    #   * Regular agent: msaAppId = instance_identity client_id (the SP
    #     Foundry can mint Bot Service tokens for natively).
    desired_msa_app_id = blueprint_id if digital_worker else instance_id
    print(
        f">> Target Bot Service msaAppId: {desired_msa_app_id} "
        f"({'blueprint' if digital_worker else 'instance identity'})"
    )

    # The agent endpoint we want the bot to point at. Match the shape the
    # Foundry portal's M365 publish uses: lowercase `activityprotocol` +
    # `2025-11-15-preview`.
    expected_endpoint = (
        f"https://{account_name}.services.ai.azure.com/api/projects/{project_name}"
        f"/agents/{agent_name}/endpoint/protocols/activityprotocol"
        f"?api-version={API_VERSION}"
    )

    # Find an existing bot for this agent. Match by endpoint substring rather
    # than display name — names are arbitrary and the portal-created bots use
    # patterns like "<agent><digits>".
    candidates = []
    for bot in _list_bots(subscription_id, resource_group, arm_token):
        endpoint = ((bot.get("properties") or {}).get("endpoint") or "").lower()
        if f"/agents/{agent_name.lower()}/endpoint/protocols/" in endpoint:
            candidates.append(bot)

    # Prefer a candidate whose msaAppId already matches the *desired* id.
    # msaAppId is immutable, so a mismatched bot can never be fixed in-place;
    # we leave it alone and create a parallel correctly-wired bot, mirroring
    # what the Foundry portal does when its publish flow encounters this
    # state. For AI Teammate (digital_worker), the desired id is the
    # blueprint app id; for regular agents, it is the instance identity.
    working = next(
        (b for b in candidates if (b.get("properties") or {}).get("msaAppId") == desired_msa_app_id),
        None,
    )

    if working:
        props = working.get("properties") or {}
        name = working.get("name")
        cur_endpoint = props.get("endpoint")
        print(f"::notice::Bot already exists for '{agent_name}': {name}")
        print(f"   msaAppId = {props.get('msaAppId')}")
        print(f"   endpoint = {cur_endpoint}")

        if cur_endpoint and cur_endpoint != expected_endpoint:
            print(f"::notice::Updating bot endpoint to {expected_endpoint}")
            patch_url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourceGroups/{resource_group}/providers/Microsoft.BotService"
                f"/botServices/{name}?api-version={BOT_API_VERSION}"
            )
            requests.patch(
                patch_url,
                headers={
                    "Authorization": f"Bearer {arm_token}",
                    "Content-Type": "application/json",
                },
                json={"properties": {"endpoint": expected_endpoint}},
                timeout=60,
            ).raise_for_status()
        return 0

    if candidates:
        # There's a bot for this agent but none have the desired msaAppId.
        # Leave them in place (deleting triggers a 7-day soft-delete cooldown
        # on the name) and create a parallel correctly-wired bot.
        existing = ", ".join(
            f"{b.get('name','?')}(msaAppId={(b.get('properties') or {}).get('msaAppId')})"
            for b in candidates
        )
        target = "blueprint" if digital_worker else "instance_identity"
        print(
            f"::warning::Existing bot(s) for '{agent_name}' ({existing}) have "
            f"mismatched msaAppId. msaAppId is immutable on Bot Service. "
            f"Creating a parallel bot wired to {target} ({desired_msa_app_id})."
        )

    # No working bot — deploy a new one. Use a timestamp-suffixed name so we
    # never collide with soft-deleted names from previous attempts.
    bot_name = f"bot-{agent_name}-{int(time.time())}"[:42]
    print(f"::notice::Creating bot {bot_name} for '{agent_name}'...")

    cmd = [
        "az", "deployment", "group", "create",
        "--name", f"bot-{agent_name}-{int(time.time())}",
        "--resource-group", resource_group,
        "--template-file", str(BICEP_FILE),
        "--parameters",
        f"agentName={agent_name}",
        f"displayName={agent_name}",
        f"botName={bot_name}",
        f"accountName={account_name}",
        f"projectName={project_name}",
        f"msaAppId={desired_msa_app_id}",
        "--query", "properties.outputs.{bot:botName.value, endpoint:messagingEndpoint.value}",
        "-o", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"::error::Bot deployment failed: {proc.stderr}", file=sys.stderr)
        return proc.returncode
    print(proc.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
