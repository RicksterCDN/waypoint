#!/usr/bin/env bash
# Discovery + naming-conventions resolver for the keystone one-click deploy (ZERO manual config).
#
# The user supplies NOTHING by hand beyond the OIDC bootstrap (AZURE_* on keystone). This
# script computes every other name/value the pipeline needs, in two buckets:
#
#   CHOSEN  — greenfield resources keystone itself owns. Names anchor on a FIXED base
#             "keystone" (the umbrella RG rg-keystone + Key Vault kv-keystone-<hash>) so they
#             are stable across runs/repos. Fabric NAME constants mirror waypoint's defaults
#             (keystone passes them as inputs but does NOT provision Fabric — waypoint does).
#   DISCOVER — existing resources whose names are TOOL-GENERATED (azd resourceToken,
#             Aspire token, Container Apps env domain) and therefore NOT derivable. We query
#             live Azure by resource-group + type and let deploy-stage outputs override later:
#               forge/waypoint resource groups, Foundry endpoint + project url,
#               App Insights resource id, Postgres server name, MSAL client id, SP object id.
#
# Why not derive everything from one AZD_ENV_NAME (as first sketched): live discovery showed
# forge (azd token wi2egf4sh4hfq), waypoint (Aspire token 7777zs47exudw) and the Container
# Apps domain (grayflower-2758f17b) each carry an INDEPENDENT tool-generated token — there is
# no single shared token, so existing names must be discovered, not assumed.
#
# Required env:
#   AZURE_SUBSCRIPTION_ID   target subscription (keystone secret/var; already bootstrapped)
#   AZURE_CLIENT_ID         deploy app (client) id — used to resolve the SP object id
# Optional env:
#   AZD_ENV_NAME            anchor for CHOSEN names (default "waypoint")
#   FORGE_ENV_NAME          forge's azd env name for RG discovery (default "forge")
#
# Emits key=value lines to $GITHUB_OUTPUT (and logs to stderr). Greenfield-safe: a missing
# resource yields an empty value, never a hard failure (downstream stages provision it).
set -euo pipefail

env_name="${AZD_ENV_NAME:-waypoint}"
forge_env="${FORGE_ENV_NAME:-forge}"
sub="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID required}"
client_id="${AZURE_CLIENT_ID:-}"

log() { echo "discover: $*" >&2; }
az_ready() { command -v az >/dev/null 2>&1 && az account show >/dev/null 2>&1; }
# Run an az query, never fail the script; print "" on any error/None/null.
azq() { local out; out="$(az "$@" 2>/dev/null || true)"; [[ "$out" == "None" || "$out" == "null" ]] && out=""; printf '%s' "$out"; }

if ! az_ready; then
  log "WARN: az not ready (no login) — emitting CHOSEN names only, discovery values blank"
fi

# ---- CHOSEN names (deterministic; stable across runs) -----------------------
# Keystone-owned resources anchor on a FIXED base "keystone" (NOT a tool token / env name),
# so the umbrella's own RG + Key Vault are stable and unambiguous across runs and repos.
# Key Vault: globally unique, 3-24 chars, start with a letter. kv-keystone-<6 hex of sha(sub)>.
kv_hash="$(printf '%s' "$sub" | shasum -a 256 2>/dev/null | cut -c1-6 || printf '%s' "$sub" | sha256sum | cut -c1-6)"
kv_name="kv-keystone-${kv_hash}"

# Dedicated umbrella resource group for keystone-owned resources (Key Vault + state RG-tags).
# Deliberately NOT waypoint-rg: keeping umbrella state out of the Aspire-managed RG avoids any
# Aspire reconciliation/teardown risk. The deploy SP (subscription Contributor) creates it.
state_rg="rg-keystone"

# Fabric / OneLake names: waypoint OWNS Fabric provisioning (capacity Bicep + provision-fabric.sh,
# gated by fabric_provision_enabled). Keystone NEVER provisions Fabric — it only passes these
# NAME constants as inputs and consumes waypoint's onelake_* outputs. Values mirror waypoint's
# own defaults so a re-run is consistent with what waypoint stands up.
fabric_capacity_name="waypointcorpus"
fabric_workspace_name="waypoint-corpus"
fabric_lakehouse_name="corpus"
fabric_sku_name="${WAYPOINT_FABRIC_SKU_NAME:-F2}"
fabric_corpus_prefix="Files/corpus"
fabric_account_url="https://onelake.dfs.fabric.microsoft.com"   # constant
ledgerfield_seed_uri="onelake://Files/corpus/seed/waypoint-seed.json"  # constant convention

# ---- DISCOVER existing topology --------------------------------------------
# forge resource group: the RG tagged azd-env-name=<forge_env> (fallback rg-<forge_env>).
forge_rg="$(azq group list --query "[?tags.\"azd-env-name\"=='${forge_env}'].name | [0]" -o tsv)"
[[ -z "$forge_rg" ]] && forge_rg="rg-${forge_env}"

# waypoint resource group: keyed off the Postgres flexible server (its tool-generated name
# is unique to waypoint), since container-app names like api/web COLLIDE with other demos in
# the subscription. Prefer a server whose name starts with the env anchor; else the only one;
# final fallbacks: an api/web container-apps RG, then literal waypoint-rg. Aspire doesn't tag.
#
# HARD GUARD: the stale/incomplete rg-brightline-app holds a duplicate api/web pair (INTERNAL
# ingress, NO postgres) and MUST NEVER be used (user directive: "anything brightline should not
# be used"). Postgres-keyed discovery already avoids it (brightline has no DB), but every query
# below ALSO explicitly excludes any brightline RG, and a defensive check errors out if a
# canonical resource still resolves there.
excl="!contains(resourceGroup,'brightline')"
waypoint_rg="$(azq resource list --resource-type Microsoft.DBforPostgreSQL/flexibleServers --query "[?${excl} && starts_with(name,'${env_name}')].resourceGroup | [0]" -o tsv)"
[[ -z "$waypoint_rg" ]] && waypoint_rg="$(azq resource list --resource-type Microsoft.DBforPostgreSQL/flexibleServers --query "[?${excl}].resourceGroup | [0]" -o tsv)"
[[ -z "$waypoint_rg" ]] && waypoint_rg="$(azq resource list --resource-type Microsoft.App/containerApps --query "[?${excl} && (name=='api'||name=='web')].resourceGroup | [0]" -o tsv)"
[[ -z "$waypoint_rg" ]] && waypoint_rg="waypoint-rg"

# Defensive: never let a brightline RG slip through as the canonical waypoint app RG.
case "$waypoint_rg" in
  *brightline*) echo "::error::discover resolved waypoint_rg to a brightline RG ('$waypoint_rg') — excluded by directive. Aborting." >&2; exit 1 ;;
esac

# (state_rg is the dedicated rg-keystone set above — NOT the waypoint app RG.)

# Foundry: the AIServices account in the forge RG + its (best-effort) project.
foundry_account="$(azq resource list -g "$forge_rg" --resource-type Microsoft.CognitiveServices/accounts --query "[?kind=='AIServices'].name | [0]" -o tsv)"
foundry_endpoint=""
foundry_project_url=""
if [[ -n "$foundry_account" ]]; then
  foundry_endpoint="$(azq cognitiveservices account show -g "$forge_rg" -n "$foundry_account" --query "properties.endpoints.\"AI Foundry API\"" -o tsv)"
  [[ -z "$foundry_endpoint" ]] && foundry_endpoint="https://${foundry_account}.services.ai.azure.com/"
  # Best-effort project name; forge-deploy's AZURE_AI_PROJECT_ENDPOINT output is authoritative.
  foundry_project="$(azq resource list --resource-type Microsoft.CognitiveServices/accounts/projects --query "[?contains(name,'${foundry_account}')].name | [0]" -o tsv)"
  foundry_project="${foundry_project##*/}"
  [[ -z "$foundry_project" ]] && foundry_project="${WAYPOINT_FOUNDRY_PROJECT_NAME:-}"
  [[ -n "$foundry_project" ]] && foundry_project_url="${foundry_endpoint%/}/api/projects/${foundry_project}"
fi

# App Insights: reuse forge's component (waypoint-rg has none of its own — Log Analytics only).
app_insights_resource_id="$(azq resource list -g "$forge_rg" --resource-type microsoft.insights/components --query "[0].id" -o tsv)"

# Log Analytics workspace backing that App Insights component (workspace-based AI links one).
# Parse the LA workspace name + RG out of the component's WorkspaceResourceId.
log_analytics_workspace_name=""
log_analytics_resource_group=""
if [[ -n "$app_insights_resource_id" ]]; then
  la_id="$(azq resource show --ids "$app_insights_resource_id" --query "properties.WorkspaceResourceId" -o tsv)"
  if [[ -n "$la_id" ]]; then
    log_analytics_workspace_name="${la_id##*/}"
    log_analytics_resource_group="$(printf '%s' "$la_id" | sed -n 's#.*/resourceGroups/\([^/]*\)/.*#\1#p')"
  fi
fi
# Fall back to the forge RG's own LA workspace if the component wasn't workspace-linked.
if [[ -z "$log_analytics_workspace_name" ]]; then
  log_analytics_workspace_name="$(azq resource list -g "$forge_rg" --resource-type Microsoft.OperationalInsights/workspaces --query "[0].name" -o tsv)"
  [[ -n "$log_analytics_workspace_name" ]] && log_analytics_resource_group="$forge_rg"
fi

# Postgres flexible server in the waypoint RG (tool-generated name).
postgres_server_name="$(azq resource list -g "$waypoint_rg" --resource-type Microsoft.DBforPostgreSQL/flexibleServers --query "[0].name" -o tsv)"

# Current waypoint WEB container-app FQDN (bare hostname) in the canonical waypoint RG.
# This grounds msal_redirect_uri: a DEPLOYED waypoint has MSAL ENABLED by default (apphost
# IsRunMode?false:true) and its web THROWS if WAYPOINT_MSAL_REDIRECT_URI is empty — so we must
# never pass an empty redirect when a web already exists. Must be the EXACT registered SPA
# redirect: the app reg registers https://<web>/auth/msal/callback AND https://<web>/login, and
# the web uses config.redirectUri directly (not just the popup-derived callback) — a bare origin
# is unregistered and triggers AADSTS50011. /login matches the live standalone WAYPOINT_MSAL_REDIRECT_URI.
# Empty only in a truly greenfield tenant (no web yet), where the wire-job pass-2 + record-state
# backfill it on the first deploy.
web_fqdn="$(azq containerapp list -g "$waypoint_rg" --query "[?starts_with(name,'web')].properties.configuration.ingress.fqdn | [0]" -o tsv)"
msal_redirect_uri=""
[[ -n "$web_fqdn" ]] && msal_redirect_uri="https://${web_fqdn}/login"

# MSAL: report the existing "waypoint" app id if present (ensure-msal creates it if missing).
msal_client_id="$(azq ad app list --display-name waypoint --query "[0].appId" -o tsv)"

# Deploy SP object id (for Fabric capacity admin membership + KV RBAC self-grant).
sp_object_id=""
[[ -n "$client_id" ]] && sp_object_id="$(azq ad sp show --id "$client_id" --query id -o tsv)"
# Runtime-computed Fabric capacity admin members (the deploy SP; humans are absent in CI).
if [[ -n "$sp_object_id" ]]; then
  fabric_admin_members_json="$(printf '["%s"]' "$sp_object_id")"
else
  fabric_admin_members_json="[]"
fi

# ---- emit -------------------------------------------------------------------
emit() { log "$1=$2"; [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "$1=$2" >> "$GITHUB_OUTPUT"; return 0; }

emit env_name "$env_name"
emit forge_rg "$forge_rg"
emit waypoint_rg "$waypoint_rg"
emit state_rg "$state_rg"
emit kv_name "$kv_name"
emit foundry_endpoint "$foundry_endpoint"
emit foundry_project_url "$foundry_project_url"
emit app_insights_resource_id "$app_insights_resource_id"
emit log_analytics_workspace_name "$log_analytics_workspace_name"
emit log_analytics_resource_group "$log_analytics_resource_group"
emit postgres_server_name "$postgres_server_name"
emit web_fqdn "$web_fqdn"
emit msal_redirect_uri "$msal_redirect_uri"
emit msal_client_id "$msal_client_id"
emit sp_object_id "$sp_object_id"
emit fabric_capacity_name "$fabric_capacity_name"
emit fabric_sku_name "$fabric_sku_name"
emit fabric_workspace_name "$fabric_workspace_name"
emit fabric_lakehouse_name "$fabric_lakehouse_name"
emit fabric_corpus_prefix "$fabric_corpus_prefix"
emit fabric_account_url "$fabric_account_url"
emit fabric_admin_members_json "$fabric_admin_members_json"
emit ledgerfield_seed_uri "$ledgerfield_seed_uri"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Discovery / conventions"
    echo ""
    echo "| key | value |"
    echo "| --- | --- |"
    echo "| forge RG | \`${forge_rg}\` |"
    echo "| waypoint RG (+ state/KV/Fabric) | \`${waypoint_rg}\` |"
    echo "| Key Vault (chosen) | \`${kv_name}\` |"
    echo "| Foundry endpoint | \`${foundry_endpoint:-<from forge output>}\` |"
    echo "| App Insights id | \`${app_insights_resource_id:-<none>}\` |"
    echo "| Postgres server | \`${postgres_server_name:-<none>}\` |"
    echo "| MSAL client id | \`${msal_client_id:-<ensure-msal creates>}\` |"
    echo "| Fabric capacity (chosen) | \`${fabric_capacity_name}\` |"
    echo "| Fabric admin members | \`${fabric_admin_members_json}\` |"
  } >> "$GITHUB_STEP_SUMMARY"
fi
