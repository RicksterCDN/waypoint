#!/usr/bin/env bash
# Stage 5 (seed-import): POST the deterministic ledgerfield seed into the freshly
# deployed Waypoint API.
#
# Waypoint exposes POST /api/admin/seed/ledgerfield (require_admin -> x-api-key
# admin). The request body is the LedgerfieldSeedImport JSON, which is exactly the
# shape of data/waypoint/waypoint-seed.json (the artifact published by stage 1).
# The endpoint is an UPSERT, so re-running is idempotent.
#
# The seed corpus also lives at onelake://Files/corpus/seed/waypoint-seed.json once
# ledgerfield-onelake-upload has run; this stage imports it into Postgres via the API
# so the app has queryable records.
#
# Required env:
#   SEED_FILE        path to waypoint-seed.json (default seed/data/waypoint/waypoint-seed.json)
# API FQDN resolution (first non-empty wins):
#   API_FQDN         Waypoint API FQDN (waypoint-deploy output api_fqdn)
#   RECORDED_API_FQDN  recorded API FQDN (preflight RG tag)
#   WAYPOINT_RG      waypoint resource group -> live `az containerapp show` discovery
# Admin key resolution (first non-empty wins):
#   ADMIN_API_KEY    admin x-api-key (provision-keyvault output; empty because GitHub
#                    strips masked secrets from job outputs)
#   KV_NAME          Key Vault name -> reads secret `waypoint-admin-key` directly
set -euo pipefail

seed_file="${SEED_FILE:-seed/data/waypoint/waypoint-seed.json}"

# --- Resolve the API FQDN ---------------------------------------------------------------
api_fqdn="${API_FQDN:-}"
[[ -n "$api_fqdn" ]] || api_fqdn="${RECORDED_API_FQDN:-}"
if [[ -z "$api_fqdn" && -n "${WAYPOINT_RG:-}" ]]; then
  echo "==> discovering Waypoint API FQDN in ${WAYPOINT_RG}…"
  api_fqdn="$(az containerapp show --name api --resource-group "$WAYPOINT_RG" \
    --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || true)"
fi
if [[ -z "$api_fqdn" ]]; then
  echo "::error::could not resolve the Waypoint API FQDN (API_FQDN/RECORDED_API_FQDN/WAYPOINT_RG all empty)." >&2
  exit 1
fi

# --- Resolve the admin x-api-key --------------------------------------------------------
# GitHub Actions refuses to propagate add-mask'd values as job outputs, so the
# provision-keyvault admin_api_key output arrives empty here. Read it straight from the
# Key Vault that generated it (Key Vault is the system of record for keystone secrets).
admin_key="${ADMIN_API_KEY:-}"
if [[ -z "$admin_key" && -n "${KV_NAME:-}" ]]; then
  echo "==> reading admin key from Key Vault ${KV_NAME}…"
  admin_key="$(az keyvault secret show --vault-name "$KV_NAME" --name waypoint-admin-key \
    --query value -o tsv 2>/dev/null || true)"
fi
if [[ -z "$admin_key" ]]; then
  echo "::error::could not resolve the Waypoint admin API key (ADMIN_API_KEY empty and KV_NAME lookup failed)." >&2
  exit 1
fi
echo "::add-mask::${admin_key}"

if [[ ! -f "$seed_file" ]]; then
  echo "::error::seed file not found: $seed_file" >&2
  exit 1
fi

url="https://${api_fqdn}/api/admin/seed/ledgerfield"
echo "==> seed-import: POST $(wc -c <"$seed_file") bytes -> ${url}"

# Retry to absorb cold-start / brief 5xx after a fresh container deploy.
attempt=0
max=5
until [[ $attempt -ge $max ]]; do
  attempt=$((attempt + 1))
  http_code="$(curl -sS -o /tmp/seed_import_resp.json -w '%{http_code}' \
    -X POST \
    -H "x-api-key: ${admin_key}" \
    -H "Content-Type: application/json" \
    --data-binary "@${seed_file}" \
    "${url}" || true)"
  if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
    echo "==> seed-import OK (HTTP ${http_code})"
    cat /tmp/seed_import_resp.json
    echo
    exit 0
  fi
  echo "    attempt ${attempt}/${max} got HTTP ${http_code}; retrying in $((attempt * 5))s" >&2
  sleep $((attempt * 5))
done

echo "::error::seed-import failed after ${max} attempts (last HTTP ${http_code})" >&2
cat /tmp/seed_import_resp.json >&2 || true
exit 1
