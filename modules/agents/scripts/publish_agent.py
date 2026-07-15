"""Publish a Foundry hosted agent to Microsoft 365 / Teams.

⚠️ The POST endpoint used here is an undocumented internal Foundry API
(`{location}.api.azureml.ms/agent-asset/v2.0/.../microsoft365/publish`).
It mirrors what the Foundry portal Publish button does. Expect breakage
when Microsoft ships a public publish API.

Inputs (env vars, all required unless noted):
    AGENT_NAME              - agent to publish (e.g. rhodes)
    PROJECT_ENDPOINT        - https://<account>.services.ai.azure.com/api/projects/<project>
    ACCOUNT_NAME            - Foundry account (cognitiveservices) resource name
    PROJECT_NAME            - Foundry project name
    SUBSCRIPTION_ID
    RESOURCE_GROUP
    LOCATION                - azure region (matches the azureml.ms subdomain)
    PUBLISH_CONFIG          - path to <agent>/publish.yaml

Usage: python scripts/publish_agent.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from azure.identity import DefaultAzureCredential


AI_AZURE_SCOPE = "https://ai.azure.com/.default"
API_VERSION = "2025-11-15-preview"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _token() -> str:
    return DefaultAzureCredential().get_token(AI_AZURE_SCOPE).token


def _fetch_agent(project_endpoint: str, agent_name: str, token: str) -> dict:
    url = f"{project_endpoint}/agents/{agent_name}?api-version={API_VERSION}"
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Foundry-Features": "AgentEndpoints=V1Preview,HostedAgents=V1Preview",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


_VERSION_LINE_RE = re.compile(
    r'^(?P<prefix>\s*app_version\s*:\s*)(?P<quote>["\'])?(?P<ver>[^"\'\s#]+)(?P=quote)?'
    r"(?P<suffix>.*)$",
    re.MULTILINE,
)


def _bump_patch(version: str) -> str:
    """Return *version* with its trailing integer bumped by one.

    Handles plain semver (``2.2.0`` → ``2.2.1``), pre-release/build suffixes
    (``2.2.0-preview`` → ``2.2.1-preview``), and any other shape by
    incrementing the last integer found anywhere in the string. Falls back
    to appending ``.1`` if no integer is present.
    """
    # Most common: dotted numeric core, optional ``-suffix`` / ``+build``.
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", version.strip())
    if m:
        core, rest = m.group(1), m.group(2)
        parts = [int(p) for p in core.split(".")]
        while len(parts) < 3:
            parts.append(0)
        parts[-1] += 1
        return ".".join(str(p) for p in parts) + rest
    # Fallback: bump last integer anywhere in the string.
    last_int = list(re.finditer(r"\d+", version))
    if last_int:
        m = last_int[-1]
        return version[: m.start()] + str(int(m.group()) + 1) + version[m.end() :]
    return version + ".1"


def _bump_publish_yaml(config_path: Path) -> str:
    """Bump ``app_version`` in *config_path* and persist the change.

    Returns the new version. The file is rewritten with a minimal,
    line-level edit so surrounding comments/formatting are preserved.
    If ``app_version`` is missing, ``1.0.0`` is inserted at the top.
    """
    text = config_path.read_text()
    match = _VERSION_LINE_RE.search(text)
    if not match:
        # No existing line — prepend one. Rare in this repo; here for safety.
        new_version = "1.0.0"
        new_text = f'app_version: "{new_version}"\n' + text
    else:
        old_version = match.group("ver")
        new_version = _bump_patch(old_version)
        quote = match.group("quote") or '"'
        new_line = (
            f'{match.group("prefix")}{quote}{new_version}{quote}{match.group("suffix")}'
        )
        new_text = text[: match.start()] + new_line + text[match.end() :]
    config_path.write_text(new_text)
    return new_version

def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT").rstrip("/")
    account_name = _env("ACCOUNT_NAME")
    project_name = _env("PROJECT_NAME")
    subscription_id = _env("SUBSCRIPTION_ID")
    resource_group = _env("RESOURCE_GROUP")
    location = _env("LOCATION")
    config_path = Path(_env("PUBLISH_CONFIG"))

    if not config_path.is_file():
        print(f"::notice::No publish config at {config_path} — skipping publish.")
        return 0

    cfg = yaml.safe_load(config_path.read_text()) or {}

    # Foundry rejects re-publishing the same app_version (tenant admins can't
    # re-approve the same version), so auto-bump on every publish. The new
    # value is persisted back to publish.yaml so the file always reflects
    # what was last published — commit it to track history.
    old_version = str(cfg.get("app_version", "1.0.0"))
    new_version = _bump_publish_yaml(config_path)
    cfg["app_version"] = new_version
    print(f">> Auto-bumped app_version: {old_version} → {new_version}")

    token = _token()
    agent = _fetch_agent(project_endpoint, agent_name, token)

    latest = (agent.get("versions") or {}).get("latest") or {}
    agent_guid = latest.get("agent_guid")
    blueprint_client_id = (latest.get("blueprint") or {}).get("client_id") \
        or (agent.get("blueprint") or {}).get("client_id")
    # The Bot Service backing the M365 / Teams channel must use the agent's
    # `instance_identity` as its msaAppId (a `ServiceIdentity` SP Foundry can
    # mint tokens for), NOT the blueprint app (which has no Bot Service FIC
    # and therefore can't mint a usable token). `botId` in the publish body
    # is what routes M365 traffic to the bot, so it has to match.
    instance_client_id = (latest.get("instance_identity") or {}).get("client_id") \
        or (agent.get("instance_identity") or {}).get("client_id")

    if not agent_guid:
        sys.exit(f"::error::Agent '{agent_name}' has no agent_guid yet — version may still be provisioning.")
    if not blueprint_client_id:
        sys.exit(f"::error::Agent '{agent_name}' has no blueprint.client_id.")
    if not instance_client_id:
        sys.exit(f"::error::Agent '{agent_name}' has no instance_identity.client_id.")

    print(f"agent_guid:        {agent_guid}")
    print(f"blueprint id:      {blueprint_client_id}")
    print(f"instance id (bot): {instance_client_id}")

    # The agent-asset publish API accepts 'Personal', 'Shared', or 'Tenant'.
    # ('Individual' was the older enum value and is now rejected with a 400.)
    # Keep 'individual' as a back-compat alias for 'personal' so existing
    # publish.yaml files don't break.
    scope_map = {
        "personal": "Personal",
        "individual": "Personal",
        "shared": "Shared",
        "tenant": "Tenant",
    }
    scope = scope_map.get(str(cfg.get("publish_scope", "personal")).lower())
    if not scope:
        sys.exit("::error::publish_scope must be 'personal', 'shared', or 'tenant'.")

    digital_worker = bool(cfg.get("digital_worker", False))

    # Per Foundry product team guidance + samples/publish-digital-worker.ps1:
    #   * Digital worker (AI Teammate): botId = blueprint client_id.
    #     This must match the Bot Service's msaAppId (set by
    #     ensure_bot_service.py) AND the Teams Developer Portal backend
    #     configuration (set by configure_teams_blueprint.py). All three
    #     must agree or Teams traffic 403's at Foundry's activityprotocol
    #     endpoint.
    #   * Regular agent: botId = instance_identity client_id (matches the
    #     instance-id bot that ensure_bot_service.py creates).
    body: dict = {
        "agentGuid": agent_guid,
        "botId": blueprint_client_id if digital_worker else instance_client_id,
        "publishAsDigitalWorker": digital_worker,
        "appPublishScope": scope,
        "subscriptionId": subscription_id,
        "agentName": agent_name,
        "appVersion": str(cfg.get("app_version", "1.0.0")),
        "shortDescription": cfg.get("short_description", ""),
        "fullDescription": cfg.get("full_description", ""),
        "developerName": cfg.get("developer_name", ""),
        "developerWebsiteUrl": cfg.get("developer_website_url", ""),
        "privacyUrl": cfg.get("privacy_url", ""),
        "termsOfUseUrl": cfg.get("terms_of_use_url", ""),
    }
    if digital_worker:
        body["useAgenticUserTemplate"] = True
        body["agenticUserTemplate"] = {
            "Id": "digitalWorkerTemplate",
            "File": "agenticUserTemplateManifest.json",
            "SchemaVersion": "0.1.0-preview",
            # The teammate's M365 identity template still references the
            # agent blueprint (this is the AI Teammate manifest, separate
            # from which bot Teams talks to).
            "AgentIdentityBlueprintId": blueprint_client_id,
            "CommunicationProtocol": "activityProtocol",
        }

    workspace = f"{account_name}@{project_name}@AML"
    url = (
        f"https://{location}.api.azureml.ms/agent-asset/v2.0"
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.MachineLearningServices"
        f"/workspaces/{workspace}/microsoft365/publish"
    )

    print(f"POST {url}")
    print(f"body: {json.dumps(body, indent=2)}")

    def _post(payload: dict) -> requests.Response:
        return requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=120,
        )

    r = _post(body)

    # If Foundry already has *this* version (e.g. retry after a partial run),
    # bump again and retry once. Cheap insurance — most of the time the
    # initial bump is already unique.
    if r.status_code >= 300:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", "")
            if err.get("code") == "UserError" and "version already exists" in msg.lower():
                bumped_again = _bump_publish_yaml(config_path)
                print(
                    f"::notice::{new_version} already exists; bumped again to {bumped_again} and retrying."
                )
                new_version = bumped_again
                cfg["app_version"] = bumped_again
                body["appVersion"] = bumped_again
                r = _post(body)
        except ValueError:
            pass

    def _summary(md: str) -> None:
        """Append markdown to the GitHub Actions job summary, if present."""
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(md.rstrip() + "\n")
        except OSError as e:
            print(f"::warning::Could not write job summary: {e}")

    admin_url = "https://admin.cloud.microsoft/#/agents/all/requested"

    if r.status_code < 300:
        print("::notice::Publish request accepted.")
        try:
            print(json.dumps(r.json(), indent=2))
        except ValueError:
            print(r.text)

        lines = [
            f"### Published `{agent_name}` to Microsoft 365 / Teams",
            "",
            f"- **Version:** `{body['appVersion']}`",
            f"- **Scope:** `{scope}`",
            f"- **Agent GUID:** `{agent_guid}`",
            f"- **Bot ID (msaAppId):** `{body['botId']}` "
            f"({'blueprint' if digital_worker else 'instance identity'})",
            "",
        ]
        if scope == "Tenant":
            print(
                f"::notice::Tenant scope: a Microsoft 365 admin must approve at "
                f"{admin_url} before users see it."
            )
            lines += [
                "> ⚠️ **Admin approval required.**",
                f"> A Microsoft 365 admin must approve this request at "
                f"[admin.cloud.microsoft]({admin_url}) before users see "
                f"`{agent_name}` in the Microsoft 365 Copilot agent store.",
                "",
                "**Next steps**",
                f"1. Open [{admin_url}]({admin_url})",
                "2. Find the pending request and click **Approve**",
                "3. Wait a few minutes for the agent to appear under *Built by your org*",
            ]
        else:
            lines += [
                "Individual scope: the agent is available immediately under "
                "**Your agents** in the Microsoft 365 Copilot agent store for the publisher.",
            ]
        _summary("\n".join(lines))
        return 0

    # Tolerate "already published with this version"
    try:
        err = r.json().get("error", {})
        msg = err.get("message", "")
        if err.get("code") == "UserError" and "version already exists" in msg.lower():
            print(f"::notice::Already published at version {body['appVersion']}. Bump app_version to republish.")
            _summary(
                f"### `{agent_name}` already published at `{body['appVersion']}`\n"
                f"Bump `app_version` in `agents/{agent_name}/publish.yaml` to publish a new version."
            )
            return 0
    except ValueError:
        pass

    print(f"::error::Publish failed [{r.status_code}]: {r.text}")
    _summary(
        f"### ❌ Publish of `{agent_name}` failed\n"
        f"- **HTTP:** `{r.status_code}`\n"
        f"- **Response:**\n```\n{r.text[:2000]}\n```"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
