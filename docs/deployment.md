# Azure deployment

Waypoint now has a root GitHub Actions entry point for repeatable Azure deployment:

```text
.github/workflows/deploy.yml
```

The current default path deploys the Waypoint app and the full hosted agent suite. Assurance Orchestrator still starts with only the WebIQ and FoundryIQ internal fan-out lanes enabled; WorkIQ and FabricIQ are deployed but disabled in the orchestrator until their tenant-specific Microsoft 365 and Fabric connections are ready.

| Component | Deploys by default | Active in Assurance Orchestrator fan-out by default | Notes |
| --- | --- | --- | --- |
| Waypoint app/API | Yes | N/A | Aspire deploy provisions the app runtime, PostgreSQL, auth settings, and optional Fabric hooks. |
| `invoice-analyst` | Yes | N/A | Hosted analyst surface. |
| `assurance-orchestrator` | Yes | N/A | Coordinates invoice assurance and applies the fan-out lane flags below. |
| WorkIQ / `collaboration-evidence-expert` | Yes | No | Kept off until live Microsoft 365 access is configured. |
| WebIQ / `market-evidence-expert` | Yes | Yes | External/web evidence lane. |
| FoundryIQ / `contract-policy-expert` | Yes | Yes | Contract/policy knowledge lane; requires the Search knowledge-base MCP connection. |
| FabricIQ / `operations-data-expert` | Yes | No | Kept off until Fabric/OneLake resources and semantic data are ready. |
| `waypoint-recorder` | Yes | N/A | Write-boundary agent endpoint is prewired for the orchestrator. |

## Required GitHub variables

Configure these as repository or environment variables before running **Actions -> Deploy Azure**:

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Entra application/client id for the GitHub OIDC deploy identity. |
| `AZURE_TENANT_ID` | Tenant id. |
| `AZURE_SUBSCRIPTION_ID` | Subscription id. |
| `AZURE_LOCATION` | Azure region. Defaults to `swedencentral` when omitted. |
| `AZURE_RESOURCE_GROUP` | Resource group. Defaults to `rg-waypoint` when omitted. |

The workflow creates or reuses a deployment Key Vault named `kv-waypoint-<subscription-hash>` unless `DEPLOY_KEY_VAULT_NAME` is set. It stores generated-once API keys and PostgreSQL passwords there so reruns are stable and no generated secret needs to be copied into GitHub.

## Optional variables

| Variable | Purpose |
| --- | --- |
| `DEPLOY_KEY_VAULT_NAME` | Override the generated deployment Key Vault name. |
| `WAYPOINT_POSTGRES_SERVER_NAME` | Stable PostgreSQL Flexible Server name. Recommended after the first successful run. |
| `WAYPOINT_POSTGRES_FIREWALL_RULES_JSON` | Override PostgreSQL firewall rules. The default permits Azure services for the app path. |
| `WAYPOINT_MSAL_CLIENT_ID` | Reuse an existing Waypoint app registration. If absent, the workflow creates one named `waypoint`. |
| `AZURE_AI_PROJECT_ENDPOINT` / `AZURE_AI_PROJECT_ID` | Deploy agents into an existing Foundry project instead of provisioning one. |
| `AZURE_AI_ACCOUNT_NAME` / `AZURE_AI_PROJECT_NAME` | Reuse an existing Foundry account/project by name. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `MODEL_NAME`, `MODEL_VERSION`, `MODEL_SKU_NAME`, `MODEL_CAPACITY` | Override the chat model deployment used by hosted agents. |
| `AZURE_AI_EMBEDDING_DEPLOYMENT_NAME`, `EMBEDDING_MODEL_NAME`, `EMBEDDING_MODEL_VERSION`, `EMBEDDING_MODEL_SKU_NAME`, `EMBEDDING_MODEL_CAPACITY` | Override the embedding deployment used by FoundryIQ knowledge retrieval. |
| Workflow inputs `workiq_enabled`, `webiq_enabled`, `foundryiq_enabled`, `fabriciq_enabled` | Control Assurance Orchestrator fan-out lanes. Defaults are WorkIQ off, WebIQ on, FoundryIQ on, FabricIQ off. |

## Running the deployment

1. In GitHub, open **Actions -> Deploy Azure -> Run workflow**.
2. Leave `deploy_app`, `deploy_agents`, `provision_agents`, and `seed_data` enabled for a first run.
3. Leave `fabric_provision_enabled` and `fabriciq_enabled` disabled unless the Fabric/OneLake path is ready.
4. Re-run safely as needed. The workflow reuses Key Vault secrets, app registrations, Azure resources, and Foundry project infrastructure where possible.

## What still needs manual setup

- The GitHub OIDC deploy identity must exist and have sufficient Azure roles before the workflow can log in.
- Foundry model quota/capacity must be available in the selected region.
- FoundryIQ requires the Bicep-managed Azure AI Search knowledge-base MCP connection. If `AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME` is missing after provisioning, the workflow fails rather than deploying a broken contract-policy expert.
- Microsoft 365/Teams publishing remains manual/admin-gated.
- Fabric/OneLake and FabricIQ fan-out are optional and remain off by default.
