# keystone architecture

Keystone is an **orchestrator**, not an application. It deploys three independently
buildable repos into one Azure environment, in dependency order, passing typed outputs
between stages — with **zero manual configuration** beyond the Azure OIDC bootstrap.

```
ledgerfield ──seed──► waypoint (Aspire app + Azure Postgres + Fabric OneLake)
                          ▲
forge (Foundry agents) ───┘  aggregator writes; the 6 read agents read
        ▲
        └── keystone provisions a Key Vault (keys), ensures the MSAL app reg, and wires endpoints
```

## Grounding: discovery + conventions

Resource names in the live environment are **tool-generated** and differ per repo (forge's
azd `resourceToken` `wi2egf4sh4hfq`, waypoint's Aspire token `7777zs47exudw`, the Container
Apps env domain `grayflower-2758f17b`) — there is **no single token** derivable from
`AZD_ENV_NAME`. So `scripts/discover.sh` splits names into two buckets:

- **CHOSEN** (resources keystone owns; FIXED names anchored on the base "keystone", not derived
  from `AZD_ENV_NAME`): the dedicated resource group `rg-keystone` and the Key Vault
  `kv-keystone-<6-hex hash of the subscription id>` (globally unique, ≤24 chars). Fabric
  capacity / workspace / lakehouse / corpus-prefix names + the OneLake account url and seed uri
  are in-workflow **constants** (waypoint's own defaults) — keystone passes them as inputs but
  does **not** provision Fabric (waypoint owns that).
- **DISCOVER** (existing tool-generated names; queried by RG + type): forge RG (`rg-forge`,
  via the `azd-env-name` tag), waypoint RG (`waypoint-rg`, keyed off the unique
  `waypoint-postgres-*` flexible server — container-app names like `api`/`web` collide with
  other demos), Foundry endpoint + project url, App Insights resource id, Log Analytics
  workspace/RG, Postgres server, MSAL client id, deploy-SP object id. The Key Vault + state tags
  live in the dedicated `rg-keystone` (created idempotently) — deliberately NOT the Aspire-managed
  waypoint RG, to avoid any reconciliation/teardown risk.

## Stage order & why

`discover` runs first to ground names, then `preflight` emits per-stage booleans
(change/existence detection); each stage below is gated on its boolean AND is independently
idempotent, so re-runs only do new or changed work and are always safe.

0a. **provision-keyvault** — PROVISION an Azure Key Vault (the deploy SP self-grants Key Vault
    Secrets Officer via its User Access Administrator role; the **waypoint-deploy SP** is granted
    the same role so waypoint reads this vault with its own identity) and **generate-once-then-read**
    the writer/reader/admin `x-api-keys` and the Postgres app & admin passwords as KV secrets (the
    admin password feeds waypoint's Postgres provisioning + `fabric_user` startup bootstrap). A
    secret is written only if absent, so values are **stable across runs** — no key store in GitHub,
    no user-set seed, and no PAT. Runs before the deploys (both tools resolve auth at deploy time).
0b. **ensure-msal** — create-if-missing the "waypoint" Entra app registration, ADDITIVELY ensure
    the `Waypoint.Read/Write/Admin` app roles (absent in the live tenant) + the identifier uri —
    leaving the existing oauth2 scopes (`user_impersonation` / `Waypoint.Admin`) and SPA redirect
    untouched — and emit the MSAL identity (tenant/client/roles/scope) for the waypoint inputs.
1. **ledgerfield** — produces `waypoint-seed.json`; waypoint needs it to seed Postgres.
2. **forge** (`azd`) — agents deploy; `agent.yaml` substitutes `WAYPOINT_WRITER_API_KEY`
   (aggregator) / `WAYPOINT_READER_API_KEY` (the 6 read agents) from the forge azd env.
3. **waypoint** (Aspire CLI) — `aspire deploy`, with keystone PASSING every computed value as
   `workflow_call` **inputs/secrets** (MSAL identity, Foundry/App-Insights/Postgres, Fabric
   names + runtime-computed admin members, api keys, pg password). Binds `APP_API_KEY_AUTH_ENABLED`
   + `APP_API_KEYS`; emits `api_fqdn`, `web_fqdn`, `api_app_id_uri`, `api_default_scope`. When
   `fabric_provision_enabled` (driven by preflight's `needs_fabric`, SKIP-ON-UNCHANGED — only
   true when the capacity is missing/unrecorded or `force_fabric`), **waypoint** (which OWNS Fabric
   provisioning) stands up the Fabric OneLake corpus lake and emits `onelake_workspace`,
   `onelake_lakehouse`, `onelake_account_url`.
4. **ledgerfield-onelake-upload** — calls ledgerfield's `onelake-upload` reusable workflow with
   the `onelake_*` coordinates (or, when Fabric was skipped as unchanged, the recorded workspace
   GUID from the RG tag + the chosen names) to upload the corpus into the lake. Runs **after**
   waypoint-deploy and **before** seed-import.
5. **seed-import** — downloads the stage-1 artifact and POSTs it to Waypoint's admin seed
   endpoint (`/api/admin/seed/ledgerfield`, x-api-key admin from Key Vault); upsert, so idempotent.
6. **wire** — push `WAYPOINT_API_BASE_URL` / `WAYPOINT_API_SCOPE` and the
   `*_EXPERT_ENDPOINT` / `AGGREGATOR_ENDPOINT` fan-out vars into the forge azd env, then run MSAL
   **pass 2**: union the deployed `web_fqdn` into the app's SPA redirect URIs (only known after
   waypoint-deploy; falls back to the recorded web FQDN RG tag when waypoint was skipped).

A final **record-state** job persists the deployed repo SHAs + OneLake workspace GUID + api/web
FQDN + MSAL client id + KV name as **Azure resource group tags** (`keystone_*`) so the next
run's preflight can detect "no change". Tags are used instead of GitHub repo variables because
the workflow `GITHUB_TOKEN` cannot write repo vars/secrets (needs `administration: write`); the
deploy SP already has Contributor on the RG via OIDC, keeping the loop human-free with no PAT.

## Auth model

The default one-click posture is **dual auth** — humans and agents authenticate differently
against the same Waypoint API, resolved by waypoint `auth.py` (x-api-key first, else MSAL bearer):

- **Humans** sign in to the web with **@caldova MSAL** and authorize via **delegated tokens**:
  the API validates the `scp` claim against the app's existing oauth2 scopes
  (`user_impersonation` → reader, `Waypoint.Admin` → admin). These already work and the SPA
  redirect already points at the web FQDN. Keystone **ensures** the MSAL app registration: it
  creates it if missing and **additively** ensures the `Waypoint.Read/Write/Admin` app roles +
  identifier uri (a separate, app-only path nothing in this demo uses today, ensured for
  forward-compatibility), leaving the working scopes + SPA redirect intact. In the `wire` job
  (pass 2) it unions the deployed web FQDN into the SPA redirect URIs. The MSAL identity values
  flow to waypoint as reusable-workflow inputs.
- **Agents** authenticate with **x-api-key (HMAC)**: one **writer** key → forge **aggregator**,
  one **reader** key → the **six** read agents (the Waypoint `writer` role expands to `reader`),
  one **admin** key → **seed-import** only.

> **App Insights (out of scope this pass):** keystone reuses forge's component
> (`appi-wi2egf4sh4hfq`) and passes its resource id so the portal can deep-link unified agent+API
> traces. Actually instrumenting waypoint's api/web to EMIT telemetry to that component is a
> separate, larger change and is **not** done here — only the resource-id input is wired.
- Headless deploy via **GitHub OIDC** → Azure (no stored cloud creds).
- The agent keys + the Postgres password live in the **deploy-provisioned Key Vault**
  (`scripts/keyvault.sh`, generate-once-then-read) and are delivered to each deploy in the form
  it consumes — **different mechanisms per repo**:
  - **forge** is `azd`: `WAYPOINT_WRITER_API_KEY` / `WAYPOINT_READER_API_KEY` are `azd env set`
    into the forge env; `agent.yaml` substitutes them at `azd deploy`.
  - **waypoint** is the **Aspire CLI**: it reads the composed `api-keys` string + the Postgres
    password from the reusable-call secrets (`WAYPOINT_API_KEYS`, `POSTGRES_APP_PASSWORD`).

## Wiring status

All cross-repo seams call the downstream repos' reusable workflows directly:

- **ledgerfield** exposes `waypoint-seed.yml` (output `artifact-name`) and `onelake-upload.yml`.
- **forge** (`azd`) exposes `deploy.yml` (`workflow_call`, output `project_endpoint`).
- **waypoint** (Aspire) exposes `deploy.yml` (`workflow_call`) — parameterized (merged) to accept
  every `WAYPOINT_*`/`WAYPOINT_FABRIC_*` value as one of 22 inputs, each with an
  `inputs.X != '' && inputs.X || vars.WAYPOINT_X` fallback for standalone runs.

`scripts/discover.sh`, `scripts/keyvault.sh`, `scripts/msal.sh`, `scripts/preflight.sh`,
`scripts/seed_import.sh`, `scripts/wire.sh`, and `scripts/oidc.sh` back the discovery,
Key-Vault, MSAL-ensure, detection, seed-import, wire, and OIDC-bootstrap steps in CI and
locally. Keystone sets **no** `WAYPOINT_*` repo variables of its own — every value is computed
at runtime and passed to waypoint's reusable workflow as an input/secret.

## The deploy workflow

The one-click workflow lives at `.github/workflows/deploy.yml`, driven by `Actions → Deploy →
Run workflow` (`workflow_dispatch`). It is idempotent and gated by the `preflight` detection
job; `force_all` / `force_fabric` / `only` / `skip` / `azd_env_name` inputs override the
detection when needed. The local mirror is `make deploy-all` (best-effort; see
`scripts/run_stage.sh`).
