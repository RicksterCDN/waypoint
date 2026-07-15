# Assurance Orchestrator demo storyline

Assurance Orchestrator demonstrates how Forge can turn invoice assurance from a slow,
document-heavy audit process into an evidence-backed operating workflow. The
story is not "an agent approves invoices." The story is "an agent collects,
reconciles, explains, and stages controlled recommendations while Waypoint keeps
the system of record, approval boundary, and audit trail."

## Narrative arc

1. **The problem is payment leakage.** Pharma CMO invoices are not validated by
   a simple two- or three-way match. The controlling truth is distributed across
   invoice PDFs, contracts, POs, quality agreements, batch records, release logs,
   milestone acceptances, materials, capacity commitments, and supplier
   correspondence.
2. **The first proof is safe read-only grounding.** Assurance Orchestrator reads governed
   Waypoint work, invoice context, findings, evidence, policies, contract
   documents, action types, runs, and cases with delegated Entra auth. It does
   not create cases, authorize actions, or mutate records.
3. **The second proof is specialist fan-out.** The workflow separates services
   and reasoning: deterministic reconciliation runs first, then WebIQ, Fabric IQ,
   WorkIQ, and Foundry IQ each gather specialist evidence in parallel.
4. **The third proof is evidence-backed judgement.** Assurance Orchestrator synthesizes the
   validator evidence into a structured decision: status, severity, amount at
   risk, policy basis, contract basis, cited evidence, contradictory evidence,
   confidence, and allowed action options.
5. **The fourth proof is governed action staging.** Assurance Orchestrator can later stage a
   recommendation, dispute packet, escalation summary, or proposed action in
   Waypoint. Humans and admin-governed Waypoint APIs remain responsible for
   approval and authorized intent.
6. **The final proof is economic learning.** After many approved runs, the
   evidence-to-judgement behavior can be distilled into smaller fine-tuned
   models for routine cases, while difficult cases continue to use the full
   workflow.

## Demo roles

| Role | Demo function |
| --- | --- |
| **Controller teammate** | Human-facing front door that receives invoice emails/PDFs, answers process/status questions, starts or schedules Assurance Orchestrator runs, and routes missing-info or approval follow-ups. |
| **Waypoint** | Runtime control plane and system of record for work, cases, evidence, allowed actions, recommendations, approvals, and audit. |
| **Assurance Orchestrator** | Forge-hosted invoice assurance orchestrator that reads Waypoint, gathers evidence, synthesizes judgements, and stages safe recommendations. |
| **Ledgerfield** | Source of domain scenarios, policies, supplier contracts, generated invoice documents, and expected reconciliation outcomes. |
| **Content Understanding** | Service that extracts invoice header, line, page, and span facts from PDF inputs. |
| **WebIQ** | External market and financial context for commercial reasonableness. |
| **Fabric IQ** | Structured enterprise data checks: PO, receipt, batch, payment, duplicate, rate, and operational integrity. |
| **WorkIQ** | Communication and collaboration context: email, Teams, meetings, approvals, supplier correspondence. |
| **Foundry IQ** | Policy, contract, SOW, quality agreement, and knowledge-index grounding. |
| **Fine-tuned task model** | Later-stage economical model for repeated evidence-to-judgement or classification tasks. |

## Invocation storyline

Assurance Orchestrator should not be the conversational front door. It should be the long-running
workhorse that performs bounded invoice assurance jobs. The natural user-facing
experience is a separate controller teammate that knows the invoice assurance
process, watches Waypoint state, receives or references invoice PDFs, and starts
Assurance Orchestrator when work is ready.

| Invocation mode | Controller teammate behavior | Assurance Orchestrator behavior |
| --- | --- | --- |
| **Scheduled heartbeat** | Periodically checks Waypoint work or receives a Foundry heartbeat and starts eligible work. | Runs the assurance workflow for selected work items. |
| **Email intake** | Receives invoice PDFs or links, captures sender/context, routes documents through the agreed ingest path, and creates or links Waypoint work. | Processes the resulting invoice/work item after it exists in the control plane. |
| **Conversational request** | Handles questions like "check invoice X" or "what happened with BluePeak?" by reading Waypoint and deciding whether a new run is needed. | Runs only when the question requires fresh assurance work. |
| **Case follow-up** | Explains run status, missing evidence, recommendations, approval needs, or escalation path to humans. | Emits structured status, evidence, judgement, and write-plan outputs. |
| **Manual demo trigger** | May be bypassed for local testing through the canvas. | Can be invoked directly for smoke tests, but this is not the production UX. |

This split keeps responsibilities clear: the controller owns human interaction,
email intake, scheduling, status Q&A, and delegation; Assurance Orchestrator owns expensive
reconciliation work, evidence synthesis, run telemetry, and Waypoint write-plan
outputs. Waypoint remains the durable source of state between them.

## Suggested demo beats

1. **Open with the risk.** Show an invoice line that looks normal in isolation
   but depends on a contract clause, batch release status, or approval trail.
2. **Show governed discovery.** Assurance Orchestrator reads `/api/work` and picks a Waypoint
   item without needing direct database access or privileged writes.
3. **Show context assembly.** Assurance Orchestrator retrieves invoice context, related
   findings, evidence, contract documents, policies, action vocabulary, and
   correlation fields.
4. **Show the extraction boundary.** PDF extraction is treated as a service step
   with source spans, not as free-form model memory.
5. **Show fan-out.** Validators run independently and return normalized evidence
   bundles, not final decisions.
6. **Show synthesis.** Assurance Orchestrator produces a clear judgement with status, severity,
   money at risk, basis summary, evidence IDs, contract/policy IDs, confidence,
   and allowed action options.
7. **Show no hidden side effects.** The response explicitly says no approvals,
   supplier messages, ERP updates, or payment side effects were performed.
8. **Show controlled staging.** In a later write-enabled phase, Assurance Orchestrator prepares
   the Waypoint recommendation or draft packet, but approval and authorize-intent
   remain separate.
9. **Show learning loop.** Approved decisions, evidence bundles, human outcomes,
   and eval results become the source for future SFT/DPO/RFT datasets.
10. **Show teammate handoff.** The controller teammate answers human process
    questions and launches or monitors Assurance Orchestrator, while Assurance Orchestrator stays focused on
    long-running assurance execution.

## Business claims to make

- Assurance Orchestrator reduces payment leakage by finding contractual, operational, and
  quality-release exceptions that basic AP matching misses.
- Waypoint makes the agent governable by owning work queues, closed action
  vocabularies, approvals, authorized intent, and audit.
- Forge makes the workflow extensible by letting specialist IQs contribute
  evidence without each one owning the final decision.
- Fine-tuning becomes credible only after the full workflow has generated
  high-quality traces, approved outcomes, and eval baselines.

## Safety and control boundaries

- Read-only testing uses delegated Entra tokens and configured Waypoint API
  scopes.
- Production service use should move to app roles, managed identity, client
  credentials, or principal allow-listing when Waypoint supports it.
- Assurance Orchestrator must not invent action types. It can only use Waypoint's closed action
  vocabulary.
- Assurance Orchestrator must not approve, authorize, send supplier communications, update ERP,
  alter payments, or execute external side effects unless a separate controlled
  production workflow explicitly authorizes that.
- Decision-relevant outputs should be snapshotted into Waypoint. Traces are
  useful for observability and training, but they are not the durable decision
  record by themselves.

## Success criteria

- The demo shows at least one invoice where the decision depends on evidence
  outside the invoice PDF.
- Every judgement cites source evidence, policies, and contract documents.
- The user can see which parts were deterministic services, which parts were
  LLM-guided, and which parts were governed by Waypoint.
- The final action is a recommendation or draft, not an uncontrolled side
  effect.
- The storyline naturally leads to fine-tuning as an optimization after repeated
  successful runs, not as the starting point.
