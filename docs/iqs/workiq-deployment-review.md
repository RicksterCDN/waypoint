# WorkIQ deployment review

Date: 2026-07-24

## TL;DR

WorkIQ should be treated as an **optional evidence lane** in the consolidated
Caldova/Waypoint demo, not as a default launch dependency. It adds a valuable
signal that FoundryIQ and FabricIQ cannot provide: user-permissioned workplace
communications from Microsoft 365. But the current implementation is not yet a
gold-standard, one-click, idempotent, repeatable, security-backed deployment.

The gold-standard path is:

1. **Non-duplicative source:** WorkIQ reads Microsoft 365 workplace evidence
   only: supplier emails, Teams threads, meeting follow-ups, approvals,
   escalations, and optional SharePoint support files. It should not duplicate
   Waypoint operational rows, Ledgerfield seed data, or scenario expected
   outcomes.
2. **Repo-owned optional lane:** this repo's root deployment should expose a
   guarded WorkIQ option that deploys `collaboration-evidence-expert`, enables
   `assurance-orchestrator` WorkIQ fan-out, provisions or reconciles the Foundry
   WorkIQ toolbox/connections, and runs WorkIQ-specific acceptance. The base demo
   must still deploy and pass when WorkIQ is disabled.
3. **Real user-delegated security:** WorkIQ calls must run on behalf of a signed
   in Microsoft 365 user. App-only authentication is not supported for WorkIQ
   data access, and CI service principals cannot prove this lane by themselves.
4. **Read-only enforcement:** the evidence expert is supposed to gather evidence
   only. The current Teams and SharePoint WorkIQ MCP servers expose write-capable
   operations, so a production-quality implementation needs a tool allow-list,
   read-only proxy, or platform policy that prevents create/update/delete/send
   actions. Prompt instructions alone are not enough.
5. **Fail-closed validation:** a successful WorkIQ gate must prove user-context
   propagation, consent, source citations, no writes, no raw restricted content,
   and a terminal Waypoint run. Missing consent, missing user context, empty
   evidence in a seeded fixture, or write-capable tools should fail the WorkIQ
   gate without blocking the base FoundryIQ demo when WorkIQ is off.

Product gaps make this harder than FabricIQ in one specific way: WorkIQ is
inherently user-scoped. That is correct for Microsoft 365 security, but it means
the lane cannot be validated by the same headless app-only deployment path that
validates FoundryIQ. New tenants need Work IQ enablement, usage-based billing,
Microsoft 365 Copilot licenses for test users, admin consent, approved MCP
servers, and a real signed-in user or agent user flow before this can be called
"one click."

## Scope

This review focuses on the current `caldova/waypoint` repository as the future
gold-standard deployment surface for the whole Caldova/Waypoint demo. Keystone
and prior Forge-only behavior are historical context only. WorkIQ should be added
to the consolidated demo only if it can meet the same standards as the default
FoundryIQ path: one click after documented tenant bootstrap, idempotent reruns,
least-privilege security, repeatable validation, and clear failure modes.

## Executive readout

The repo already contains a credible WorkIQ evidence expert:
`modules/agents/agents/collaboration-evidence-expert`. Its prompt is correctly
scoped to workplace communication evidence; it is read-only by intent; it returns
a shared IQ evidence contract; and its runtime uses a Foundry toolbox backed by
`UserEntraToken` connections so WorkIQ calls can bind to the invoking user's
authority.

That is the right design direction, but it is not yet a deployable product lane
for the consolidated demo. The root `Deploy Azure` workflow explicitly filters
the launch fleet to `invoice-analyst`, `assurance-orchestrator`,
`contract-policy-expert`, and `waypoint-recorder`; it sets
`ENABLE_WORKIQ_CONNECTIONS=false` and `ASSURANCE_ORCHESTRATOR_WORKIQ_ENABLED=false`.
The README and deployment docs correctly describe the launch package as
FoundryIQ-only.

The biggest blocker is not agent code. It is the end-to-end identity path. WorkIQ
requires a signed-in Microsoft 365 user context, while the root acceptance run
and orchestrator fan-out currently run through Azure/Foundry tokens from the
deployment identity. The `collaboration-evidence-expert` can forward Foundry
request context to its toolbox, but the current orchestrator client invokes
downstream experts with its own `DefaultAzureCredential` token and does not prove
Microsoft 365 user-context propagation from the Waypoint app through the
orchestrator into WorkIQ.

## Current repo posture

| Area | Current state | Assessment |
| --- | --- | --- |
| Root deployment | FoundryIQ-only launch fleet; WorkIQ disabled in root workflow controls | Correct default until the lane is security- and validation-ready. |
| WorkIQ agent | `collaboration-evidence-expert` hosted agent exists under `modules/agents` | Good base asset, but not in the root launch matrix. |
| Tooling | `collaboration-evidence-tools` toolbox binds `WorkIQCopilot`, `WorkIQTeams`, and `WorkIQSharePoint` | Good central toolbox pattern, but tool set is broader than read-only evidence needs. |
| Auth model | Runtime forwards Foundry request context to `UserEntraToken` WorkIQ connections | Correct principle; not yet proven through the full Waypoint app -> orchestrator -> expert path. |
| Orchestrator fan-out | Supports `ASSURANCE_ORCHESTRATOR_WORKIQ_ENABLED`; default is false | Good feature flag, but no root opt-in lane or acceptance gate yet. |
| CI smoke | Agent deployment smoke checks Foundry control-plane health, not WorkIQ evidence retrieval | Necessary but insufficient for WorkIQ because M365 user context is not present. |
| Evals | Minimal golden checks for purpose, boundary, no-write behavior, and no-evidence handling | Useful guardrails; not enough to prove live WorkIQ retrieval quality or safety. |
| M365 publishing | Generic `publish.yaml` exists; publish/listing/admin-consent paths require user-delegated actions | Not one-click today and not specific enough for this agent. |

## What WorkIQ should source

WorkIQ's unique value is workplace collaboration evidence that is not already in
Waypoint or the synthetic corpus:

- email threads with supplier commitments, dispute notices, credits, delivery
  changes, invoice attachments, or finance/procurement approvals;
- Teams chats or channel messages about quality holds, shortages, batch
  exceptions, escalation decisions, or invoice handling;
- meeting follow-ups, action items, and summaries when available through the
  user's Microsoft 365 context;
- SharePoint documents only as supporting evidence, such as supplier QBR notes,
  exception trackers, or approved remediation plans.

The evidence should be cited with stable Microsoft 365 locators: message IDs,
thread IDs, Teams links, channel/message IDs, file URLs, or document IDs. The
agent should return `authority=user_delegated` whenever evidence came from the
signed-in user's WorkIQ authority.

## Duplication analysis

WorkIQ is not duplicative when it reads real Microsoft 365 communications. It
answers a different question than the other IQ planes:

| Plane | Canonical signal | WorkIQ overlap risk |
| --- | --- | --- |
| FoundryIQ | Contract and policy grounding from `contracts-kb` | WorkIQ should not restate contract clauses unless a communication cites them. |
| FabricIQ | Operational rows mirrored from Waypoint Postgres | WorkIQ should not mirror invoice, supplier, line, or reconciliation tables. |
| WebIQ | Public external market/context evidence | WorkIQ should not replace public-web corroboration. |
| WorkIQ | User-scoped M365 communications and files | Unique if seeded/queried as workplace context rather than scenario answers. |

The demo risk is synthetic data leakage. If we seed test emails or Teams content,
those messages must look like normal workplace communications and must not embed
the expected final decision, evaluator labels, or scenario answer key. Otherwise
WorkIQ becomes another copy of the puzzle solution rather than an independent IQ
plane.

## Best practices already present

- The agent prompt is narrow: gather workplace evidence only; do not reconcile,
  decide, or write to Waypoint.
- The shared evidence contract includes `source_ref`, `authority`, `channel`,
  `classification`, and `confidence`, which are the right fields for audit.
- The runtime uses a central Foundry toolbox endpoint instead of hardcoding each
  MCP call inside agent logic.
- `UserEntraToken` is the right direction for Microsoft 365 access because every
  request must honor the signed-in user's permissions.
- The toolbox labels are intentionally stable (`WorkIQCopilot`, `WorkIQTeams`,
  `WorkIQSharePoint`) so redeploys do not accidentally drop tools.
- The agent disables WorkIQ tools when no toolbox endpoint is configured instead
  of pretending evidence was gathered.
- The orchestrator has explicit WorkIQ enablement flags, so this lane can remain
  off by default while being developed.

## Hacky or fragile areas today

1. **No proven user-context handoff.** The collaboration expert forwards current
   Foundry request context to its toolbox, but the full assurance path currently
   invokes experts through the orchestrator's Foundry token. A WorkIQ-enabled E2E
   run must prove the original signed-in user reaches WorkIQ.
2. **Control-plane smoke can pass while WorkIQ is unusable.** Current smoke checks
   agent activity and image status, not a live M365 evidence query.
3. **Preview server URLs and tool names are hardcoded.** Microsoft preview docs
   warn that MCP tool names and parameters can change. Current config still
   references fixed WorkIQ Copilot/Teams/SharePoint server IDs and historical
   non-tenant URLs.
4. **Write-capable tools are exposed to a read-only agent.** Teams and SharePoint
   MCP references include create, update, delete, send, sharing, and permission
   operations. The prompt says not to write, but the toolbox currently sets
   `require_approval="never"` for those servers.
5. **SharePoint endpoint history already caused failures.** The repo includes a
   guard test because an old placeholder SharePoint URL returned HTTP 400 and
   crashed MCP setup. That is evidence of preview endpoint drift.
6. **Publish metadata is generic.** The WorkIQ agent's `publish.yaml` still has
   placeholder descriptions, developer URLs, and terms/privacy URLs.
7. **Manual M365 listing/admin steps remain outside CI.** The repo comments
   correctly state that Microsoft 365/Teams listing and OAuth admin-consent grants
   require user-delegated tokens and cannot be completed by the CI service
   principal.

## Product gaps and constraints

| Gap or constraint | Impact on a gold-standard Waypoint deploy |
| --- | --- |
| WorkIQ MCP is preview | Microsoft says preview features are not meant for production use and can have restricted functionality or changing tool names/parameters. |
| App-only WorkIQ auth is not supported | Headless CI cannot prove WorkIQ evidence with only the GitHub OIDC deployment identity. |
| OBO / Agent 365 permission setup requires admin consent | New tenants need a Global Administrator or approved admin workflow before users can authenticate. |
| Usage-based billing must be enabled | WorkIQ API access depends on Copilot Credits / usage-based billing setup and spending policies in Microsoft 365 admin center. |
| Microsoft 365 Copilot license requirement | Users invoking WorkIQ MCP servers need appropriate Microsoft 365 Copilot licensing; test users must be licensed and provisioned. |
| VNet integration is not supported for Foundry WorkIQ | A locked-down Foundry project with VNet-restricted endpoints is incompatible with the documented WorkIQ path. |
| WorkIQ connection fields are immutable after creation | Incorrect Foundry connection values require delete/recreate logic, not simple update-in-place. |
| Admin center MCP allow/block may vary by region | Tenant governance controls may not be uniformly available, complicating preflight and supportability. |
| M365 data readiness is asynchronous | License assignment, WorkIQ index readiness, mailbox/OneDrive provisioning, and RBAC can take minutes to hours. |
| WorkIQ tools include side-effecting operations | Product/tooling needs finer-grained read-only selection or the repo needs a safe intermediary. |
| Data boundary differs from Azure-only services | Foundry docs warn that WorkIQ may move processing outside the Azure compliance boundary; residency follows Microsoft 365 tenant policy rather than the Foundry region. |

## What would be required for a correct implementation

### 1. Deployment model

- Add root workflow inputs such as `enable_workiq` and keep the default false.
- Extend the deployment plan/manifest so WorkIQ changes affect only the WorkIQ
  lane and do not redeploy unrelated launch agents.
- Add `collaboration-evidence-expert` to the selected agent matrix only when
  WorkIQ is enabled.
- Set `ENABLE_WORKIQ_CONNECTIONS=true`,
  `ASSURANCE_ORCHESTRATOR_WORKIQ_ENABLED=true`, and the WorkIQ expert endpoint
  only in that opt-in lane.
- Ensure unchanged reruns reuse the same WorkIQ connections, toolbox name,
  app registration references, and acceptance fixture rather than creating
  duplicates.

### 2. Tenant and admin preflight

Add a preflight that fails with clear remediation when any prerequisite is
missing:

- Work IQ service principal exists in the tenant.
- Usage-based billing / Copilot Credits is active and scoped to the intended
  test users or group.
- Test users have Microsoft 365 Copilot licenses and mailbox/Teams/SharePoint
  resources are provisioned.
- Required WorkIQ MCP servers are allowed in Microsoft 365 admin center where the
  control is available.
- The selected WorkIQ path has its identity prerequisites: `WorkIQAgent.Ask` for
  the Foundry WorkIQ A2A path, or the required Agent 365 MCP permissions for the
  catalog-server path used by this repo.
- Admin consent has been granted for the app or Agent 365 permission set that
  will call WorkIQ.
- Foundry developer, agent runtime identity, and user identities have the needed
  Foundry project roles.
- The target Foundry project is not VNet-restricted for the WorkIQ path.

### 3. Security model

- Preserve OBO/user-delegated access. Do not try to bypass WorkIQ with app-only
  Graph permissions for the default evidence path.
- Scope WorkIQ access to a demo/test security group rather than the whole tenant.
- Replace prompt-only read-only controls with enforceable controls:
  - prefer a read-only WorkIQ/Copilot search surface if it satisfies evidence
    needs;
  - otherwise create a repo-owned read-only MCP proxy or policy layer that exposes
    only list/search/read operations;
  - never expose Teams post/delete/update or SharePoint create/delete/share
    operations to `collaboration-evidence-expert` with `require_approval="never"`.
- Require evidence redaction rules for restricted or IP-sensitive content: source
  refs are allowed; raw content in summaries is not.
- Record tool invocation metadata for audit without storing sensitive M365
  message bodies in Waypoint.

### 4. User-context propagation

The full E2E path must choose and prove one user-context model:

- **Interactive reviewer path:** a signed-in Waypoint reviewer triggers assurance;
  the app/API obtains an OBO-compatible user token or request context; the
  orchestrator preserves it when invoking `collaboration-evidence-expert`; the
  expert forwards it to the WorkIQ toolbox.
- **Agent user / AI Teammate path:** an Agent 365 agent user is licensed and
  granted explicit access to seeded demo content; the agent user invokes WorkIQ
  with its own identity and audit trail.
- **Direct WorkIQ evidence path:** the reviewer directly invokes the WorkIQ
  expert from an M365/Foundry surface and the orchestrator consumes the cited
  evidence artifact. This is less seamless, but easier to make honest than
  pretending a headless CI run is user delegated.

Until one of those paths is implemented, WorkIQ should remain excluded from
default assurance acceptance.

### 5. Evidence fixtures

For a repeatable demo, WorkIQ needs deterministic, tenant-local workplace
fixtures:

- a test supplier thread in email;
- a Teams chat or channel thread with invoice/batch/PO references;
- optional SharePoint support files below WorkIQ file-size and permission limits;
- documented users/groups that can access the content;
- idempotent creation and cleanup, or a preflight that verifies the fixture exists
  without mutating production tenant content.

This seeding must avoid duplicating scenario answer keys. The fixture can contain
normal business evidence, but final recovery/approval labels remain in the
scenario metadata and recorder judgement, not in M365 messages.

### 6. Acceptance gates

A WorkIQ-enabled successful E2E run should prove:

- root deployment selected WorkIQ intentionally;
- WorkIQ connections/toolbox exist with expected stable names and no duplicate
  drift;
- the agent can list only approved/read-safe tools;
- a seeded Microsoft 365 user-context query returns at least one relevant cited
  WorkIQ evidence item;
- each WorkIQ claim has `source_ref`, `channel`, `authority=user_delegated`, and
  no restricted raw content;
- no write tool was invoked;
- the orchestrator includes WorkIQ as a validator when enabled;
- the recorder finalizes the run through the normal sole-writer path;
- an unchanged rerun reuses resources and does not duplicate evidence fixtures,
  app registrations, bot listings, or toolbox versions beyond intentional version
  promotion.

## Cost and licensing posture

WorkIQ is not a Fabric capacity problem. There is no F2/F64-style capacity SKU to
size for this lane. The cost model is Microsoft 365 licensing plus usage-based
Copilot Credits.

Minimum cost controls for a demo-grade implementation:

- license only the demo/test users that need WorkIQ;
- scope WorkIQ usage-based billing policies to a security group;
- set monthly policy and per-user limits in Microsoft 365 admin center;
- set alerts and hard caps before enabling broad use;
- capture WorkIQ tool usage by user/agent/service so high-cost prompts or loops
  can be identified;
- keep WorkIQ disabled by default in the root demo until cost policies are known
  to be active.

Scaling to many sellers should not imply one WorkIQ user or one agent per seller.
The production topology should be based on reviewer/user groups and the M365
data they are allowed to see. If a seller boundary requires strict tenant or data
isolation, that is an M365/identity architecture decision, not a Waypoint agent
scaling knob.

## New-tenant manual setup blockers

These steps are currently manual or admin-mediated enough to block a true
"one-click from a fresh tenant" WorkIQ lane:

1. Enable usage-based billing / Copilot Credits for WorkIQ API in Microsoft 365
   admin center.
2. Create the Work IQ service principal in the tenant.
3. Assign Microsoft 365 Copilot licenses to the test users and wait for
   provisioning/index readiness.
4. Create or approve the Entra app used for OBO WorkIQ access.
5. Grant tenant-wide admin consent for `WorkIQAgent.Ask`.
6. Configure WorkIQ Foundry connection redirect URIs after the connection is
   created.
7. Allow/approve the required WorkIQ MCP servers in Microsoft 365 admin center
   where those controls are available.
8. Approve or publish any Microsoft 365/Teams agent surface if using an AI
   Teammate or app-store flow.
9. Seed or verify demo M365 evidence under the right users/groups.

Each of these needs either automation with explicit admin confirmation or a
preflight that marks WorkIQ unavailable without failing the base demo.

## Recommended implementation sequence

1. Keep WorkIQ disabled by default in the root deployment.
2. Add WorkIQ preflight only, with no mutation, and report exactly which tenant
   prerequisites are missing.
3. Add a read-only WorkIQ tool surface or proxy; block write-capable tools before
   any E2E run.
4. Add deterministic M365 fixture verification for one test user.
5. Add direct `collaboration-evidence-expert` live validation using that user
   context.
6. Add orchestrator user-context propagation and prove WorkIQ survives fan-out.
7. Add root opt-in deployment wiring and acceptance gates.
8. Only then consider enabling WorkIQ in presenter/demo flows.

## Recommendation

WorkIQ is strategically valuable, but it should remain **off by default** until
the repo can prove the identity, read-only, cost, and acceptance path. The right
target is not "deploy every IQ plane at any cost." The right target is a
gold-standard Caldova/Waypoint demo where each optional IQ lane earns inclusion
by being non-duplicative, least-privilege, idempotent, and validated.

For WorkIQ, that means solving user-context propagation and read-only tool
enforcement before calling the lane production-ready.

## References checked

- [Work IQ overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/)
- [Enable your tenant for Work IQ](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/enable-work-iq)
- [Connect agents to Microsoft 365 with Work IQ - Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/work-iq)
- [Work IQ MCP overview - Agent 365](https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview)
- [Work IQ MCP overview - Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/use-work-iq)
- [Work IQ Copilot reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-copilot-work-iq)
- [Work IQ Teams reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-teams-work-iq)
- [Work IQ SharePoint reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-sharepoint-work-iq)
- [Agent 365 Identity](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/identity)
- [Create, test, and deploy a toolbox in Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox)
- [Manage agents for Microsoft 365 Copilot](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/manage)
- [Agents admin guide for Microsoft 365](https://learn.microsoft.com/en-us/microsoft-365/copilot/agent-essentials/m365-agents-admin-guide)
- [Microsoft 365 Copilot connectors overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/overview-copilot-connector)
- [Usage-based billing and cost management for Copilot Credits](https://learn.microsoft.com/en-us/microsoft-365/copilot/usage-based-billing-overview-copilot-credits)
- [Managing AI experiences enabled by usage-based billing](https://learn.microsoft.com/en-us/microsoft-365/copilot/usage-based-billing-manage-copilot-credits)
- [Microsoft 365 Copilot licensing](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-licensing)
