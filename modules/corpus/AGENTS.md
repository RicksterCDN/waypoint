# Ledgerfield agent guide

Ledgerfield provides reusable fictional data, source documents, invoice artifacts, and local database seeding for Waypoint-style agentic demos.

Key rules for agents:

- Treat JSON and Markdown sources as canonical. Generated `.docx`, invoice HTML, and invoice PDF files are outputs.
- Use `uv` and the `ledgerfield` Python CLI; do not add parallel scripting stacks unless explicitly requested.
- Preserve the evidence-puzzle separation: source contracts/policies contain realistic clauses, while scenario metadata contains expected outcomes and evidence mappings.
- Supplier-facing invoices must not reveal internal decision state, expected findings, or demo answers.
- Keep invoice realism data split between stable supplier `billing_profile` defaults and invoice-specific `document_metadata`.
- Prefer FastAPI endpoints and CLI commands over hand-editing generated outputs.

Useful commands:

```powershell
uv sync
uv run ledgerfield setup
uv run ledgerfield generate-all
uv run ledgerfield generate-waypoint-seed
uv run ledgerfield doctor
uv run ledgerfield serve
uv run ledgerfield generate-invoices --format html
uv run ledgerfield generate-docx
docker compose up -d postgres
uv run ledgerfield db seed --if-needed --append-cycles 1
```

Agent-facing HTTP surface:

- `GET /api/agent/manifest` discovers canonical source paths, commands, endpoints, and generated artifact folders.
- `GET /api/agent/doctor` returns source counts, generated artifact counts, and PDF page-size status.
- `POST /api/agent/bootstrap` generates artifacts, and can optionally seed Postgres with `seed_db=true`.

Waypoint seed export:

- `uv run ledgerfield generate-waypoint-seed` writes `data/waypoint/waypoint-seed.json`, the import payload Waypoint should send to its own admin endpoint instead of copying Ledgerfield corpus files into the app repo.

Portable skill:

- `.github/skills/ledgerfield-demo-data/SKILL.md` can be copied into a consuming project's `.github/skills/` directory so agents in that project know how to discover, generate, validate, and seed Ledgerfield data.
