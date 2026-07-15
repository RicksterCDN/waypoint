#!/usr/bin/env bash
# Run one deploy stage locally (mirror of the deploy.yml jobs).
# Usage: run_stage.sh <ledgerfield|forge|waypoint|onelake-upload|seed-import> <git-ref>
#
# This is the LOCAL counterpart to the GitHub Actions workflow, and is BEST-EFFORT only.
# In CI the canonical path is .github/workflows/deploy.yml (which calls each repo's reusable
# workflow, with preflight gating + RG-tag state). Locally these steps just drive the same
# az/azd/aspire/uv tools against checkouts beside this repo — no gating, no state recording.
# Each stage is idempotent. Prefer CI for a real one-click deploy.
#
# Env (from .env):
#   AZD_ENV_NAME (default forge)
#   LEDGERFIELD_DIR / FORGE_DIR / WAYPOINT_DIR (default ../ledgerfield, ../forge, ../waypoint)
#   ONELAKE_WORKSPACE / ONELAKE_LAKEHOUSE / ONELAKE_ACCOUNT_URL / WAYPOINT_FABRIC_CORPUS_PREFIX
#   API_FQDN / WAYPOINT_ADMIN_API_KEY  (for seed-import)
set -euo pipefail

stage="${1:?stage required}"
ref="${2:-main}"

env_name="${AZD_ENV_NAME:-forge}"
ledgerfield_dir="${LEDGERFIELD_DIR:-../ledgerfield}"
forge_dir="${FORGE_DIR:-../forge}"
waypoint_dir="${WAYPOINT_DIR:-../waypoint}"

echo "==> keystone stage: ${stage} @ ${ref}"
case "${stage}" in
  ledgerfield)
    if [[ -d "${ledgerfield_dir}" ]]; then
      ( cd "${ledgerfield_dir}" && git fetch --quiet && git checkout --quiet "${ref}" \
        && uv sync && uv run ledgerfield generate-waypoint-seed | tee counts.json )
      echo "    seed at ${ledgerfield_dir}/data/waypoint/waypoint-seed.json"
    else
      echo "    WARN: ${ledgerfield_dir} not found; skipping seed generation" >&2
    fi
    ;;
  forge)
    if [[ -d "${forge_dir}" ]]; then
      ( cd "${forge_dir}" && git fetch --quiet && git checkout --quiet "${ref}" \
        && azd env select "${env_name}" \
        && azd provision --no-prompt \
        && azd deploy --no-prompt )
      echo "    (API keys come from the deploy-provisioned Key Vault; see scripts/keyvault.sh.)"
    else
      echo "    WARN: ${forge_dir} not found; skipping forge deploy" >&2
    fi
    ;;
  waypoint)
    if [[ -d "${waypoint_dir}" ]]; then
      # Dual auth: agents use x-api-key (composed by keygen), humans use MSAL.
      ( cd "${waypoint_dir}" && git fetch --quiet && git checkout --quiet "${ref}" \
        && aspire deploy --non-interactive )
      echo "    Capture api_fqdn/web_fqdn/api_app_id_uri/api_default_scope (+ onelake_* if Fabric)."
    else
      echo "    WARN: ${waypoint_dir} not found; skipping waypoint deploy" >&2
    fi
    ;;
  onelake-upload)
    : "${ONELAKE_WORKSPACE:?set ONELAKE_WORKSPACE (waypoint onelake_workspace output)}"
    : "${ONELAKE_LAKEHOUSE:?set ONELAKE_LAKEHOUSE (waypoint onelake_lakehouse output)}"
    if [[ -d "${ledgerfield_dir}" ]]; then
      ( cd "${ledgerfield_dir}" && uv run ledgerfield upload-onelake \
          --workspace "${ONELAKE_WORKSPACE}" \
          --lakehouse "${ONELAKE_LAKEHOUSE}" \
          --account-url "${ONELAKE_ACCOUNT_URL:-https://onelake.dfs.fabric.microsoft.com}" \
          --corpus-prefix "${WAYPOINT_FABRIC_CORPUS_PREFIX:-Files/corpus}" )
    else
      echo "    WARN: ${ledgerfield_dir} not found; skipping onelake upload" >&2
    fi
    ;;
  seed-import)
    : "${API_FQDN:?set API_FQDN (waypoint api_fqdn output)}"
    : "${WAYPOINT_ADMIN_API_KEY:?set WAYPOINT_ADMIN_API_KEY (keygen admin key)}"
    API_FQDN="${API_FQDN}" ADMIN_API_KEY="${WAYPOINT_ADMIN_API_KEY}" \
      SEED_FILE="${SEED_FILE:-${ledgerfield_dir}/data/waypoint/waypoint-seed.json}" \
      scripts/seed_import.sh
    ;;
  *)
    echo "unknown stage: ${stage}" >&2; exit 2 ;;
esac
