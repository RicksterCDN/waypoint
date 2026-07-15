#!/usr/bin/env bash
#
# Idempotent Microsoft Fabric provisioning for the Waypoint OneLake corpus lake.
#
# Runs as an azd `postprovision` hook AFTER `infra/fabric-capacity.bicep` has created the Fabric
# capacity. Fabric workspaces and lakehouses are NOT ARM/Bicep-provisionable, so this script calls
# the Fabric REST API (https://api.fabric.microsoft.com) to:
#   1. create (or reuse) a workspace and assign it to the capacity,
#   2. create (or reuse) a lakehouse in that workspace,
#   3. grant the Waypoint container-app managed identity AND the deploy service principal a workspace
#      Member role (the MI for runtime OneLake reads; the deploy SP for corpus uploads/writes),
#   4. publish APP_ONELAKE_* values back to the azd environment.
#
# The script is safe to re-run: every create is preceded by a look-up by display name.
#
# Prerequisites (one-time tenant verification — see docs/onelake-corpus.md):
#   - Fabric tenant Developer settings "Service principals can call Fabric public APIs" and
#     "Service principals can create workspaces, connections, and deployment pipelines" enabled for
#     the deploy identity and the Waypoint managed identity. In the caldova tenant these are
#     enabled org-wide, so no action is required there.
#
# Required environment (set by azd / apphost or `azd env set`):
#   WAYPOINT_FABRIC_PROVISION_ENABLED   "true" to run; anything else is a no-op.
#   WAYPOINT_FABRIC_CAPACITY_ID         ARM resource id of the Fabric capacity (bicep output).
#   WAYPOINT_FABRIC_WORKSPACE_NAME      Display name for the workspace (e.g. "waypoint-corpus").
#   WAYPOINT_FABRIC_LAKEHOUSE_NAME      Display name for the lakehouse (e.g. "corpus").
# Optional:
#   WAYPOINT_FABRIC_MI_PRINCIPAL_ID     Object id of the Waypoint container-app managed identity.
#   WAYPOINT_FABRIC_DEPLOY_PRINCIPAL_ID Object id of the deploy/OIDC service principal that writes the
#                                       corpus into OneLake (e.g. keystone's deploy identity / the
#                                       ledgerfield onelake-upload stage). Granted a workspace Member role.
#   WAYPOINT_FABRIC_CORPUS_PREFIX       Defaults to "Files/corpus".
#   WAYPOINT_FABRIC_API_BASE            Defaults to "https://api.fabric.microsoft.com/v1".

set -euo pipefail

FABRIC_API_BASE="${WAYPOINT_FABRIC_API_BASE:-https://api.fabric.microsoft.com/v1}"
CORPUS_PREFIX="${WAYPOINT_FABRIC_CORPUS_PREFIX:-Files/corpus}"

if [[ "${WAYPOINT_FABRIC_PROVISION_ENABLED:-false}" != "true" ]]; then
  echo "[fabric] WAYPOINT_FABRIC_PROVISION_ENABLED is not 'true'; skipping Fabric provisioning."
  exit 0
fi

for required in WAYPOINT_FABRIC_CAPACITY_ID WAYPOINT_FABRIC_WORKSPACE_NAME WAYPOINT_FABRIC_LAKEHOUSE_NAME; do
  if [[ -z "${!required:-}" ]]; then
    echo "[fabric] ERROR: $required is required when provisioning is enabled." >&2
    exit 1
  fi
done

echo "[fabric] Acquiring Fabric API token..."
TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"

auth_get() {
  curl -sS -H "Authorization: Bearer ${TOKEN}" "$1"
}

auth_post() {
  curl -sS -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -X POST "$1" -d "$2"
}

# The Fabric REST API identifies a capacity by its Fabric capacity GUID, NOT the ARM resource id.
# WAYPOINT_FABRIC_CAPACITY_ID is the ARM id (…/Microsoft.Fabric/capacities/<name>); resolve the
# matching Fabric capacity GUID from GET /v1/capacities by display name (= the ARM capacity name).
CAPACITY_NAME="${WAYPOINT_FABRIC_CAPACITY_ID##*/}"
echo "[fabric] Resolving Fabric capacity GUID for '${CAPACITY_NAME}'..."
CAPACITY_GUID="$(
  auth_get "${FABRIC_API_BASE}/capacities" \
    | python3 -c "import sys,json; n=sys.argv[1].lower(); d=json.load(sys.stdin); print(next((c['id'] for c in d.get('value',[]) if (c.get('displayName') or '').lower()==n), ''))" \
      "${CAPACITY_NAME}"
)"
if [[ -z "${CAPACITY_GUID}" ]]; then
  echo "[fabric] ERROR: could not resolve a Fabric capacity GUID for '${CAPACITY_NAME}'." >&2
  echo "[fabric] Capacities visible to this identity:" >&2
  auth_get "${FABRIC_API_BASE}/capacities" >&2 || true
  exit 1
fi
echo "[fabric] Capacity GUID = ${CAPACITY_GUID}"

# Idempotently grant a principal a workspace Member role (look-up-before-create).
# Args: <principal_object_id> <human label>
ensure_workspace_role() {
  local principal_id="$1"
  local principal_label="$2"
  if [[ -z "${principal_id}" ]]; then
    echo "[fabric] ${principal_label} principal id not set; skipping role assignment."
    return 0
  fi
  echo "[fabric] Ensuring ${principal_label} has a workspace Member role..."
  local existing_role
  existing_role="$(
    auth_get "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/roleAssignments" \
      | python3 -c "import sys,json; p=sys.argv[1]; d=json.load(sys.stdin); print(next((r['id'] for r in d.get('value',[]) if r.get('principal',{}).get('id')==p), ''))" \
        "${principal_id}"
  )"
  if [[ -z "${existing_role}" ]]; then
    local role_body
    role_body="$(python3 -c "import json,sys; print(json.dumps({'principal': {'id': sys.argv[1], 'type': 'ServicePrincipal'}, 'role': 'Member'}))" "${principal_id}")"
    auth_post "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/roleAssignments" "${role_body}" >/dev/null
    echo "[fabric] Added workspace Member role for ${principal_label}."
  else
    echo "[fabric] ${principal_label} already has a workspace role."
  fi
}

echo "[fabric] Resolving workspace '${WAYPOINT_FABRIC_WORKSPACE_NAME}'..."
WORKSPACE_ID="$(
  auth_get "${FABRIC_API_BASE}/workspaces" \
    | python3 -c "import sys,json; n=sys.argv[1]; d=json.load(sys.stdin); print(next((w['id'] for w in d.get('value',[]) if w.get('displayName')==n), ''))" \
      "${WAYPOINT_FABRIC_WORKSPACE_NAME}"
)"

if [[ -z "${WORKSPACE_ID}" ]]; then
  echo "[fabric] Creating workspace and assigning capacity..."
  CREATE_BODY="$(WAYPOINT_FABRIC_CAPACITY_GUID="${CAPACITY_GUID}" python3 -c "import json,os; print(json.dumps({'displayName': os.environ['WAYPOINT_FABRIC_WORKSPACE_NAME'], 'capacityId': os.environ['WAYPOINT_FABRIC_CAPACITY_GUID']}))")"
  CREATE_RESPONSE="$(auth_post "${FABRIC_API_BASE}/workspaces" "${CREATE_BODY}")"
  WORKSPACE_ID="$(printf '%s' "${CREATE_RESPONSE}" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('id',''))
except Exception:
    print('')")"
  if [[ -z "${WORKSPACE_ID}" ]]; then
    echo "[fabric] ERROR: workspace create returned no id. Fabric API response:" >&2
    printf '%s\n' "${CREATE_RESPONSE}" >&2
    exit 1
  fi
else
  echo "[fabric] Workspace exists (${WORKSPACE_ID}); ensuring capacity assignment..."
  ASSIGN_BODY="$(WAYPOINT_FABRIC_CAPACITY_GUID="${CAPACITY_GUID}" python3 -c "import json,os; print(json.dumps({'capacityId': os.environ['WAYPOINT_FABRIC_CAPACITY_GUID']}))")"
  auth_post "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/assignToCapacity" "${ASSIGN_BODY}" >/dev/null || true
fi

if [[ -z "${WORKSPACE_ID}" ]]; then
  echo "[fabric] ERROR: failed to resolve or create workspace." >&2
  exit 1
fi

echo "[fabric] Resolving lakehouse '${WAYPOINT_FABRIC_LAKEHOUSE_NAME}'..."
LAKEHOUSE_ID="$(
  auth_get "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/lakehouses" \
    | python3 -c "import sys,json; n=sys.argv[1]; d=json.load(sys.stdin); print(next((l['id'] for l in d.get('value',[]) if l.get('displayName')==n), ''))" \
      "${WAYPOINT_FABRIC_LAKEHOUSE_NAME}"
)"

if [[ -z "${LAKEHOUSE_ID}" ]]; then
  echo "[fabric] Creating lakehouse..."
  LH_BODY="$(python3 -c "import json,os; print(json.dumps({'displayName': os.environ['WAYPOINT_FABRIC_LAKEHOUSE_NAME']}))")"
  LAKEHOUSE_ID="$(
    auth_post "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/lakehouses" "${LH_BODY}" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))"
  )"
else
  echo "[fabric] Lakehouse exists (${LAKEHOUSE_ID})."
fi

ensure_workspace_role "${WAYPOINT_FABRIC_MI_PRINCIPAL_ID:-}" "the Waypoint managed identity"
ensure_workspace_role "${WAYPOINT_FABRIC_DEPLOY_PRINCIPAL_ID:-}" "the deploy service principal"

echo "[fabric] Publishing APP_ONELAKE_* values to the azd environment..."
if command -v azd >/dev/null 2>&1; then
  azd env set APP_ONELAKE_WORKSPACE "${WORKSPACE_ID}" || true
  azd env set APP_ONELAKE_LAKEHOUSE "${WAYPOINT_FABRIC_LAKEHOUSE_NAME}" || true
  azd env set APP_ONELAKE_CORPUS_PREFIX "${CORPUS_PREFIX}" || true
fi

echo "[fabric] Done."
echo "[fabric]   workspace_id=${WORKSPACE_ID}"
echo "[fabric]   lakehouse=${WAYPOINT_FABRIC_LAKEHOUSE_NAME} (${LAKEHOUSE_ID})"
echo "[fabric]   corpus_prefix=${CORPUS_PREFIX}"

# In GitHub Actions, surface the resolved lake coordinates as step outputs so the reusable deploy
# workflow can emit them (workspace GUID + lakehouse) to keystone / ledgerfield's onelake-upload.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "onelake_workspace=${WORKSPACE_ID}"
    echo "onelake_lakehouse=${WAYPOINT_FABRIC_LAKEHOUSE_NAME}"
    echo "onelake_corpus_prefix=${CORPUS_PREFIX}"
  } >> "${GITHUB_OUTPUT}"
fi
