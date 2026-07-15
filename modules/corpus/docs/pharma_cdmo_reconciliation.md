# Pharma CDMO Contract: Invoice Reconciliation Data Model

## Purpose
This document defines the contractual data structures and validation logic required to reconcile invoices from a Contract Development and Manufacturing Organization (CDMO).

---

# 1. Core Data Domains

Invoice reconciliation in pharma manufacturing requires joining the following domains:

Contract Pricing + Batch Records + Quality Status + Milestones + Materials + Capacity + Invoice Lines

---

# 2. Contract Data Model

## Commercial Terms
currency: string
pricing_model: unit | batch | milestone | hybrid
MOQ: integer

## Batch Records
batch_id: string
sku_id: string
actual_quantity: integer
release_status: released | rejected | reworked | pending

## Milestones
milestone_id: string
amount: decimal
approval_status: approved | pending | rejected

## Materials
material_id: string
quantity_consumed: decimal
unit_cost: decimal

## Capacity
reserved_capacity: integer
utilized_capacity: integer
minimum_fee: decimal

## Quality
acceptance_criteria: string
rejection_policy: string

## Packaging & Logistics
shipment_id: string
shipping_cost: decimal

## Payment Terms
invoice_trigger: batch_release | shipment | milestone
payment_due_days: integer

## Pricing Adjustments
escalation_rate: decimal
volume_discounts: threshold + discount

## True-Ups
forecast_volume vs actual_volume adjustment

---

# 3. Invoice Schema

invoice_id: string
invoice_date: date
line_items:
- type: unit | milestone | material | capacity | logistics
- reference_id
- quantity
- unit_price
- total_amount

---

# 4. Reconciliation Output
status: matched | variance | disputed
variance_amount
reason

---

# 5. Validation Rules

Price must match contract or tier
Quantity must respect MOQ and released batch volume
Only released batches billable
Milestones must be approved
Material cost = qty x cost
Capacity fees follow minimum rules
Rejected batches not billable
Invoice only allowed after trigger event
Apply pricing adjustments
Apply true-ups

---

# 6. Pseudocode

for invoice:
  for line:
    validate_price
    validate_quantity
    validate_batch
    validate_milestone
    validate_quality

  compute_variance
  assign status

---

# TL;DR

Reconcile using:
- Pricing
- Batch + release status
- Milestones
- Materials
- Capacity
- Quality
- Timing

This is operational + financial reconciliation combined.
