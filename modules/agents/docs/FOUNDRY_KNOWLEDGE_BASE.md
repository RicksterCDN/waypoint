# Foundry contracts knowledge base

This note captures the Forge-owned resource changes and automation gap for the
Ledgerfield contracts knowledge base used by `contract-policy-expert`.

## Ownership

Forge owns the Foundry resources:

- Azure AI Foundry / AI Services project
- hosted agents
- Azure AI Search service and Foundry Search connection
- Foundry RemoteTool MCP connection used by `contract-policy-expert`
- model deployments needed by agents and knowledge-base ingestion

Ledgerfield owns the source corpus. Waypoint owns the runtime API/database and
OneLake corpus. Keystone can orchestrate cross-repo sequencing, but should not
own the Foundry resources.

## Resource changes

The knowledge base is now named `contracts-kb` instead of the placeholder
`hopper-kb`.

Forge provision now expects two model deployments:

| Deployment | Default | Purpose |
| --- | --- | --- |
| Chat | `gpt-5.5` by CI default | Hosted agents and KB answer synthesis |
| Embedding | `text-embedding-3-large` | Azure AI Search knowledge-source ingestion |

The deploy workflow seeds these azd values:

- `AZURE_AI_EMBEDDING_DEPLOYMENT_NAME`
- `EMBEDDING_MODEL_NAME`
- `EMBEDDING_MODEL_SKU_NAME`
- `EMBEDDING_MODEL_CAPACITY`

## Automation resource index

These are the resources/configuration items introduced or changed by the
contracts KB work that may need automation coverage.

| Item | Type | Owner | Current automation | Automation gap |
| --- | --- | --- | --- | --- |
| `contracts-kb` | Azure AI Search knowledge base | Forge | `initialize_contracts_kb.py` creates/updates it idempotently; runnable via `make contracts-kb-seed` or `contracts-kb-seed.yml` | None — corpus must exist first (Ledgerfield upload) |
| `contracts-ks` | Azure AI Search blob knowledge source | Forge | Same initializer + seed workflow/Make target create/update it idempotently | None — depends on uploaded corpus |
| `contracts-ks-indexer` | Auto-generated Search indexer | Forge (Search) | Daily `P1D` schedule; immediate reindex via `make contracts-kb-reindex` / `--run-indexer` | None |
| `kb-mcp-connection` | Foundry `RemoteTool` project connection | Forge | Bicep creates the connection; initializer refreshes metadata/target | None |
| `text-embedding-3-large` | AI Services model deployment | Forge | Default Bicep model deployment + deploy workflow azd env | Existing environments need provision rerun; quota/capacity failures need CI surfacing |
| `knowledge` | Azure Storage blob container | Forge | Search Bicep module creates container; name exposed as `AZURE_AI_SEARCH_CONTAINER_NAME` | Corpus upload owned by Ledgerfield |
| Blob soft-delete | Storage account `blobServices/default` `deleteRetentionPolicy` | Forge | Enabled (7d) in `storage.bicep` (`enableBlobSoftDelete`); paired with the datasource deletion-detection policy set by the initializer | Existing environments need a provision rerun to enable |
| Deployer/user Storage RBAC | `Storage Blob Data Contributor` on Forge storage | Forge | Granted to `principalId` in Search Bicep module | Existing environments may need a provision rerun |
| Search-to-AI Services RBAC | `Cognitive Services User` and `Foundry User` for Search managed identity | Forge | Added to Search Bicep module | Required for Search knowledge-source embedding generation and Foundry project/account data-plane access |
| Ledgerfield contracts | Source corpus artifact | Ledgerfield | `ledgerfield upload-contracts-kb` CLI + `contracts-kb-upload.yml` sync blobs | Owned by Ledgerfield |
| `scripts\initialize_contracts_kb.py` | Forge data-plane initializer | Forge | Wired to `contracts-kb-seed.yml` (dispatch + call) and `make contracts-kb-seed`; `--skip-upload` decouples it from a Ledgerfield checkout | None |

Recommended first automation increment: add a manual Forge workflow that accepts a
Ledgerfield ref/path and runs `scripts\initialize_contracts_kb.py` after
provision. Keystone can later call the same workflow once Ledgerfield publishes a
stable corpus artifact.

## Data-plane initialization

Bicep creates the Search service, storage container, Search connection, and MCP
connection target. It does not populate the knowledge base.

The knowledge base (`contracts-kb`), knowledge source (`contracts-ks`), the
auto-generated indexer (`contracts-ks-indexer`), and the `kb-mcp-connection`
target are now **code-managed** — created idempotently, with no portal steps —
by `scripts/initialize_contracts_kb.py`. It can run three ways:

- `.github/workflows/contracts-kb-seed.yml` (opt-in `workflow_dispatch`, and
  `workflow_call` for the Keystone umbrella).
- `make contracts-kb-seed` (standalone from a Forge checkout — see below).
- direct invocation of the script.

### No-portal standalone seed from Forge

After Ledgerfield has synced the contract corpus into the storage container
(via `ledgerfield upload-contracts-kb` / its `contracts-kb-upload.yml`), ensure
the KB objects and reindex — all coordinates resolve from the azd env outputs:

```bash
az login
azd env select forge
make contracts-kb-seed          # ensure contracts-kb / contracts-ks / MCP + reindex
make contracts-kb-reindex       # (optional) re-run contracts-ks-indexer only
```

The deploy identity already holds the roles this needs (granted in
`infra/core/search/azure_ai_search.bicep`): `Storage Blob Data Contributor`,
`Search Service Contributor`, and `Search Index Data Contributor`.

Provision exposes these azd outputs for Keystone / Ledgerfield to consume:

| azd output | Example |
| --- | --- |
| `AZURE_STORAGE_ACCOUNT_NAME` | `stwi2egf4sh4hfq` |
| `AZURE_AI_SEARCH_CONTAINER_NAME` | `knowledge` |
| `AZURE_AI_SEARCH_SERVICE_ENDPOINT` | `https://search-wi2egf4sh4hfq.search.windows.net` |
| `AZURE_AI_SEARCH_KNOWLEDGE_SOURCE_NAME` | `contracts-ks` |
| `AZURE_AI_SEARCH_INDEXER_NAME` | `contracts-ks-indexer` |
| `AZURE_AI_SEARCH_KNOWLEDGE_BASE_NAME` | `contracts-kb` |

### Self-contained Forge upload (no Ledgerfield workflow)

If you want Forge to also upload from a sibling Ledgerfield checkout rather than
relying on the Ledgerfield upload tooling:

```bash
make contracts-kb-seed SKIP_UPLOAD=false LEDGERFIELD_ROOT=../ledgerfield
# add PRUNE_STALE=true to also delete blobs left over from a prefix rename
make contracts-kb-seed SKIP_UPLOAD=false LEDGERFIELD_ROOT=../ledgerfield PRUNE_STALE=true
```

`--prune-stale` (Make: `PRUNE_STALE=true`) deletes blobs under `contracts/` that
are not part of the current upload set, so a `cmo-*` → `sup-*` rename does not
leave both sets indexed. It applies only to the standalone upload path; the
Keystone path relies on Ledgerfield's self-cleaning `upload-contracts-kb` sync.

### Reindex mechanism

The `contracts-ks` knowledge source auto-generates an Azure AI Search datasource,
index, skillset, and a **system-named indexer** (conventionally
`contracts-ks-indexer`). It carries a daily (`P1D`) refresh schedule, so the
corpus re-ingests automatically once a day. The primary code-managed reingest is
re-running the initializer, which re-PUTs the knowledge source and triggers
ingestion — this does not depend on knowing the indexer name. For an immediate
refresh you can additionally trigger the indexer with `make contracts-kb-reindex`,
the `--run-indexer` flag (which resolves the indexer name dynamically from the
live indexer list), or `run_indexer: true` on the seed workflow.

### Deletion detection (rename cleanup)

Foundry IQ generates the blob datasource with **no** `dataDeletionDetectionPolicy`,
so deleting a blob (for example the `cmo-*` → `sup-*` rename) leaves an **orphaned
document** in the index — a plain indexer run adds the new blobs but never removes
the stale ones. Two pieces, both code-managed, make a normal indexer run reconcile
deletions with no extra pipeline stage:

1. **Storage** — `infra/core/storage/storage.bicep` enables blob **soft-delete**
   (`enableBlobSoftDelete`, 7-day retention) on the knowledge storage account, so a
   normal `az storage blob delete` becomes a *soft*-delete the indexer can detect.
2. **Datasource** — the initializer patches the auto-generated datasource with a
   `NativeBlobSoftDeleteDeletionDetectionPolicy` (`_ensure_deletion_detection`). It
   is best-effort, idempotent (only PUTs when missing/different), and re-applied on
   every run so it self-heals if Foundry IQ regenerates the datasource on a KS
   re-PUT. Opt out with `--skip-deletion-detection` /
   `AZURE_AI_SEARCH_SKIP_DELETION_DETECTION=1`.

> **Verified behavior (why the self-heal matters):** Foundry IQ *regenerates the
> datasource on every knowledge-source PUT* — a KS PUT reverts the datasource's
> `dataDeletionDetectionPolicy` to `null`. The policy therefore only survives if it
> is re-applied after each KS PUT, which the initializer does (with an
> apply-verify-retry loop that tolerates the datasource being provisioned
> asynchronously). A **plain indexer run does not touch the datasource**, so the
> policy persists across every steady-state reindex once set. In the Keystone
> 2-touchpoint flow neither `azd provision` (it does not PUT the knowledge source)
> nor Ledgerfield (blob sync + plain indexer run) re-PUTs the KS, so the policy set
> at KB/KS creation persists straight through to Ledgerfield's reindex.
>
> Empirically validated live against the real cmo-* corpus: deleting a real
> `cmo-*.md` blob and running a **plain** `contracts-ks-indexer` run dropped its
> index document (e.g. `docs/$count` 6→5, `search=cmo` 5→4, the specific doc gone).

With both in place, Ledgerfield's self-cleaning sync (which hard-deletes stale
blobs, now soft-deletes) followed by its own `contracts-ks-indexer` run fully
removes the orphaned `cmo-*` index documents — so the Keystone flow stays **2
touchpoints** (no post-sync Forge reingest stage required). The datasource policy
is applied once at KB/KS creation (seed) and persists across steady-state indexer
runs.

### Workflow outputs (for Keystone → Ledgerfield)

Keystone reads the contracts-kb coordinates from the **forge-deploy workflow**
(`.github/workflows/deploy.yml`) — the provision touchpoint it already calls —
and passes them to Ledgerfield's `contracts-kb-upload`. `deploy.yml` exposes them
as `workflow_call` outputs (resolved from the live azd/ARM deployment outputs,
never hardcoded):

| `deploy.yml` output | Source azd output | Example |
| --- | --- | --- |
| `contracts_kb_storage_account` | `AZURE_STORAGE_ACCOUNT_NAME` | `stwi2egf4sh4hfq` |
| `contracts_kb_container` | `AZURE_AI_SEARCH_CONTAINER_NAME` | `knowledge` |
| `contracts_kb_search_endpoint` | `AZURE_AI_SEARCH_SERVICE_ENDPOINT` | `https://search-wi2egf4sh4hfq.search.windows.net` |
| `contracts_kb_indexer` | `AZURE_AI_SEARCH_INDEXER_NAME` | `contracts-ks-indexer` |

The opt-in `contracts-kb-seed.yml` workflow exposes the same four
`workflow_call` outputs (for the greenfield KB/KS-creation path), so either
workflow can feed the uploader:

| Output | Example |
| --- | --- |
| `contracts_kb_storage_account` | `stwi2egf4sh4hfq` |
| `contracts_kb_container` | `knowledge` |
| `contracts_kb_search_endpoint` | `https://search-wi2egf4sh4hfq.search.windows.net` |
| `contracts_kb_indexer` | `contracts-ks-indexer` |

Keystone is a **2-touchpoint** flow: forge provision (`deploy.yml`, emits the
coordinates above + ensures roles) → Ledgerfield `contracts-kb-upload`
(self-cleaning blob sync, then an immediate `POST /indexers('contracts-ks-indexer')/run`
when passed `--search-endpoint`). Forge does not expose a separate reingest
entrypoint for Keystone — the deterministic indexer + Ledgerfield's post-sync run
and the daily `P1D` schedule cover refresh.

The legacy full manual initializer (uploads Ledgerfield Markdown itself) is:

```powershell
python scripts\initialize_contracts_kb.py `
  --resource-group rg-forge `
  --chat-deployment-name gpt-5-mini `
  --embedding-deployment-name text-embedding-3-large
```

The script:

1. Finds the sibling Ledgerfield repo (only when not `--skip-upload`).
2. Stages Ledgerfield contract Markdown from:
   - `data\contracts\source-markdown`
3. Uploads them into Forge storage container `knowledge` under the `contracts`
   folder.
4. Creates/updates Search knowledge source `contracts-ks`.
5. Creates/updates Search knowledge base `contracts-kb`.
6. Ensures the datasource's native blob soft-delete deletion-detection policy so
   deleted blobs (rename cleanup) drop their orphaned index docs on the next
   indexer run (skip with `--skip-deletion-detection`).
7. Updates Foundry RemoteTool connection `kb-mcp-connection` to point at the
   `contracts-kb` MCP endpoint.
8. Optionally (`--run-indexer`) triggers `contracts-ks-indexer` for an immediate
   reindex.

## Supplier-first retrieval

The contracts KB should not be the first place that discovers which supplier an
invoice belongs to. The invoice workflow should first resolve a canonical
supplier through deterministic Waypoint/Ledgerfield metadata, then ask the KB for
contract evidence scoped to that supplier.

Current behavior:

- The initializer uploads only Ledgerfield contract Markdown files.
- The Search knowledge source scans only the `contracts` folder in the storage
  container.
- KB retrieval instructions tell the model to prefer evidence for the supplier
  already resolved by the caller and avoid cross-supplier evidence.

Principled automation target:

1. Ledgerfield publishes a versioned contract manifest with canonical
   `supplier_id`, `supplier_name`, aliases, and contract file paths.
2. Waypoint or the invoice extraction stage resolves the invoice supplier to that
   canonical supplier record.
3. Forge ingests the manifest-selected contract files and preserves supplier
   identity in blob paths or Search metadata.
4. FoundryIQ queries the KB with the resolved supplier name/aliases and, when the
   preview KB APIs expose stable filters, a supplier filter rather than a broad
   corpus search.

## Known setup issues

The first live initializer run failed during blob upload with Azure CLI
`upload-batch --auth-mode login` because the signed-in user did not have
`Storage Blob Data Contributor` on the Forge storage account. Container metadata
was readable, but blob upload still required Storage data-plane RBAC.

The second live initializer run uploaded the corpus but failed when creating
`contracts-ks` because Azure AI Search could not connect to AI Services with its
managed identity. The error requested `Cognitive Services User` on the AI
Services account; `Cognitive Services OpenAI User` was not sufficient for this
knowledge-source operation.

That same error can also appear when the knowledge-source payload points at the
Foundry project-services endpoint (`*.services.ai.azure.com`) instead of the AI
Services/Cognitive Services endpoint (`*.cognitiveservices.azure.com`). The
initializer resolves and uses the AI Services account endpoint for embedding and
answer-synthesis model references.

Automation should ensure the identity running the initializer has:

- `Storage Blob Data Contributor` on the Forge storage account.
- `Search Service Contributor` on the Azure AI Search service.
- `Search Index Data Contributor` on the Azure AI Search service.

Automation should also ensure the Search service managed identity has:

- `Storage Blob Data Reader` on the Forge storage account.
- `Cognitive Services User` on the AI Services account.
- `Foundry User` on the AI Services account.

The Search roles were already represented in Bicep for the deploying principal,
but the Storage upload role and the exact Search-to-AI Services role were
missing. Both are now included in the Search module so future provision runs can
grant them consistently.

During the live run, the initializer also exposed an endpoint/defaulting issue:
`az resource list` may return `properties: null`, and the Search knowledge-source
payload must use the AI Services account endpoint, not the Foundry project
endpoint. The initializer now hydrates the endpoint from the AI Services account
and tolerates missing `properties` in generic resource-list responses.

## Live exercise result

The first live exercise against `rg-forge` created and verified:

| Item | Live state |
| --- | --- |
| Embedding deployment | `text-embedding-3-large`, model version `1`, SKU `Standard`, capacity `50`, succeeded |
| Uploaded corpus | 5 Ledgerfield contract files in storage container `knowledge`, under `contracts` |
| Knowledge source | `contracts-ks` with `folderPath: contracts` and generated datasource, skillset, indexer, and index |
| Knowledge base | `contracts-kb` using `contracts-ks` and `gpt-5-mini` |
| MCP connection | `kb-mcp-connection` targeting `contracts-kb` MCP endpoint |
| Indexer result | 5 contract items processed, 0 failed |
| Index document count | 6 indexed contract documents/chunks after rebuild |

The FoundryIQ live smoke is now:

```powershell
python scripts\test_foundryiq_kb.py
```

That smoke is read-only. It validates `contracts-ks`, `contracts-kb`,
`kb-mcp-connection`, the Search-hosted KB MCP `knowledge_base_retrieve` tool, and
that retrieval returns source refs. The hosted FoundryIQ container path still
needs a separate deployed-agent invocation after toolbox refresh.

The earlier broad live exercise uploaded `policies` and `suppliers` blobs before
the scope was narrowed. Those non-contract blobs were removed from the live
`knowledge` container after the contract-only rebuild.

## Automation view

This is intentionally not automatic on every Forge deploy yet. It mutates shared
Search data-plane state and depends on Ledgerfield corpus availability.

Recommended automation path:

1. Forge CI provisions shared Foundry/Search/model resources.
2. Ledgerfield produces a versioned contracts corpus artifact and supplier
   manifest.
3. A manual Forge `workflow_dispatch`, or a Keystone orchestration stage, runs
   `scripts\initialize_contracts_kb.py` against that artifact.
4. The workflow records the Ledgerfield source ref and KB name in the run summary.

This keeps resource ownership in Forge while allowing Keystone to automate the
cross-repo sequence once Ledgerfield has a stable corpus artifact.
