# Ledgerfield Copilot instructions

Ledgerfield is the canonical demo-data and artifact-generation repository for the Waypoint contract manufacturing invoice assurance demo. Treat it as a shared corpus/tooling repo, not as the Waypoint application itself.

Use Python with `uv` for all tooling. Do not reintroduce PowerShell document conversion scripts. Run commands from the repository root:

```powershell
uv sync
uv run ledgerfield setup
uv run ledgerfield generate-all
uv run ledgerfield generate-waypoint-seed
uv run ledgerfield doctor
uv run ledgerfield generate-invoices --format html
uv run ledgerfield generate-docx
uv run ledgerfield serve
```

Canonical invoice facts live in `data/invoices/supplier-invoices.json`. Generated invoice HTML/PDF files under `data/invoices/html/` and `data/invoices/pdf/` are disposable outputs and should not be committed.

Keep supplier billing defaults in each invoice generation profile's `billing_profile`. Keep transaction-specific invoice details in each invoice's `document_metadata`. Do not put expected reconciliation decisions into supplier-facing invoices, contracts, or policies.

Contracts and policies are evidence puzzles for LLM reasoning. Keep expected findings, decision states, leakage categories, and evidence mappings in `data/scenarios/invoice-assurance-scenarios.json`.

Supplier-specific source documents and generated artifacts must use the supplier ID prefix, for example `sup-001-aster-ridge-biomanufacturing-sow.md`. General company policy documents should not be supplier-prefixed.

When changing data generation, validate with:

```powershell
uv run python -m json.tool data\invoices\supplier-invoices.json
uv run python -m compileall -q src
uv run ledgerfield generate-invoices --format html
git diff --check
```

For local Postgres demo data:

```powershell
docker compose up -d postgres
uv run ledgerfield db seed --if-needed --append-cycles 1
uv run ledgerfield db append --cycles 2
```

Consuming apps and agents should prefer `GET /api/agent/manifest`, `GET /api/agent/doctor`, and `POST /api/agent/bootstrap` for discovery, status, and one-call local artifact/bootstrap workflows.

Waypoint should import generated `data/waypoint/waypoint-seed.json` through its own admin endpoint rather than copying Ledgerfield source corpus files into the Waypoint repo.
