#!/usr/bin/env bash
#
# Idempotent suspend / resume / scale / status control for the Waypoint Microsoft Fabric capacity.
#
# F64 (the Fabric IQ baseline) bills while the capacity is Active, so this script is the "turn it off
# when idle" half of the cost story. It is called on-demand by humans/keystone and on a cron by
# .github/workflows/fabric-capacity-scheduler.yml. Every action is a no-op when the capacity is
# already in the requested state, so it is safe to run repeatedly and from a schedule.
#
# Uses the Microsoft.Fabric/capacities ARM control plane via `az rest`:
#   - suspend / resume : POST .../<capacity>/suspend|resume  (stops / starts compute billing)
#   - scale <SKU>      : PATCH sku.name                       (resize, e.g. F64 <-> F2, stays queryable)
#   - status           : GET  state + sku
#
# Usage:
#   fabric-capacity-control.sh suspend
#   fabric-capacity-control.sh resume
#   fabric-capacity-control.sh scale F2
#   fabric-capacity-control.sh status
#
# Target resolution (in priority order):
#   WAYPOINT_FABRIC_CAPACITY_ID                       full ARM resource id, OR
#   WAYPOINT_FABRIC_CAPACITY_NAME + AZURE_RESOURCE_GROUP (+ optional AZURE_SUBSCRIPTION_ID)
#
# Env:
#   FABRIC_API_VERSION   ARM api-version (default 2023-11-01).

set -euo pipefail

API_VERSION="${FABRIC_API_VERSION:-2023-11-01}"

usage() {
  cat >&2 <<'EOF'
Usage: fabric-capacity-control.sh <suspend|resume|scale|status> [SKU]
  suspend       Pause the capacity (stops compute billing). No-op if already paused.
  resume        Resume the capacity. No-op if already active.
  scale <SKU>   Resize the capacity SKU (e.g. F64, F2). No-op if already that SKU.
  status        Print the capacity state and SKU.
EOF
  exit 2
}

ACTION="${1:-}"
[[ -z "${ACTION}" ]] && usage

# --- Resolve the capacity ARM resource id ------------------------------------------------
CAPACITY_ID="${WAYPOINT_FABRIC_CAPACITY_ID:-}"
if [[ -z "${CAPACITY_ID}" ]]; then
  if [[ -z "${WAYPOINT_FABRIC_CAPACITY_NAME:-}" || -z "${AZURE_RESOURCE_GROUP:-}" ]]; then
    echo "[capacity] ERROR: set WAYPOINT_FABRIC_CAPACITY_ID, or WAYPOINT_FABRIC_CAPACITY_NAME + AZURE_RESOURCE_GROUP." >&2
    exit 1
  fi
  echo "[capacity] Resolving capacity '${WAYPOINT_FABRIC_CAPACITY_NAME}' in RG '${AZURE_RESOURCE_GROUP}'..."
  CAPACITY_ID="$(az resource show \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${WAYPOINT_FABRIC_CAPACITY_NAME}" \
    --resource-type Microsoft.Fabric/capacities \
    ${AZURE_SUBSCRIPTION_ID:+--subscription "${AZURE_SUBSCRIPTION_ID}"} \
    --query id --output tsv)"
fi
if [[ -z "${CAPACITY_ID}" ]]; then
  echo "[capacity] ERROR: could not resolve the Fabric capacity resource id." >&2
  exit 1
fi

get_state() { az rest --method get --url "${CAPACITY_ID}?api-version=${API_VERSION}" --query "properties.state" -o tsv 2>/dev/null || echo ""; }
get_sku()   { az rest --method get --url "${CAPACITY_ID}?api-version=${API_VERSION}" --query "sku.name"          -o tsv 2>/dev/null || echo ""; }

STATE="$(get_state)"
SKU="$(get_sku)"
echo "[capacity] ${CAPACITY_ID##*/}: state=${STATE:-unknown} sku=${SKU:-unknown}"

case "${ACTION}" in
  status)
    exit 0
    ;;

  suspend)
    # Fabric reports the paused state as "Paused"; older/API variants may say "Suspended".
    if [[ "${STATE}" == "Paused" || "${STATE}" == "Suspended" ]]; then
      echo "[capacity] Already paused; nothing to do."
      exit 0
    fi
    echo "[capacity] Suspending (stops compute billing)..."
    az rest --method post --url "${CAPACITY_ID}/suspend?api-version=${API_VERSION}"
    echo "[capacity] Suspend requested."
    ;;

  resume)
    if [[ "${STATE}" == "Active" ]]; then
      echo "[capacity] Already active; nothing to do."
      exit 0
    fi
    echo "[capacity] Resuming..."
    az rest --method post --url "${CAPACITY_ID}/resume?api-version=${API_VERSION}"
    echo "[capacity] Resume requested."
    ;;

  scale)
    TARGET_SKU="${2:-}"
    if [[ -z "${TARGET_SKU}" ]]; then
      echo "[capacity] ERROR: scale requires a target SKU (e.g. F64, F2)." >&2
      usage
    fi
    if [[ "${SKU}" == "${TARGET_SKU}" ]]; then
      echo "[capacity] Already at SKU ${TARGET_SKU}; nothing to do."
      exit 0
    fi
    echo "[capacity] Scaling ${SKU:-unknown} -> ${TARGET_SKU}..."
    az rest --method patch --url "${CAPACITY_ID}?api-version=${API_VERSION}" \
      --headers "Content-Type=application/json" \
      --body "{\"sku\":{\"name\":\"${TARGET_SKU}\",\"tier\":\"Fabric\"}}"
    echo "[capacity] Scale to ${TARGET_SKU} requested."
    ;;

  *)
    usage
    ;;
esac

echo "[capacity] New state: $(get_state) sku: $(get_sku)"
