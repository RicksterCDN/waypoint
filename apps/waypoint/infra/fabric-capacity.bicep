@description('Name of the Microsoft Fabric capacity (3-63 lowercase letters/numbers).')
param capacityName string

@description('Azure region for the Fabric capacity. Sweden Central supports all Fabric workloads.')
param location string = resourceGroup().location

@description('Fabric capacity SKU. F64 is the Fabric IQ (semantic model + Data Agent) baseline — Data Agents / Copilot require a paid F-SKU and F64 unlocks the full Copilot experience. F2 is the smallest viable SKU for plain OneLake/Lakehouse reads. Suspend or scale down via infra/scripts/fabric-capacity-control.sh when idle to control cost.')
@allowed([
  'F2'
  'F4'
  'F8'
  'F16'
  'F32'
  'F64'
])
param skuName string = 'F64'

@description('JSON array of capacity administrator AAD object IDs (users, groups, or service principals). At least one is required.')
param administratorMembersJson string = '[]'

var administratorMembers = json(administratorMembersJson)

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  sku: {
    name: skuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: administratorMembers
    }
  }
}

output capacityName string = capacity.name
output capacityId string = capacity.id
output location string = capacity.location
