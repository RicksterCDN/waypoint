# AI Teammate (Microsoft 365) wiring

How to take a hosted Foundry agent in this repo and make it appear as an
**AI Teammate** ("digital worker") in Microsoft 365 / Teams, and what each
moving part actually does. Captures the real, end-to-end flow we shipped
for `rhodes` and `lovelace` after a few preview-API potholes.

> The `make publish <agent>` chain covers most of this, but **two steps
> are still manual today** (see [Known gaps](#known-gaps)).

---

## Table of contents

- [Two surfaces, two code paths](#two-surfaces-two-code-paths)
- [The four identities involved](#the-four-identities-involved)
- [What `make publish <agent>` does](#what-make-publish-agent-does)
- [Manual steps you still have to do](#manual-steps-you-still-have-to-do)
- [The activity protocol mount](#the-activity-protocol-mount)
- [Per-hire Foundry User grant](#per-hire-foundry-user-grant)
- [End-to-end checklist](#end-to-end-checklist)
- [Troubleshooting](#troubleshooting)
- [Known gaps](#known-gaps)

---

## Two surfaces, two code paths

A hosted agent can be reached two different ways once published:

| Surface | Path inside the container | Token shape | Handled by |
|---|---|---|---|
| Teams "direct bot chat" / Foundry Playground | `POST /responses` (SSE) | Foundry/Entra | `ResponsesHostServer` (Agent Framework) |
| **AI Teammate hire** (a `lovelacev1729`-style identity hires the agent) | `POST /api/messages` (Bot Framework Activity) | AI Teammate JWT (audience = blueprint MSA AppId) | `mount_activity_protocol(...)` |

Both run in the same container. Foundry does NOT auto-bridge AI Teammate
traffic into the Responses path — you must mount `/api/messages` yourself.

---

## The four identities involved

This is where most of the confusion lives. Every published agent has
four distinct Entra identities, and they each do exactly one thing:

| Identity | Type | What it does | Where to find it |
|---|---|---|---|
| **Project MI** | User-assigned MI | Foundry control-plane identity for the project | `319d9ca7-…` (shared across all agents in the project) |
| **`instance_identity`** | Service Identity (MI) | Immutable per-agent identity that backs the **Bot Service `msaAppId`** | Foundry agent definition → `instance_identity.client_id` |
| **`blueprint`** | Entra application | The AI Teammate "blueprint" app. JWTs to `/api/messages` target *its* audience. Owns the federated identity credentials. | Foundry agent definition → `blueprint.client_id` (also in `.azure/<agent>/.env` as `<AGENT>_BLUEPRINT_CLIENT_*`) |
| **Per-hire MI** | User-assigned MI | Created by M365 each time someone hires the agent (`lovelacev1729`, `rhodesv1519`, …). What `DefaultAzureCredential` resolves to *inside the per-hire container*. | Resource group, displayName matches `^<agent>v?\d+$` |

You will end up with **two Bot Service entries per agent** in the RG — one
keyed off `instance_identity`, one keyed off `blueprint`. That is expected;
both endpoint URLs point at the Foundry activity protocol.

### Federated identity credentials on the blueprint app

The blueprint app needs **four FICs** or `/api/messages` token exchange
fails with an opaque `-60018`:

| FIC name | Subject | Created by |
|---|---|---|
| `ProjectManagedIdentityFederatedIdentityCredential` | Project MI `client_id` | Foundry on publish |
| `Mos1pAppCredential` | `/eid1/c/pub/t/<tenant_b64>/a/<m365_b64>/<blueprint_b64>` | M365 backend |
| `fmi-fic` | `/eid1/c/pub/t/<tenant_b64>/a/GvQ2ByUERku9tRVj7_AjhQ/AzureAI/FMI` | Foundry |
| `InstanceManagedIdentity-<agent>` | `instance_identity.client_id` | **You, today.** See [Known gaps](#known-gaps) |

If the per-hire chat hangs and App Insights shows `-60018` with no
diagnostic logs, this last FIC is what's missing.

### ⚠️ The blueprint client secret must be a real value, not empty

The `agent.yaml` for every agent wires two env vars from azd into the
container:

```
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=${<AGENT>_BLUEPRINT_CLIENT_ID}
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=${<AGENT>_BLUEPRINT_CLIENT_SECRET}
```

With `AUTHTYPE=ClientSecret` (the default the M365 Agents SDK uses), MSAL
**only** sends `CLIENT_SECRET` to AAD when exchanging for the agentic
blueprint token. It does **not** fall back to federated credentials — that
path requires `AUTHTYPE=federated_credentials`, which Foundry does not set.

If `<AGENT>_BLUEPRINT_CLIENT_SECRET` is unset / empty, the container
deploys cleanly, the `/api/messages` route mounts, the inbound JWT
validates, and then the SDK fails at the very last step with:

```
AADSTS7000216: 'client_assertion', 'client_secret' or 'request' is
required for the 'client_credentials' grant type
```

which surfaces upstream as a generic `-60018`.

**Trap:** the Foundry control-plane API (`GET /agents/<name>`) redacts
`CLIENTSECRET` to `""` on read. So an env-diff between a working agent
and a broken one will look **identical** even though one has a real
secret at runtime and the other has nothing. Trust the SDK diagnostics,
not the API.

Create / rotate the secret and persist it to azd once per agent:

```bash
NEW_SECRET=$(az ad app credential reset \
    --id <blueprint_client_id> \
    --display-name '<agent>-agent-runtime' \
    --years 2 --query password -o tsv)
azd env set <AGENT>_BLUEPRINT_CLIENT_ID <blueprint_client_id>
azd env set <AGENT>_BLUEPRINT_CLIENT_SECRET "$NEW_SECRET"
azd deploy <agent>
```

### Endpoint `version_selector` must route to `@latest`

`patch_agent_endpoint.py` always PATCHes the agent endpoint to
`agent_version: "@latest", traffic_percentage: 100`. If the selector ever
gets pinned to a numeric version (e.g. a manual portal edit or a stale
first-publish flow), subsequent `azd deploy` creates new agent versions
that the endpoint never routes to — your code changes silently never
reach Teams traffic. The script's idempotent re-run fixes it.

---

## What `make publish <agent>` does

Calls these scripts in order (all idempotent):

1. **[`patch_agent_endpoint.py`](../scripts/patch_agent_endpoint.py)** —
   strips the `Entra` auth scheme from the agent endpoint, leaving
   `BotServiceRbac` only. Without this, every first-time Teams user gets an
   "Open Foundry login" OAuth card before the agent can answer.
2. **[`ensure_bot_service.py`](../scripts/ensure_bot_service.py)** —
   creates the Azure Bot Service + Teams channel, points its messaging
   endpoint at the Foundry activity-protocol URL.
3. **[`ensure_foundry_application.py`](../scripts/ensure_foundry_application.py)**
   — registers the "Built for your org" catalog entry so the agent appears
   in the M365 admin center.
4. **[`publish_agent.py`](../scripts/publish_agent.py)** — submits the M365
   publish request (uses `publish.yaml`).
5. **[`grant_blueprint_oauth2.py`](../scripts/grant_blueprint_oauth2.py)** —
   grants OAuth2 admin consent on the blueprint SP for APX + Prod MCP. The
   agent simply will not respond without this. Requires Application
   Administrator (or higher) in the tenant.
6. **[`grant_instance_mi_roles.py`](../scripts/grant_instance_mi_roles.py)**
   — grants **Foundry User** to any existing per-hire MIs. No-op until at
   least one hire exists. See [Per-hire Foundry User grant](#per-hire-foundry-user-grant).
7. **[`print_publish_next_steps.py`](../scripts/print_publish_next_steps.py)**
   — banner with admin-approval / Teams Dev Portal / hire URLs.

---

## Manual steps you still have to do

### 1. Approve the publish request in the M365 admin center

The publish in step 4 above just *submits* a request. A tenant admin has to
approve it at
`https://admin.microsoft.com` → Integrated apps → Pending requests → Approve.
Until that happens, the agent is invisible to end users.

### 2. Configure the Teams Developer Portal blueprint backend

After admin approval, open
`https://dev.teams.microsoft.com/tools/agent-blueprint/<blueprintId>` →
**Configuration** → set **Bot Based** + **Bot ID = `<blueprintId>`** → Save.

Without this M365 routes hire traffic to the wrong backend and your
container never sees `POST /api/messages`. The repo includes
[`configure_teams_blueprint.py`](../scripts/configure_teams_blueprint.py)
(`make teams-config <agent>`) that *attempts* the PUT, but the Teams Dev
Portal API rejects CLI tokens with 403 today, so the script falls back to
opening the browser.

### 3. Create the `InstanceManagedIdentity-<agent>` FIC

```bash
# Look up the blueprint app object id and the instance_identity.client_id
# from the Foundry agent definition first, then:
az rest --method post \
  --uri "https://graph.microsoft.com/v1.0/applications/<blueprint_obj_id>/federatedIdentityCredentials" \
  --headers "Content-Type=application/json" \
  --body '{
    "name": "InstanceManagedIdentity-<agent>",
    "issuer": "https://login.microsoftonline.com/<tenant>/v2.0",
    "subject": "<instance_identity.client_id>",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

There's a [known-gap TODO](#known-gaps) to fold this into a
`scripts/ensure_blueprint_instance_fic.py` and wire it into `make publish`.

### 4. Hire the agent in M365, then grant the per-hire MI

After someone hires the agent in M365 (Copilot → AI Teammate gallery), run:

```bash
make grant-hires <agent>
```

Wait **5–15 minutes** for the Foundry-User role assignment to propagate
before the first chat will succeed — the per-hire MI is brand new and
the running container may also be caching an earlier "no permissions"
token response. If you don't want to wait, `azd deploy <agent>` forces
a container restart and flushes the token cache. See
[Per-hire Foundry User grant](#per-hire-foundry-user-grant) below for why.

---

## The activity protocol mount

[`agents/<name>/activity_protocol.py`](../agents/lovelace/activity_protocol.py)
adds `POST /api/messages` to the existing `ResponsesHostServer`. It is
**purely additive** — the Responses path is never modified, so direct bot
chat keeps working even if the teammate wiring is broken.

Key design choices documented inline:

- Uses the **Microsoft 365 Agents SDK for Python**
  (`microsoft-agents-hosting-*`), NOT legacy `botbuilder-core`. The
  BotBuilder `CloudAdapter` validates Bot Framework v1 JWTs, which is the
  wrong token shape for AI Teammate.
- **No-op when the blueprint client id env var is unset**, so local dev
  and pre-publish builds don't crash.
- Wrapped in a broad `try/except` because the whole function runs *before*
  `host.run()`; an exception here would kill the Responses path too.
- **Does NOT pass `auth_handlers=["AGENTIC"]`** to the message handler.
  The AGENTIC handler triggers the SDK's `user_fic` OBO exchange for the
  agentUser identity, which currently returns AADSTS65001 ("user or admin
  has not consented") because the agentUser never went through interactive
  consent for the freshly-minted per-hire app. For a simple text reply we
  only need the instance/application token (which the SDK acquires for the
  typing indicator).
- Hands the SDK a **tool-less clone of the agent** so per-hire containers
  don't try to call the project toolbox MCP endpoint (which the per-hire
  MI has no access to).

### Why there is a diagnostic monkey-patch

The SDK's `MsalAuth.get_agentic_application_token` silently returns `None`
on any non-success MSAL response, which surfaces upstream as a generic
`-60018` with no detail. The activity_protocol module monkey-patches that
method to log the raw MSAL payload at WARNING (so it shows up in App
Insights). Without this you cannot tell whether the failure is a missing
FIC, the wrong scope, a misconfigured client secret, or an AADSTS consent
gap.

Keep this monkey-patch until the AI Teammate auth path is GA and stable.

---

## Per-hire Foundry User grant

When a user hires an agent, M365 mints a fresh `ServiceIdentity` principal
in your tenant. That principal is what `DefaultAzureCredential` resolves to
inside the per-hire container, and it is the identity `FoundryChatClient`
uses to call the project `/openai/v1/responses` endpoint.

Hires get **one of two** displayName shapes depending on the hire path:

* `<agent>v?<digits>[suffix]` — e.g. `lovelacev1729`, `rhodes1710email`.
  Foundry-generated, no user input.
* **User-chosen displayName** — e.g. `Fibey Amanda3`, `Signal Digital Worker2`.
  Whatever the hirer typed when picking the agent from the M365 Copilot
  store.

For the call to succeed, each per-hire principal needs the
**Foundry User** role (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) on **both**
the Foundry account and the project. That one role covers all the data
actions documented at [aka.ms/FoundryPermissions](https://aka.ms/FoundryPermissions)
for hosted agents (`responses/*`, `agents/*`, `agents/storage/*`).

`scripts/grant_instance_mi_roles.py` discovers hires two ways:

1. **By blueprint linkage (preferred).** When `BLUEPRINT_CLIENT_ID` is in
   the environment, it lists all `ServiceIdentity` SPs in the tenant and
   keeps the ones whose Graph beta `agentIdentityBlueprintId` matches.
   This catches **both** Foundry-named and user-named hires.
2. **By displayName regex (legacy).** Matches `^<agent>v?\d+[a-z]*$`. Used
   when `BLUEPRINT_CLIENT_ID` is unset, and always run as an additional
   pass so the union of both methods is granted.

The Makefile's `grant-hires` and `publish` targets auto-derive
`BLUEPRINT_CLIENT_ID` from the per-agent azd env var
`${AGENT_UPPER}_BLUEPRINT_CLIENT_ID` (e.g. `FIBEY_COORDINATOR_BLUEPRINT_CLIENT_ID`),
so the blueprint-linkage path is on by default once an agent has been
published.

The grant is idempotent — repeated runs collapse to the same role assignment.

> **This is an anti-pattern, but it is the current preview reality.** See
> [Known gaps](#known-gaps) for the planned automation.

---

## End-to-end checklist

For a brand-new agent (`oracle`):

1. `make new-agent oracle` — scaffolds `agents/oracle/`.
2. Edit `main.py`, `toolbox.py`, `agent.yaml`.
3. `git commit && git push` → CI deploys (or run `azd deploy oracle` locally).
4. `make publish oracle`.
5. Admin approves the publish request in the M365 admin center.
6. Open Teams Dev Portal → Configuration → Bot Based + Bot ID = blueprint id → Save.
7. Add the `InstanceManagedIdentity-oracle` FIC on the blueprint app.
8. Hire the agent in M365.
9. `make grant-hires oracle` (wait ~60s).
10. Send a test message in Teams.

For an existing agent after code-only changes:

1. `azd deploy <agent>` (fresh image).
2. Send a test message. The existing hire keeps working — no re-hire needed.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Test message gets no reply, no inbound `POST /api/messages` in App Insights | Teams Dev Portal backend not set, OR admin hasn't approved the publish request, OR endpoint `version_selector` pinned to an old version | Manual step 1 + 2 above; re-run `make publish <agent>` to re-PATCH `version_selector` to `@latest` |
| Direct `/responses` ping returns **HTTP 424 `session_not_ready`** "did not become ready within the expected timeout" — and App Insights shows **zero** traffic for the agent's role (not even `/readiness` probes) | Container is crashing during import / startup before App Insights can attach. Most common cause: `AZURE_AI_MODEL_DEPLOYMENT_NAME` substituted to an empty string because the azd env var is missing. `main.py` raises `EnvironmentError` on import and the process exits. | `azd env get-values \| grep AZURE_AI_MODEL_DEPLOYMENT_NAME` — if missing/empty, run `azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME gpt-5.5` (or whichever deployment is active on your project), then `azd deploy <agent>`. The same pattern catches any other required env var that resolves to `""` |
| Inbound `POST /api/messages` → 500 with `-60018` and `[diag] CCA token payload={'error': 'invalid_client', ... AADSTS7000216 ...}` | `<AGENT>_BLUEPRINT_CLIENT_SECRET` is empty or stale (the Foundry API redacts it to `""` on read, so env-diff is misleading) | Run the `az ad app credential reset` block in [The blueprint client secret must be a real value, not empty](#⚠️-the-blueprint-client-secret-must-be-a-real-value-not-empty) then `azd deploy <agent>` |
| Inbound `POST /api/messages` → 500 with `-60018` and no `[diag]` logs | Container image predates the monkey-patch | `azd deploy <agent>` to rebuild |
| `[diag] CCA token payload` shows AADSTS70021 / token-exchange error | Missing `InstanceManagedIdentity-<agent>` FIC on the blueprint app | Manual step 3 above |
| Inbound POST succeeds, but reply is `PermissionDenied: principal <guid> lacks Microsoft.CognitiveServices/accounts/AIServices/agents/write` | Per-hire MI hasn't been granted Foundry User yet, OR the grant hasn't propagated | `make grant-hires <agent>`, then wait 5–15 min (or `azd deploy <agent>` to flush the container token cache) |
| `make grant-hires <agent>` reports `No per-instance MIs found` even though a hire exists in the tenant (e.g. you can see "Fibey Amanda3" or "Signal Digital Worker2" in Entra) | The hire was named by the user during the M365 hire flow rather than by Foundry's `<agent>v?\d+[suffix]` convention, so the legacy displayName regex misses it | Make sure `${AGENT_UPPER}_BLUEPRINT_CLIENT_ID` is set in your azd env (the Makefile auto-passes it as `BLUEPRINT_CLIENT_ID`). The script will then ALSO discover hires via Graph's `agentIdentityBlueprintId` field, which catches user-named hires. If you don't have the blueprint client id, grab it from `azd env get-values \| grep BLUEPRINT_CLIENT_ID` and re-run `make grant-hires <agent>` |
| **First** Teams message after `make grant-hires` returns the `[diag] AuthenticationError 401 PermissionDenied agents/write` Python traceback in chat, but a **second** message ~30s later succeeds | Expected behavior: Foundry's data-plane authorization cache had the pre-grant ("no role") decision cached for this principal. First call hits the stale cache → 401 → activity_protocol's error handler surfaces the traceback in chat. The cache TTL is short (~30–60s), so the next message reaches the freshly-resolved role assignment | No action — confirm a subsequent message succeeds. If 401s continue past 5 min, run `azd deploy <agent>` to force a container token-cache flush, or verify the grant landed with `az role assignment list --assignee <hire-principal-id> --all` |
| First-time user gets an "Open Foundry login" OAuth card | `Entra` auth scheme still on the agent endpoint | `make publish <agent>` (re-runs `patch_agent_endpoint.py`) |
| Direct bot chat works, hired chat doesn't | This is the normal split — check inbound `POST /api/messages` traces vs `POST /responses` traces to localize | Compare with a known-working agent in the same project |
| Code change deployed (`azd deploy`) but Teams still shows old behavior | Endpoint `version_selector` is pinned to an old numeric version, not `@latest` | `make publish <agent>` to re-PATCH selector |

Useful App Insights query when debugging a hire:

```kusto
traces
| where timestamp > ago(15m)
| where cloud_RoleName == "<agent>"
| project timestamp, message
| order by timestamp asc
```

---

## Known gaps

Things `make publish` does **not** do today, ranked by how badly they
should be automated:

1. **`InstanceManagedIdentity-<agent>` FIC creation.** Manual `az rest` PUT
   today. Plan: add `scripts/ensure_blueprint_instance_fic.py`, call it
   from `make publish`.
2. **Teams Dev Portal backend config.** The API rejects CLI tokens with
   403; the script falls back to opening a browser. Plan: surface a loud
   "click this URL" banner from `make publish` instead of burying it in
   `print_publish_next_steps`.
3. **Per-hire Foundry User grant on hire.** Today: hire → wait → run
   `make grant-hires` → wait for RBAC propagation → first message works.
   Options (ranked easiest first):
   1. **Scheduled cron via GitHub Actions** — every 5 min, loop discovered
      agents, run `grant_instance_mi_roles.py`. No new Azure infra.
      Race window ≤ 5 min. **Recommended near-term.**
   2. **Group-based RBAC** — Entra group `foundry-user-<agent>-hires`
      granted Foundry User on account+project once; per-hire job only
      adds MI to group. Cheaper per-hire, still post-hire.
   3. **Event-driven** — Activity Log alert on
      `Microsoft.ManagedIdentity/userAssignedIdentities/write` in the RG
      → Logic App / Function → `grant_instance_mi_roles.py`. Near-real-time
      but adds infra to operate.
   4. **Wait for Foundry GA.** AI Teammate preview is expected to grow a
      "grant per-hire MIs Foundry User on this project" checkbox.

PRs welcome on any of the above.
