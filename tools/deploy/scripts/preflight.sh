#!/usr/bin/env bash
# Preflight change/existence detection for the keystone one-click deploy.
#
# Emits one boolean per pipeline stage so deploy.yml can gate each downstream job
# with `if:`. The goal is that re-runs only do NEW or CHANGED work — but every
# stage is ALSO idempotent on its own, so a false "needs" is at worst wasted time,
# never breakage.
#
# Detection combines three signals:
#   1. Ref-change: resolve each repo ref -> commit SHA (gh api) and compare to the
#      SHA recorded on the last successful run. Recorded state lives as AZURE RESOURCE
#      GROUP TAGS (keystone_<repo>_sha, keystone_seed_sha, keystone_onelake_workspace,
#      keystone_api_fqdn) written by record-state — NOT GitHub repo variables, because
#      the workflow's GITHUB_TOKEN cannot write repo vars/secrets (needs admin). The
#      deploy SP already has Contributor on the RG via OIDC, so tags are the no-PAT store.
#   2. Azure/Fabric/Foundry existence probes (az CLI; requires a prior azure/login).
#   3. workflow_dispatch overrides: FORCE_ALL / ONLY / SKIP / FORCE_FABRIC.
#
# Final gate per stage (also re-applied in deploy.yml `if:`):
#   run = (FORCE_ALL || needs_X) && not_skipped(X) && (ONLY=="" || ONLY==X)
# keygen is exempt (deterministic + cheap): it ALWAYS runs so downstream always has keys.
#
# Required env:
#   GH_REPO            owner/repo of THIS keystone repo (e.g. caldova/waypoint)
#   LEDGERFIELD_REF / FORGE_REF / WAYPOINT_REF   refs being deployed (default main)
#   FORCE_ALL          "true" to run everything
#   ONLY               single stage name to run in isolation ("" = all)
#   SKIP               comma-separated stage names to force-skip
#   FORCE_FABRIC       "true" to force Fabric (re)provision even when unchanged
# Optional env (all discovered by the `discover` job; probes skipped gracefully if missing):
#   STATE_RG           RG holding the keystone state tags + Key Vault (dedicated rg-keystone)
#   WAYPOINT_RG        RG holding the waypoint api/web container apps (default STATE_RG)
#   KV_NAME            Key Vault to read the reader key from (for the seeded-state probe)
#   FABRIC_CAPACITY_NAME   chosen Fabric capacity name (existence probe)
#   API_FQDN           waypoint API FQDN (else read from the keystone_api_fqdn RG tag)
#
# Stage names: keygen ledgerfield forge waypoint fabric onelake_upload contracts_kb seed wire
set -euo pipefail

gh_repo="${GH_REPO:?GH_REPO required (owner/repo)}"
ledgerfield_ref="${LEDGERFIELD_REF:-main}"
forge_ref="${FORGE_REF:-main}"
waypoint_ref="${WAYPOINT_REF:-main}"
force_all="${FORCE_ALL:-false}"
force_fabric="${FORCE_FABRIC:-false}"
only="${ONLY:-}"
skip="${SKIP:-}"
rg="${STATE_RG:-${AZURE_RESOURCE_GROUP:-}}"
waypoint_rg="${WAYPOINT_RG:-$rg}"
kv_name="${KV_NAME:-}"

log() { echo "preflight: $*" >&2; }

# Resolve a branch/tag/sha ref in a repo to a full commit SHA (empty on failure).
resolve_sha() {
  local repo="$1" ref="$2"
  gh api "repos/${repo}/commits/${ref}" --jq '.sha' 2>/dev/null || true
}

# Is `az` usable (CLI present and a subscription in context)?
az_ready() {
  command -v az >/dev/null 2>&1 && az account show >/dev/null 2>&1
}

# ---- recorded state: read the RG tags written by record-state ---------------
# One az call; parse with jq. Missing RG/az/tags -> all-empty (treated as "changed").
tags_json="{}"
if [[ -n "$rg" ]] && az_ready; then
  tags_json="$(az group show --name "$rg" --query tags -o json 2>/dev/null || echo '{}')"
  [[ -z "$tags_json" || "$tags_json" == "null" ]] && tags_json="{}"
fi
tag() { echo "$tags_json" | jq -r --arg k "$1" '.[$k] // ""' 2>/dev/null || echo ""; }

rec_ledgerfield_sha="$(tag keystone_ledgerfield_sha)"
rec_forge_sha="$(tag keystone_forge_sha)"
rec_waypoint_sha="$(tag keystone_waypoint_sha)"
rec_seed_sha="$(tag keystone_seed_sha)"
rec_contracts_kb_sha="$(tag keystone_contracts_kb_sha)"
rec_contracts_kb_storage="$(tag keystone_contracts_kb_storage)"
rec_onelake_workspace="$(tag keystone_onelake_workspace)"
rec_web_fqdn="$(tag keystone_web_fqdn)"
rec_api_fqdn="$(tag keystone_api_fqdn)"
API_FQDN="${API_FQDN:-$rec_api_fqdn}"
log "recorded tags ledgerfield=$rec_ledgerfield_sha forge=$rec_forge_sha waypoint=$rec_waypoint_sha seed=$rec_seed_sha onelake=$rec_onelake_workspace contracts_kb_storage=$rec_contracts_kb_storage"

# Read the READER key from Key Vault (the same secret keyvault.sh generated), for the
# seeded-state probe. Empty when the vault/secret/az is absent -> probe simply skipped.
reader_key=""
if [[ -n "$kv_name" ]] && az_ready; then
  reader_key="$(az keyvault secret show --vault-name "$kv_name" --name waypoint-reader-key --query value -o tsv 2>/dev/null || true)"
fi

# Does a container app exist in the waypoint resource group?
containerapp_exists() {
  local name="$1"
  [[ -n "$waypoint_rg" ]] || return 1
  az_ready || return 1
  az containerapp show --name "$name" --resource-group "$waypoint_rg" \
    --query name -o tsv >/dev/null 2>&1
}

# Does the Fabric capacity exist (in the state RG)?
fabric_capacity_exists() {
  local cap="${FABRIC_CAPACITY_NAME:-${WAYPOINT_FABRIC_CAPACITY_NAME:-}}"
  [[ -n "$cap" && -n "$rg" ]] || return 1
  az_ready || return 1
  az resource show --resource-group "$rg" --name "$cap" \
    --resource-type Microsoft.Fabric/capacities -o none 2>/dev/null
}

# Has the Waypoint API already been seeded? (reader x-api-key GET /api/invoices)
# Returns 0 (seeded) when it CANNOT probe (no FQDN/key) so a missing probe never
# forces a needless re-seed; the SHA-drift signal remains primary.
api_already_seeded() {
  [[ -n "${API_FQDN:-}" && -n "${reader_key:-}" ]] || return 0
  local count
  count="$(curl -fsS -H "x-api-key: ${reader_key}" \
    "https://${API_FQDN}/api/invoices" 2>/dev/null | grep -c '"invoice_id"' || true)"
  [[ "${count:-0}" -gt 0 ]]
}

# ---- resolve refs ----------------------------------------------------------
ledgerfield_sha="$(resolve_sha caldova/waypoint "$ledgerfield_ref")"
forge_sha="$(resolve_sha caldova/waypoint "$forge_ref")"
waypoint_sha="$(resolve_sha caldova/waypoint "$waypoint_ref")"
log "resolved SHAs ledgerfield=$ledgerfield_sha forge=$forge_sha waypoint=$waypoint_sha"

changed() { # changed <current_sha> <recorded_sha>
  local cur="$1" rec="$2"
  [[ -z "$cur" || -z "$rec" || "$cur" != "$rec" ]]
}

# ---- per-stage detection ---------------------------------------------------
# keygen ALWAYS runs (deterministic HMAC derivation is cheap and must always supply
# keys to downstream deploys, even under only=/skip=). Emitted true for transparency.
needs_keygen=true

# ledgerfield seed: ref changed since last deploy.
if changed "$ledgerfield_sha" "$rec_ledgerfield_sha"; then needs_ledgerfield=true; else needs_ledgerfield=false; fi

# forge: ref changed OR foundry/agents not yet deployed (proxy: SHA unrecorded).
if changed "$forge_sha" "$rec_forge_sha"; then needs_forge=true; else needs_forge=false; fi

# waypoint: ref changed OR the api/web container apps are missing.
needs_waypoint=false
if changed "$waypoint_sha" "$rec_waypoint_sha"; then needs_waypoint=true; fi
if ! containerapp_exists api || ! containerapp_exists web; then needs_waypoint=true; fi

# fabric: SKIP-ON-UNCHANGED — provision only when the capacity is missing OR the
# workspace GUID was never recorded (RG tag) OR the operator forces it. When skipped,
# onelake-upload is fed from the recorded GUID + WAYPOINT_FABRIC_* vars instead of the
# (empty) waypoint outputs. provision-fabric.sh idempotency is the backstop.
needs_fabric=false
if [[ -z "$rec_onelake_workspace" ]]; then needs_fabric=true; fi
if ! fabric_capacity_exists; then needs_fabric=true; fi
if [[ "$force_fabric" == "true" ]]; then needs_fabric=true; fi

# onelake upload: seed/ledgerfield changed OR fabric (re)provisioned this run.
if [[ "$needs_ledgerfield" == "true" || "$needs_fabric" == "true" ]]; then needs_onelake_upload=true; else needs_onelake_upload=false; fi

# contracts-kb upload: seed the contracts-kb Foundry IQ knowledge base blob container.
# PRIMARY = ledgerfield contracts corpus drift (ledgerfield SHA vs the recorded
# keystone_contracts_kb_sha tag); SECONDARY = forge KB (re)provisioned this run
# (needs_forge — the KB's storage/search coordinates may have moved). Independent of
# waypoint/fabric: it only needs forge's KB storage account, so it can run in parallel
# with waypoint-deploy. Idempotent self-cleaning blob sync is the backstop for a false true.
needs_contracts_kb=false
if changed "$ledgerfield_sha" "$rec_contracts_kb_sha"; then needs_contracts_kb=true; fi
if [[ "$needs_forge" == "true" ]]; then needs_contracts_kb=true; fi

# seed import: PRIMARY = seed SHA drift vs the recorded keystone_seed_sha tag;
# SECONDARY (bootstrap/recovery) = API reports zero invoices (reader probe).
needs_seed=false
if changed "$ledgerfield_sha" "$rec_seed_sha"; then needs_seed=true; fi
if [[ "$needs_seed" == "false" && -n "$rec_seed_sha" ]] && ! api_already_seeded; then needs_seed=true; fi
# A seed import needs the freshly-built artifact, so guarantee ledgerfield-seed runs too.
if [[ "$needs_seed" == "true" ]]; then needs_ledgerfield=true; fi

# wire: forge or waypoint deployed this run (endpoints may have moved).
if [[ "$needs_forge" == "true" || "$needs_waypoint" == "true" ]]; then needs_wire=true; else needs_wire=false; fi

# ---- apply overrides -------------------------------------------------------
is_skipped() { [[ ",$skip," == *",$1,"* ]]; }
gate() { # gate <stage> <needs_bool> -> echoes final true/false
  local stage="$1" needs="$2" run
  if [[ -n "$only" ]]; then
    [[ "$only" == "$stage" ]] && run=true || run=false
  elif is_skipped "$stage"; then
    run=false
  elif [[ "$force_all" == "true" ]]; then
    run=true
  else
    run="$needs"
  fi
  echo "$run"
}

# keygen bypasses only/skip/force — it must always run so downstream has keys.
run_keygen=true
run_ledgerfield="$(gate ledgerfield "$needs_ledgerfield")"
run_forge="$(gate forge "$needs_forge")"
run_waypoint="$(gate waypoint "$needs_waypoint")"
run_fabric="$(gate fabric "$needs_fabric")"
run_onelake_upload="$(gate onelake_upload "$needs_onelake_upload")"
run_contracts_kb="$(gate contracts_kb "$needs_contracts_kb")"
run_seed="$(gate seed "$needs_seed")"
run_wire="$(gate wire "$needs_wire")"

# only=<stage> coupling: also run the hard producers a target needs to function.
#   seed needs the freshly-built seed artifact from ledgerfield-seed (same run).
#   onelake_upload needs lake coordinates; if none are recorded, waypoint must (re)provision.
if [[ "$only" == "seed" ]]; then run_ledgerfield=true; fi
if [[ "$only" == "onelake_upload" && -z "$rec_onelake_workspace" ]]; then run_waypoint=true; run_fabric=true; fi
# contracts_kb needs forge's KB storage coordinates; if none were recorded, forge must
# (re)deploy this run to emit them (mirrors the onelake_upload -> waypoint coupling above).
if [[ "$only" == "contracts_kb" && -z "$rec_contracts_kb_storage" ]]; then run_forge=true; fi

# ---- emit ------------------------------------------------------------------
# Logs the decision to stderr and writes key=value to $GITHUB_OUTPUT (when set).
emit() { # emit key value
  log "decision $1=$2"
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "$1=$2" >> "$GITHUB_OUTPUT"
  return 0
}

emit needs_keygen "$run_keygen"
emit needs_ledgerfield "$run_ledgerfield"
emit needs_forge "$run_forge"
emit needs_waypoint "$run_waypoint"
emit needs_fabric "$run_fabric"
emit needs_onelake_upload "$run_onelake_upload"
emit needs_contracts_kb "$run_contracts_kb"
emit needs_seed "$run_seed"
emit needs_wire "$run_wire"
emit ledgerfield_sha "$ledgerfield_sha"
emit forge_sha "$forge_sha"
emit waypoint_sha "$waypoint_sha"
emit recorded_onelake_workspace "$rec_onelake_workspace"
emit recorded_contracts_kb_storage "$rec_contracts_kb_storage"
emit recorded_web_fqdn "$rec_web_fqdn"
emit recorded_api_fqdn "$rec_api_fqdn"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Preflight detection"
    echo ""
    echo "| stage | run |"
    echo "| --- | --- |"
    echo "| keygen | $run_keygen |"
    echo "| ledgerfield-seed | $run_ledgerfield |"
    echo "| forge-deploy | $run_forge |"
    echo "| waypoint-deploy | $run_waypoint |"
    echo "| fabric (provision) | $run_fabric |"
    echo "| onelake-upload | $run_onelake_upload |"
    echo "| contracts-kb-upload | $run_contracts_kb |"
    echo "| seed-import | $run_seed |"
    echo "| wire | $run_wire |"
    echo ""
    echo "_overrides: force_all=$force_all force_fabric=$force_fabric only='${only:-}' skip='${skip:-}'_"
  } >> "$GITHUB_STEP_SUMMARY"
fi
