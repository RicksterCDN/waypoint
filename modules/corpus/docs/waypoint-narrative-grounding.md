# Waypoint narrative grounding

## Where Waypoint fits

Waypoint is **column three** of the broader Road to Start demo story. It is not the whole company narrative, the full product-launch storyline, or the complete supply-chain acceleration arc.

The larger story is about a digitally advanced pharmaceutical manufacturer using AI across business, operations, development, and security to respond to market pressure. Waypoint represents one focused lane inside that story: **contract manufacturing invoice assurance and supplier oversight**.

## Demo framing

The business has already identified pressure to move faster and rely more heavily on contract manufacturers. Waypoint picks up at the point where increased contract manufacturing activity creates risk: more supplier invoices, more contract terms, more approval context, and more chances for leakage or compliance-sensitive mistakes.

Waypoint helps teams inspect supplier invoice decisions against the evidence that should govern payment:

- Master service agreements and contract terms
- Purchase orders and authorized work
- Internal receipts and delivered-vs-billed records
- Batch records and QA release logs
- Production schedules and capacity context
- Supplier correspondence and approval history

Anne's reconciliation model sharpens this further: Waypoint is reconciling **contract pricing, batch records, quality status, milestones, materials, capacity, and invoice lines**. This is not only financial matching; it is operational and financial reconciliation combined.

## Core product promise

Waypoint finds issues in supplier invoices by comparing what was billed against what was contracted, authorized, delivered, and approved.

Examples of findings include:

- Invoice dates that do not match contract-specified billing windows
- Charges that exceed budget or contracted category limits
- Work billed at the wrong contractual rate
- Invoice lines without a matching PO, batch, or receipt
- Duplicate invoices or duplicate QA/release fees
- Off-contract tooling, setup, or process charges
- Delivered-vs-billed mismatches between supplier invoices and internal receipt records
- Items requiring escalation because they touch sensitive product, formulation, or IP context

## Reconciliation domains

The app should make these domains visible in the story and, where useful, in the UI:

- **Commercial terms**: currency, pricing model, minimum order quantity, tiered pricing, escalation rates, volume discounts, and true-ups.
- **Batch records**: batch ID, SKU, actual quantity, release status, rework/rejection state, and released volume.
- **Milestones**: milestone ID, contract amount, approval status, and whether the milestone is billable.
- **Materials**: material consumed, quantity, unit cost, pass-through rules, and variance from expected consumption.
- **Capacity**: reserved capacity, utilized capacity, minimum fees, and unused-capacity charges.
- **Quality**: acceptance criteria, release status, rejection policy, deviations, and whether rejected or reworked batches can be invoiced.
- **Packaging and logistics**: shipment references, packaging charges, shipping cost, and whether logistics fees are contractually allowed.
- **Payment timing**: invoice trigger event, billing window, payment terms, and whether billing is allowed after batch release, shipment, or milestone approval.

## Validation logic

Waypoint should present invoice decisions as the result of concrete validation checks:

- Price matches the contract, tier, escalation clause, or approved pricing adjustment.
- Quantity respects MOQ, released batch volume, and authorized production.
- Only released batches are billable unless the contract explicitly allows another trigger.
- Rejected batches are not billable unless rework or rejection terms allow a specific charge.
- Milestone charges require approved milestone completion.
- Material pass-through charges reconcile to quantity consumed times contracted or approved unit cost.
- Capacity fees follow reserved capacity, utilized capacity, and minimum-fee rules.
- Invoice timing follows the contract trigger event: batch release, shipment, or milestone approval.
- Volume discounts, escalation rates, and true-ups are applied before determining variance.

Reconciliation outputs should use business-facing states such as **matched**, **variance**, and **disputed**, with variance amount, reason, evidence, and recommended next action.

## Business terms to use

Use professional procurement, finance, and regulated-manufacturing language so the story feels credible to business buyers:

- **Invoice assurance**: the product category shorthand for validating invoice accuracy before approval or dispute.
- **Supplier invoice validation**: the operational process Waypoint supports.
- **Contract compliance** or **contractual adherence**: checking billed work against negotiated terms, rates, billing windows, and obligations.
- **Payment leakage** or **contract leakage**: the business value frame for overpayments, missed credits, off-contract charges, and negotiated savings that are not realized.
- **Invoice discrepancy management**: the workflow for routing, explaining, approving, disputing, or recovering exceptions.
- **Procure-to-pay (P2P) controls**: the broader finance/procurement control environment around requisitioning, ordering, receiving, invoicing, and payment.
- **Three-way matching**: matching purchase order, receipt, and invoice. In this demo, Waypoint extends that control with contract and pharma-manufacturing context.
- **Evidence-backed exception**: a finding that includes source-linked documents, reason codes, and recommended next action.
- **Recovery opportunity**: a disputed or recoverable amount tied to an invoice finding.
- **Contract development and manufacturing organization (CDMO)**: the supplier type in the pharma scenario.
- **Quality agreement**: the regulated-manufacturing agreement that defines manufacturing and quality responsibilities between the company and the CDMO.
- **Batch production and control records**: regulated records that document production and control information for each drug batch.
- **Release testing** or **quality release**: testing and approval evidence used before distribution and, in this story, before accepting related invoice charges.
- **Minimum order quantity (MOQ)**: the contractual minimum production or purchase quantity that affects billable volume.
- **True-up**: a later adjustment that reconciles forecast volume, reserved capacity, or estimated charges against actuals.

## Professional ideas to round out the story

- **Move from AP automation to assurance.** The demo should not sound like basic invoice processing. Waypoint is the assurance layer for high-risk, high-context supplier spend.
- **Position exceptions as decisions, not alerts.** Each exception should answer: what happened, why it matters, what evidence supports it, what money or risk is at stake, and what action is recommended.
- **Show a control extension.** Traditional P2P controls match PO, receipt, and invoice. Waypoint adds contract clauses, quality agreements, batch records, QA release, supplier correspondence, and sensitive product context.
- **Separate leakage from risk.** Some findings are financial recovery opportunities; others are compliance, IP, quality, or supplier-governance risks.
- **Use a recovery pipeline.** Treat findings like a managed book of work: detected, triaged, assigned, disputed, credited, approved, escalated, or closed.
- **Make supplier communication part of the product.** The response draft should cite the contract term, invoice line, receipt or batch evidence, and requested supplier action.
- **Make governance visible.** Show audit trail, human acceptance, approvals, policy reasons, and why an item was auto-approved, held, disputed, or escalated.

## Where AI matters

Waypoint should be explicit about which parts need language-model reasoning and which parts do not.

The most valuable LLM-heavy work is **contract-context reasoning**: interpreting invoice language against MSAs, contract clauses, amendments, approval history, and messy supplier context. LLMs also add value when generating grounded explanations and drafting supplier responses or dispute messages.

Many other checks can be handled with deterministic joins, rules, traditional models, or code:

- PO matching
- Receipt matching
- Duplicate detection
- Rate-table checks
- Budget/category threshold checks
- Delivered-vs-billed reconciliation
- MOQ, capacity minimum, approved milestone, and release-status checks
- Pricing adjustment, volume discount, escalation, and true-up calculations
- Known supplier, SKU, batch, and fee-code normalization

## Narrative guardrails

When building the app, keep Waypoint grounded in this lane:

- Do frame Waypoint as the evidence and control surface for contract manufacturing invoice decisions.
- Do show AI pinpointing where contract context changes the decision.
- Do show source-linked evidence, traceability, and controlled action.
- Do show response generation as an output of the investigation.
- Do show operational evidence and financial reconciliation in the same decision.
- Do not imply Waypoint owns the whole Road to Start storyline.
- Do not make Waypoint primarily a generic supply-chain planning tool.
- Do not make every finding feel LLM-only when rules, joins, or models would be more credible.

## Demo artifact naming

Prefix every supplier/customer-specific generated document with the customer ID from the seed data so contracts, invoices, receipts, and evidence stay traceable across the demo.

Examples:

- `sup-001-aster-ridge-biomanufacturing-sow.docx`
- `sup-002-northstar-fill-finish-quality-agreement.docx`
- `sup-014-atlas-regional-manufacturing-invoice-2026-10.docx`

Do not prefix general company policy documents because they apply across suppliers.

Examples:

- `invoice-reconciliation-policy.docx`
- `quality-release-billability-policy.docx`
- `invoice-dispute-and-recovery-procedure.docx`

## One-sentence summary

Waypoint is the contract manufacturing invoice assurance lane of the Road to Start story: it reconciles supplier invoices against contract pricing, POs, receipts, batch records, quality release, milestones, materials, capacity, and timing rules to find leakage, explain the finding, and draft the right response.

## Research anchors

- FDA guidance on contract manufacturing quality agreements says quality agreements help parties define and document manufacturing activities and responsibilities for CGMP compliance.
- 21 CFR 211.188 requires batch production and control records for each batch, including production/control details, dates, equipment, components, control results, yield, labeling records, and investigations.
- 21 CFR 211.165 requires laboratory determination of conformance to final specifications before drug product release.
- Procure-to-pay references emphasize invoice matching, audit trails, compliance controls, fraud prevention, visibility, and expenditure control as core business outcomes.
- Anne's CDMO reconciliation model identifies the core join across contract pricing, batch records, quality status, milestones, materials, capacity, and invoice lines, with matched, variance, and disputed outcomes.
