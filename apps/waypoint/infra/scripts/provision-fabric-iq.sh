#!/usr/bin/env bash
#
# Idempotent, notebook-free provisioning of the Waypoint "Fabric IQ" AI layer:
# a Direct Lake **semantic model** + a published **Fabric Data Agent** over the mirrored operational
# Postgres financial core (default) — or the keystone corpus lakehouse Tables (legacy source).
#
# This is the IaC replacement for the interactive Deploy_EnterpriseLakehouse_VendorModel notebook's
# AI steps. It runs as an azd `postprovision` hook AFTER provision-fabric.sh has created the
# workspace + lakehouse, and as a deploy-workflow step. Semantic models and Data Agents are NOT
# ARM/Bicep types, so — exactly like the workspace/lakehouse — they are created through the Fabric
# public REST API. All the REST orchestration lives in the companion provision_fabric_iq.py; this
# wrapper only gates execution and mints the Entra tokens that module consumes.
#
# The whole thing is safe to re-run: every item is looked up by display name and updated in place.
#
# Prerequisites:
#   - The lakehouse already has managed Delta Tables written (ledgerfield onelake-upload).
#   - A running, paid F-SKU capacity (F64 recommended — Data Agents/Copilot require a paid F-SKU).
#   - The deploy identity is a workspace Member (provision-fabric.sh grants this) and the Fabric
#     tenant "Copilot / Fabric data agent" AI settings are enabled (see docs/fabric-iq.md).
#
# Required environment (set by azd / the deploy workflow):
#   WAYPOINT_FABRIC_IQ_ENABLED        "true" to run; anything else is a no-op.
#   WAYPOINT_FABRIC_WORKSPACE         Workspace GUID (preferred), OR
#   WAYPOINT_FABRIC_WORKSPACE_NAME    Workspace display name (resolved when the GUID is absent).
# Source selection:
#   WAYPOINT_FABRIC_IQ_SOURCE         "mirrored" (default) grounds the model on the mirrored
#                                     operational Postgres financial core; "lakehouse" grounds it on
#                                     the keystone corpus Delta Tables (legacy).
#   WAYPOINT_FABRIC_MIRRORED_DATABASE_NAME  Mirrored DB item display name (mirrored; default WaypointMirror).
#   WAYPOINT_FABRIC_MIRRORED_SCHEMA         SQL schema of the mirrored tables (mirrored; default "_public").
#   WAYPOINT_FABRIC_LAKEHOUSE_NAME    Lakehouse display name (required only for the lakehouse source).
# Optional:
#   WAYPOINT_FABRIC_SEMANTIC_MODEL_NAME  Default "CaldovaIQ".
#   WAYPOINT_FABRIC_DATA_AGENT_NAME      Default "WaypointDataAgent".
#   WAYPOINT_FABRIC_IQ_TABLES            Comma-separated table allow-list (default: all managed).
#   WAYPOINT_FABRIC_IQ_AGENT_STRICT      "true" (default) fails on Data Agent errors; "false" warns.
#   WAYPOINT_FABRIC_IQ_AGENT_INSTRUCTIONS  Override the Data Agent steering instructions.
#   WAYPOINT_FABRIC_IQ_ALLOW_EMPTY       "true" turns "no Tables yet" into a soft skip (exit 0) so a
#                                        first one-click deploy (Tables uploaded later by ledgerfield)
#                                        doesn't fail; a later idempotent re-run then completes IQ.
#   WAYPOINT_FABRIC_API_BASE / WAYPOINT_ONELAKE_DFS_BASE  API base overrides.

set -euo pipefail

if [[ "${WAYPOINT_FABRIC_IQ_ENABLED:-false}" != "true" ]]; then
  echo "[fabric-iq] WAYPOINT_FABRIC_IQ_ENABLED is not 'true'; skipping Fabric IQ provisioning."
  exit 0
fi

IQ_SOURCE_TYPE="${WAYPOINT_FABRIC_IQ_SOURCE:-mirrored}"
if [[ "${IQ_SOURCE_TYPE}" == "lakehouse" && -z "${WAYPOINT_FABRIC_LAKEHOUSE_NAME:-}" ]]; then
  echo "[fabric-iq] ERROR: WAYPOINT_FABRIC_LAKEHOUSE_NAME is required when WAYPOINT_FABRIC_IQ_SOURCE=lakehouse." >&2
  exit 1
fi

# Prefer an explicit workspace GUID (published by provision-fabric.sh as APP_ONELAKE_WORKSPACE),
# falling back to the display name for resolution inside the Python engine.
WORKSPACE_ID="${WAYPOINT_FABRIC_WORKSPACE:-${APP_ONELAKE_WORKSPACE:-}}"
WORKSPACE_NAME="${WAYPOINT_FABRIC_WORKSPACE_NAME:-}"
if [[ -z "${WORKSPACE_ID}" && -z "${WORKSPACE_NAME}" ]]; then
  echo "[fabric-iq] ERROR: set WAYPOINT_FABRIC_WORKSPACE (GUID) or WAYPOINT_FABRIC_WORKSPACE_NAME." >&2
  exit 1
fi

echo "[fabric-iq] Acquiring Entra tokens (Fabric API + OneLake storage + Power BI)..."
FABRIC_TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"
STORAGE_TOKEN="$(az account get-access-token --resource https://storage.azure.com --query accessToken -o tsv)"
# Power BI token is best-effort (used only for an explicit Direct Lake refresh); never fatal.
POWERBI_TOKEN="$(az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv 2>/dev/null || true)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FABRIC_TOKEN="${FABRIC_TOKEN}" \
STORAGE_TOKEN="${STORAGE_TOKEN}" \
POWERBI_TOKEN="${POWERBI_TOKEN}" \
FABRIC_API_BASE="${WAYPOINT_FABRIC_API_BASE:-https://api.fabric.microsoft.com/v1}" \
ONELAKE_DFS_BASE="${WAYPOINT_ONELAKE_DFS_BASE:-https://onelake.dfs.fabric.microsoft.com}" \
WORKSPACE_ID="${WORKSPACE_ID}" \
WORKSPACE_NAME="${WORKSPACE_NAME}" \
IQ_SOURCE_TYPE="${IQ_SOURCE_TYPE}" \
MIRRORED_DATABASE_NAME="${WAYPOINT_FABRIC_MIRRORED_DATABASE_NAME:-WaypointMirror}" \
MIRRORED_SCHEMA_NAME="${WAYPOINT_FABRIC_MIRRORED_SCHEMA:-_public}" \
LAKEHOUSE_NAME="${WAYPOINT_FABRIC_LAKEHOUSE_NAME:-}" \
SEMANTIC_MODEL_NAME="${WAYPOINT_FABRIC_SEMANTIC_MODEL_NAME:-CaldovaIQ}" \
DATA_AGENT_NAME="${WAYPOINT_FABRIC_DATA_AGENT_NAME:-WaypointDataAgent}" \
TABLES="${WAYPOINT_FABRIC_IQ_TABLES:-}" \
AGENT_STRICT="${WAYPOINT_FABRIC_IQ_AGENT_STRICT:-true}" \
AGENT_INSTRUCTIONS="${WAYPOINT_FABRIC_IQ_AGENT_INSTRUCTIONS:-}" \
IQ_ALLOW_EMPTY="${WAYPOINT_FABRIC_IQ_ALLOW_EMPTY:-false}" \
  python3 "${SCRIPT_DIR}/provision_fabric_iq.py"

# Surface the model/agent names to the azd environment so the next `aspire deploy` can publish them
# onto the API (APP_FABRIC_IQ_*) for /config deep-linking.
if command -v azd >/dev/null 2>&1; then
  azd env set APP_FABRIC_IQ_SEMANTIC_MODEL "${WAYPOINT_FABRIC_SEMANTIC_MODEL_NAME:-CaldovaIQ}" || true
  azd env set APP_FABRIC_IQ_DATA_AGENT "${WAYPOINT_FABRIC_DATA_AGENT_NAME:-WaypointDataAgent}" || true
fi

echo "[fabric-iq] Done."
