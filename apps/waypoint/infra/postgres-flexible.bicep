@description('Name of the Azure PostgreSQL Flexible Server.')
param serverName string

@description('Azure region for the Azure PostgreSQL Flexible Server.')
param location string = resourceGroup().location

@description('PostgreSQL administrator login for server bootstrap only. Do not use from the application.')
param administratorLogin string = 'waypointadmin'

@description('PostgreSQL administrator password for server bootstrap only.')
@secure()
param administratorLoginPassword string

@description('Least-privilege PostgreSQL role used by the Waypoint API.')
param applicationUserName string = 'waypoint_app'

@description('Application database name.')
param databaseName string = 'waypoint'

@description('PostgreSQL engine version.')
param version string = '17'

// Fabric Mirroring for Azure Database for PostgreSQL (the zero-ETL CDC feed that grounds
// FabricIQ) does NOT support the Burstable tier, so the default is General Purpose. It is
// still overridable (Waypoint:Postgres:SkuName / SkuTier) for cost-sensitive, mirroring-off
// environments, but leave it on a mirroring-supported tier whenever enableFabricMirroring is true.
@description('Azure PostgreSQL Flexible Server SKU name.')
param skuName string = 'Standard_D2ds_v5'

@description('Azure PostgreSQL Flexible Server SKU tier. Burstable is NOT supported by Fabric mirroring.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'GeneralPurpose'

@description('Prepare the server for Fabric Mirroring by enabling the System-Assigned Managed Identity (the one hard prerequisite that is a genuine ARM property) and pre-tuning the customer-settable capacity parameters. The remaining prerequisites (azure_cdc preload + per-database registration, wal_level, and azure.fabric_mirror_enabled) are applied by the Azure portal "Fabric mirroring" enablement workflow, which currently has no public ARM/CLI/REST API. Gated so the default/non-mirrored deploy path is unchanged.')
param enableFabricMirroring bool = false

@description('Number of databases to be mirrored from this server (drives azure_cdc.max_fabric_mirrors and max_worker_processes: +3 background workers per mirrored database).')
@minValue(1)
@maxValue(6)
param mirroredDatabaseCount int = 1

@description('max_worker_processes for the mirrored server. Fabric requires +3 background workers per mirrored database on top of the engine baseline (8). Customer-settable, so we pre-tune it here.')
param mirroringMaxWorkerProcesses int = 8 + (3 * mirroredDatabaseCount)

@description('Provisioned storage in GB.')
@minValue(32)
param storageSizeGB int = 32

@description('Backup retention in days.')
@minValue(7)
@maxValue(35)
param backupRetentionDays int = 7

@description('JSON array of explicit IPv4 firewall rules: [{"name":"aca-egress-1","startIpAddress":"1.2.3.4","endIpAddress":"1.2.3.4"}].')
param firewallRulesJson string = '[]'

var firewallRules = json(firewallRulesJson)

// Fabric Mirroring is unsupported on the Burstable tier (the portal "Get Started" button is
// disabled with "Mirroring is not supported for Burstable SKUs"). When mirroring is enabled we
// therefore coerce any Burstable request up to GeneralPurpose so an enableFabricMirroring=true
// deploy can NEVER land on a mirroring-incompatible tier, even if a stray cost override sets
// Burstable. This keeps the mirroring prep idempotent and self-healing: re-running the deploy
// scales an existing Burstable server up to a supported tier as part of the same pass.
var mirroringForcesGeneralPurpose = enableFabricMirroring && skuTier == 'Burstable'
var effectiveSkuTier = mirroringForcesGeneralPurpose ? 'GeneralPurpose' : skuTier
var effectiveSkuName = mirroringForcesGeneralPurpose && startsWith(skuName, 'Standard_B')
  ? 'Standard_D2ds_v5'
  : skuName

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: {
    name: effectiveSkuName
    tier: effectiveSkuTier
  }
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    version: version
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    storage: {
      autoGrow: 'Disabled'
      storageSizeGB: storageSizeGB
      type: 'Premium_LRS'
    }
  }
  identity: {
    type: enableFabricMirroring ? 'SystemAssigned' : 'None'
  }
}

// --- Fabric Mirroring source-server prerequisites (only when enableFabricMirroring) ------
// IMPORTANT (verified live against a GeneralPurpose PG17 server + real Fabric F-capacity):
// The Azure Database for PostgreSQL side of Fabric Mirroring is enabled by a PORTAL-ORCHESTRATED
// control-plane workflow ("Fabric mirroring" blade -> Get Started -> Prepare -> Restart). That
// workflow preloads and REGISTERS the proprietary azure_cdc extension per selected database, sets
// wal_level=logical + max_worker_processes, and flips azure.fabric_mirror_enabled=on at the end.
// Per Microsoft's docs, azure.fabric_mirror_enabled "is set automatically at the end of the server
// enablement workflow, so you shouldn't change it manually", and shared_preload_libraries rejects
// the azure_cdc value outright. As of 2026-06 there is NO public ARM/CLI/REST API for that PG-side
// enablement workflow, so it cannot be fully expressed as IaC yet. Setting those parameters by hand
// leaves the server "not ready for Mirroring" (the mirror starts, then Stops with InputValidationError).
//
// What IaC CAN do deterministically, and does here:
//   1. Enable the System-Assigned Managed Identity (SAMI) — the one hard prerequisite that is a
//      genuine ARM property (the portal blade only links out to this; it doesn't toggle it for you).
//      azure_cdc uses the SAMI to authenticate to OneLake.
//   2. Pre-tune the customer-settable capacity parameters (max_worker_processes,
//      azure_cdc.max_fabric_mirrors) so the server is right-sized before enablement. These are
//      documented as customer-tunable and do not conflict with the enablement workflow.
// The remaining one-time portal enablement is captured as a runbook step in docs/fabric-iq.md.
resource maxWorkerProcesses 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = if (enableFabricMirroring) {
  name: 'max_worker_processes'
  parent: server
  properties: {
    value: string(mirroringMaxWorkerProcesses)
    source: 'user-override'
  }
}

resource maxFabricMirrors 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = if (enableFabricMirroring) {
  name: 'azure_cdc.max_fabric_mirrors'
  parent: server
  properties: {
    value: string(mirroredDatabaseCount)
    source: 'user-override'
  }
  dependsOn: [
    maxWorkerProcesses
  ]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  name: databaseName
  parent: server
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource firewallRuleResources 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = [for rule in firewallRules: {
  name: rule.name
  parent: server
  properties: {
    startIpAddress: rule.startIpAddress
    endIpAddress: rule.endIpAddress
  }
}]

// Fabric Mirroring runs from a Microsoft-hosted cloud connection whose outbound IP is not in the
// per-container ACA egress allowlist, so creating the Fabric connection to this server times out
// unless "Allow public access from Azure services" is on. That maps to the well-known 0.0.0.0
// firewall rule (start=end=0.0.0.0), which permits Azure-internal services (including Fabric) while
// still blocking the public internet. Only added when mirroring is enabled so non-mirroring servers
// keep their tighter, explicit-IP-only posture.
resource allowAzureServicesFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (enableFabricMirroring) {
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  parent: server
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output serverName string = server.name
output primaryEndpoint string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
output applicationUserName string = applicationUserName

@description('Principal id of the server System-Assigned Managed Identity (empty when mirroring is disabled). Fabric grants this identity Read+Write on the mirrored database.')
output serverIdentityPrincipalId string = enableFabricMirroring ? server.identity.principalId : ''
output fabricMirroringEnabled bool = enableFabricMirroring

@description('Effective compute tier the server was provisioned on (may be coerced up from Burstable to GeneralPurpose when Fabric Mirroring is enabled).')
output effectiveSkuTier string = effectiveSkuTier

@description('True when a Burstable request was coerced to GeneralPurpose because Fabric Mirroring is enabled.')
output skuCoercedForMirroring bool = mirroringForcesGeneralPurpose
