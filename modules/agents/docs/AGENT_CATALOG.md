# Forge agent catalog

This catalog is the quick reference for current Caldova Forge agents, their
deployment shape, their tool/IQ access, and which agents should be used for
Caliber optimization, RFT, or RLE planning.

For the current shipped/stubbed state, resource map, deployment paths, and Jess
handoff checklist, see [`FORGE_CURRENT_STATE.md`](FORGE_CURRENT_STATE.md).

## Current agent matrix

| Agent / folder | Deployment shape | Role | Access / IQ surfaces | Read/write posture | Typical caller / use | RFT / calibration relevance |
| --- | --- | --- | --- | --- | --- | --- |
| `agents/assurance-analyst` / `assurance-analyst` | Hosted Foundry agent | Human-facing read-only analyst for invoice-assurance Q&A, run/case status, and grounded explanation. | Direct Waypoint status tools for run/case status; WaypointIQ toolbox (`waypoint-iq`) for operational facts; FoundryIQ contract/policy KB toolbox (`assurance-analyst-tools` over `kb-mcp-connection`) for grounding; Teams/M365 activity surface. | Read-only. No Waypoint writes, approvals, case creation, or final reconciliation decisions. | Teams/M365 status questions, human-facing demo Q&A, Playground smoke, and audience-visible explanation of why an invoice should be reviewed/recovered/escalated. | **Primary Caliber/RFT/RLE target when the goal is a hosted agent that reasons across WaypointIQ + FoundryIQ and reports assurance status.** Optimize for grounded multi-plane explanations, concise status reporting, source separation, no-write boundaries, and calibrated uncertainty. |
| `agents/assurance-orchestrator` / `assurance-orchestrator` | Hosted Foundry agent | Deterministic invoice-assurance coordinator. Gathers inputs, fans out to experts, normalizes evidence, drafts write plans, and gates the workflow. | Content Understanding, Waypoint read tools / WaypointIQ read path, hosted expert Responses fan-out, waypoint-recorder handoff. | Current proof path is read-only for Waypoint writes; later may write only through governed WaypointIQ once write authority is proven. | Pipeline driver and workflow orchestrator. | Not the first RFT target. Prefer deterministic tests and trace/eval coverage for orchestration reliability; tune only narrow summarization/adjudication behavior if needed. |
| `agents/waypoint-recorder` / `waypoint-recorder` | Hosted Foundry agent | Final normalizer and governed write boundary for approved assurance results. | Waypoint write tools / future WaypointIQ write tools; final policy checks. | Write-capable by design. Owns governed Waypoint persistence when approved. | Assurance Orchestrator handoff after evidence normalization and write-plan preview. | High-risk RFT target because it owns writes. Use deterministic validation, policy gates, and human review before any model optimization. |
| `agents/contract-policy-expert` / `contract-policy-expert` | Hosted Foundry agent | Narrow FoundryIQ evidence lane for contract, policy, pricing, and prior-finding retrieval. Replaces the old `foundryiq-expert` lineage lane. | FoundryIQ knowledge-base MCP (`kb-mcp-connection`, `knowledge_base_retrieve`) through `contract-policy-expert-tools` plus local fallback behavior. | Read-only evidence extraction. No Waypoint writes, final reconciliation, case creation, or authorization. | Assurance Orchestrator hosted Responses fan-out, evals, and evidence-contract extraction tests. | Secondary/narrow target. Useful for prompt/rubric/evidence-contract optimization and possible specialist RFT, but **not** the Caliber target when the desired behavior is hosted cross-plane reasoning. |
| `agents/operations-data-expert` / `operations-data-expert` | Hosted Foundry agent | Operations/FabricIQ evidence lane for structured operational data. Current implementation is stubbed until real FabricIQ data is available. | FabricIQ / structured-data tools when available; otherwise honest no-source fallback behavior. | Read-only evidence extraction. | Assurance Orchestrator hosted Responses fan-out for operational structured-data evidence. | Not current Caliber RFT target; wait for real FabricIQ source data and golden cases. |
| `agents/market-evidence-expert` / `market-evidence-expert` | Hosted Foundry agent | Market/WebIQ evidence lane for external corroboration. | Foundry native web-search grounding. | Read-only evidence extraction. | Assurance Orchestrator hosted Responses fan-out for market/external corroboration. | Candidate only for web-grounding behavior if a future eval set shows systematic issues. |
| `agents/collaboration-evidence-expert` / `collaboration-evidence-expert` | Hosted Foundry agent | WorkIQ evidence lane for workplace collaboration evidence. | Foundry toolbox `collaboration-evidence-tools` backed by UserEntraToken WorkIQCopilot, WorkIQTeams, and WorkIQSharePoint RemoteTool connections. | Read-only evidence extraction. | Assurance Orchestrator hosted Responses fan-out for collaboration/workplace evidence. | Candidate only after WorkIQ trace/dataset coverage exists; preserve privacy and source boundaries. |

## Caliber target guidance

Caliber should target `assurance-analyst` for live hosted-agent RFT/RLE planning
when the objective is a model that can reason across both:

- **WaypointIQ** operational state: invoice, work, finding, run, case, evidence,
  and status facts.
- **FoundryIQ** contract/policy grounding: contract clauses, rate cards, policies,
  and prior findings from the knowledge base.

`contract-policy-expert` remains important, but it is narrower: it is the
single-plane FoundryIQ evidence expert and the successor to the deprecated
`foundryiq-expert` hosted lane. Use its eval assets for specialist evidence
contract and retrieval-rubric work; do not treat deprecated names such as
`foundryiq-expert` as current optimization targets.

## Naming and deployment rules

- Hosted services in `azure.yaml`: `assurance-orchestrator`,
  `waypoint-recorder`, `assurance-analyst`, `contract-policy-expert`,
  `operations-data-expert`, `market-evidence-expert`, and
  `collaboration-evidence-expert`.
- Prompt-agent replacements: none in the current active fleet. Historical
  prompt-agent migration notes are retained for context only.
- Deprecated lineage names such as `foundryiq-expert`, `fabriciq-expert`,
  `webiq-expert`, and `workiq-expert` may appear in historical commits or
  handoff notes only. Current docs, eval configs, and Caliber manifests should
  use the responsibility-based names above.

## Inventory boundaries

- `waypoint-operations-expert` is a planned prompt agent for operational
  reasoning over `waypoint-iq`; it is not part of the current active fleet and
  does not have an `agents/waypoint-operations-expert/` source folder yet.
- `release_captain` eval assets are historical/orphaned until an active
  `release-captain` agent is added back to the fleet. Do not count them as a
  current Caldova Forge agent.
- `publish.yaml` files can exist before an agent is ready for Microsoft 365 /
  Teams publishing. Treat template values such as "Short one-liner describing
  this agent" as publishing metadata debt, not evidence that the agent is
  publish-ready.
