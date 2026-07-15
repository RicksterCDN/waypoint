# Shared demo data

Waypoint uses the shared demo-data corpus in `caldova/waypoint`.

That repository is the source of truth for supplier records, contract source documents, policy source documents, invoice-assurance scenarios, and generated document artifacts used by the contract manufacturing invoice assurance demo.

Waypoint owns the authenticated runtime API and data layer that the web app, agents, and external projects call. Runtime callers should use Waypoint `/api/*` endpoints rather than calling Ledgerfield directly, except for corpus/tooling workflows that generate or refresh shared seed artifacts.

The built-in Waypoint seed is intentionally small and validates the full runtime shape: 2 suppliers, 1 contract document, 1 policy, 1 scenario, 1 invoice, 2 invoice lines, 1 reconciliation finding, and 2 evidence references. Larger demo sets should be generated in Ledgerfield and imported through Waypoint's admin seed endpoint.

For local richer demos, generate Ledgerfield's Waypoint seed (`uv run ledgerfield generate-waypoint-seed`) and point Waypoint at the generated JSON with `APP_LEDGERFIELD_SEED_PATH` or Aspire configuration `Waypoint:Ledgerfield:SeedPath`. Local Aspire run mode also auto-discovers `waypoint-seed.json` under sibling Ledgerfield worktrees in `.copilot\copilot-worktrees\ledgerfield` or `.copilot\repos\ledgerfield`. When this path is configured or auto-discovered, Waypoint loads the Ledgerfield seed instead of the built-in fallback on fresh startup.

Production should not implicitly load demo data on startup. Production population should use an explicit admin-authenticated import to `/api/admin/seed/ledgerfield`, typically from a Ledgerfield release artifact. The deployed API uses an Aspire-provisioned PostgreSQL-compatible database and will not fall back to local sample data in publish mode.

Ledgerfield-owned seed findings should include first-class Waypoint basis fields:

- `contract_document_ids`: contract/source-document records that justify the finding
- `policy_ids`: policy records that justify the finding
- `basis_summary`: short display text describing the contract/policy basis

Waypoint validates those references during import and surfaces them through `/api/invoice-decisions` and invoice detail routes so the app can show whether each invoice problem is contract-backed, policy-backed, or both.

Keep app-specific implementation notes in this repository. Keep reusable demo data, evidence documents, and scenario metadata in `ledgerfield` to avoid drift across `caldova` projects.
