#!/usr/bin/env bash
#
# Idempotent LOCAL OneLake configuration for the Waypoint API.
#
# This is the local-development counterpart to infra/scripts/provision-fabric.sh. It does NOT
# create or modify any Fabric resource: it RESOLVES the existing corpus workspace GUID from its
# stable display name (via the Fabric REST API, using your `az login`) and upserts the
# APP_ONELAKE_* settings into a local, gitignored `api/.env`. Both `aspire run` and a direct
# `uvicorn` launch run from `./api`, so that file is picked up automatically by app/common/settings.py.
#
# Why resolve-by-name instead of hardcoding a GUID: the workspace GUID is tenant- and
# deploy-specific and changes if the workspace is recreated. Resolving from the display name keeps
# local config correct without pasting GUIDs into /tmp env files. Re-running just refreshes the value.
#
# This script touches NOTHING that the deploy / keystone one-click path uses. In a deployed
# container there is no `api/.env`; apphost.cs sets real APP_ONELAKE_* env vars which always take
# precedence over a dotenv file in pydantic-settings.
#
# Prerequisites:
#   - `az login` as a user who is a Member of the Fabric corpus workspace.
#   - python3 (used for JSON parsing, same as provision-fabric.sh).
#
# Usage:
#   ./infra/scripts/onelake-local-env.sh
#   WAYPOINT_FABRIC_WORKSPACE_NAME=waypoint-corpus ./infra/scripts/onelake-local-env.sh
#
# Configuration (env overrides; sensible defaults match the deploy workflow):
#   WAYPOINT_FABRIC_WORKSPACE_NAME   Workspace display name to resolve (default "waypoint-corpus").
#   WAYPOINT_FABRIC_LAKEHOUSE_NAME   Lakehouse display name              (default "corpus").
#   WAYPOINT_FABRIC_CORPUS_PREFIX    Corpus path prefix                  (default "Files/corpus").
#   WAYPOINT_FABRIC_ACCOUNT_URL      OneLake DFS endpoint                (default https://onelake.dfs.fabric.microsoft.com).
#   WAYPOINT_FABRIC_API_BASE         Fabric REST base                    (default https://api.fabric.microsoft.com/v1).
#   WAYPOINT_LOCAL_ENV_FILE          Target dotenv file                  (default <repo>/api/.env).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WORKSPACE_NAME="${WAYPOINT_FABRIC_WORKSPACE_NAME:-waypoint-corpus}"
LAKEHOUSE_NAME="${WAYPOINT_FABRIC_LAKEHOUSE_NAME:-corpus}"
CORPUS_PREFIX="${WAYPOINT_FABRIC_CORPUS_PREFIX:-Files/corpus}"
ACCOUNT_URL="${WAYPOINT_FABRIC_ACCOUNT_URL:-https://onelake.dfs.fabric.microsoft.com}"
FABRIC_API_BASE="${WAYPOINT_FABRIC_API_BASE:-https://api.fabric.microsoft.com/v1}"
ENV_FILE="${WAYPOINT_LOCAL_ENV_FILE:-${REPO_ROOT}/api/.env}"

if ! command -v az >/dev/null 2>&1; then
  echo "[onelake-local] ERROR: the Azure CLI ('az') is required. Install it and run 'az login'." >&2
  exit 1
fi
if ! az account show >/dev/null 2>&1; then
  echo "[onelake-local] ERROR: not logged in. Run 'az login' first." >&2
  exit 1
fi

echo "[onelake-local] Acquiring Fabric API token..."
TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"

auth_get() {
  curl -sS -H "Authorization: Bearer ${TOKEN}" "$1"
}

echo "[onelake-local] Resolving workspace '${WORKSPACE_NAME}' (read-only)..."
WORKSPACE_ID="$(
  auth_get "${FABRIC_API_BASE}/workspaces" \
    | python3 -c "import sys,json; n=sys.argv[1]; d=json.load(sys.stdin); print(next((w['id'] for w in d.get('value',[]) if w.get('displayName')==n), ''))" \
      "${WORKSPACE_NAME}"
)"

if [[ -z "${WORKSPACE_ID}" ]]; then
  echo "[onelake-local] ERROR: no workspace named '${WORKSPACE_NAME}' is visible to this identity." >&2
  echo "[onelake-local] Either you are not a Member of it, or it has not been provisioned yet." >&2
  echo "[onelake-local] Workspaces visible to you:" >&2
  auth_get "${FABRIC_API_BASE}/workspaces" \
    | python3 -c "import sys,json; [print('  -',w.get('displayName')) for w in json.load(sys.stdin).get('value',[])]" >&2 || true
  exit 1
fi
echo "[onelake-local] Workspace GUID = ${WORKSPACE_ID}"

# Best-effort confirmation that the lakehouse exists; warn but do not fail (reads may still resolve
# once it is created, and this script never mutates Fabric state).
LAKEHOUSE_ID="$(
  auth_get "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/lakehouses" \
    | python3 -c "import sys,json; n=sys.argv[1]; d=json.load(sys.stdin); print(next((l['id'] for l in d.get('value',[]) if l.get('displayName')==n), ''))" \
      "${LAKEHOUSE_NAME}" 2>/dev/null || true
)"
if [[ -z "${LAKEHOUSE_ID}" ]]; then
  echo "[onelake-local] WARN: lakehouse '${LAKEHOUSE_NAME}' not found in the workspace yet (continuing)."
fi

# Idempotent upsert: replace any existing APP_ONELAKE_* line, preserve everything else, append fresh.
upsert() {
  local key="$1" value="$2"
  local tmp="${ENV_FILE}.tmp"
  touch "${ENV_FILE}"
  grep -v "^${key}=" "${ENV_FILE}" > "${tmp}" 2>/dev/null || true
  mv "${tmp}" "${ENV_FILE}"
  printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
}

echo "[onelake-local] Writing APP_ONELAKE_* to ${ENV_FILE}..."
mkdir -p "$(dirname "${ENV_FILE}")"
upsert APP_ONELAKE_ACCOUNT_URL "${ACCOUNT_URL}"
upsert APP_ONELAKE_WORKSPACE "${WORKSPACE_ID}"
upsert APP_ONELAKE_LAKEHOUSE "${LAKEHOUSE_NAME}"
upsert APP_ONELAKE_CORPUS_PREFIX "${CORPUS_PREFIX}"

echo "[onelake-local] Done."
echo "[onelake-local]   APP_ONELAKE_ACCOUNT_URL=${ACCOUNT_URL}"
echo "[onelake-local]   APP_ONELAKE_WORKSPACE=${WORKSPACE_ID}"
echo "[onelake-local]   APP_ONELAKE_LAKEHOUSE=${LAKEHOUSE_NAME}"
echo "[onelake-local]   APP_ONELAKE_CORPUS_PREFIX=${CORPUS_PREFIX}"
echo "[onelake-local] Restart the API (uvicorn or 'aspire run') to pick up the new config."
