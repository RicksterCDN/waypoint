"""Grant the Foundry User role to every per-instance MI of a hosted agent.

Each time a Microsoft 365 AI Teammate "hire" happens, Foundry mints a fresh
ServiceIdentity managed identity in the tenant whose displayName matches
``<agentName><timestamp>`` or ``<agentName>v<timestamp>`` (e.g.
``lovelace1701`` or ``rhodesv1519``). That per-hire identity is
what ``DefaultAzureCredential`` resolves to inside the container, so it is
the principal the FoundryChatClient uses to call the project
``/openai/v1/responses`` endpoint.

For the call to succeed, each per-instance MI needs the **Foundry User**
role (id ``53ca6127-db72-4b80-b1b0-d745d6d5456d``) on both the Foundry
account and the project — that single role covers all of the data actions
documented at https://aka.ms/FoundryPermissions for hosted agents
(``responses/*``, ``agents/*``, ``agents/storage/*``).

This script is idempotent and safe to run repeatedly. It is wired into
``make publish <agent>`` and exposed as ``make grant-hires <agent>`` so the
demo flow doesn't require manual ``az role assignment create`` after every
new hire.

Required env vars:
    AGENT_NAME              - the hosted agent's folder/azd service name
    ACCOUNT_NAME            - Foundry / AIServices account name
    PROJECT_NAME            - Foundry project name
    RESOURCE_GROUP          - resource group of the account
    SUBSCRIPTION_ID         - subscription id

Optional env vars:
    BLUEPRINT_CLIENT_ID     - the blueprint app's client_id. When set, the
                              script also discovers hires via Graph's
                              ``agentIdentityBlueprintId`` field, which
                              catches user-named hires (e.g. "Fibey Amanda3")
                              that the legacy displayName regex would miss.
                              Wired in from azd env by the Makefile's
                              ``grant-hires`` target.

The caller (user or service principal) needs ``Microsoft.Authorization/
roleAssignments/write`` on the account/project scope — typically Owner or
Role Based Access Control Administrator.
"""

from __future__ import annotations

import os
import re
import sys
import time

import requests
from azure.identity import AzureCliCredential


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
ROLE_API_VERSION = "2022-04-01"


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"::error::Missing required env var: {name}")
    return val


def _list_per_instance_mis(graph_token: str, agent: str) -> list[dict]:
    """Return ServiceIdentity SPs whose displayName matches ``<agent>v?\\d+[suffix]``."""
    # Foundry uses several naming conventions depending on hire path:
    #   * ``<agent>``               (bare instance, no suffix)
    #   * ``<agent>v<HHMM>``        (rhodes)
    #   * ``<agent><HHMM>``         (lovelace)
    #   * ``<agent><HHMM><suffix>`` (e.g. ``rhodes1710email`` for email-enabled
    #     instances)
    # We match all four with an optional 'v' separator, an optional numeric
    # timestamp, and an optional trailing lowercase word. The whole suffix
    # group is optional so a bare ``<agent>`` displayName still matches.
    pattern = re.compile(rf"^{re.escape(agent)}(v?\d+[a-z]*)?$")
    url = (
        "https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=startswith(displayName,'{agent}')"
        "&$select=id,displayName,servicePrincipalType"
        "&$top=999"
    )
    found: list[dict] = []
    while url:
        r = requests.get(url, headers={"Authorization": f"Bearer {graph_token}"}, timeout=30)
        r.raise_for_status()
        body = r.json()
        for sp in body.get("value", []):
            if sp.get("servicePrincipalType") != "ServiceIdentity":
                continue
            if not pattern.match(sp.get("displayName") or ""):
                continue
            found.append(sp)
        url = body.get("@odata.nextLink")
    return found


def _list_hires_by_blueprint(graph_token: str, blueprint_client_id: str) -> list[dict]:
    """Return ServiceIdentity SPs linked to ``blueprint_client_id``.

    More reliable than ``_list_per_instance_mis`` because it doesn't depend on
    Foundry's hire-naming convention — works for user-named hires like
    "Fibey Amanda3" that the regex misses entirely.

    Graph's beta ``servicePrincipals`` resource exposes two custom fields on
    ``ServiceIdentity`` principals:

    * ``agentIdentityBlueprintId`` — the blueprint app's ``appId`` (i.e. our
      ``<AGENT>_BLUEPRINT_CLIENT_ID`` env var).
    * ``agentAppId``               — same value (the SDK uses both names).

    Server-side ``$filter`` on these fields returns 400 (not indexed), so we
    list all ``ServiceIdentity`` SPs and filter client-side. The tenant
    typically has tens — never thousands — so a single page suffices.

    Skips the *instance identity* SP (the one whose displayName ends with
    ``-AgentIdentity``) because that principal already gets its roles wired
    by Foundry at agent-deploy time. Only true per-hire SPs need grants.
    """
    found: list[dict] = []
    url = (
        "https://graph.microsoft.com/beta/servicePrincipals"
        "?$filter=servicePrincipalType eq 'ServiceIdentity'"
        "&$select=id,displayName,appId,agentIdentityBlueprintId,agentAppId"
        "&$top=999"
    )
    while url:
        r = requests.get(url, headers={"Authorization": f"Bearer {graph_token}"}, timeout=30)
        r.raise_for_status()
        body = r.json()
        for sp in body.get("value", []):
            bp = sp.get("agentIdentityBlueprintId") or sp.get("agentAppId")
            if bp != blueprint_client_id:
                continue
            # Skip the instance-identity SP — Foundry manages its roles.
            name = sp.get("displayName") or ""
            if name.endswith("-AgentIdentity"):
                continue
            found.append(sp)
        url = body.get("@odata.nextLink")
    return found


def _ensure_role(arm_token: str, scope: str, principal_id: str, label: str) -> bool:
    """Idempotently create the Foundry User assignment at ``scope``."""
    sub = _env("SUBSCRIPTION_ID")
    role_def_id = (
        f"/subscriptions/{sub}/providers/Microsoft.Authorization/"
        f"roleDefinitions/{FOUNDRY_USER_ROLE_ID}"
    )
    # Deterministic GUID from (scope, principal, role) so repeated runs collapse.
    import uuid as _uuid
    namespace = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    assignment_name = str(_uuid.uuid5(namespace, f"{scope}|{principal_id}|{FOUNDRY_USER_ROLE_ID}"))
    url = (
        f"https://management.azure.com{scope}/providers/Microsoft.Authorization/"
        f"roleAssignments/{assignment_name}?api-version={ROLE_API_VERSION}"
    )
    body = {
        "properties": {
            "roleDefinitionId": role_def_id,
            "principalId": principal_id,
            "principalType": "ServicePrincipal",
        }
    }
    r = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {arm_token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    if r.status_code < 300:
        print(f"  ok ({label}) — granted")
        return True
    try:
        err = (r.json().get("error") or {})
    except ValueError:
        err = {}
    code = err.get("code") or ""
    msg = err.get("message") or r.text
    if code == "RoleAssignmentExists" or "already exists" in msg.lower():
        print(f"  ok ({label}) — already granted")
        return True
    if r.status_code == 400 and "PrincipalNotFound" in msg:
        # Per-instance MI replication can lag a few seconds after creation.
        print(f"  retrying ({label}) — principal not yet replicated")
        time.sleep(5)
        r2 = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {arm_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        if r2.status_code < 300:
            print(f"  ok ({label}) — granted (after retry)")
            return True
        msg = (r2.json().get("error") or {}).get("message") or r2.text
        print(f"::warning::{label} grant failed: {r2.status_code} {msg}")
        return False
    print(f"::warning::{label} grant failed: {r.status_code} {code} {msg}")
    return False


def main() -> None:
    agent = _env("AGENT_NAME")
    account = _env("ACCOUNT_NAME")
    project = _env("PROJECT_NAME")
    rg = _env("RESOURCE_GROUP")
    sub = _env("SUBSCRIPTION_ID")
    # Optional — when set, also discover hires via blueprint linkage. This
    # catches user-named hires (e.g. "Fibey Amanda3") that the legacy regex
    # would otherwise miss. Wired in from azd env in the Makefile's
    # ``grant-hires`` target as ``${AGENT_UPPER}_BLUEPRINT_CLIENT_ID``.
    blueprint_client_id = os.environ.get("BLUEPRINT_CLIENT_ID") or ""

    account_scope = (
        f"/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
    )
    project_scope = f"{account_scope}/projects/{project}"

    cred = AzureCliCredential()
    graph_token = cred.get_token(GRAPH_SCOPE).token
    arm_token = cred.get_token(ARM_SCOPE).token

    print(f">> Discovering per-instance MIs for agent '{agent}'...")
    discovered: dict[str, dict] = {}
    for sp in _list_per_instance_mis(graph_token, agent):
        discovered[sp["id"]] = sp
    legacy_count = len(discovered)
    if blueprint_client_id:
        print(f"   Also searching by blueprint {blueprint_client_id}...")
        for sp in _list_hires_by_blueprint(graph_token, blueprint_client_id):
            discovered[sp["id"]] = sp
        blueprint_only = len(discovered) - legacy_count
        if blueprint_only:
            print(
                f"   Found {blueprint_only} additional hire(s) via blueprint linkage "
                "(not matched by the legacy name regex)."
            )
    mis = list(discovered.values())
    if not mis:
        print(
            f"   No per-instance MIs found (pattern '{agent}[v]<timestamp>' or "
            "blueprint linkage). If a teammate has not been hired yet, this is "
            "expected. Re-run after the first hire."
        )
        return

    print(f"   Found {len(mis)}: {', '.join(sp['displayName'] for sp in mis)}")
    failures = 0
    for sp in mis:
        pid = sp["id"]
        name = sp["displayName"]
        print(f">> Granting Foundry User to {name} ({pid})")
        ok1 = _ensure_role(arm_token, account_scope, pid, f"{name} @ account")
        ok2 = _ensure_role(arm_token, project_scope, pid, f"{name} @ project")
        if not (ok1 and ok2):
            failures += 1

    if failures:
        print(
            f"::warning::{failures} principal(s) could not be granted. "
            "See messages above. Caller may lack Microsoft.Authorization/"
            "roleAssignments/write on the account/project scope."
        )
        sys.exit(1)
    print(f">> Done. {len(mis)} per-instance MI(s) have Foundry User on account + project.")


if __name__ == "__main__":
    main()
