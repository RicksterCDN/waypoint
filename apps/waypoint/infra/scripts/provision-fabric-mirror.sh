#!/usr/bin/env bash
#
# Idempotent, notebook-free provisioning of the Waypoint FabricIQ *source mirror*:
# a Fabric "Mirrored Azure Database for PostgreSQL" that continuously replicates the
# operational financial core (suppliers, invoices, invoice_lines, reconciliation_findings)
# into OneLake, where the FabricIQ Direct Lake semantic model + Data Agent ground on it.
#
# This runs AFTER the Postgres server exists with the mirroring prerequisites provisioned
# (infra/postgres-flexible.bicep with enableFabricMirroring=true: SAMI + logical WAL +
# azure_cdc) and AFTER provision-fabric.sh has created the workspace. The dedicated PostgreSQL
# mirroring ROLE (fabric_user: login/createdb/createrole/replication + azure_cdc_admin + table
# ownership) is bootstrapped by the API at startup using the admin bootstrap connection it
# already holds (api/app/common/database.py::_bootstrap_fabric_mirroring_role), so this script
# needs NEITHER an admin connection NOR psql in the deploy path — only WAYPOINT_FABRIC_MIRROR_PASSWORD
# (the SAME value the API used, sourced from the keystone Key Vault) to create the Fabric
# connection credential. Mirrored databases are not ARM/Bicep types, so — like the
# workspace/lakehouse/semantic-model — they are created through the Fabric public REST API. All
# the REST orchestration lives in the companion provision_fabric_mirror.py; this wrapper gates
# execution and mints the Entra token that module consumes.
#
# Safe to re-run: the connection and mirrored database are looked up by display name and
# reused; the role bootstrap SQL is idempotent; starting an already-running mirror is a no-op.
#
# Required environment (set by azd / the deploy workflow):
#   WAYPOINT_FABRIC_MIRROR_ENABLED     "true" to run; anything else is a no-op.
#   WAYPOINT_FABRIC_MIRROR_SERVER      Source PostgreSQL FQDN
#                                      (e.g. waypoint-postgres.postgres.database.azure.com).
#   WAYPOINT_FABRIC_WORKSPACE          Workspace GUID (preferred), OR
#   WAYPOINT_FABRIC_WORKSPACE_NAME     Workspace display name (resolved when the GUID is absent).
#   WAYPOINT_FABRIC_MIRROR_USER        PostgreSQL mirroring role login (default fabric_user).
#   WAYPOINT_FABRIC_MIRROR_PASSWORD    PostgreSQL mirroring role password (required).
# Optional:
#   WAYPOINT_FABRIC_MIRROR_ADMIN_CONNECTION  (LOCAL/OUT-OF-BAND ONLY) psql-style admin connection
#                                      string. The deploy path leaves this empty because the API
#                                      bootstraps the role; set it only for local validation to run
#                                      fabric-mirror-role.sql before mirroring.
#   WAYPOINT_FABRIC_MIRROR_DATABASE    Source database name (default waypoint).
#   WAYPOINT_FABRIC_MIRROR_SCHEMA      Schema owning the mirrored tables (default public).
#   WAYPOINT_FABRIC_MIRROR_APP_USER    Application role re-granted DML (default waypoint_app).
#   WAYPOINT_FABRIC_MIRROR_TABLES      Comma-separated table list (default the four core tables).
#   WAYPOINT_FABRIC_MIRROR_NAME        Mirrored item display name (default WaypointMirror).
#   WAYPOINT_FABRIC_MIRROR_CONNECTION_ID  Existing Fabric connection GUID to reuse.
#   WAYPOINT_FABRIC_MIRROR_RETENTION_DAYS  Delta retention 1-30 (default 7).
#   WAYPOINT_FABRIC_MIRROR_STRICT      "true" (default) fails on start/poll errors; "false" warns.
#   WAYPOINT_FABRIC_MIRROR_SAMI_OBJECT_ID  Override for the source-server SAMI object id granted a
#                                      write-capable workspace role. Auto-resolved from the server
#                                      FQDN when unset; set this only if resolution can't reach ARM.
#   WAYPOINT_FABRIC_MIRROR_SAMI_ROLE   Workspace role granted to the SAMI (default Contributor).
#   WAYPOINT_FABRIC_API_BASE           API base override.

set -euo pipefail

if [[ "${WAYPOINT_FABRIC_MIRROR_ENABLED:-false}" != "true" ]]; then
  echo "[fabric-mirror] WAYPOINT_FABRIC_MIRROR_ENABLED is not 'true'; skipping mirror provisioning."
  exit 0
fi

if [[ -z "${WAYPOINT_FABRIC_MIRROR_SERVER:-}" ]]; then
  echo "[fabric-mirror] ERROR: WAYPOINT_FABRIC_MIRROR_SERVER (source PostgreSQL FQDN) is required." >&2
  exit 1
fi
if [[ -z "${WAYPOINT_FABRIC_MIRROR_PASSWORD:-}" ]]; then
  echo "[fabric-mirror] ERROR: WAYPOINT_FABRIC_MIRROR_PASSWORD is required." >&2
  exit 1
fi

WORKSPACE_ID="${WAYPOINT_FABRIC_WORKSPACE:-${APP_ONELAKE_WORKSPACE:-}}"
WORKSPACE_NAME="${WAYPOINT_FABRIC_WORKSPACE_NAME:-}"
if [[ -z "${WORKSPACE_ID}" && -z "${WORKSPACE_NAME}" ]]; then
  echo "[fabric-mirror] ERROR: set WAYPOINT_FABRIC_WORKSPACE (GUID) or WAYPOINT_FABRIC_WORKSPACE_NAME." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MIRROR_USER="${WAYPOINT_FABRIC_MIRROR_USER:-fabric_user}"
MIRROR_APP_USER="${WAYPOINT_FABRIC_MIRROR_APP_USER:-waypoint_app}"
MIRROR_DATABASE="${WAYPOINT_FABRIC_MIRROR_DATABASE:-waypoint}"

# 1) Bootstrap the PostgreSQL mirroring role (idempotent) when an admin connection is provided.
if [[ -n "${WAYPOINT_FABRIC_MIRROR_ADMIN_CONNECTION:-}" ]]; then
  if ! command -v psql >/dev/null 2>&1; then
    echo "[fabric-mirror] ERROR: psql is required to apply the role bootstrap SQL." >&2
    exit 1
  fi
  echo "[fabric-mirror] Applying idempotent mirroring-role bootstrap SQL..."
  psql "${WAYPOINT_FABRIC_MIRROR_ADMIN_CONNECTION}" \
    -v ON_ERROR_STOP=1 \
    -v fabric_user="${MIRROR_USER}" \
    -v fabric_password="${WAYPOINT_FABRIC_MIRROR_PASSWORD}" \
    -v app_user="${MIRROR_APP_USER}" \
    -v mirror_db="${MIRROR_DATABASE}" \
    -f "${SCRIPT_DIR}/fabric-mirror-role.sql"
else
  echo "[fabric-mirror] WAYPOINT_FABRIC_MIRROR_ADMIN_CONNECTION not set; skipping role bootstrap SQL."
fi

# 2) Create/refresh the Fabric mirrored database and start mirroring.
echo "[fabric-mirror] Acquiring Fabric API token..."
FABRIC_TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"

# Resolve the source PostgreSQL server's system-assigned managed identity (SAMI) object id.
# azure_cdc writes the mirrored Delta tables to OneLake AS this identity, so it must hold a
# write-capable workspace role (Contributor) or mirroring runs but every table fails with
# CDC_ERR_SYS_ONELAKE_PERMISSION_DENIED. An explicit override wins; otherwise resolve by the
# server name parsed from the FQDN (RG-independent via `az resource list`). Best-effort: an empty
# value makes the Python provisioner warn/skip rather than fail (unless MIRROR_STRICT forces it).
SAMI_OBJECT_ID="${WAYPOINT_FABRIC_MIRROR_SAMI_OBJECT_ID:-}"
if [[ -z "${SAMI_OBJECT_ID}" ]]; then
  PG_SERVER_NAME="${WAYPOINT_FABRIC_MIRROR_SERVER%%.*}"
  PG_RESOURCE_ID="$(az resource list --resource-type Microsoft.DBforPostgreSQL/flexibleServers \
    --query "[?name=='${PG_SERVER_NAME}'].id | [0]" -o tsv 2>/dev/null || true)"
  if [[ -n "${PG_RESOURCE_ID}" ]]; then
    SAMI_OBJECT_ID="$(az resource show --ids "${PG_RESOURCE_ID}" \
      --query "identity.principalId" -o tsv 2>/dev/null || true)"
  fi
  if [[ -n "${SAMI_OBJECT_ID}" ]]; then
    echo "[fabric-mirror] Resolved source-server SAMI object id ${SAMI_OBJECT_ID} for workspace grant."
  else
    echo "[fabric-mirror] WARN: could not resolve the source-server SAMI object id for '${PG_SERVER_NAME}'." >&2
    echo "[fabric-mirror]       Set WAYPOINT_FABRIC_MIRROR_SAMI_OBJECT_ID to grant it Contributor explicitly." >&2
  fi
fi

FABRIC_TOKEN="${FABRIC_TOKEN}" \
FABRIC_API_BASE="${WAYPOINT_FABRIC_API_BASE:-https://api.fabric.microsoft.com/v1}" \
WORKSPACE_ID="${WORKSPACE_ID}" \
WORKSPACE_NAME="${WORKSPACE_NAME}" \
MIRRORED_DATABASE_NAME="${WAYPOINT_FABRIC_MIRROR_NAME:-WaypointMirror}" \
SOURCE_SERVER="${WAYPOINT_FABRIC_MIRROR_SERVER}" \
SOURCE_DATABASE="${MIRROR_DATABASE}" \
SOURCE_SCHEMA="${WAYPOINT_FABRIC_MIRROR_SCHEMA:-public}" \
MIRROR_TABLES="${WAYPOINT_FABRIC_MIRROR_TABLES:-suppliers,invoices,invoice_lines,reconciliation_findings}" \
RETENTION_DAYS="${WAYPOINT_FABRIC_MIRROR_RETENTION_DAYS:-7}" \
CONNECTION_ID="${WAYPOINT_FABRIC_MIRROR_CONNECTION_ID:-}" \
FABRIC_MIRROR_USER="${MIRROR_USER}" \
FABRIC_MIRROR_PASSWORD="${WAYPOINT_FABRIC_MIRROR_PASSWORD}" \
SOURCE_SERVER_SAMI_OBJECT_ID="${SAMI_OBJECT_ID}" \
SOURCE_SERVER_SAMI_ROLE="${WAYPOINT_FABRIC_MIRROR_SAMI_ROLE:-Contributor}" \
MIRROR_STRICT="${WAYPOINT_FABRIC_MIRROR_STRICT:-true}" \
  python3 "${SCRIPT_DIR}/provision_fabric_mirror.py"

echo "[fabric-mirror] Done."
