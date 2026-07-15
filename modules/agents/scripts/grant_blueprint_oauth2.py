"""Grant OAuth2 admin consent on an agent blueprint SP for the Microsoft
backing services the agent depends on at runtime.

Without these grants, a published Foundry digital-worker agent will:
  * return 401 to its own Bot Service callers   (missing APX consent)
  * reply with "Sorry, something went wrong"    (missing Prod MCP consent)
  * fail to reach its tool runtime              (missing Prod MCP consent)

This is a direct Python port of the product team's
`create-blueprintsp-oauth2-grants.ps1` from the foundry-ai-teammate sample.

Required env vars:
    AGENT_NAME              - e.g. rhodes
    PROJECT_ENDPOINT        - https://<account>.services.ai.azure.com/api/projects/<project>
    APX_APP_ID              - Messaging Bot API Application app ID for the target cloud
    PROD_MCP_APP_ID         - Agent Tools app ID for the target cloud

The caller (user or service principal) must hold one of:
    * Global Administrator
    * Privileged Role Administrator
    * Application Administrator
    * Cloud Application Administrator
in Microsoft Entra. If the caller lacks the role, the script prints a
manual remediation block and exits 0 (so CI does not fail).

Idempotent: "Permission entry already exists" is treated as success.
"""

from __future__ import annotations

import json
import os
import sys

import requests
from azure.identity import AzureCliCredential


API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Microsoft app the Foundry Bot Service auth layer uses ("Messaging Bot API Application").
# Without consent, Bot Service -> agent returns 401 Unauthorized.
APX_APP_ID = os.environ.get("APX_APP_ID", "")
APX_SCOPES = "AgentData.ReadWrite"

# Microsoft app the agent uses to invoke MCP tools at runtime ("Agent Tools"). Without
# consent, the agent fails when invoked through Teams or M365 Copilot.
PROD_MCP_APP_ID = os.environ.get("PROD_MCP_APP_ID", "")
PROD_MCP_SCOPES = " ".join([
    "McpServers.M365Admin.All",
    "McpServers.DASearch.All",
    "McpServers.WebSearch.All",
    "McpServers.Files.All",
    "AgentTools.MOSEvents.All",
    "McpServers.Admin365Graph.All",
    "McpServers.ERPAnalytics.All",
    "McpServers.DataverseCustom.All",
    "McpServers.Dataverse.All",
    "McpServers.D365Service.All",
    "McpServers.D365Sales.All",
    "McpServers.Management.All",
    "McpServersMetadata.Read.All",
    "McpServers.Developer.All",
    "McpServers.CopilotMCP.All",
    "McpServers.OneDriveSharepoint.All",
    "McpServers.Mail.All",
    "McpServers.Teams.All",
    "McpServers.Me.All",
    "McpServers.Calendar.All",
    "McpServers.SharepointLists.All",
    "McpServers.Knowledge.All",
    "McpServers.Excel.All",
    "McpServers.Word.All",
    "McpServers.PowerPoint.All",
])


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _sp_object_id(graph_token: str, app_id: str, *, auto_provision: bool = False) -> str:
    """Resolve a service principal objectId from its appId.

    If ``auto_provision`` is True and no SP exists, materialize a service
    principal in the current tenant for the given (typically first-party
    Microsoft-owned) appId. Microsoft Graph's
    ``POST /servicePrincipals {"appId": "..."}`` is the supported way to
    surface a first-party app in a tenant on demand — no app registration
    is required because the app already exists in the home tenant.
    """
    r = requests.get(
        "https://graph.microsoft.com/v1.0/servicePrincipals",
        headers={"Authorization": f"Bearer {graph_token}"},
        params={"$filter": f"appId eq '{app_id}'", "$select": "id,displayName"},
        timeout=30,
    )
    r.raise_for_status()
    val = r.json().get("value") or []
    if val:
        return val[0]["id"]

    if not auto_provision:
        sys.exit(
            f"::error::No service principal found for appId {app_id}. "
            "It may not be provisioned in this tenant yet."
        )

    print(
        f"::notice::No service principal for first-party app {app_id} in this tenant — "
        "provisioning one via Graph (POST /servicePrincipals)…"
    )
    create = requests.post(
        "https://graph.microsoft.com/v1.0/servicePrincipals",
        headers={
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
        },
        json={"appId": app_id},
        timeout=60,
    )
    if create.status_code >= 300:
        try:
            err = create.json().get("error") or {}
        except ValueError:
            err = {}
        code = err.get("code", "") or ""
        msg = err.get("message", "") or ""
        # If another caller provisioned it between our GET and POST, fall
        # through to a second lookup.
        if "already exists" not in msg.lower() and code != "Request_MultipleObjectsWithSameKeyValue":
            sys.exit(
                f"::error::Failed to provision service principal for {app_id}: "
                f"{create.status_code} {code} {msg}"
            )
        # Re-query.
        r2 = requests.get(
            "https://graph.microsoft.com/v1.0/servicePrincipals",
            headers={"Authorization": f"Bearer {graph_token}"},
            params={"$filter": f"appId eq '{app_id}'", "$select": "id"},
            timeout=30,
        )
        r2.raise_for_status()
        val2 = r2.json().get("value") or []
        if not val2:
            sys.exit(f"::error::Provisioned but cannot find SP for {app_id}.")
        return val2[0]["id"]
    return create.json()["id"]


def _grant(
    graph_token: str,
    client_sp_id: str,
    resource_sp_id: str,
    scope: str,
    label: str,
) -> bool:
    """Create an OAuth2 permission grant. Returns True on success/idempotent."""
    body = {
        "clientId": client_sp_id,
        "consentType": "AllPrincipals",
        "principalId": None,
        "resourceId": resource_sp_id,
        "scope": scope,
    }
    r = requests.post(
        "https://graph.microsoft.com/v1.0/oauth2PermissionGrants",
        headers={
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    if r.status_code < 300:
        print(f"  ok ({label})")
        return True
    # Graph returns 400 Request_BadRequest when a grant already exists.
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    msg = err.get("message", "") or ""
    code = err.get("code", "") or ""
    if "Permission entry already exists" in msg:
        print(f"  ok ({label}) — already granted")
        return True
    if r.status_code in (401, 403) or code in {"Authorization_RequestDenied", "Authorization_IdentityNotFound"}:
        print(f"::warning::Graph denied the {label} grant ({r.status_code} {code}): {msg}")
        return False
    print(f"::error::{label} grant failed: {r.status_code} {code} {msg}")
    return False


def _manual_instructions(blueprint_app_id: str) -> None:
    print()
    print("Could not create the grants automatically.")
    print("Run this once from a shell with an Application Administrator / Global Admin login:")
    print()
    print(f"  AGENT_NAME=<name> PROJECT_ENDPOINT=<endpoint> \\")
    print(f"    az login --scope https://graph.microsoft.com/.default --allow-no-subscriptions")
    print(f"  python3 scripts/grant_blueprint_oauth2.py")
    print()
    print("Or grant consent in the Azure portal under the blueprint app:")
    print(
        f"  https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Permissions/appId/{blueprint_app_id}"
    )


def _signed_in_user_object_id(graph_token: str) -> str | None:
    """Return the human user's Graph object id, or None if not a user.

    When the caller is a service principal (e.g. CI under an OIDC federated
    credential), ``/me`` returns 400/404 and we return None so the owner
    step can be skipped without failing the publish flow.
    """
    r = requests.get(
        "https://graph.microsoft.com/v1.0/me?$select=id,userPrincipalName",
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30,
    )
    if r.status_code >= 300:
        return None
    body = r.json() or {}
    return body.get("id")


def _application_object_id(graph_token: str, app_id: str) -> str | None:
    """Resolve the Graph object id of the Application registration for *app_id*."""
    r = requests.get(
        "https://graph.microsoft.com/v1.0/applications",
        headers={"Authorization": f"Bearer {graph_token}"},
        params={"$filter": f"appId eq '{app_id}'", "$select": "id"},
        timeout=30,
    )
    if r.status_code >= 300:
        return None
    val = r.json().get("value") or []
    return val[0]["id"] if val else None


def _add_blueprint_owner(
    graph_token: str, blueprint_app_id: str
) -> None:
    """Add the signed-in user as an owner of the blueprint app registration.

    Without this, Teams Developer Portal renders the agent identity
    blueprint read-only ("You don't have access to edit this agent
    identity blueprint"). Idempotent: "already exists" is success.

    Skipped silently when:
      * the caller is a service principal (no ``/me``), or
      * the blueprint's Application object isn't visible to Graph (rare
        — happens when the app is owned by a different tenant).
    """
    print()
    print(">> Adding signed-in user as owner of the blueprint app (Dev Portal edit access)...")
    user_obj_id = _signed_in_user_object_id(graph_token)
    if not user_obj_id:
        print(
            "::notice::Caller is not a human user (likely CI service principal) — "
            "skipping blueprint owner add. Re-run `make grants <agent>` from a user shell."
        )
        return

    app_obj_id = _application_object_id(graph_token, blueprint_app_id)
    if not app_obj_id:
        print(
            f"::warning::Could not locate Application registration for {blueprint_app_id} "
            "via Graph; skipping owner add."
        )
        return

    r = requests.post(
        f"https://graph.microsoft.com/v1.0/applications/{app_obj_id}/owners/$ref",
        headers={
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
        },
        json={
            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_obj_id}"
        },
        timeout=60,
    )
    if r.status_code < 300:
        print("  ok — you can now edit the blueprint in Teams Developer Portal.")
        return
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    msg = (err.get("message") or "").lower()
    code = err.get("code") or ""
    if "already exist" in msg or code == "Request_BadRequest" and "already" in msg:
        print("  ok — already an owner.")
        return
    if r.status_code in (401, 403):
        print(
            f"::warning::Graph denied owner-add ({r.status_code} {code}): {msg or '(no message)'}. "
            "Run from a shell signed in as a tenant admin, or add yourself manually in the Azure portal."
        )
        return
    print(f"::warning::Owner-add failed: {r.status_code} {code} {msg or '(no message)'}")

    print("Could not create the grants automatically.")
    print("Run this once from a shell with an Application Administrator / Global Admin login:")
    print()
    print(f"  AGENT_NAME=<name> PROJECT_ENDPOINT=<endpoint> \\")
    print(f"    az login --scope https://graph.microsoft.com/.default --allow-no-subscriptions")
    print(f"  python3 scripts/grant_blueprint_oauth2.py")
    print()
    print("Or grant consent in the Azure portal under the blueprint app:")
    print(
        f"  https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Permissions/appId/{blueprint_app_id}"
    )


def main() -> int:
    agent_name = _env("AGENT_NAME")
    project_endpoint = _env("PROJECT_ENDPOINT").rstrip("/")

    cred = AzureCliCredential()
    ai_token = cred.get_token(AI_SCOPE).token
    graph_token = cred.get_token(GRAPH_SCOPE).token

    # Resolve the blueprint SP for the named agent.
    r = requests.get(
        f"{project_endpoint}/agents/{agent_name}?api-version={API_VERSION}",
        headers={
            "Authorization": f"Bearer {ai_token}",
            "Foundry-Features": "AgentEndpoints=V1Preview,HostedAgents=V1Preview",
        },
        timeout=30,
    )
    r.raise_for_status()
    agent = r.json()
    latest = (agent.get("versions") or {}).get("latest") or {}
    blueprint_app_id = (latest.get("blueprint") or {}).get("client_id") \
        or (agent.get("blueprint") or {}).get("client_id")
    if not blueprint_app_id:
        sys.exit(f"::error::Agent '{agent_name}' has no blueprint.client_id.")

    print(f"Agent:                {agent_name}")
    print(f"Blueprint app id:     {blueprint_app_id}")

    if not APX_APP_ID or not PROD_MCP_APP_ID:
        sys.exit("::error::Set APX_APP_ID and PROD_MCP_APP_ID for the target cloud.")

    blueprint_sp = _sp_object_id(graph_token, blueprint_app_id)
    # APX + Prod MCP are first-party Microsoft apps. In a fresh tenant
    # they don't have a service principal until something causes one to be
    # created. Auto-provision so `make publish` works on day one.
    apx_sp = _sp_object_id(graph_token, APX_APP_ID, auto_provision=True)
    mcp_sp = _sp_object_id(graph_token, PROD_MCP_APP_ID, auto_provision=True)
    print(f"Blueprint SP id:      {blueprint_sp}")
    print(f"APX SP id:            {apx_sp}  (Messaging Bot API Application)")
    print(f"Prod MCP SP id:       {mcp_sp}  (Agent Tools)")
    print()
    print("Granting OAuth2 admin consent (AllPrincipals)...")

    ok_apx = _grant(graph_token, blueprint_sp, apx_sp, APX_SCOPES, "APX AgentData.ReadWrite")
    ok_mcp = _grant(graph_token, blueprint_sp, mcp_sp, PROD_MCP_SCOPES, "Prod MCP scopes")

    if not (ok_apx and ok_mcp):
        _manual_instructions(blueprint_app_id)
        # Don't fail CI — operator can complete the step.
        return 0

    # Make the running human the Dev Portal owner so they can edit the
    # blueprint UI. No-op for CI / service-principal callers.
    _add_blueprint_owner(graph_token, blueprint_app_id)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n### Blueprint OAuth2 grants for `{agent_name}`\n\n"
                    f"- APX `AgentData.ReadWrite` — granted\n"
                    f"- Prod MCP scopes — granted\n"
                )
        except OSError:
            pass
    print()
    print("::notice::Blueprint OAuth2 grants applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
