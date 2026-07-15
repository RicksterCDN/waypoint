#!/usr/bin/env bash
# Stage 4 (wire): push the cross-repo endpoint/config values back to forge.
#
# Auth is handled out-of-band by scripts/keyvault.sh (stage 0a), which provisions a Key Vault and seeds the
# x-api-key secrets into BOTH azd env stores up front — so wire no longer grants
# app roles or touches managed-identity principal ids. It is now a pure
# endpoint/config push (consumer of the already-deployed outputs).
#
# Required env (set by deploy.yml or .env):
#   API_FQDN, API_DEFAULT_SCOPE, API_APP_ID_URI  — waypoint deploy outputs
#   WORKIQ_ENDPOINT .. AGGREGATOR_ENDPOINT        — forge fan-out endpoints
#   AZD_ENV_NAME (default forge), FORGE_DIR (default ../forge)
set -euo pipefail

env_name="${AZD_ENV_NAME:-forge}"
forge_dir="${FORGE_DIR:-../forge}"
api_scope="${API_DEFAULT_SCOPE:-${API_APP_ID_URI:-<unset>}/.default}"

echo "==> Pushing forge agent configuration into azd env '${env_name}' (${forge_dir})"
echo "    WAYPOINT_API_BASE_URL=${API_FQDN:-<unset>}"
echo "    WAYPOINT_API_SCOPE=${api_scope}"
echo "    WORKIQ_EXPERT_ENDPOINT=${WORKIQ_ENDPOINT:-<unset>}"
echo "    WEBIQ_EXPERT_ENDPOINT=${WEBIQ_ENDPOINT:-<unset>}"
echo "    FOUNDRYIQ_EXPERT_ENDPOINT=${FOUNDRYIQ_ENDPOINT:-<unset>}"
echo "    FABRICIQ_EXPERT_ENDPOINT=${FABRICIQ_ENDPOINT:-<unset>}"
echo "    AGGREGATOR_ENDPOINT=${AGGREGATOR_ENDPOINT:-<unset>}"

if [[ -d "${forge_dir}" ]]; then
  azd env set WAYPOINT_API_BASE_URL "${API_FQDN:-}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set WAYPOINT_API_SCOPE "${api_scope}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set WORKIQ_EXPERT_ENDPOINT "${WORKIQ_ENDPOINT:-}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set WEBIQ_EXPERT_ENDPOINT "${WEBIQ_ENDPOINT:-}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set FOUNDRYIQ_EXPERT_ENDPOINT "${FOUNDRYIQ_ENDPOINT:-}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set FABRICIQ_EXPERT_ENDPOINT "${FABRICIQ_ENDPOINT:-}" --cwd "${forge_dir}" --environment "${env_name}"
  azd env set AGGREGATOR_ENDPOINT "${AGGREGATOR_ENDPOINT:-}" --cwd "${forge_dir}" --environment "${env_name}"
  echo "    TODO: redeploy pacioli/status-concierge so the new endpoint config takes effect."
else
  echo "    WARN: forge dir '${forge_dir}' not found; skipping forge azd env push" >&2
fi
