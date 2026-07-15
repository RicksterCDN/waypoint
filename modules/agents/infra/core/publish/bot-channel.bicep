// Bot Service + Microsoft Teams channel for a published Foundry hosted agent.
//
// Runs *after* `azd deploy` (not part of main provision) because the agent's
// instance_identity client_id only exists once the agent version is created.
// The deploy workflow deploys this module via `az deployment group create`,
// passing in the instance_identity client_id read from the live agent JSON.
//
// What this gives you:
//   - An Azure Bot Service backed by the agent's instance identity (SingleTenant)
//   - Messaging endpoint wired to the agent's Activity Protocol URL
//   - Teams channel enabled
//
// What this does NOT give you:
//   - Listing in the Microsoft 365 Copilot agent store (still needs the user-context
//     publish call done via `make publish` from a developer laptop).
//
// IMPORTANT: We use `instance_identity.client_id` here, NOT `blueprint.client_id`.
// The instance identity is a `ServiceIdentity` SP managed by Foundry — Bot Service
// can mint tokens for it natively. The blueprint app has no Bot Service federated
// credential, so a bot configured with the blueprint as msaAppId cannot mint
// tokens and Teams/M365 silently 403's at the agent endpoint.
// (The older `foundry-ai-teammate` sample's botservice.bicep uses blueprint;
// that path no longer matches what the Foundry portal does today.)

@description('Logical agent name (e.g. "rhodes"). Used to derive resource names.')
param agentName string

@description('Display name shown in the Teams app and Bot Service blade.')
param displayName string = agentName

@description('Foundry account (Cognitive Services) name. Used to build the messaging endpoint.')
param accountName string

@description('Foundry project name. Used to build the messaging endpoint.')
param projectName string

@description('The agent instance_identity client_id. Read at deploy time from the Foundry agent JSON (.versions.latest.instance_identity.client_id).')
param msaAppId string

@description('Entra tenant ID hosting the agent instance identity.')
param msaAppTenantId string = subscription().tenantId

@description('Bot Service SKU. F0 is free and sufficient for Teams + M365.')
@allowed([
  'F0'
  'S1'
])
param sku string = 'F0'

@description('Tags applied to every resource.')
param tags object = {}

@description('Explicit Bot Service resource name. Pass a fresh name for each create — Bot Service holds soft-deleted names for 7 days. Truncated to 42 chars.')
param botName string

// Bot Service resources are always created in "global", regardless of where the
// rest of the stack lives. The control plane is global; channels are regional.
var botLocation = 'global'

// Activity Protocol endpoint exposed by the Foundry agent.
// IMPORTANT: matches the path shape the Foundry portal's M365 publish flow uses:
//   - lowercase `activityprotocol`
//   - `api-version=2025-11-15-preview`
// The older camel-case `activityProtocol` + `2025-05-15-preview` shape (from the
// foundry-ai-teammate sample) is accepted by the gateway but the routing differs
// between the two; channel delivery currently expects this exact shape.
var messagingEndpoint = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}/agents/${agentName}/endpoint/protocols/activityprotocol?api-version=2025-11-15-preview'

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: take(botName, 42)
  location: botLocation
  tags: tags
  kind: 'azurebot'
  sku: {
    name: sku
  }
  properties: {
    displayName: displayName
    endpoint: messagingEndpoint
    msaAppId: msaAppId
    msaAppType: 'SingleTenant'
    msaAppTenantId: msaAppTenantId
    disableLocalAuth: false
    isCmekEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  location: botLocation
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
      enableCalling: false
    }
  }
}

output botName string = bot.name
output botResourceId string = bot.id
output messagingEndpoint string = messagingEndpoint
