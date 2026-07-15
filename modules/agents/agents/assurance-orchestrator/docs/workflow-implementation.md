# Assurance Orchestrator workflow implementation plan

Assurance Orchestrator should be implemented as a bounded invoice assurance workflow with clear
contracts between deterministic service stages, LLM-guided reasoning stages,
specialist IQ validators, Waypoint control-plane reads/writes, and telemetry.

The key implementation principle is: **models reason over scoped evidence;
services own extraction, retrieval, math, persistence, and side effects.**

Assurance Orchestrator should also be treated as a worker agent, not the primary human-facing
teammate. A separate controller teammate should own email intake, scheduling,
status Q&A, and delegation. Assurance Orchestrator should own long-running invoice assurance
execution.

## Recommended architecture

Start with an explicit Python workflow module shaped like the Brightline
orchestration pattern, then evaluate moving the same contracts into Microsoft
Agent Framework Workflows once the graph and event contracts stabilize.

Brightline lessons to carry forward:

- Define bounded steps and typed result contracts.
- Give each step only the tools it needs.
- Fan out evidence-gathering steps with `asyncio.gather`.
- Record structured events and display Markdown separately.
- Capture tool-call snapshots for audit and debugging.
- Retry individual evidence steps without restarting the whole run.
- Make final synthesis tool-free and evidence-only.
- Keep side-effect delivery in deterministic code, not model-selected tool calls.

Agent Framework Workflows may become useful when Assurance Orchestrator needs durable graph
semantics, checkpointing, richer branching, and built-in workflow observability.
The first pass should not block on adopting the framework if hand-rolled
orchestration gets us to a testable demo faster.

## Invocation architecture

Use a controller/worker split:

| Component | Responsibility | Should not do |
| --- | --- | --- |
| **Controller teammate** | Human-facing process agent. Receives invoice emails/PDFs or links, answers status/process questions, watches scheduled heartbeat signals, reads Waypoint, starts Assurance Orchestrator runs, and routes follow-ups. | Perform deep reconciliation, invent findings, approve actions, or bypass Waypoint state. |
| **Assurance Orchestrator** | Workhorse invoice assurance agent. Runs bounded long-running workflows, calls extraction/services/IQs, synthesizes evidence-backed judgements, prepares write plans, and emits telemetry. | Own broad conversational UX, email relationship management, uncontrolled side effects, or autonomous approvals. |
| **Waypoint** | Shared control plane. Stores work, cases, run anchors, action vocabulary, recommendations, approvals, authorized intent, evidence, and audit. | Delegate durable decision state to transient agent traces. |

Recommended invocation modes:

1. **Scheduled heartbeat.** A controller job or Foundry heartbeat checks Waypoint
   `/api/work`, selects ready items, and starts Assurance Orchestrator with a work target and
   correlation envelope.
2. **Email-triggered intake.** A controller teammate receives invoice PDFs or
   document links, records sender/context, routes the document through the
   agreed ingest path, creates or links Waypoint work, and invokes Assurance Orchestrator when
   ready.
3. **Conversational trigger.** A user asks about an invoice, supplier, or
   process. The controller reads Waypoint first and invokes Assurance Orchestrator only when
   fresh assurance work is needed.
4. **Case follow-up.** The controller reads Assurance Orchestrator/Waypoint run status and
   explains missing evidence, recommendations, approval needs, or escalation
   path to humans.
5. **Manual testing.** The Responses Chat canvas may invoke Assurance Orchestrator directly for
   development and demos, but production should route through the controller and
   Waypoint.

Controller-to-Assurance Orchestrator input should be a small stable envelope:

```json
{
  "request_type": "assure_invoice",
  "invoice_id": "invoice-789",
  "finding_id": null,
  "waypoint_case_id": "case-123",
  "waypoint_run_id": null,
  "source": "heartbeat|email|chat|manual",
  "requested_by": {
    "name": "Avery Finance",
    "email": "avery@example.com"
  },
  "correlation": {
    "foundry_conversation_id": "conv-123",
    "app_insights_operation_id": "op-456"
  },
  "constraints": {
    "read_only": true,
    "max_runtime_minutes": 30,
    "allowed_write_phase": "none"
  }
}
```

Assurance Orchestrator-to-controller output should be equally structured:

```json
{
  "run_id": "assurance-orchestrator-run-001",
  "status": "completed|partial|failed",
  "invoice_id": "invoice-789",
  "waypoint_case_id": "case-123",
  "judgements": [],
  "write_plan": null,
  "missing_evidence": [],
  "human_followups": [],
  "side_effects_performed": false
}
```

## Stage map

| Stage | Type | Responsibility | Output |
| --- | --- | --- | --- |
| `discover_work` | Service | Read Waypoint work queue and select invoice/case target. | `WaypointWorkTarget` |
| `prepare_run` | Service | Create local run envelope and correlation IDs. Read-only first; Waypoint run POST later. | `Assurance OrchestratorRunContext` |
| `extract_invoice` | Service + optional LLM assist | Extract PDF header/line facts with Content Understanding and normalize fields. | `ExtractedInvoice` |
| `resolve_context` | Service | Read Waypoint invoice context, policies, contracts, evidence, findings, allowed actions. | `InvoiceContextBundle` |
| `deterministic_reconciliation` | Code | Duplicate, PO, receipt, rate, MOQ, true-up, milestone, release, math, and timing checks. | `ValidatorResult` |
| `webiq_validation` | LLM/tool executor | Market and financial reasonableness. | `ValidatorResult` |
| `fabriciq_validation` | Tool executor | Structured operational/finance integrity checks. | `ValidatorResult` |
| `workiq_validation` | Tool executor | Supplier correspondence, approvals, and collaboration evidence. | `ValidatorResult` |
| `foundryiq_validation` | LLM/tool executor | Contract, SOW, quality agreement, and policy grounding. | `ValidatorResult` |
| `synthesize_judgement` | LLM, tool-free | Merge evidence into structured judgement and action options. | `InvoiceJudgement` |
| `prepare_waypoint_write_plan` | Code + schema validation | Convert judgement into case/recommendation/draft/action payloads without posting. | `WaypointWritePlan` |
| `post_waypoint_staging` | Service, later phase | Stage recommendation/draft/proposed action when auth/idempotency are explicit. | `WaypointWriteResult` |

## LLM-guided vs service-guided work

| Work | Owner |
| --- | --- |
| PDF OCR/layout extraction and span capture | Content Understanding |
| Auth, HTTP, retries, pagination, schema validation | Waypoint client code |
| Duplicate/match/math/rate checks | Deterministic Python |
| Contract clause interpretation | LLM grounded by Foundry IQ evidence |
| Messy supplier line normalization | LLM assisted, schema constrained |
| Market reasonableness | WebIQ evidence plus LLM summary |
| Structured data integrity | Fabric IQ queries and deterministic checks |
| Communication/approval discovery | WorkIQ search and summarization |
| Final judgement | LLM, tool-free, evidence-only |
| Waypoint persistence | Deterministic code with explicit payload schemas |
| Approval/authorization | Waypoint/human-admin workflow, not Assurance Orchestrator autonomous action |

## Proposed contracts

Keep contracts small, serializable, and stable enough to persist in events,
traces, eval rows, and future training data.

```python
@dataclass
class Assurance OrchestratorRunContext:
    run_id: str
    waypoint_case_id: str | None
    waypoint_run_id: str | None
    waypoint_action_id: str | None
    foundry_agent_name: str | None
    foundry_conversation_id: str | None
    foundry_response_id: str | None
    app_insights_operation_id: str | None
```

```python
@dataclass
class ValidatorResult:
    validator_id: str
    status: Literal["completed", "partial", "failed"]
    findings: list[FindingCandidate]
    evidence: list[EvidenceReference]
    confidence: float
    summary: str
    tool_calls: list[ToolCallSnapshot]
    error: str | None = None
```

```python
@dataclass
class InvoiceJudgement:
    invoice_id: str
    status: Literal["matched", "variance", "disputed", "escalated"]
    waypoint_decision: Literal["approve", "recover", "escalate", "review"]
    severity: Literal["low", "medium", "high", "critical"]
    category: str
    money_at_risk: Decimal
    confidence: float
    summary: str
    basis_summary: str
    evidence_ids: list[str]
    contract_document_ids: list[str]
    policy_ids: list[str]
    contradictory_evidence_ids: list[str]
    proposed_next_actions: list[str]
```

## Workflow behavior

1. Start each run with a `run_id` and correlation envelope.
2. Read Waypoint work and invoice context before model reasoning.
3. Normalize PDF facts and Waypoint facts into one canonical invoice record.
4. Run deterministic checks before IQ fan-out so validators can see known
   discrepancies and avoid rediscovering basic math.
5. Fan out IQ validators in parallel. Each validator receives only relevant
   context and only its own tools.
6. Treat validator failures as partial evidence, not necessarily whole-run
   failure. Surface unavailable systems explicitly in the final judgement.
7. Synthesize with no tools. The synthesizer receives only normalized invoice
   facts, deterministic checks, validator results, allowed action types, and
   Waypoint correlation fields.
8. Validate the judgement JSON against a strict schema.
9. Build a Waypoint write plan but do not post in the current read-only phase.
10. Later, enable posting recommendations/drafts/proposed actions behind an
    explicit feature flag and auth check.

## Events and telemetry

Emit both machine-readable events and user-readable Markdown display text.
Recommended event names:

- `assurance-orchestrator.run.started`
- `assurance-orchestrator.work.discovered`
- `assurance-orchestrator.invoice.extraction.started`
- `assurance-orchestrator.invoice.extraction.completed`
- `assurance-orchestrator.context.resolved`
- `assurance-orchestrator.validator.started`
- `assurance-orchestrator.validator.completed`
- `assurance-orchestrator.validator.failed`
- `assurance-orchestrator.synthesis.started`
- `assurance-orchestrator.synthesis.completed`
- `assurance-orchestrator.write_plan.prepared`
- `assurance-orchestrator.waypoint.staging.posted`
- `assurance-orchestrator.run.completed`

Telemetry attributes should include `run_id`, `waypoint_case_id`,
`waypoint_run_id`, `waypoint_action_id`, `invoice_id`, `finding_id`,
`foundry_agent_name`, `foundry_conversation_id`, `foundry_response_id`, and
`app_insights_operation_id` when available.

## Waypoint write phases

| Phase | Enabled behavior |
| --- | --- |
| **Phase 0: read-only** | Current state. Read work/context/actions/runs/cases/domain truth and produce local scout summaries. |
| **Phase 1: write plan only** | Build validated case/recommendation/draft/action payloads locally but do not POST. |
| **Phase 2: staged recommendations** | POST cases, recommendations, drafts, and proposed actions through governed admin/service auth. No approvals or authorize-intent. |
| **Phase 3: approval integration** | Read or submit approval records only through explicit human/admin workflow. |
| **Phase 4: authorized intent** | POST authorize-intent only after approval, idempotency key, snapshot, and action policy are present. |

## Testing strategy

- Unit-test path normalization, environment loading, auth header construction,
  and Waypoint client endpoint helpers.
- Unit-test deterministic reconciliation checks with Ledgerfield scenario data.
- Snapshot-test validator prompts and structured outputs.
- Contract-test judgement JSON and Waypoint write-plan schemas.
- Integration-test read-only production API calls with delegated test auth.
- Add eval cases for clean match, duplicate, rate variance, quality release,
  MOQ/true-up, milestone, and supplier correspondence scenarios.
- Regression-test that no write endpoints are called unless the write feature
  flag is enabled.

## Open decisions

- Whether Agent Framework Workflows should be adopted before or after the first
  write-plan implementation.
- Whether Content Understanding outputs should be persisted in Waypoint, Foundry
  storage, Fabric/OneLake, or an external document store.
- Which IQ validators are required for every invoice and which can be selected
  by a routing model.
- How Waypoint will formalize idempotency for case/recommendation/draft/action
  creation before approvals and authorization.
- What confidence threshold lets a fine-tuned smaller model short-circuit the
  full expensive fan-out workflow for routine cases.
