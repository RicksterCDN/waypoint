# WebIQ deployment review

Date: 2026-07-24

## TL;DR

WebIQ should be treated as an **optional evidence lane** in the consolidated
Caldova/Waypoint demo, not as a default launch dependency. It adds a signal that
FoundryIQ, FabricIQ, and WorkIQ do not provide: current public-web corroboration
about supplier financial health, market rates, regulatory notices, shortages,
recalls, litigation, and other external conditions that can explain or challenge
an invoice exception.

The gold-standard path is:

1. **Non-duplicative source:** WebIQ reads public external sources only. It
   should not search for private invoice IDs, duplicate Waypoint operational
   rows, restate Ledgerfield expected findings, quote contract clauses as if they
   were web evidence, or mine Microsoft 365 workplace communications.
2. **Pick one tool contract:** the repo currently contains two WebIQ stories.
   `market-evidence-expert` uses Foundry's native `web_search` tool, while some
   infra comments and historical docs describe a direct Microsoft Web IQ MCP
   connection named `web-iq`. A production-quality lane needs one explicit
   contract. For near-term one-click deployment, **Foundry Web Search is the
   cleaner default** because it is GA, server-side, and does not require a
   tenant-specific Web IQ API key. Direct Microsoft Web IQ MCP should be a
   separate opt-in once entitlement, API-key storage, and connection
   reconciliation are automated.
3. **Repo-owned optional lane:** this repo's root deployment should expose a
   guarded `enable_webiq` option that deploys `market-evidence-expert`, enables
   `assurance-orchestrator` WebIQ fan-out, validates the selected web-grounding
   tool, and runs WebIQ-specific acceptance. The base FoundryIQ demo must still
   deploy and pass when WebIQ is disabled.
4. **Security-backed search policy:** WebIQ prompts must be sanitized before they
   leave the Azure compliance boundary, no secrets or sensitive personal data
   should be sent to web grounding, and source domains/results must be treated as
   untrusted input.
5. **Fail-closed validation:** a successful WebIQ gate must prove tool invocation
   and citation-bearing evidence for a known-positive fixture. Empty evidence,
   uncited claims, public-web searches for private invoice IDs, disabled web
   grounding, missing MCP credentials, or fallback-only answers should fail the
   WebIQ gate without blocking the base demo when WebIQ is off.

WebIQ is less infrastructure-heavy than FabricIQ and less tenant-identity-heavy
than WorkIQ, but it has its own productionization gap: public web grounding is
not deterministic, has extra cost and data-boundary terms, can be disabled by an
administrator, and may not produce useful evidence for fictional suppliers unless
the demo has a controlled known-positive external context. The current repo has
good agent code and prompt shape, but it is not yet a one-click, idempotent,
repeatable, security-backed WebIQ implementation.

## Scope

This review focuses on the current `caldova/waypoint` repository as the future
gold-standard deployment surface for the whole Caldova/Waypoint demo. Prior
Forge/Keystone behavior is historical context only. WebIQ should be added to the
consolidated demo only if it can meet the same standards as the default
FoundryIQ path: one click after documented tenant bootstrap, idempotent reruns,
least-privilege security, repeatable validation, and clear failure modes.

## Executive readout

The repo already contains a credible WebIQ evidence expert:
`modules/agents/agents/market-evidence-expert`. Its prompt is correctly scoped
to public market and supplier-financial evidence; it explicitly forbids searching
for private invoice IDs; it returns the shared IQ evidence contract; and the
orchestrator supplies market context from Waypoint so web searches can use
supplier names, spend categories, line items, and amounts instead of private
accounting identifiers.

That design is directionally right, but the consolidated launch path does not
deploy WebIQ today. The root deployment manifest and workflow select only
`invoice-analyst`, `assurance-orchestrator`, `contract-policy-expert`, and
`waypoint-recorder`; they set `ASSURANCE_ORCHESTRATOR_WEBIQ_ENABLED=false` and
leave `WEBIQ_EXPERT_ENDPOINT` empty. The README, architecture docs, deployment
docs, and status docs correctly describe the one-click launch package as
FoundryIQ-only.

The biggest implementation gap is not the prompt. It is the product/tooling
contract. Current `market-evidence-expert` code and `agent.yaml` use Foundry's
native web search path. Current shared infra comments say the intended WebIQ
target is instead a hosted Microsoft Web IQ MCP server at
`https://api.microsoft.ai/v3/mcp` with a secret-backed `web-iq` project
connection that is **not** provisioned by Bicep. Those are different operational
paths with different security, cost, entitlement, and idempotency requirements.
The repo should choose one explicit default before WebIQ becomes a gold-standard
optional lane.

## Current repo posture

| Area | Current state | Assessment |
| --- | --- | --- |
| Root deployment | FoundryIQ-only launch fleet; WebIQ disabled and endpoint blank | Correct default until the lane has a clear tool contract and acceptance gate. |
| WebIQ agent | `market-evidence-expert` hosted agent exists under `modules/agents` | Good base asset, but not in the root launch matrix. |
| Agent tool path | `main.py` calls `FoundryChatClient.get_web_search_tool(search_context_size=...)` | Clean GA-aligned path if the repo standardizes on Foundry Web Search. |
| Infra comments | `modules/agents/infra/main.bicep` says WebIQ should use direct Web IQ MCP connection `web-iq` and not Bing grounding | Conflicts with agent code/workflow comments and must be resolved before one-click. |
| Orchestrator fan-out | Supports `ASSURANCE_ORCHESTRATOR_WEBIQ_ENABLED`; default is false | Good feature flag, but no root opt-in lane or acceptance gate yet. |
| Market context | Orchestrator builds a WebIQ-specific context block from invoice supplier/category/line data | Strong design: avoids useless/private invoice-id searches. |
| Recorder grounding guard | Tests require citation-bearing evidence before counting a WebIQ lane as grounded | Good governance guardrail. |
| Historical smoke | Prior hosted direct smoke returned source-bearing WebIQ evidence; diagnostic fan-out completed WebIQ | Useful proof of possibility, not proof of current root one-click deployment. |
| Publishing metadata | `publish.yaml` remains template/generic | Not production-ready for any M365/Agent 365 publishing path. |

## What WebIQ should source

WebIQ's unique value is external context that a reviewer could independently
verify on the public web:

- published market-rate benchmarks for CMO/CDMO services, fill-finish work,
  surge capacity, rush manufacturing, expedited logistics, cold-chain shipping,
  and related pharmaceutical manufacturing categories;
- input-cost signals such as producer-price indexes, energy/logistics indices,
  sterile manufacturing capacity reports, bioreactor/consumables trends, or
  supply-chain disruption reports;
- supplier financial-health signals: public filings, credit/rating notes, plant
  closures or expansions, layoffs, litigation, product recalls, FDA or other
  regulator notices, import alerts, shortage notices, or force-majeure news;
- public corroboration for broad industry events that could explain or challenge
  invoice premiums, delay claims, or capacity constraints.

WebIQ should cite stable source locators: URLs, publisher names, article/report
dates, regulator identifiers, or filing references. A useful WebIQ evidence item
should say what external source was checked and whether it supports `approve`,
`recover`, `escalate`, `review`, or `unknown`.

## Duplication analysis

WebIQ is not duplicative when it reads public external context. It answers a
different question than the other IQ planes:

| Plane | Canonical signal | WebIQ overlap risk |
| --- | --- | --- |
| FoundryIQ | Contract and policy grounding from `contracts-kb` | WebIQ should not restate contract clauses unless a public source independently discusses the issue. |
| FabricIQ | Operational rows mirrored from Waypoint Postgres | WebIQ should not read or recreate supplier, invoice, line, or reconciliation tables. |
| WorkIQ | User-scoped Microsoft 365 workplace communications | WebIQ should not replace internal emails, Teams threads, approvals, or SharePoint files. |
| WebIQ | Public web market/regulatory/supplier-financial evidence | Unique if it stays external and source-cited. |

The demo risk is synthetic leakage. If we create public pages or demo fixtures to
make fictional supplier signals discoverable, they must look like realistic
external sources and must not encode the expected final decision, evaluator
labels, or scenario answer key. Otherwise WebIQ becomes another copy of the
puzzle solution rather than an independent evidence plane.

## Best practices already present

- The agent prompt is narrow: gather external market and supplier-financial
  evidence only; do not reconcile, decide, or write to Waypoint.
- The prompt explicitly forbids public-web searches for private invoice IDs.
- The orchestrator passes a market context block with supplier/category/line
  details so WebIQ can search meaningful public terms.
- The agent returns the shared IQ evidence contract with `source_ref`,
  `classification`, `confidence`, `supports`, `summary`, and `unsupported`.
- The runtime uses one tool only and sets `store=False`, reducing persistence and
  blast radius.
- The recorder does not count an empty or uncited WebIQ lane as grounded.
- WebIQ is feature-flagged off by default, so the base FoundryIQ demo remains
  deployable while WebIQ is hardened.
- When no external signal exists, the expected behavior is an empty evidence list
  rather than guessed public corroboration.

## Hacky or fragile areas today

1. **Two competing WebIQ implementations are documented.** Agent code uses
   Foundry native Web Search; infra comments and migration docs describe direct
   Microsoft Web IQ MCP with `web-iq` `CustomKeys`. A correct one-click lane
   cannot leave this ambiguous.
2. **Root deployment excludes the agent.** `market-evidence-expert` is in
   `modules/agents/azure.yaml`, but the root manifest and workflow do not include
   it in the launch selected-agent set.
3. **No root acceptance gate proves WebIQ.** Existing acceptance proves the
   FoundryIQ KB path, not WebIQ tool invocation, citations, or known-positive
   market evidence.
4. **Fictional suppliers may have no public web footprint.** That is an honest
   result, but it makes demo validation hard unless we use stable, generic market
   benchmarks or controlled public fixtures.
5. **Public-web outputs are non-deterministic.** Search indexes, rankings,
   snippets, publication availability, and freshness change over time, so
   idempotent deployment does not imply identical WebIQ evidence.
6. **Public web is untrusted input.** Search results can contain stale data,
   SEO spam, adversarial instructions, or low-quality claims. The agent must cite
   and qualify evidence rather than letting web text steer decisions directly.
7. **Data-boundary terms are easy to miss.** Foundry Web Search and Bing
   grounding send data outside Azure compliance and geographic boundaries. That
   is materially different from querying Waypoint, FoundryIQ, or Fabric-only
   resources.
8. **Direct Web IQ MCP requires a secret and entitlement.** The infra deliberately
   does not create the `web-iq` connection because a keyless update could wipe a
   secret-backed `CustomKeys` connection. That is not one-click until the secret
   source and reconciliation semantics are owned by this repo.
9. **Latency and cost can compound.** The prompt asks for 2-4 searches. In batch
   assurance, WebIQ can multiply tool calls, model tokens, and runtime.
10. **Historical docs are stale or split-era.** Several module docs record prior
    hosted/prompt-agent experiments, but the current canonical root deployment is
    FoundryIQ-only.

## Product gaps and constraints

| Gap or constraint | Impact on a gold-standard Waypoint deploy |
| --- | --- |
| Web grounding data boundary | Microsoft docs state Grounding with Bing/Web Search data can flow outside Azure compliance and geo boundaries and is not covered by the Microsoft Data Protection Addendum. |
| Usage-based cost | Web Search / Grounding with Bing incurs extra costs beyond model tokens, so the lane needs budgets, rate limits, and per-run caps. |
| Admin disablement | Azure admins can disable Grounding with Bing and Web Search at subscription/resource-group scope; preflight must detect this. |
| Region and model support | Web Search and Bing grounding support depends on Foundry Agent Service region and model; the deployment region must be checked before enabling the lane. |
| Public web non-determinism | Search results, indexing freshness, and source availability change, so evidence quality gates must assert source-bearing behavior without depending on one brittle snippet. |
| Prompt/data minimization | Invoice context sent to WebIQ may leave Azure compliance boundaries; only non-secret, non-sensitive semantic context should be used. |
| Domain restriction tradeoff | Domain-restricted search is useful for safety, but it requires custom search configuration and can reduce recall. Some Bing Custom Search paths remain preview or require additional setup. |
| Microsoft Web IQ limited access | The direct Web IQ MCP service is currently limited access for select enterprise customers, so new tenants cannot assume entitlement. |
| Secret-backed MCP connection | Direct Web IQ MCP requires API-key or equivalent authentication stored in a Foundry project connection; this needs Key Vault-backed, no-wipe reconciliation. |
| MCP approval/allow-listing | If direct MCP is used, allowed tools must be constrained, e.g. `web` and `browse`, and all tool calls/results must be logged and treated as untrusted. |

## What would be required for a correct implementation

### 1. Decide and codify the WebIQ tool path

Choose one default and remove contradictory comments/docs.

**Recommended near-term default:** Foundry Web Search.

- Keep `market-evidence-expert` on `FoundryChatClient.get_web_search_tool()`.
- Treat it as WebIQ for the demo's evidence-plane vocabulary, while documenting
  that the product primitive is Foundry Web Search / Grounding with Bing.
- Do not create a self-managed Bing grounding resource unless the repo needs
  parameters that `web_search` does not expose.
- Add preflight that proves the selected model/region supports Web Search and
  that the subscription has not disabled it.

**Separate direct Web IQ MCP option:** only enable when the tenant has access.

- Add an explicit `enable_webiq_mcp` or `webiq_tool_provider=web_search|webiq_mcp`
  control rather than overloading the current WebIQ flag.
- Store the Web IQ API key in the deployment Key Vault, never in repo variables
  or plain workflow logs.
- Create or reconcile the `web-iq` project connection only when the secret is
  present; never update it with empty credentials.
- Use an allow-list such as `web` and `browse`, not a broad MCP surface.
- Fail preflight with clear remediation when the Web IQ entitlement, endpoint, or
  credential is missing.

### 2. Deployment model

- Add root workflow input/control such as `enable_webiq`, default false.
- Include the WebIQ control in the deployment controls hash so reruns with WebIQ
  enabled/disabled are tracked.
- Extend `tools/deploy/deployment.manifest.json` to include
  `market-evidence-expert` only for the WebIQ lane.
- Deploy `market-evidence-expert` only when WebIQ is enabled.
- Set `ASSURANCE_ORCHESTRATOR_WEBIQ_ENABLED=true` and
  `WEBIQ_EXPERT_ENDPOINT=<project_endpoint>/agents/market-evidence-expert` only
  in that lane.
- Keep `WEBIQ_EXPERT_ENDPOINT` empty and WebIQ disabled when the lane is off.
- Ensure unchanged reruns reuse the same agent version, selected tool provider,
  connection names, Key Vault secrets, and acceptance fixture.

### 3. Tenant and admin preflight

Preflight should fail with actionable remediation when any required prerequisite
is missing:

- selected Foundry region supports hosted agents, the model, Web Search, and any
  chosen Bing grounding/custom-search tool;
- the selected model supports Web Search / Bing grounding;
- web grounding has not been disabled by Azure subscription/resource-group
  policy;
- the operator has acknowledged the Grounding with Bing terms, data-boundary
  behavior, and extra cost;
- if using direct Web IQ MCP, the tenant has Web IQ access, the API key exists in
  Key Vault, and the `web-iq` project connection is present and healthy;
- if using domain restriction, the Bing Custom Search resource/instance and
  allowed domains exist and can return at least one known test source.

### 4. Security and governance

- Send only minimal semantic invoice context to WebIQ: supplier name, public
  category terms, line descriptions, amounts/currency, and broad dispute
  category. Do not send API keys, raw contracts, internal comments, private
  invoice attachments, personal data, or expected outcomes.
- Log the generated query intent and returned source URLs, but redact any
  sensitive prompt fragments from public artifacts.
- Treat all web text as untrusted; the recorder and orchestrator should rely on
  citations and confidence, not hidden instructions in fetched pages.
- Require source URLs for WebIQ evidence; uncited WebIQ summaries should remain
  ungrounded and should not influence the final decision as a consulted expert.
- Cap searches per invoice and per batch to control cost and latency.
- Prefer domain allow-lists for production or regulated demos when the evidence
  should come from known regulator, market-data, supplier, or news domains.

### 5. Evidence quality and acceptance

The WebIQ acceptance gate should prove:

- the deployed `market-evidence-expert` is active when WebIQ is enabled and
  absent/disabled when it is not;
- the selected tool provider is callable from the hosted agent;
- the agent does not search the private invoice ID;
- a known-positive fixture returns at least one evidence item with a URL
  `source_ref`;
- empty evidence is accepted only for explicitly no-signal scenarios, not for the
  known-positive gate;
- the orchestrator can call the WebIQ expert and preserve the lane output;
- the recorder counts WebIQ as consulted only when evidence is cited;
- a full assurance run reaches a terminal Waypoint state through
  `waypoint-recorder`;
- an unchanged rerun does not create duplicate agents, connections, secrets, or
  Waypoint runs.

### 6. Known-positive fixture strategy

Because Caldova suppliers are fictional, WebIQ needs a careful validation
strategy:

- Use stable, real public sources for generic market facts such as manufacturing
  price indexes, rush logistics context, FDA notices, or industry capacity
  conditions.
- Avoid asking the public web to know a fictional invoice or supplier unless the
  demo intentionally publishes controlled public fixtures.
- If controlled public fixtures are created, keep them outside the repo's answer
  key and make them realistic external notices, not hidden evaluator labels.
- Write assertions around source-bearing behavior and evidence shape, not exact
  wording from a web snippet.

## New-tenant manual setup blockers

These are product or operational gaps that block "easy path" WebIQ setup for a
new tenant unless this repo preflights and guides them:

| Manual step today | Why it matters | Required repo behavior |
| --- | --- | --- |
| Confirm web grounding is allowed | Admin policy can disable Web Search / Grounding with Bing | Preflight subscription/resource policy and fail with remediation. |
| Acknowledge terms, data boundary, and cost | Grounding with Bing has separate terms, cost, and DPA/geography implications | Make this an explicit deployment control/acknowledgement for WebIQ-enabled runs. |
| Confirm region/model tool support | Foundry tools are region/model dependent | Extend region-capacity preflight to include WebIQ tool support. |
| Create direct Web IQ access/key if using MCP | Microsoft Web IQ is limited access and key-backed | Keep MCP provider off unless entitlement and Key Vault secret are present. |
| Configure domain-restricted search if required | Regulated demos may need approved domains only | Provide scripted creation/reconciliation or document the admin-owned setup. |
| Provide known-positive external evidence | Fictional suppliers usually have no public footprint | Include a stable fixture strategy and acceptance assertions. |

## Cost posture

WebIQ does **not** require Fabric capacity and does not require Microsoft 365
Copilot licenses. Its costs are primarily:

- model input/output tokens for the WebIQ expert;
- web grounding/tool calls, billed separately from base model usage;
- optional Bing Custom Search or Microsoft Web IQ MCP usage if those paths are
  selected;
- extra latency and retry cost when the orchestrator invokes WebIQ for many
  invoices.

The right scale model is **per assurance run / per search**, not per seller. For
20,000 sellers, the risk is not an F64-style fixed capacity bill; it is the
number of assurance events multiplied by 2-4 searches, model tokens, retries,
and batch concurrency. A production-like deployment should add per-batch caps,
budgets, sampling/skip rules, and telemetry that reports WebIQ calls and
citations per run.

## Implementation sequence

1. Decide whether the repo's WebIQ lane uses Foundry Web Search by default or
   direct Microsoft Web IQ MCP; document and enforce that choice.
2. Remove or update contradictory WebIQ comments in `modules/agents/infra`,
   `modules/agents/.github/workflows/deploy.yml`, and historical module docs so
   operators do not provision the wrong connection.
3. Add root `enable_webiq` controls and include `market-evidence-expert` in the
   selected agent matrix only when enabled.
4. Extend preflight for Web Search/model/region/admin-policy support and, if
   applicable, Web IQ MCP entitlement and Key Vault secret readiness.
5. Add WebIQ acceptance that drives the hosted expert with a known-positive
   market context and checks URL citations.
6. Add orchestrator E2E acceptance with WebIQ enabled and verify the recorder
   persists a terminal, citation-bearing run.
7. Add unchanged-rerun checks for no duplicate agents/connections/secrets and no
   duplicate Waypoint runs.
8. Add cost/rate-limit telemetry and caps before enabling WebIQ for batch demos.

## Recommendation

Implement WebIQ after FoundryIQ remains green and before any direct Web IQ MCP
claim is made in the one-click story.

The recommended near-term path is to standardize on **Foundry Web Search** as the
WebIQ evidence primitive for this repo, because it is GA, uses the existing
hosted-agent runtime shape, avoids tenant-specific Web IQ API keys, and can be
validated with a known-positive public-web fixture. Keep direct Microsoft Web IQ
MCP as a separate advanced option until the product entitlement, secret-backed
connection lifecycle, and no-wipe idempotency path are owned by this repo.

Do not make WebIQ part of the default launch fleet until:

- the tool path ambiguity is removed;
- root deployment can opt it in and out cleanly;
- preflight catches region/model/admin-policy/cost/data-boundary blockers;
- acceptance proves source-bearing WebIQ evidence through the orchestrator and
  recorder; and
- disabled WebIQ still leaves the base Caldova/Waypoint demo one-click green.

## References checked

Microsoft and repo references checked on 2026-07-24:

- Microsoft Learn: **Overview of web grounding capabilities in Foundry**.
- Microsoft Learn: **Use web search tool in Foundry Agent Service**.
- Microsoft Learn: **Use Grounding with Bing Search tools with the agents API**.
- Microsoft Learn: **Web search with the Responses API**.
- Microsoft Learn: **Manage Grounding With Bing Access**.
- Microsoft Learn: **Quotas and limits for Microsoft Foundry Agent Service**.
- Microsoft Learn: **Connect to MCP Server Endpoints for agents**.
- Microsoft Learn Agent Framework: **Hosted MCP Tools**.
- Microsoft Web IQ public site and MCP documentation landing page.
- Repo: `.github/workflows/deploy.yml`.
- Repo: `tools/deploy/deployment.manifest.json`.
- Repo: `modules/agents/agents/market-evidence-expert`.
- Repo: `modules/agents/agents/assurance-orchestrator/expert_clients.py`.
- Repo: `modules/agents/infra/main.bicep`.
- Repo: `modules/agents/.github/workflows/deploy.yml`.
- Repo: `modules/agents/docs/FORGE_CURRENT_STATE.md`.
- Repo: `modules/agents/docs/PROMPT_AGENT_MIGRATION.md`.
- Repo: `modules/agents/docs/AGENT_PIPELINE_AUDIT.md`.
- Repo: `docs/deployment.md`, `docs/architecture.md`, and `docs/status.md`.
