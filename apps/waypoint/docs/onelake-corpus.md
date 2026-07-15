# OneLake corpus lake + context gateway

Waypoint stores the demo corpus (invoices, contracts, policies, evidence) in a **Microsoft Fabric
OneLake lakehouse** and exposes document text through the API context gateway. This replaces the
old local-worktree seed hack and lets forge expert agents resolve an invoice into supplier + lines
+ linked contract/policy **document text** + evidence in one call.

The lake is **optional**: with no Fabric capacity configured (e.g. a local `aspire run`), the API
degrades gracefully — document endpoints return metadata with `content_source: "uri-only"` and
`text: null`, exactly as before OneLake existed. Nothing 500s on a missing lake.

---

## Architecture

| Concern | Where |
| --- | --- |
| Fabric capacity (**F64** default) | `infra/fabric-capacity.bicep` (`Microsoft.Fabric/capacities`) — ARM/Bicep-provisionable |
| Workspace + lakehouse + MI role | `infra/scripts/provision-fabric.sh` (Fabric REST, azd `postprovision` hook in `azure.yaml`) — **not** Bicep-provisionable |
| Fabric IQ (Direct Lake semantic model + Data Agent) | `infra/scripts/provision-fabric-iq.sh` (Fabric REST) — see [`fabric-iq.md`](./fabric-iq.md) |
| Capacity cost control (suspend/resume/scale) | `infra/scripts/fabric-capacity-control.sh` + `.github/workflows/fabric-capacity-scheduler.yml` |
| Provisioning gate | `Waypoint:Fabric:ProvisionEnabled` (+ `Waypoint:Fabric:IqEnabled`) in `apphost.cs` (publish mode only) |
| Runtime OneLake reads | `api/app/common/onelake.py` (`azure-storage-file-datalake` over the OneLake DFS endpoint, `DefaultAzureCredential`) |
| Settings | `APP_ONELAKE_ACCOUNT_URL`, `APP_ONELAKE_WORKSPACE`, `APP_ONELAKE_LAKEHOUSE`, `APP_ONELAKE_CORPUS_PREFIX` |

OneLake honors **Fabric workspace roles**, not Azure RBAC. The Waypoint container-app managed
identity must be a workspace **Member** (the postprovision hook does this) to read OneLake Files.

---

## Local development

When you run the API **locally** (a direct `uvicorn`, or `aspire run` — both execute from `./api`),
configuration comes from a gitignored **`api/.env`** loaded by `app/common/settings.py`. This is the
local counterpart to the deploy path; it is never used in deployed/keystone runs (containers ship no
`api/.env`, and `apphost.cs` sets real `APP_ONELAKE_*` env vars, which take precedence over a dotenv
file in pydantic-settings).

To point a local API at the corpus lake, **resolve the workspace GUID from its display name** — never
paste a GUID, since it is tenant/deploy-specific and changes if the workspace is recreated:

```sh
az login                                   # as a Member of the Fabric corpus workspace
cp api/.env.example api/.env               # first time only
./infra/scripts/onelake-local-env.sh       # idempotent: upserts APP_ONELAKE_* into api/.env
```

`onelake-local-env.sh` is the **read-only** local sibling of `provision-fabric.sh`: it creates and
mutates nothing, it just looks the workspace up by display name (default `waypoint-corpus`) and writes
the four `APP_ONELAKE_*` values into `api/.env`. Re-run it any time (e.g. after the workspace is
recreated) — it is idempotent. Override the target with env vars, e.g.
`WAYPOINT_FABRIC_WORKSPACE_NAME=my-corpus ./infra/scripts/onelake-local-env.sh`.

Then restart the API; the lightbox document/PDF previews resolve real bytes from OneLake. With
`api/.env` absent or `APP_ONELAKE_WORKSPACE` empty, the API degrades gracefully to URI-only metadata.

---

## One-time tenant bootstrap

**In the `caldova` tenant these Fabric Developer settings are already Enabled for the entire
organization, so no bootstrap action is required here — just verify.** The settings are listed for
portability to other tenants; a service principal can only call Fabric REST (create the workspace +
lakehouse, assign roles) when they are enabled.

1. **Verify Fabric admin Developer settings** (Fabric Admin Portal → Tenant settings → Developer
   settings). These must be enabled for the deploy identity + container MI so a service principal
   can call Fabric REST (create the workspace + lakehouse, assign roles):
   - **"Service principals can call Fabric public APIs"** — Enabled
   - **"Service principals can create workspaces, connections, and deployment pipelines"** — Enabled
   - **"Allow service principals to create and use profiles"** — Enabled
   - **"Block ResourceKey Authentication"** — Disabled

   For **`caldova` these are already enabled org-wide, so no security group is needed.** (For
   portability to another tenant, either enable org-wide or scope to a security group containing
   **both** the CI/OIDC deploy SP and the Waypoint container-app managed identity.)
2. Provide the **capacity admin** object id(s) for `infra/fabric-capacity.bicep`
   (`administratorMembersJson`) — typically the global admin and/or the deploy identity.
3. After the first `azd provision`, confirm the workspace + lakehouse exist and the Waypoint MI is a
   workspace **Member**. The org-wide setting lets the SP *call* the APIs, but OneLake data-plane
   reads still require the per-workspace **role assignment** — the postprovision hook does this
   automatically (`POST /v1/workspaces/{id}/roleAssignments`). The hook is idempotent and
   re-runnable.

---

## Enabling provisioning

Set these before `azd provision` / `aspire deploy` (the capacity bills while Active — **F64** is the
default so the Fabric IQ Data Agent/Copilot works; suspend or scale it when idle, see below):

```sh
azd env set Waypoint:Fabric:ProvisionEnabled true
azd env set Waypoint:Fabric:CapacityName    waypointcorpus
azd env set Waypoint:Fabric:SkuName         F64
azd env set Waypoint:Fabric:LakehouseName   corpus
azd env set Waypoint:Fabric:AdministratorMembersJson '["<admin-object-id>"]'

# Consumed by the postprovision hook (infra/scripts/provision-fabric.sh):
azd env set WAYPOINT_FABRIC_PROVISION_ENABLED true
azd env set WAYPOINT_FABRIC_CAPACITY_ID    "<capacity ARM id from the bicep deployment>"
azd env set WAYPOINT_FABRIC_WORKSPACE_NAME waypoint-corpus
azd env set WAYPOINT_FABRIC_LAKEHOUSE_NAME corpus
azd env set WAYPOINT_FABRIC_MI_PRINCIPAL_ID "<waypoint container-app MI object id>"
```

After the hook runs it publishes `APP_ONELAKE_WORKSPACE` (the workspace GUID) back to the azd
environment; the next deploy passes it to the API container as `APP_ONELAKE_WORKSPACE`.

Region: **Sweden Central** (co-located with the rest of Waypoint; verified to support all Fabric
workloads, not Power BI only).

### Re-attaching an existing workspace without re-provisioning

`fabric_provision_enabled` stands up (or idempotently re-confirms) the capacity, workspace,
lakehouse, and role grants — it is the heavy first-time path, used by the keystone "one-click"
umbrella. A plain push to `main` (or `workflow_dispatch`) does **not** pass that input, so it never
wired OneLake onto the API, leaving `APP_ONELAKE_*` empty and the PDF viewer degraded to the
"document not available" fallback on the live site.

To make every standalone deploy attach an **already-provisioned** workspace — idempotently, without
paying for re-provisioning — set the workspace GUID as a repo variable:

```sh
gh variable set WAYPOINT_FABRIC_WORKSPACE --body "<workspace-guid>"
```

On each deploy the workflow then:

1. Passes the GUID into `apphost.cs` (via `Waypoint:Fabric:Workspace`), so `aspire deploy` publishes
   the correct `APP_ONELAKE_ACCOUNT_URL` / `APP_ONELAKE_WORKSPACE` / `APP_ONELAKE_LAKEHOUSE` /
   `APP_ONELAKE_CORPUS_PREFIX` (the account-url/lakehouse/prefix defaults are now applied even when CI
   passes empty strings for them).
2. Runs the **"Attach existing OneLake workspace to API"** step, which sets `APP_ONELAKE_WORKSPACE`
   plus `AZURE_CLIENT_ID` (the API container's user-assigned MI client id) so `DefaultAzureCredential`
   selects the right identity for OneLake reads.

> **Prerequisite:** the API container-app managed identity must already be a **Member** of the
> workspace. The initial `fabric_provision_enabled` run (or keystone one-click) grants this via
> `provision-fabric.sh`; the re-attach path assumes it persists. Leave `WAYPOINT_FABRIC_WORKSPACE`
> unset to keep standalone deploys OneLake-free.

### Cost control — suspend / resume / scale

F64 is left **Active** after provision so the `/context` endpoint, OneLake reads, and the Fabric IQ
Data Agent work during active dev/demos. It bills while Active, so cost control is idempotent and
scripted — use `infra/scripts/fabric-capacity-control.sh` rather than ad-hoc `az` calls:

```sh
export WAYPOINT_FABRIC_CAPACITY_NAME=waypointcorpus
export AZURE_RESOURCE_GROUP=<rg>

./infra/scripts/fabric-capacity-control.sh status     # print state + SKU
./infra/scripts/fabric-capacity-control.sh suspend    # stop compute billing when idle
./infra/scripts/fabric-capacity-control.sh resume     # before a demo
./infra/scripts/fabric-capacity-control.sh scale F2   # stay queryable but cheaper
```

Each action is a no-op when the capacity is already in the target state. While suspended, OneLake
reads fail and the API falls back to URI-only metadata.

`.github/workflows/fabric-capacity-scheduler.yml` automates this: a nightly cron **auto-suspends**,
and `workflow_dispatch` runs `status | suspend | resume | scale` on demand. Resume is never
scheduled. See [`fabric-iq.md`](./fabric-iq.md) for the full runbook.

---

## Files / Tables layout contract (for the ledgerfield session, step 3)

The ledgerfield session owns the actual corpus upload and Delta writes. It MUST write to this exact
layout so Waypoint's reader resolves documents and the [Fabric IQ](./fabric-iq.md) semantic model +
Data Agent can query the tables via the SQL endpoint.

### Lakehouse `Files/` — unstructured documents

```
Files/corpus/
  contracts/            contract markdown + docx     e.g. cmo-001-msa.md, cmo-001-msa.docx
  policies/             policy markdown + docx
  invoices/
    html/               invoice HTML                 INV-2026-08034.html
    pdf/                invoice PDF                  INV-2026-08034.pdf
  evidence/             evidence documents
  seed/
    waypoint-seed.json  canonical seed (replaces the local-worktree hack)
```

Waypoint resolves a stored URI to a lake path best-effort (see `resolve_corpus_path` in
`api/app/common/onelake.py`). Unmappable URIs degrade to `content_source: "uri-only"`.

### Lakehouse `Tables/` — Delta, mirroring `api/app/modules/records/schemas.py`

Column names/types align 1:1 with the Postgres JSONB schema so Delta + Postgres stay consistent:

- **`invoices`** — `id`, `supplier_id`, `scenario_id`, `invoice_number`, `invoice_date`, `status`,
  `currency`, `total_amount`, `html_uri`, `pdf_uri`
- **`invoice_lines`** — `id`, `invoice_id`, `description`, `quantity`, `unit_price`, `amount`, `sku`,
  `purchase_order`
- **`reconciliation_findings`** — `id`, `invoice_id`, `scenario_id`, `category`, `severity`,
  `status`, `summary`, `overpayment_amount`, `contract_document_ids`, `policy_ids`, `evidence_ids`,
  `basis_summary`
- *(optional)* `suppliers`, `contract_documents`, `policies`, `evidence_references`

### Seed delivery

Point the seed at the lake instead of the local worktree:

```sh
azd env set Waypoint:Ledgerfield:SeedUri onelake://Files/corpus/seed/waypoint-seed.json
```

`api/app/common/database.py` reads `onelake://` seed URIs through `OneLakeClient`. The
`POST /api/admin/seed/ledgerfield` endpoint remains available for runtime re-seed. Local-path and
blob seed sources are retained for back-compat.

---

## API surface

All endpoints are `require_reader`.

- **`GET /api/invoices/{id}/context`** — the one-call gateway forge experts hit. Returns the invoice
  (supplier + lines + findings + evidence) plus linked `contract_documents` and `policies` **with
  document text included by default** (that is the bundle's purpose), plus cases / allowed actions /
  redactions.
- **`GET /api/contract-documents/{id}`** — metadata by default; add **`?include_content=true`** to
  include `text` + `content_source`.
- **`GET /api/policies/{id}`** — metadata by default; add **`?include_content=true`** to include
  `text` + `content_source`.

`content_source` is `"onelake"` when text was read from the lake, or `"uri-only"` when no lake is
configured / the file is absent (text is then `null`).
