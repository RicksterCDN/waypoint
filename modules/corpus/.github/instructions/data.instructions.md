---
applyTo: "data/**/*.json,data/**/*.md"
---

Ledgerfield data files are source-of-truth demo corpus files. Keep them readable, deterministic, and reviewable in diffs.

For invoice JSON:

- Keep `data/invoices/supplier-invoices.json` as canonical invoice facts.
- Stable supplier billing identity belongs in `document_generation.profiles[].billing_profile`.
- Invoice-specific details belong in `invoices[].document_metadata`.
- Non-matched invoice lines should reference `scenario_id` values from `data/scenarios/invoice-assurance-scenarios.json`.
- Do not expose internal decision state or expected findings in supplier-facing rendered invoice text.

For contracts and policies:

- Keep contracts and policies realistic and concise.
- Do not put expected demo findings inside contract or policy prose.
- Supplier-specific documents must be prefixed with the supplier ID.
