#!/usr/bin/env bash
# Idempotently grant one or more principals a role on an EXISTING Fabric workspace.
#
# provision-fabric.sh already grants the Waypoint managed identity and the deploy service principal a
# workspace Member role, but that only runs on the provisioning path (WAYPOINT_FABRIC_PROVISION_ENABLED
# =true, i.e. when the workspace is first stood up). When a later deploy REUSES an already-provisioned
# workspace (fabric_provision_enabled=false) and then enables the Fabric mirror / Fabric IQ steps, those
# steps call the Fabric REST API as the deploy service principal and 401 unless that principal is a
# workspace member. This script closes that gap for the reuse path without re-running provisioning, so
# the whole flow stays idempotent and keystone-repeatable instead of needing a manual roleAssignments
# POST (which is how it was unblocked during the first live enablement).
#
# Look-up-before-create keeps it safe to run on every deploy: principals that already hold any workspace
# role are left untouched (the role is not downgraded/upgraded).
#
# Environment:
#   WAYPOINT_FABRIC_WORKSPACE            Fabric workspace GUID to grant roles on (required).
#   WAYPOINT_FABRIC_WORKSPACE_ROLE       Role to grant (default Member).
#   WAYPOINT_FABRIC_ROLE_PRINCIPALS      Space/newline-separated list of "<object_id>:<type>:<label>"
#                                        entries, e.g. "43360839-...:ServicePrincipal:the deploy SP".
#                                        <type> defaults to ServicePrincipal and <label> to the id when
#                                        omitted. Empty ids are skipped.
set -euo pipefail

FABRIC_API_BASE="https://api.fabric.microsoft.com/v1"
WORKSPACE_ID="${WAYPOINT_FABRIC_WORKSPACE:-}"
ROLE="${WAYPOINT_FABRIC_WORKSPACE_ROLE:-Member}"

if [[ -z "${WORKSPACE_ID}" ]]; then
  echo "[fabric-roles] WAYPOINT_FABRIC_WORKSPACE not set; nothing to do."
  exit 0
fi
if [[ -z "${WAYPOINT_FABRIC_ROLE_PRINCIPALS:-}" ]]; then
  echo "[fabric-roles] WAYPOINT_FABRIC_ROLE_PRINCIPALS not set; nothing to do."
  exit 0
fi

echo "[fabric-roles] Acquiring Fabric API token..."
TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"

auth_get() {
  curl -sS -H "Authorization: Bearer ${TOKEN}" "$1"
}
auth_post() {
  curl -sS -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -X POST "$1" -d "$2"
}

# Idempotently grant a single principal the workspace role (look-up-before-create).
ensure_workspace_role() {
  local principal_id="$1" principal_type="$2" principal_label="$3"
  if [[ -z "${principal_id}" ]]; then
    echo "[fabric-roles] ${principal_label} principal id empty; skipping."
    return 0
  fi
  echo "[fabric-roles] Ensuring ${principal_label} (${principal_id}) has a workspace role..."
  local existing
  existing="$(
    auth_get "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/roleAssignments" \
      | python3 -c "import sys,json; p=sys.argv[1]; d=json.load(sys.stdin); print(next((r['id'] for r in d.get('value',[]) if r.get('principal',{}).get('id')==p), ''))" \
        "${principal_id}"
  )"
  if [[ -n "${existing}" ]]; then
    echo "[fabric-roles] ${principal_label} already has a workspace role; leaving as-is."
    return 0
  fi
  local body
  body="$(python3 -c "import json,sys; print(json.dumps({'principal': {'id': sys.argv[1], 'type': sys.argv[2]}, 'role': sys.argv[3]}))" \
    "${principal_id}" "${principal_type}" "${ROLE}")"
  auth_post "${FABRIC_API_BASE}/workspaces/${WORKSPACE_ID}/roleAssignments" "${body}" >/dev/null
  echo "[fabric-roles] Granted ${ROLE} to ${principal_label}."
}

while IFS= read -r entry; do
  [[ -z "${entry// }" ]] && continue
  pid="${entry%%:*}"
  rest="${entry#*:}"
  if [[ "${rest}" == "${entry}" ]]; then
    ptype="ServicePrincipal"; plabel="${pid}"
  else
    ptype="${rest%%:*}"
    plabel="${rest#*:}"
    [[ -z "${ptype}" ]] && ptype="ServicePrincipal"
    [[ "${plabel}" == "${rest}" || -z "${plabel}" ]] && plabel="${pid}"
  fi
  ensure_workspace_role "${pid}" "${ptype}" "${plabel}"
done <<< "${WAYPOINT_FABRIC_ROLE_PRINCIPALS}"

echo "[fabric-roles] Done."
