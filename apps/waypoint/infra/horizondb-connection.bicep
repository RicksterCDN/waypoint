@description('Name of the existing Waypoint API Azure Container App.')
param apiContainerAppName string

@description('Resource group containing the existing Waypoint API Azure Container App.')
param apiContainerAppResourceGroup string = resourceGroup().name

@description('PostgreSQL-compatible Azure HorizonDB connection string.')
@secure()
param horizonDbConnectionString string

@description('Secret name used by the API container app for the HorizonDB connection string.')
param secretName string = 'horizondb-connection'

@description('Resource ID of a user-assigned managed identity allowed to run deployment scripts.')
param deploymentScriptIdentityResourceId string

resource bindConnection 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'bind-horizondb-connection'
  location: resourceGroup().location
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deploymentScriptIdentityResourceId}': {}
    }
  }
  properties: {
    azCliVersion: '2.64.0'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    environmentVariables: [
      {
        name: 'API_CONTAINER_APP_NAME'
        value: apiContainerAppName
      }
      {
        name: 'API_CONTAINER_APP_RESOURCE_GROUP'
        value: apiContainerAppResourceGroup
      }
      {
        name: 'HORIZONDB_SECRET_NAME'
        value: secretName
      }
      {
        name: 'HORIZONDB_CONNECTION_STRING'
        secureValue: horizonDbConnectionString
      }
    ]
    scriptContent: '''
      set -euo pipefail

      az containerapp secret set \
        --name "$API_CONTAINER_APP_NAME" \
        --resource-group "$API_CONTAINER_APP_RESOURCE_GROUP" \
        --secrets "$HORIZONDB_SECRET_NAME=$HORIZONDB_CONNECTION_STRING"

      az containerapp update \
        --name "$API_CONTAINER_APP_NAME" \
        --resource-group "$API_CONTAINER_APP_RESOURCE_GROUP" \
        --set-env-vars "APP_DATABASE_CONNECTION=secretref:$HORIZONDB_SECRET_NAME"
    '''
  }
}

output apiContainerAppName string = apiContainerAppName
output horizonDbSecretName string = secretName
