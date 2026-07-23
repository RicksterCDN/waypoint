# FabricIQ deployment review

Date: 2026-07-23

This review cross-checks the current Waypoint monorepo against the earlier
multi-repo deployment across Keystone, Waypoint, Forge, Ledgerfield, and Caliber,
with a focus on how Fabric was used to implement the FabricIQ hosted agent.

## Executive readout

The prior FabricIQ effort proved a real end-to-end path: Fabric capacity,
Postgres-to-Fabric mirroring, a Direct Lake semantic model, a published Fabric
Data Agent, and the `operations-data-expert` hosted agent reading live
operational rows. The successful proof was scoped to four mirrored tables:
`suppliers`, `invoices`, `invoice_lines`, and `reconciliation_findings`.

The current consolidated launch repo does **not** deploy FabricIQ by default.
The root deployment is intentionally FoundryIQ-only. Fabric, OneLake, and the
FabricIQ agent assets remain in the repository as opt-in/future-development
paths. That split is directionally right, but the repo now contains mixed-era
documentation: some docs still describe `operations-data-expert` as a stub,
while the agent code contains the live dual-path implementation from the prior
deployment.

## Current repo posture

| Area | Current state | Cross-check |
| --- | --- | --- |
| Root deployment | FoundryIQ-only launch fleet: `invoice-analyst`, `assurance-orchestrator`, `contract-policy-expert`, `waypoint-recorder` | Matches `README.md`, `docs/deployment.md`, and `docs/architecture.md`; FabricIQ is explicitly excluded from default one-click launch. |
| Waypoint app Fabric assets | `apps/waypoint` still contains Fabric capacity, OneLake, mirror, semantic model, and Data Agent scripts | These are reusable/standalone opt-in assets, not the root launch path. |
| FabricIQ hosted agent | `modules/agents/agents/operations-data-expert` has the live dual-path implementation | `README.md` in that agent is stale and still says stub. |
| Ledgerfield corpus upload | `modules/corpus` can upload corpus files and Delta tables to OneLake | Useful for corpus storage and demos; not proof that FabricIQ is grounded. |
| Caliber | Quality/eval/RFT support, focused on `contract-policy-expert` | Caliber is not a Fabric data source and was not part of the FabricIQ runtime grounding path. |

## What we sourced from Fabric

### OneLake corpus lane

Ledgerfield wrote synthetic corpus assets into a Fabric lakehouse:

- `Files/corpus/contracts`
- `Files/corpus/policies`
- `Files/corpus/invoices/html`
- `Files/corpus/invoices/pdf`
- `Files/corpus/evidence`
- `Files/corpus/seed/waypoint-seed.json`

It also wrote Delta tables:

- `suppliers`
- `invoices`
- `invoice_lines`
- `reconciliation_findings`
- `contract_documents`
- `policies`
- `evidence_references`

Waypoint's API can read document bytes/text back from OneLake with an ADLS Gen2
client and fall back to URI-only metadata when OneLake is not configured.

### FabricIQ operational-data lane

The real FabricIQ proof did **not** need the entire corpus lakehouse. The
validated source was a Fabric mirrored Azure Database for PostgreSQL item over
the Waypoint operational core, surfaced to the agent through the mirrored SQL
analytics endpoint as `_public.*` tables:

- `_public.invoices`
- `_public.suppliers`
- `_public.invoice_lines`
- `_public.reconciliation_findings`

The successful validation returned five real `fabric://_public.*` refs for
`INV-2026-08034` and server-side `queryinsights` confirmed matching successful
SELECTs and row counts.

## Was the Fabric data duplicative?

Yes, in two different ways.

1. The OneLake corpus tables duplicated data already imported into Waypoint from
   `waypoint-seed.json`. This was acceptable for a lakehouse/corpus demo, but it
   is not the cleanest evidence source for FabricIQ.
2. The early lakehouse-source FabricIQ path could read
   `reconciliation_findings` generated from Ledgerfield scenario expectations.
   That risks turning FabricIQ into a second copy of known answers instead of an
   independent operational evidence plane.

The later mirrored-Postgres design was better. It still duplicates data by
design, because mirroring creates an analytical replica, but Waypoint remains
the system of record and Fabric becomes the governed read-only analytics plane.
The important boundary is that FabricIQ should read operational facts only;
contract/policy interpretation belongs to FoundryIQ.

## Best practices we used

- **Kept Waypoint as system of record.** Fabric mirrored operational data for
  analytics; it did not become the write path.
- **Kept the recorder as sole writer.** `operations-data-expert` remained
  read-only and returned evidence contracts only.
- **Made provisioning mostly idempotent.** Scripts used lookup-before-create,
  update-in-place, generated-once Key Vault secrets, and deterministic stage
  outputs instead of click-path state.
- **Used Fabric role assignments, not SQL grants, for the mirror endpoint.**
  The mirrored SQL endpoint is read-only and rejected contained-user grants, so
  the durable fix was workspace/item RBAC.
- **Split interactive and headless agent paths.** The Fabric Data Agent tool was
  OBO/user-scoped and suitable for interactive surfaces; headless orchestrator
  fan-out used deterministic TDS reads with a managed identity.
- **Constrained the agent's blast radius.** The headless SQL tool had an
  allow-list of four tables and parameterized queries; prompts explicitly
  prohibited contracts, policies, or emailed invoice claims.
- **Made absence explicit.** When Fabric variables, ODBC drivers, auth, or SQL
  access were missing, the expert returned an honest unavailable/empty evidence
  contract rather than fabricating Fabric-backed claims.
- **Used OneLake GUID path resolution.** The uploader/reader resolved lakehouses
  to GUID path segments to avoid tenants where friendly-name support is disabled.

## What was hacked through or operationally fragile

| Issue | What happened | Why it was fragile |
| --- | --- | --- |
| Data Agent OBO on headless runs | The Foundry Fabric Data Agent tool failed on headless Responses turns because no signed-in user token existed | Required a two-path design: OBO only for interactive/Teammate, direct SQL for headless. |
| Wrong identity presented to Fabric SQL | The hosted container presented a per-agent AgentIdentity, not the project managed identity initially granted in Fabric | Required token-claim logging and an explicit Fabric Viewer grant for the actual AgentIdentity. |
| AgentIdentity rotation | Per-agent identities can rotate if an agent/environment is recreated | Keystone/Waypoint need a repeatable way to inject the current consumer identity into Fabric workspace grants. |
| ODBC packaging | The headless TDS path needed `msodbcsql18` present in the hosted-agent image | This was fixed, but it is a platform-specific dependency outside Python package management. |
| `_public` schema | Mirrored Postgres `public` surfaced as `_public` in Fabric SQL analytics | Easy to miss; a wrong schema broke Direct Lake and SQL reads. |
| Portal-only mirror enablement | PostgreSQL Fabric mirroring required a source-server enable/prepare/restart step | The repo could set Bicep prerequisites, but the final server preparation was not fully API/IaC at the time. |
| `fabric_user` bootstrap | Mirroring required a dedicated Postgres role with replication/CDC privileges and table ownership | We moved this toward idempotent bootstrap, but the underlying role requirements are awkward and high-privilege. |
| Evolving REST surface | Raw scripts used Fabric REST definitions for item creation/update/publish | The Data Agent API is now public, but the Learn REST item-definition docs still lag parts of this surface. |

## What had to be Python/scripted because Bicep could not do it

Bicep was appropriate for Azure resources such as the Fabric capacity and
PostgreSQL prerequisites. It was not enough for Fabric SaaS items and data-plane
operations.

| File | Why it existed |
| --- | --- |
| `apps/waypoint/infra/scripts/provision-fabric.sh` | Creates/reuses Fabric workspace and lakehouse, assigns workspace to capacity, grants workspace roles, and publishes OneLake coordinates. |
| `apps/waypoint/infra/scripts/provision_fabric_mirror.py` | Creates/reuses a Fabric connection and mirrored PostgreSQL database item, starts mirroring, grants source-server SAMI workspace access, and polls status. |
| `apps/waypoint/infra/scripts/provision_fabric_iq.py` | Builds/updates a Direct Lake semantic model and published Fabric Data Agent from item definitions. |
| `apps/waypoint/infra/scripts/ensure-fabric-workspace-roles.sh` | Idempotently grants workspace roles to downstream consumers such as Forge agent identities. |
| `apps/waypoint/infra/scripts/fabric-mirror-role.sql` | Bootstraps the Postgres `fabric_user` role for mirroring when run out-of-band/local. |
| `modules/corpus/src/ledgerfield/onelake_upload.py` | Writes corpus documents through OneLake DFS/ADLS APIs and Delta tables through `deltalake` over `abfss://`. |
| `modules/agents/scripts/initialize_fabric_data_agent.py` | Creates/updates the Foundry project connection to the Fabric Data Agent. |

## Known Microsoft product gaps and constraints

Checked against Microsoft Learn/Fabric documentation on 2026-07-23:

- Fabric Data Agent is GA, read-only, and requires paid Fabric capacity
  (`F2+` or Power BI Premium capacity with Fabric enabled). It supports governed
  sources such as lakehouses, warehouses, semantic models, KQL databases,
  mirrored databases, and ontologies, but it does not support unstructured files
  directly.
- Fabric Data Agent uses user credentials/permissions for schema and query
  access. It is read-only and respects Purview/DLP/access restrictions.
- A Data Agent can use up to five data sources. Responses are currently capped
  at 25 rows and 25 columns, and data sources must be in the same capacity region
  as the agent workspace.
- Tenant Copilot/Azure OpenAI settings are required and can take up to an hour
  to take effect. Microsoft warns that use from Foundry, Copilot Studio, M365
  Copilot, MCP, or other non-Fabric services can move responses outside the
  Fabric compliance/geographic boundary.
- OneLake supports ADLS/Blob-compatible APIs, but permissions and item
  management remain Fabric experiences. Direct API callers need the Storage
  token audience. Some tools reject the OneLake DFS endpoint because it is not
  an ADLS `dfs.core.windows.net` URL.
- Fabric mirrored PostgreSQL does not support Burstable compute tiers, views,
  partitioned/external tables, many PostgreSQL data types including `json/jsonb`,
  or DDL changes on existing mirrored tables without stop/restart/reseed.
  Source DB permissions do not propagate to Fabric.
- Mirrored PostgreSQL requires a specially privileged source role and table
  ownership. A source database can only be mirrored to one Fabric item/workspace
  at a time, and only up to 1,000 tables are mirrored.
- The mirrored SQL analytics endpoint is read-only. This is why Fabric workspace
  RBAC/item access was the right repair path for agent reads, not SQL `CREATE
  USER`/`GRANT`.
- Fabric REST item-definition APIs support automated deployment patterns, and
  Microsoft has announced public Data Agent API/SDK support, but first-class
  Bicep coverage for workspace/lakehouse/mirrored database/semantic model/Data
  Agent remains a gap for this scenario.

## What a validated current E2E with FabricIQ would require

The current implementation can be made to prove FabricIQ again, but it is not
only a flag flip from the launch deployment. A trustworthy E2E would need to
restore FabricIQ as a first-class deployment lane, prove the data path, and prove
the hosted-agent path in the same acceptance envelope.

### Success criteria

A successful FabricIQ-added E2E should prove all of these in one run:

1. Clean deploy creates or reuses the Waypoint app, Foundry resources, corpus
   seed, and the default launch agents.
2. Fabric capacity, workspace, lakehouse, mirrored PostgreSQL item, Direct Lake
   semantic model, and Fabric Data Agent are created or reconciled idempotently.
3. PostgreSQL mirroring is running against a non-Burstable source server and the
   mirrored SQL endpoint exposes `_public.suppliers`, `_public.invoices`,
   `_public.invoice_lines`, and `_public.reconciliation_findings`.
4. `operations-data-expert` is deployed and wired into the orchestrator fan-out
   with the headless SQL path configured.
5. A known-positive invoice, for example `INV-2026-08034`, returns non-empty
   `fabric://_public.*` evidence from all four operational tables.
6. The orchestrator includes FabricIQ evidence in the final synthesis and the
   recorder persists the terminal Waypoint run.
7. Acceptance fails closed on FabricIQ fallback, empty Fabric evidence, missing
   role grants, or a non-terminal run.
8. An unchanged rerun reuses existing resources, secrets, and Fabric items rather
   than creating duplicates or requiring manual cleanup.

### Required repo work

| Area | Needed for E2E |
| --- | --- |
| Root deploy workflow | Reintroduce FabricIQ as an explicit selected lane: Fabric provisioning, mirror provisioning, IQ provisioning, operations-data-expert deploy, orchestrator endpoint wiring, and FabricIQ acceptance. |
| Deployment state | Persist Fabric workspace, lakehouse, mirrored DB, semantic model, Data Agent, SQL endpoint, and consumer grants as non-secret state, with secrets in Key Vault only. |
| Waypoint app deploy | Pass `fabric_provision_enabled`, `fabric_mirror_enabled`, `fabric_iq_enabled`, Fabric names, mirror names, and consumer-member grants through the app deployment. |
| Postgres | Enforce GeneralPurpose or MemoryOptimized when mirroring is enabled; reject or coerce Burstable cost overrides. |
| Fabric mirror | Ensure source-server SAMI, `fabric_user`, table ownership, Fabric connection, mirrored DB item, and start/poll logic are idempotent. |
| Fabric IQ | Build/update the Direct Lake semantic model and published Data Agent from item definitions or the current supported SDK/API. |
| Forge agents | Include `operations-data-expert` in the selected agent matrix, set `FABRIC_MIRRORED_SQL_ENDPOINT`, `FABRIC_MIRRORED_DATABASE`, `FABRIC_MIRRORED_SCHEMA`, and keep the ODBC image dependency validated. |
| Fabric RBAC | Grant the actual presented hosted-agent identity Viewer on the Fabric workspace or item. Do not assume the project managed identity is the reader without token-claim proof. |
| Ledgerfield | Keep OneLake corpus upload as storage/context only, not the FabricIQ source of operational truth. |
| Caliber | Add a FabricIQ known-positive eval only after the deployment lane is real; keep it separate from contract-policy RFT workflows. |

### Validation sequence

1. **Preflight**: confirm Fabric tenant Copilot/Data Agent settings, paid capacity
   availability, region compatibility, PostgreSQL tier, Key Vault access, Fabric
   API access, and expected role-assignment rights.
2. **App and data deploy**: deploy Waypoint with Fabric mirror and IQ enabled;
   import seed data; verify the API remains healthy after role bootstrap.
3. **Mirror proof**: poll mirroring status, then query the SQL analytics endpoint
   for table existence and row counts in the four `_public` tables.
4. **Fabric item proof**: verify the semantic model and Data Agent exist, are
   updated in place on rerun, and bind to the intended mirrored source.
5. **Agent proof**: invoke `operations-data-expert` directly for the known
   invoice and require real `fabric://_public.*` refs.
6. **Pipeline proof**: invoke `assurance-orchestrator`; require the run to
   finalize through `waypoint-recorder` and include FabricIQ evidence in the
   recorded payload.
7. **Server-side proof**: capture the mirrored endpoint query history or
   equivalent logs showing the expected identity reading `_public.*` with
   non-zero row counts.
8. **Idempotency proof**: rerun the same deployment unchanged and verify no
   duplicate Fabric items, no reset secrets, no duplicate mirrored DB, and no
   replacement hosted-agent versions unless inputs changed.

### Explicit demo unblocks that would be hacky

These are acceptable only as short-lived validation unblocks. They should not be
described as the desired production posture.

| Hack | Why it might unblock a demo | Why it is not a best practice |
| --- | --- | --- |
| Portal-enable PostgreSQL Fabric mirroring manually | Clears the source-server "Prepare / Restart / ready for mirroring" blocker when no public API path is available | Not one-click, not auditable in repo state, and easy to miss in a fresh tenant. |
| Manually scale Postgres from Burstable to GeneralPurpose | Makes the portal mirroring button available immediately | Cost and downtime are outside the reviewed deployment plan unless codified. |
| Manually grant Fabric workspace Viewer to a discovered AgentIdentity | Fastest fix for the wrong-presented-identity `18456` failure | Per-agent identities can rotate; manual grants are invisible to a clean deploy. |
| Put Fabric workspace IDs, Data Agent IDs, SQL endpoints, or consumer IDs in repo variables by hand | Gets wiring unstuck without building discovery | Creates tenant-specific drift and makes reruns depend on tribal knowledge. |
| Run `psql` from a CI runner with an admin connection to create `fabric_user` | Can bootstrap the mirroring role without changing app startup code | Exposes admin connectivity to CI, adds firewall fragility, and expands blast radius. |
| Use a broad workspace Admin/Contributor grant for all deploy and agent identities | Avoids fine-grained RBAC troubleshooting | Over-privileges read-only agents and makes least-privilege review impossible. |
| Use a developer/user token for Fabric API or Data Agent testing | Quickly proves the Fabric item works interactively | Does not prove the hosted headless path and may bypass the actual production identity boundary. |
| Attach the Fabric Data Agent OBO tool to headless Responses runs | Looks like the most "native" FabricIQ path | It fails without a signed-in user token; headless must use a deterministic managed-identity read path. |
| Treat OneLake seed/corpus Delta tables as FabricIQ evidence | Produces easy positive answers | Duplicates expected outcomes and weakens FabricIQ as an independent operational evidence plane. |
| Allow empty FabricIQ evidence but still mark the run successful | Keeps the demo moving when Fabric is unhealthy | Masks the exact capability being validated and recreates the earlier "consulted expert" but no grounding problem. |

### Minimum acceptable temporary E2E

If the only goal is to prove the current implementation once, the smallest
honest path is:

1. Keep FabricIQ off by default.
2. Run a dedicated FabricIQ-enabled deployment in an isolated environment.
3. Allow the portal-only mirroring preparation step if the product still has no
   public API for it, but record it explicitly in the deployment evidence.
4. Codify every post-portal value before rerun: workspace id, mirrored DB name,
   SQL endpoint, Data Agent id, and actual agent identity grant.
5. Require the direct expert invoke, orchestrator run, recorder finalization, and
   unchanged rerun before calling the E2E successful.

Anything less proves that Fabric resources can exist, not that FabricIQ is a
validated, repeatable evidence lane.

## What could have been better

1. **Pick one FabricIQ source earlier.** The lakehouse-source path duplicated
   seed answers. The mirrored operational core is the cleaner FabricIQ source.
2. **Avoid raw REST once the Data Agent SDK is stable.** The hand-authored
   multi-part JSON definitions were effective but brittle.
3. **Make agent identity discovery first-class.** Keystone should not depend on
   manually captured per-agent object IDs for `WAYPOINT_FABRIC_CONSUMER_MEMBERS`.
4. **Add a FabricIQ acceptance gate before default enablement.** A real gate
   should invoke `operations-data-expert` and verify non-empty `fabric://_public`
   evidence plus server-side/read-path proof.
5. **Clean up stale docs.** Current docs disagree on whether
   `operations-data-expert` is stubbed, opt-in, or live. The root launch docs are
   correct that FabricIQ is not default; the agent-local README is stale.
6. **Keep Caliber separate but extend it when FabricIQ returns.** Caliber already
   handles datasets, graders, lineage, gates, and RFT planning. It should add a
   FabricIQ known-positive eval only if FabricIQ becomes a supported launch lane.
7. **Make cost posture explicit.** Fabric capacity and GeneralPurpose Postgres
   are more expensive than the default lightweight path; auto-suspend/resume and
   runbook warnings should be part of any FabricIQ-enabled deployment.

## Recommendation

Keep the consolidated launch FoundryIQ-only unless there is a clear product need
to re-enable FabricIQ. If FabricIQ returns to the default path, first reconcile
the docs/code posture, remove the seed-lakehouse source as an evidence path,
codify downstream Fabric consumer identities, and add an acceptance gate that
proves live mirrored-table grounding from `operations-data-expert`.

## Microsoft references checked

- Fabric Data Agent concepts:
  <https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent>
- Fabric Data Agent tenant settings:
  <https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings>
- OneLake ADLS/Blob API access:
  <https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api>
- OneLake table APIs:
  <https://learn.microsoft.com/en-us/fabric/onelake/table-apis/table-apis-overview>
- Fabric mirrored PostgreSQL limitations:
  <https://learn.microsoft.com/en-us/fabric/mirroring/azure-database-postgresql-limitations>
- Fabric REST item-definition APIs:
  <https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/item-definition-overview>
- Fabric Data Agent public API announcement:
  <https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Fabric-data-agent-API-is-now-public-Build-Fabric-data-agents/ba-p/5230588>
