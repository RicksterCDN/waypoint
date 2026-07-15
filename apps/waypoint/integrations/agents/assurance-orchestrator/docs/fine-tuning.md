# Assurance Orchestrator fine-tuning plan

Assurance Orchestrator's fine-tuning story is economic optimization after repeated successful
expert runs. The first version should use a strong model, deterministic checks,
Content Understanding, specialist IQ fan-out, and Waypoint grounding to generate
high-quality decisions. Once those decisions are reviewed and approved, they
become training and evaluation data for smaller task models.

The goal is not to fine-tune away governance. The goal is to reduce latency and
cost for repeated evidence-to-decision tasks while preserving Waypoint controls,
citations, auditability, and human approval boundaries.

The controller teammate and Assurance Orchestrator workhorse should be optimized separately.
Assurance Orchestrator fine-tuning should target structured assurance tasks. Controller
fine-tuning, if needed later, should target conversational process guidance,
intake triage, and status explanation without teaching it to perform deep
reconciliation itself.

## Best fine-tuning opportunities

| Target | Input | Output | Training path |
| --- | --- | --- | --- |
| **Judgement synthesis** | Invoice facts, deterministic checks, validator evidence, allowed actions. | Strict `InvoiceJudgement` JSON plus concise rationale. | SFT first; DPO later for preference/style. |
| **Finding classification** | Invoice line, scenario context, evidence snippets. | Category, severity, status, and confidence. | SFT. |
| **Evidence ranking** | Candidate evidence from validators. | Ranked evidence IDs and explanation of relevance. | SFT; RFT later if graders are reliable. |
| **Supplier dispute drafting** | Judgement, contract basis, policy basis, evidence. | Supplier-ready dispute or credit request draft. | SFT + DPO. |
| **Validator routing** | Work item, invoice facts, available context. | Which validators/IQs to run and why. | SFT after enough workflow traces. |
| **Reasoning quality** | Scenario prompt and evidence set. | Correct decision under known expected outcome. | RFT later, if we can build strong graders. |
| **Controller intake triage** | Email/chat request, attachments or links, Waypoint state. | Intake classification, required missing info, whether to start Assurance Orchestrator. | SFT later, separate from Assurance Orchestrator. |

Do not fine-tune the whole agent loop first. Keep API reads/writes, auth,
extraction, math, deterministic reconciliation, and side-effect boundaries in
services and code.

## Dataset capture loop

Each completed run should produce a durable training candidate record. Store the
full record in a safe internal artifact store or Waypoint-managed export, not in
ad hoc logs.

```json
{
  "run_id": "assurance-orchestrator-run-2026-06-19-001",
  "waypoint_case_id": "case-123",
  "waypoint_run_id": "run-456",
  "invoice_id": "invoice-789",
  "scenario_id": "surge-capacity-before-approval",
  "input": {
    "invoice_facts": {},
    "deterministic_checks": [],
    "validator_results": [],
    "allowed_actions": []
  },
  "output": {
    "status": "escalated",
    "decision": "recover",
    "severity": "high",
    "money_at_risk": "25000.00",
    "basis_summary": "Capacity surcharge requires written schedule approval.",
    "evidence_ids": [],
    "contract_document_ids": [],
    "policy_ids": [],
    "proposed_next_actions": []
  },
  "review": {
    "approved_by": "human-reviewer-or-admin",
    "approved_at": "2026-06-19T00:00:00Z",
    "accepted": true,
    "corrections": []
  },
  "metrics": {
    "model": "large-reference-model",
    "latency_ms": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "estimated_cost_usd": "0.00"
  }
}
```

## SFT path

Use supervised fine-tuning when we have high-quality input-output pairs. This is
the first and most likely useful path for Assurance Orchestrator.

Recommended first SFT task:

```text
Input: normalized invoice facts + deterministic checks + validator evidence +
allowed Waypoint actions

Output: strict InvoiceJudgement JSON
```

Why this is a good fit:

- The task is repeated across many invoices.
- The desired output shape is stable.
- Human-reviewed Waypoint outcomes can become labels.
- A smaller model can learn domain vocabulary, category mapping, and basis
  summarization.
- We can measure schema validity, evidence coverage, decision match, latency,
  and cost.

Minimum useful dataset target:

- 50-100 curated examples for a first smoke fine-tune.
- 300-500 high-quality examples for a meaningful demo comparison.
- Hold out entire suppliers/scenarios/categories for evaluation to avoid
  memorizing wording.

## DPO path

Use DPO after SFT if the model is directionally correct but needs preference
alignment.

Good Assurance Orchestrator DPO pairs:

- Preferred: cites controlling contract/policy evidence. Rejected: generic
  explanation with weak citations.
- Preferred: concise supplier dispute draft with clear requested action.
  Rejected: verbose or accusatory draft.
- Preferred: escalates when quality-release evidence is missing. Rejected:
  overconfident approval.
- Preferred: admits unavailable evidence. Rejected: invents missing operational
  proof.

DPO is most useful for judgement tone, escalation discipline, and supplier
communication quality.

## RFT path

Use RFT later, only for tasks with reliable graders. Assurance Orchestrator has some promising
RFT surfaces, but they require careful grader design to avoid reward hacking.

Potential graders:

- JSON schema validity.
- Required evidence IDs are present.
- Decision matches known Ledgerfield scenario label.
- Money-at-risk equals expected amount within tolerance.
- Policy IDs include the required policy for the exception category.
- No prohibited side effects or unauthorized action language.

RFT is not the first move because invoice assurance has nuanced evidence and
policy interpretation. A weak grader could reward shallow keyword matching or
overconfident decisions.

## Evaluation metrics

Track both quality and economics:

| Metric | Purpose |
| --- | --- |
| Schema validity | Output can be posted or staged safely. |
| Decision accuracy | Matches approved or golden decision. |
| Category accuracy | Correct exception type. |
| Severity accuracy | Correct escalation level. |
| Money-at-risk error | Financial correctness. |
| Evidence precision/recall | Uses the right evidence and avoids irrelevant citations. |
| Policy/contract coverage | Cites required governing documents. |
| Hallucination rate | Does not invent facts, evidence, or actions. |
| Escalation discipline | Escalates low-confidence or missing-evidence cases. |
| Latency | Shows demo improvement. |
| Token/cost reduction | Justifies smaller model deployment. |

## Demo comparison

The fine-tuning demo should compare three paths:

1. **Reference workflow.** Large model + deterministic checks + full IQ fan-out.
2. **Small base model.** Same judgement task without fine-tuning.
3. **Small fine-tuned model.** Same judgement task after SFT.

Compare on a held-out evaluation set:

- accuracy and citation quality,
- schema validity,
- low-confidence escalation behavior,
- latency,
- cost per invoice,
- percentage of cases that can skip full fan-out,
- percentage of cases routed back to the reference workflow.

The ideal result is not "the small model replaces the agent." The ideal result
is "the small fine-tuned model handles routine cases cheaply and routes hard
cases back to the full governed workflow."

## Production routing pattern

After deployment, use the fine-tuned model as a first-pass task model:

1. Run deterministic checks and resolve context.
2. Ask the fine-tuned model for judgement JSON.
3. Validate schema, evidence references, allowed actions, confidence, and
   prohibited side-effect language.
4. Accept only high-confidence, low-risk, well-cited routine cases.
5. Route low-confidence, high-severity, missing-evidence, or novel cases to the
   full Assurance Orchestrator fan-out workflow.
6. Continue capturing outcomes for future evals and iterative training.

## Data hygiene

- Do not train on secrets, raw credentials, or unnecessary PII.
- Prefer redacted context bundles when possible.
- Preserve stable evidence IDs and document IDs instead of embedding excessive
  sensitive excerpts.
- Split train/validation/test by supplier, scenario, and category when possible.
- Keep a lineage record from dataset row to Waypoint case/run and source model.
- Do not train on unreviewed model outputs as if they were truth.

## Foundry workflow

When enough reviewed examples exist:

1. Baseline the reference model and candidate smaller base model on the eval set.
2. Convert reviewed examples to SFT JSONL.
3. Validate the dataset.
4. Submit an SFT job for the smaller candidate model.
5. Evaluate checkpoints; do not blindly deploy the final checkpoint.
6. Deploy the best checkpoint as a separate task-model deployment.
7. Add model identity, dataset version, eval result, and cost metrics to Assurance Orchestrator
   run metadata.
8. Re-run periodically as Waypoint accumulates new reviewed decisions.
