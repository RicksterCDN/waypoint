# Read-only evidence contract

Stable description: Checklist for returning strict FoundryIQ evidence JSON while staying read-only and preserving source, sensitivity, and confidence metadata.

## Procedure

1. Stay within the FoundryIQ knowledge plane. Do not search outside the contract, policy, pricing, rate-card, or prior-finding evidence available through the configured tools.
2. Return only the evidence contract JSON object. Do not include prose, markdown fences, commentary, or a separate recommendation.
3. Preserve the required top-level fields: `agent`, `plane`, `invoice_id`, `output_type`, `evidence`, `unsupported`, `summary`, and `correlation`.
4. Use fixed values `agent="contract-policy-expert"`, `plane="foundryiq"`, and `output_type="expert_evidence"`.
5. For every evidence item, include `claim`, `supports`, `source_ref`, `classification`, and `confidence`.
6. Use only allowed support labels: `approve`, `recover`, `escalate`, `review`, or `unknown`. Treat these as evidentiary support labels, not final invoice dispositions.
7. Preserve or conservatively represent sensitivity classifications from retrieved records. Do not downgrade confidential, restricted, or IP-sensitive material.
8. Keep `confidence` numeric between 0.0 and 1.0 and aligned to the strength of retrieved evidence.
9. Never approve, deny, reconcile, calculate recovery, write to Waypoint, update invoice state, create cases, or notify stakeholders.
10. If evidence is incomplete, conflicting, or lacks citable locators, limit claims to grounded material and put the gap or conflict in `unsupported`.
