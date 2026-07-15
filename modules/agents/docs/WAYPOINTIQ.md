# WaypointIQ toolbox boundary

WaypointIQ is the agent-facing capability for governed access to Caldova
Waypoint. Waypoint remains the backing software/API and system of record;
WaypointIQ is the Foundry toolbox surface that Caldova Forge agents should bind
to when they need invoice-assurance context or governed writes. A future
Waypoint operations expert prompt agent can provide a reasoning layer over those
tools; the toolbox itself is not an agent.

## Why this boundary exists

Forge currently reaches Waypoint through direct Python clients inside hosted
agents:

- `assurance-orchestrator` has read-only Waypoint tools.
- `waypoint-recorder` has the only Waypoint write tools.
- `assurance-analyst` has read-only run/case status tools and is a hosted,
  read-only agent over WaypointIQ plus FoundryIQ contract/policy grounding.

That works for hosted agents, but it does not give prompt agents or future
agentic workflows a reusable, governed Waypoint capability. WaypointIQ should
lift the existing Waypoint read/write contracts into explicit OpenAPI toolbox
surfaces first. A remote MCP server can still be added later if Waypoint needs
custom tool orchestration beyond the REST API contract.

## Agent/tool split

Caldova Forge should keep the same pattern it uses for the other experts:
agents reason, tools provide capabilities, and workflows govern when those
capabilities are used.

| Layer | Responsibility |
| --- | --- |
| `waypoint-operations-expert` (planned) | Future prompt agent that reasons over Caldova/Waypoint operational state, policy, and safe action selection. No current source folder exists yet. |
| `waypoint-iq` toolbox | OpenAPI tool surface that exposes curated Waypoint read/write/admin capabilities. |
| Caldova Waypoint | System of record for work, invoices, findings, cases, runs, recommendations, drafts, policies, authorization, and audit. |

Do not describe WaypointIQ as an agent. WaypointIQ is the tool surface. A future
Waypoint operations expert can use that surface, including Foundry tool search if
the toolbox grows beyond a small fixed operation set.

## Capability split

| Capability group | Authority | Intended consumers |
| --- | --- | --- |
| `read` | Read Waypoint work, invoices, findings, evidence, contract documents, policies, runs, and cases. | `assurance-orchestrator`, `assurance-analyst`, future `waypoint-operations-expert`, future read-only prompt agents. |
| `write` | Open run anchors, create/locate assurance cases, stage recommendations, and create drafts. | `assurance-orchestrator`, `waypoint-recorder`, and explicitly approved write-capable operational agents after WaypointIQ write authority is proven. |
| `admin` | Seed/import/admin/authorization operations. | Human or admin automation only; not bound to production agents by default. |

Keep these capability groups inside one toolbox named `waypoint-iq`. The split is
expressed through operation metadata, agent binding policy, and Waypoint
authorization rather than separate toolbox identities. This preserves the current
Forge governance rule while keeping one durable IQ contract.

## Initial OpenAPI tool map

`waypoint-iq` should start from a curated Waypoint OpenAPI document that includes
the read operations already used by `agents/assurance-orchestrator/waypoint_tools.py`
and `agents/assurance-analyst/waypoint_status_tools.py`:

| WaypointIQ read tool | Backing Waypoint API |
| --- | --- |
| `waypointiq_get_work` | `GET /api/work` |
| `waypointiq_get_action_types` | `GET /api/actions/types` |
| `waypointiq_get_runs` | `GET /api/runs` |
| `waypointiq_get_cases` | `GET /api/cases` |
| `waypointiq_get_invoice` | `GET /api/invoices/{id}` |
| `waypointiq_get_invoice_context` | `GET /api/invoices/{id}/context` |
| `waypointiq_get_invoice_decisions` | `GET /api/invoice-decisions` |
| `waypointiq_get_findings` | `GET /api/findings` |
| `waypointiq_get_evidence` | `GET /api/evidence` |
| `waypointiq_get_contract_document` | `GET /api/contract-documents/{id}` |
| `waypointiq_get_policy` | `GET /api/policies/{id}` |

The same curated document can include write and admin operations, but those
operations must carry explicit authority metadata and remain governed by
Waypoint-side authorization. Assurance Orchestrator is read-only in the current proof path, but
that is a deferred posture until WaypointIQ exists rather than a permanent
architecture. Waypoint Recorder remains the current governed write boundary until
WaypointIQ write authority is proven.

Waypoint is a FastAPI app and can emit `/openapi.json` when API docs are
enabled. Production Waypoint disables docs by default, so the toolbox input
should be a checked/exported OpenAPI artifact from the Waypoint repo or a
deployment-time fetch from an explicitly docs-enabled environment. Do not point
Foundry at an unrestricted full API spec; curate the spec down to the
read/write surfaces above.

## Auth and safety model

- Read tools require Waypoint reader authority.
- Write tools require Waypoint writer authority and should be bound only to
  explicitly approved write-capable agents. In the current proof path that is
  `waypoint-recorder`; after WaypointIQ is proven, Assurance Orchestrator can also write through this
  governed surface.
- Admin tools are not exposed to agent runtime surfaces unless a specific human
  admin workflow is approved.
- Tool results must carry Waypoint correlation IDs where available:
  `waypoint_run_id`, `waypoint_case_id`, `waypoint_action_id`,
  `waypoint_invoice_id`.
- Write tools must report `side_effects_performed` and structured correlation
  data; they must not return success-shaped results after failed writes.

## Foundry toolbox shape

Forge can register WaypointIQ as one OpenAPI tool in an actual Foundry toolbox
resource named `waypoint-iq`. The generated OpenAPI document pins the Waypoint
server URL at export time, so local and hosted environments pass the endpoint
into the export step rather than changing the toolbox name.

The live toolbox is project-scoped, versioned, and exposed as an MCP endpoint:

```text
{project_endpoint}/toolboxes/waypoint-iq/mcp?api-version=v1
```

Hosted agents should consume that endpoint through `TOOLBOX_ENDPOINT`. The
repo-local `iqs/waypoint-iq/toolbox.yaml` is only source/projection data for
creating that platform resource through Foundry Toolkit, Foundry Portal, or
`azd` toolbox resources. A cloud toolbox cannot call a developer machine's
`localhost`, so the local export is for confidence only.

Prompt-agent caveat: a toolbox endpoint is MCP-compatible, but a live smoke with
a disposable prompt agent showed prompt-agent MCP binding does not currently
authenticate to a Foundry toolbox endpoint. Direct toolbox MCP calls with an
Azure token worked, but prompt-agent `MCPTool` binding failed with 401 without a
connection and with `ARA OBO token request failed` when routed through
`RemoteTool` connections. Until that platform path is proven, bind prompt agents
to supported tools directly and use toolbox endpoints for hosted agents.

Use one of these auth modes:

- `managed_identity` with `security_scheme.audience:
  api://<waypoint-api-client-id>` when the Foundry project managed identity or
  agent identity is granted Waypoint app roles.
- `connection` with a project connection when the environment intentionally
  uses a scoped API key or another static credential.

### Managed identity enablement for Waypoint

Waypoint managed-identity auth is an Entra app-role assignment problem. Do not
mint or store a secret for this path.

1. Confirm the Waypoint API app registration exposes app roles for the runtime
   authorities Forge needs:
   - `Waypoint.Read`
   - `Waypoint.Write`
   - `Waypoint.Admin`
2. Resolve the managed identity that will call Waypoint:
   - For a Foundry toolbox OpenAPI tool with `auth.type: managed_identity`, this
     should be the Foundry project managed identity used by the toolbox.
   - For a hosted agent calling Waypoint directly, this is the hosted agent or
     project identity used by that runtime.
3. Assign the Waypoint app role to that service principal with Microsoft Graph
   app-role assignment:
   - `Waypoint.Read` for read-only Assurance Orchestrator/Assurance Analyst usage.
   - `Waypoint.Write` only for explicitly approved write-capable surfaces.
   - `Waypoint.Admin` only for human/admin automation, not default agent
     runtime bindings.
4. Configure the toolbox OpenAPI auth with the Waypoint API audience, normally
   the API app ID URI:

   ```yaml
   auth:
     type: managed_identity
     security_scheme:
       audience: api://<waypoint-api-client-id>
   ```

5. Smoke the deployed toolbox MCP endpoint before wiring agents:
   `initialize`, `tools/list`, `tool_search`, then a safe read-only
   `call_tool` such as `waypointiq_get_work`.

If Waypoint requires delegated user context for a workflow, managed identity is
not enough. Use the delegated-token bridge path instead and document the exact
operation that needs user authority.

2026-06-27 live smoke against hosted Waypoint proved the route:

- Online Waypoint API:
  `https://api.grayflower-2758f17b.swedencentral.azurecontainerapps.io`
- Waypoint API app registration: `waypoint`
  (`<waypoint-api-client-id>`)
- Granted and left in place:
  - `Waypoint.Read` for Forge project MI
    `ai-account-wi2egf4sh4hfq/projects/ai-project-forge`
    (`<foundry-project-resource-id>`)
  - `Waypoint.Read` for Forge account MI `ai-account-wi2egf4sh4hfq`
    (`<foundry-account-resource-id>`)

> Note (2026 refresh): the Forge Foundry `ai-project-forge` project managed
> identity object id above (`85bf76be-…`) is stale — the live project MI is now
> **`<foundry-project-managed-identity-object-id>`** (parent account MI
> `<foundry-account-managed-identity-object-id>`). Use the live project MI as the
> principal for new grants and Foundry project connections (e.g. the
> `waypoint-data-agent-connection` MicrosoftFabric connection used by
> operations-data-expert).
- Updated hosted Waypoint `APP_MSAL_ALLOWED_APP_IDS` to include both Forge MI
  app IDs.
- Temporary toolbox `waypoint-iq-mi-smoke-20260627` used an OpenAPI tool with
  `managed_identity` auth and successfully called
  `waypoint_iq___waypointiq_get_work` through Tool Search / `call_tool`.
- The temporary toolbox was deleted after the smoke; the Waypoint app-role and
  allow-list configuration were intentionally left in place.
- Durable toolbox `waypoint-iq` version 1 was then created in the Forge Foundry
  project using `iqs/waypoint-iq/scripts/deploy_toolbox.py`. Re-running the
  script reported it already up to date, and the durable MCP endpoint passed
  `initialize`, `tools/list`, `tool_search`, and `call_tool` for
  `waypoint_iq___waypointiq_get_work` against hosted Waypoint.

Example shape:

```python
from azure.ai.projects.models import OpenApiTool

TOOLBOX = [
    OpenApiTool(
        name="waypoint_iq",
        spec=WAYPOINTIQ_READ_OPENAPI_SPEC,
        auth={
            "type": "managed_identity",
            "security_scheme": {
                "audience": "api://<waypoint-api-client-id>",
            },
        },
    ),
]
```

Exact constructor names may vary across the `azure-ai-projects` preview SDK, so
verify the current SDK shape before wiring automation. The durable contract is:
Foundry toolbox entry type `openapi`, curated OpenAPI spec, and explicit auth
metadata.

## Hosted-agent WaypointIQ auth: the durable path (2026-07-02)

This section is the operational source of truth for how Forge agents reach hosted
Waypoint today. It applies to `assurance-analyst`, `assurance-orchestrator`, and
`waypoint-recorder`. Read it before wiring or debugging any agent's Waypoint auth.

### The Waypoint app-only contract (what every caller must satisfy)

Hosted Waypoint validates app-only (agent) callers against **both**:

1. A Waypoint app role on the caller's identity (`Waypoint.Read` and/or
   `Waypoint.Write`), and
2. Membership in Waypoint's `APP_MSAL_ALLOWED_APP_IDS` allow-list.

`APP_API_KEY_AUTH_ENABLED=false` on deployed Waypoint, so **API-key (`x-api-key`)
auth is rejected** — every app-only caller must present an Entra **bearer token**
for an identity that is both allow-listed and role-granted. There are two
supported ways to produce that bearer, depending on how the agent calls Waypoint.

### Pattern A — server-side toolbox (managed_identity via the Foundry project MI)

Use this when an agent consumes Waypoint **reads through a Foundry toolbox**
(the `assurance-analyst` model). The Foundry ToolServer executes the OpenAPI tool
server-side and authenticates as the **Foundry project managed identity**.

- Toolbox OpenAPI auth: `{ "type": "managed_identity", "security_scheme": {
  "audience": "api://<waypoint-api-client-id>" } }`.
- Identity used: the project MI
  `<foundry-project-resource-id>` (object id
  `<foundry-project-managed-identity-object-id>`, display name
  `ai-account-wi2egf4sh4hfq/projects/ai-project-forge`).
- Why this over agentic-identity: the project MI is **stable across agent
  redeploys and per-hire (Teams) identities**, so token resolution is
  deterministic. An earlier `AgenticIdentityToken` connection
  (`waypoint-iq-agentic`) was tried, but agentic-token *minting* depends on
  per-call agent instance/blueprint context that regressed platform-side
  (`requires AgentInstanceClientId and AgentBlueprintClientId, or an
  ApplicationName`). Managed_identity avoids that entirely. The agentic path is
  retained only as a documented fallback
  (`scripts/ensure_waypoint_iq_agentic.py`) for environments that cannot
  allow-list the project MI.
- **Why toolbox-side, not an in-container MCP client:** reaching a Waypoint
  toolbox via a *client-side* `MCPStreamableHTTPTool` inside the hosted-agent
  container crashes on Foundry deactivation (anyio "exit cancel scope in a
  different task") → corrupted tool result → `"Function failed"`. Server-side
  toolbox tools (executed by the ToolServer) do not have this problem. The
  analyst therefore folds a **read-only (GET-only)** projection of `waypoint-iq`
  into its own `assurance-analyst-tools` toolbox and keeps only lightweight
  client bridges (e.g. the FoundryIQ knowledge-base MCP tool).

Idempotent scripts (run via `make`, and wired into `.github/workflows/deploy.yml`
for `assurance-analyst`):

```bash
# 1. Flip the durable waypoint-iq toolbox OpenAPI auth to managed_identity.
make waypoint-iq-auth            # scripts/ensure_waypoint_iq_managed_identity.py

# 2. Merge a read-only projection of waypoint-iq into assurance-analyst-tools
#    (also sets managed_identity explicitly, so the analyst path can't drift).
make analyst-waypoint-tool       # scripts/ensure_analyst_waypoint_tool.py
```

### Pattern B — in-container Python client (bearer via the agent identity)

Use this when an agent calls Waypoint directly from Python inside its container
(the `assurance-orchestrator` and `waypoint-recorder` model). The container calls
Waypoint as its **own hosted-agent AgentIdentity** via `DefaultAzureCredential`
against `WAYPOINT_API_SCOPE` (`api://<waypoint-api-client-id>/.default`).

- Orchestrator AgentIdentity `632d4b3d-...` is allow-listed and holds
  `Waypoint.Read`.
- Recorder AgentIdentity `ca9c0407-...` is allow-listed and holds `Waypoint.Write`.
- Contract-policy-expert (FoundryIQ) AgentIdentity `a4fa1197-...` is allow-listed
  and holds `Waypoint.Read`. It calls Waypoint via its `gather_foundry_evidence`
  fallback tool to ground contract/policy evidence; without the role + allow-list
  entry the fallback 401s and FoundryIQ reports "no contract found".

> **Pitfall — API key shadows bearer.** The shared `waypoint_client.py` must
> **prefer the bearer token whenever `WAYPOINT_API_SCOPE` is set**, and only send
> `x-api-key` when no scope is configured. If an agent still ships a
> `WAYPOINT_API_KEY` (e.g. `agent.yaml` binding `${WAYPOINT_READER_API_KEY}` /
> writer key) and the client sends `x-api-key` unconditionally, calls **401**
> against a Waypoint with key-auth disabled. Durable fix: prefer-bearer
> precedence in the client **and** remove the `${WAYPOINT_*_API_KEY}` bindings
> from the agent `agent.yaml` so a future deploy cannot reintroduce the shadow.

### The allow-list is durable — persisted in Waypoint IaC, not just live

Adding an identity to Waypoint's live container-app `APP_MSAL_ALLOWED_APP_IDS`
env var is **not** durable: a Waypoint redeploy rebuilds the env from config and
drops it (this drop of the project MI was the original root cause of the analyst
401s). The durable source of truth is the GitHub Actions **repo variable**
`WAYPOINT_MSAL_ALLOWED_APP_IDS` on `caldova/waypoint`, consumed by that repo's
`deploy.yml` as `Waypoint__Msal__AllowedAppIds → APP_MSAL_ALLOWED_APP_IDS`.

When you allow-list a new agent/project identity, update **both** the live env
(to unblock now) and the repo variable (so it survives redeploys). Current
allow-list membership:

| App id | Identity | Roles |
| --- | --- | --- |
| `85bf76be` | Forge project MI (toolbox managed_identity) | `Waypoint.Read` |
| `7f034f22` | assurance-analyst AgentInstance | `Waypoint.Read` |
| `632d4b3d` | assurance-orchestrator AgentIdentity | `Waypoint.Read` |
| `ca9c0407` | waypoint-recorder AgentIdentity | `Waypoint.Write` |
| `a4fa1197` | contract-policy-expert (FoundryIQ) AgentIdentity | `Waypoint.Read` |
| `437106c2` | (pre-existing operational id) | — |

### Applying this to the orchestrator

`assurance-orchestrator` uses **Pattern B** (in-container `waypoint_client.py`),
not the toolbox. It does **not** need the managed_identity toolbox change. To make
orchestrator runs stop 401ing it needs, on its own branch:

1. Clear `WAYPOINT_API_KEY` in the deployed env (and prefer-bearer precedence in
   `waypoint_client.py`) so it authenticates as AgentIdentity `632d4b3d`.
2. Keep `WAYPOINT_API_SCOPE=${WAYPOINT_API_SCOPE}/.default`.
3. Remove the `${WAYPOINT_*_API_KEY}` bindings from `agent.yaml` for durability.

Its identity `632d4b3d` (and the recorder's `ca9c0407`) are already allow-listed
and role-granted, so no Waypoint-side change is required for the orchestrator once
the client sends a bearer.

## Waypoint operations expert target

The Waypoint operations expert is planned, not current. It should become a
Foundry prompt agent once the read toolbox is stable. Its source should live
under `agents/waypoint-operations-expert/prompt.md` with Prompty-compatible
frontmatter and Forge deployment metadata, matching the existing expert pattern.
Its toolbox should bind to curated WaypointIQ tools rather than direct Python
Waypoint clients.

Assurance Orchestrator should consult the Waypoint operations expert for operational interpretation,
action selection, and write-plan review. Assurance Orchestrator should still own deterministic
workflow gates, and the summarization/adjudication step should only compress or
interpret already-collected evidence instead of discovering new facts.

## Implementation target

The durable implementation should live with Caldova Waypoint because it owns the
API schema, auth policy, and app registration. Forge should consume WaypointIQ as
OpenAPI toolbox entries instead of duplicating Waypoint client code in every
agent.

The initial contract lives under `iqs/waypoint-iq/`:

- `openapi.json` - curated OpenAPI contract exported from a docs-enabled
  Waypoint endpoint.
- `toolbox.yaml` - draft source payload for the real `waypoint-iq` Foundry toolbox.
- `scripts/export_openapi.py` - endpoint-parameterized exporter.
- `scripts/deploy_toolbox.py` - idempotent Foundry toolbox create/update script.
- `tests/smoke_local.py` - local read-path smoke against localhost Waypoint.

Live Forge status: `waypoint-iq` version 3 exists and targets
`https://api.grayflower-2758f17b.swedencentral.azurecontainerapps.io`, with its
OpenAPI tool on `managed_identity` auth (Forge project MI).

`assurance-analyst` is deployed as a hosted demo agent. Its single client-side
bridge points at its own `assurance-analyst-tools` toolbox (version 4 =
FoundryIQ `kb-mcp-connection` knowledge-base tool + a **read-only** GET-only
projection of `waypoint-iq`, both executed server-side). It no longer runs a
client-side Waypoint MCP bridge (see "Hosted-agent WaypointIQ auth" above).

Recommended rollout:

1. Export and curate the `waypoint-iq` OpenAPI spec from the Waypoint project,
   then smoke it locally.
2. Bind `assurance-orchestrator` and `assurance-analyst` to read operations in `waypoint-iq`.
3. Prove write operations through WaypointIQ locally before allowing Assurance Orchestrator or
   Waypoint Recorder to execute them.
4. Introduce `waypoint-operations-expert` as a prompt agent bound to `waypoint-iq` read operations for
   operational reasoning and write-plan review.
5. Bind `waypoint-recorder` to WaypointIQ write operations and keep direct Python write tools as
   rollback until a confidence review.
6. Only then consider prompt-agent versions of the coordinator/write surfaces.
