@description('Name of the Azure HorizonDB cluster.')
param clusterName string

@description('Azure region for the Azure HorizonDB cluster.')
param location string = resourceGroup().location

@description('PostgreSQL administrator login for cluster bootstrap only. Do not use from the application.')
param administratorLogin string = 'waypointadmin'

@description('PostgreSQL administrator password for cluster bootstrap only.')
@secure()
param administratorLoginPassword string

@description('Least-privilege PostgreSQL role used by the Waypoint API.')
param applicationUserName string = 'waypoint_app'

@description('Application database name.')
param databaseName string = 'waypoint'

@description('PostgreSQL engine version. Azure HorizonDB preview currently supports version 17.')
param version string = '17'

@description('Number of vCores assigned to each HorizonDB replica.')
@minValue(1)
@maxValue(96)
param vCores int = 2

@description('Number of readable high availability replicas.')
@minValue(1)
param replicaCount int = 1

@description('Availability zone placement policy for replicas.')
@allowed([
  'BestEffort'
  'Strict'
])
param zonePlacementPolicy string = 'BestEffort'

@description('JSON array of explicit IPv4 firewall rules: [{"name":"aca-egress-1","startIpAddress":"1.2.3.4","endIpAddress":"1.2.3.4"}]. Keep empty until private networking or known egress addresses are configured.')
param firewallRulesJson string = '[]'

var firewallRules = json(firewallRulesJson)
var poolName = 'DefaultPool'

resource cluster 'Microsoft.HorizonDb/clusters@2026-01-20-preview' = {
  name: clusterName
  location: location
  properties: {
    createMode: 'Create'
    version: version
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    vCores: vCores
    replicaCount: replicaCount
    zonePlacementPolicy: zonePlacementPolicy
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource firewallRuleResources 'Microsoft.HorizonDb/clusters/pools/firewallRules@2026-01-20-preview' = [for rule in firewallRules: {
  name: '${cluster.name}/${poolName}/${rule.name}'
  properties: {
    description: rule.?description ?? rule.name
    startIpAddress: rule.startIpAddress
    endIpAddress: rule.endIpAddress
  }
}]

output clusterName string = cluster.name
output primaryEndpoint string = cluster.properties.fullyQualifiedDomainName
output databaseName string = databaseName
output applicationUserName string = applicationUserName
