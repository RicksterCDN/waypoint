"""Print the manual next-steps banner after `make publish`.

`make publish` automates everything server-side (Bot Service, Teams channel,
Foundry application, M365 publish request, OAuth2 consent on the blueprint SP),
but three gates still require a human:

  1. Tenant admin approves the publish request in the M365 admin center.
  2. A maker configures the agent blueprint in the Teams Developer Portal
     (Bot ID == Blueprint ID — the Bot Service is already wired up, this is
     just metadata so the blueprint shows up correctly in Teams).
  3. An end user "hires" the agent by creating an instance from
     Apps -> Agents for your team in Microsoft Teams.

This script re-fetches the agent metadata from Foundry, extracts the blueprint
client_id, and prints a high-contrast banner with the exact deep links the user
needs to click. Designed to be the final step in `make publish` AND to be
runnable on its own via `make next-steps <agent>` whenever a demo presenter
needs the URLs again.

Inputs (env vars):
    AGENT_NAME        - agent to look up (e.g. rhodes)
    PROJECT_ENDPOINT  - Foundry project endpoint
    PUBLISH_CONFIG    - optional path to <agent>/publish.yaml (controls the
                        scope-specific wording; defaults to tenant scope).

Exit code is always 0 (informational only) unless required env vars are missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import yaml
from azure.identity import DefaultAzureCredential


AI_AZURE_SCOPE = "https://ai.azure.com/.default"
API_VERSION = "2025-11-15-preview"

ADMIN_APPROVAL_URL = "https://admin.cloud.microsoft/#/agents/all/requested"
TEAMS_DEV_PORTAL_BLUEPRINT_URL = "https://dev.teams.microsoft.com/tools/agent-blueprint"
TEAMS_APPS_URL = "https://teams.microsoft.com/_#/apps"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _fetch_blueprint_id(project_endpoint: str, agent_name: str) -> str | None:
    """Re-fetch the agent and return its blueprint client_id, or None on failure.

    Failures here are non-fatal: we still want to print the banner with the
    other URLs even if the blueprint-specific deep link can't be computed.
    """
    try:
        token = DefaultAzureCredential().get_token(AI_AZURE_SCOPE).token
    except Exception as e:  # noqa: BLE001 — credential errors are environmental
        print(f"::warning::Could not acquire Foundry token to look up blueprint id: {e}")
        return None

    url = f"{project_endpoint.rstrip('/')}/agents/{agent_name}?api-version={API_VERSION}"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Foundry-Features": "AgentEndpoints=V1Preview,HostedAgents=V1Preview",
            },
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"::warning::Could not fetch agent '{agent_name}' to resolve blueprint id: {e}")
        return None

    agent = r.json()
    latest = (agent.get("versions") or {}).get("latest") or {}
    return (latest.get("blueprint") or {}).get("client_id") \
        or (agent.get("blueprint") or {}).get("client_id")


def _publish_scope(config_path: Path | None) -> str:
    """Return 'tenant' or 'individual' for wording. Defaults to tenant when unknown."""
    if not config_path or not config_path.is_file():
        return "tenant"
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return "tenant"
    return str(cfg.get("publish_scope", "tenant")).lower()


def _print_banner(agent_name: str, blueprint_id: str | None, scope: str) -> None:
    bar = "═" * 78
    thin = "─" * 78
    bp_link = (
        f"{TEAMS_DEV_PORTAL_BLUEPRINT_URL}/{blueprint_id}"
        if blueprint_id
        else TEAMS_DEV_PORTAL_BLUEPRINT_URL
    )

    print()
    print(bar)
    print(f"  ✅  `{agent_name}` published. Four manual steps remain before it's hireable.")
    print(bar)

    # Step 1 — admin approval
    print()
    print("  STEP 1 / 4 — Tenant admin: approve the publish request")
    print(thin)
    if scope == "tenant":
        print("  A Microsoft 365 admin must approve the request before anyone in the tenant")
        print("  can see this agent in the Copilot / Teams agent catalog.")
    else:
        print("  Individual scope: the publisher sees the agent under 'Your agents' immediately.")
        print("  Admin approval is only required for tenant-scope publishes.")
    print()
    print(f"    → {ADMIN_APPROVAL_URL}")
    print()
    print("  In the admin center, open the pending request for this agent and click")
    print("  'Approve request and activate'.")

    # Step 2 — Teams Dev Portal blueprint config
    print()
    print("  STEP 2 / 4 — Maker: configure the agent blueprint in Teams Developer Portal")
    print(thin)
    print("  The Bot Service is already wired up server-side. This Dev Portal step")
    print("  is where you confirm the Bot ID matches the Blueprint ID so Teams routes")
    print("  conversations correctly.")
    print()
    print(f"    → {bp_link}")
    if blueprint_id:
        print(f"      (Blueprint ID: {blueprint_id})")
    else:
        print("      (Blueprint ID not resolved — open the generic page and search by agent name.)")
    print()
    print("  Tip: the Dev Portal lists only 100 blueprints. If yours isn't visible,")
    print("  paste the deep link above (with the blueprint ID) directly into the URL bar.")

    # Step 3 — wire the blueprint id AND a real client secret into the container
    upper = agent_name.upper().replace('-', '_')
    env_id = f"{upper}_BLUEPRINT_CLIENT_ID"
    env_secret = f"{upper}_BLUEPRINT_CLIENT_SECRET"
    print()
    print("  STEP 3 / 4 — Maker: wire the blueprint id AND secret into the container")
    print(thin)
    print("  Hired AI Teammate instances POST Activities to the container at")
    print("  `/api/messages`. The Microsoft 365 Agents SDK reads two env vars:")
    print("    - CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID    (blueprint app id)")
    print("    - CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET (blueprint secret)")
    print("  Both MUST be set. With AUTHTYPE=ClientSecret the SDK only uses the")
    print("  secret value; an empty secret yields AADSTS7000216 ('client_assertion,")
    print("  client_secret or request is required') and surfaces upstream as a generic")
    print("  -60018. The Foundry control-plane API REDACTS the secret to \"\" on read,")
    print("  so an env-diff against a working agent looks identical — do not trust it.")
    print()
    if blueprint_id:
        print(f"    # 1) Reset the blueprint app secret and capture the value (one-time per agent).")
        print(f"    NEW_SECRET=$(az ad app credential reset \\")
        print(f"        --id {blueprint_id} \\")
        print(f"        --display-name '{agent_name}-agent-runtime' \\")
        print(f"        --years 2 --query password -o tsv)")
        print(f"    azd env set {env_id} {blueprint_id}")
        print(f"    azd env set {env_secret} \"$NEW_SECRET\"")
    else:
        print(f"    NEW_SECRET=$(az ad app credential reset \\")
        print(f"        --id <blueprint-id-from-publish-output> \\")
        print(f"        --display-name '{agent_name}-agent-runtime' \\")
        print(f"        --years 2 --query password -o tsv)")
        print(f"    azd env set {env_id} <blueprint-id-from-publish-output>")
        print(f"    azd env set {env_secret} \"$NEW_SECRET\"")
    print(f"    azd deploy {agent_name}")
    print()
    print("  You also need an InstanceManagedIdentity-<agent> federated credential on")
    print("  the blueprint app whose subject is the agent's instance_identity.client_id.")
    print("  See docs/AI_TEAMMATE.md \"Manual steps\" for the az rest one-liner.")
    print()
    print("  Until this is done, direct Teams bot chat works (it uses the Foundry")
    print("  Responses↔Activity bridge), but AI Teammate hires won't reply.")

    # Step 4 — hire (create instance)
    print()
    print("  STEP 4 / 4 — End user: hire the teammate (create an agent instance)")
    print(thin)
    print("  Once steps 1, 2 & 3 are done, an end user 'hires' the teammate by")
    print("  creating an agent instance from inside Microsoft Teams:")
    print()
    print("    1. Open Microsoft Teams")
    print("       → https://teams.microsoft.com/")
    print("    2. Apps → Agents for your team")
    print(f"       → find `{agent_name}` and click 'Create instance'")
    print("    3. Follow the in-Teams configuration prompts to complete hiring.")
    print()
    print("  The 'instance' is a per-team or per-user materialization of the blueprint.")
    print("  Hiring is a deliberate user action by design — there's no public API to")
    print("  hire on a user's behalf.")

    print()
    print(bar)
    print("  Re-print these steps any time with:  make next-steps " + agent_name)
    print(bar)
    print()


def _summary_md(agent_name: str, blueprint_id: str | None, scope: str) -> None:
    """Mirror the banner into $GITHUB_STEP_SUMMARY when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    bp_link = (
        f"{TEAMS_DEV_PORTAL_BLUEPRINT_URL}/{blueprint_id}"
        if blueprint_id
        else TEAMS_DEV_PORTAL_BLUEPRINT_URL
    )
    md = [
        f"### Next steps to make `{agent_name}` hireable",
        "",
        f"**Publish scope:** `{scope}`  ",
        f"**Blueprint ID:** `{blueprint_id or 'unresolved'}`",
        "",
        "| # | Who | Action | Link |",
        "|---|-----|--------|------|",
        f"| 1 | Tenant admin | Approve the publish request | [admin.cloud.microsoft]({ADMIN_APPROVAL_URL}) |",
        f"| 2 | Maker | Confirm Bot ID == Blueprint ID in the Teams Dev Portal | [dev.teams.microsoft.com]({bp_link}) |",
        f"| 3 | Maker | Reset blueprint secret, `azd env set` both `{agent_name.upper().replace('-', '_')}_BLUEPRINT_CLIENT_ID` and `_CLIENT_SECRET`, then `azd deploy {agent_name}` (see banner) | — |",
        f"| 4 | End user | Hire the teammate by creating an instance in Teams → Apps → Agents for your team | [teams.microsoft.com]({TEAMS_APPS_URL}) |",
        "",
        "> Hiring is a deliberate user action by design — no public API exists to hire on a user's behalf.",
    ]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
    except OSError as e:
        print(f"::warning::Could not write job summary: {e}")


def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT")
    config_path_str = os.environ.get("PUBLISH_CONFIG")
    config_path = Path(config_path_str) if config_path_str else None

    scope = _publish_scope(config_path)
    blueprint_id = _fetch_blueprint_id(project_endpoint, agent_name)

    _print_banner(agent_name, blueprint_id, scope)
    _summary_md(agent_name, blueprint_id, scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
