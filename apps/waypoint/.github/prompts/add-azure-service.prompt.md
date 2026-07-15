---
description: Add a new Azure service integration
---

# Add Azure Service: {{service}}

Integrate {{service}} into the Aspire-orchestrated application.

## Requirements

- Service: {{service}}
- Purpose: {{purpose}}

## Steps

### 1. Update `apphost.cs`

Add the NuGet package directive and resource definition:

```csharp
#:package Aspire.Hosting.Azure.{{Service}}@13.4.3

// Add the resource
var {{resource}} = builder.AddAzure{{Service}}("{{resource}}");

// Connect to API
var api = builder.AddUvicornApp(...)
    .WaitFor({{resource}})
    .WithEnvironment("APP_{{SERVICE}}_CONNECTION", {{resource}}.Resource.ConnectionStringExpression)
    // ... other config
```

### 2. Update `api/pyproject.toml`

Add Azure SDK dependencies:

```toml
dependencies = [
    # ... existing
    "azure-{{sdk}}>=x.x.0",
    "azure-identity>=1.19.0",
]
```

### 3. Create `api/app/common/{{service}}.py`

Implement service client:

```python
from azure.identity.aio import DefaultAzureCredential
from .settings import get_settings

class {{Service}}Service:
    def __init__(self):
        settings = get_settings()
        self.credential = DefaultAzureCredential()
        # Initialize client
```

### 4. Update `api/app/common/settings.py`

Add configuration:

```python
{{service}}_connection: str = ""
```

### 5. Update `AGENTS.md`

Document the integration for future reference.

## Azure Services Quick Reference

- **Cosmos DB**: `Aspire.Hosting.Azure.CosmosDB`, `azure-cosmos`
- **Blob Storage**: `Aspire.Hosting.Azure.Storage`, `azure-storage-blob`
- **Service Bus**: `Aspire.Hosting.Azure.ServiceBus`, `azure-servicebus`
- **Key Vault**: `Aspire.Hosting.Azure.KeyVault`, `azure-keyvault-secrets`
- **Redis**: `Aspire.Hosting.Azure.Redis`, `redis`
