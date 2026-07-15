# Caldova Waypoint app

Caldova Waypoint is the invoice assurance application runtime. It combines a **FastAPI** backend, **React Router v7** frontend, OpenTelemetry tracing, PostgreSQL-compatible persistence, Microsoft Fabric/OneLake integration, and **.NET Aspire** orchestration for local development and Azure deployment.

This app is the governed system of record for supplier invoices, findings, cases, agent runs, recommendations, drafts, approvals, and audit.

## ✨ Features

- **FastAPI Backend** — Python 3.13, async support, automatic API docs, Pydantic validation
- **OpenTelemetry Tracing** — Distributed tracing across frontend and backend, visible in Aspire dashboard
- **React Router v7** — Modern React with SSR, file-based routing, TypeScript
- **Tailwind CSS** — Utility-first CSS with dark mode support
- **Vite** — Fast dev server with HMR and optimized production builds
- **.NET Aspire** — Local orchestration with dashboard, environment management, and Azure deployment
- **GitHub Actions** — CI workflow for testing, linting, and Docker builds

## 📋 Prerequisites

- [.NET SDK 10.0+](https://dotnet.microsoft.com/download)
- [Node.js 22+](https://nodejs.org/)
- [Python 3.13+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Fast Python package manager
- [Aspire CLI](https://aspire.dev/get-started/install-cli/)

```bash
# Install Aspire CLI (choose one)
curl -sSL https://aspire.dev/install.sh | bash    # Linux/macOS
irm https://aspire.dev/install.ps1 | iex          # Windows PowerShell
```

## 🚀 Quick Start

```bash
# From the repository root
cd apps/waypoint

# Run setup script (checks prerequisites, installs Aspire CLI, sets up pre-commit)
./setup.sh        # Linux/macOS
.\setup.ps1       # Windows PowerShell

# Start the application
aspire run
```

Or manually:

```bash
# (Optional) Install pre-commit hooks for local linting
uv tool install pre-commit
pre-commit install

# Run with Aspire (installs dependencies automatically)
aspire run
```

This will:

1. Install Python dependencies via `uv`
2. Install Node.js dependencies via `npm`
3. Start the FastAPI backend
4. Start the React frontend with API proxy
5. Open the Aspire dashboard for tracing and logs

**Access points:**

- **Web App**: <http://localhost:5173> (or port shown in Aspire)
- **API Docs**: <http://localhost:8000/docs>
- **Aspire Dashboard**: <http://localhost:15888>

## 📁 Project Structure

```text
apps/waypoint/
├── apphost.cs              # Aspire orchestration
├── api/                    # FastAPI backend
│   ├── app/
│   │   ├── main.py         # FastAPI entry point
│   │   ├── telemetry.py    # OpenTelemetry setup
│   │   ├── common/         # Shared utilities
│   │   │   ├── settings.py # Pydantic configuration
│   │   │   └── tracer.py   # @trace decorator
│   │   └── modules/
│   │       └── items/      # Example CRUD module
│   │           ├── schemas.py
│   │           ├── service.py
│   │           └── routes.py
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
├── web/                    # React frontend
│   ├── app/
│   │   ├── root.tsx        # Root layout + telemetry
│   │   ├── routes.ts       # Route definitions
│   │   └── routes/
│   │       ├── home.tsx    # Landing page
│   │       └── items.tsx   # API demo page
│   ├── lib/
│   │   └── telemetry.ts    # Browser OpenTelemetry
│   ├── vite.config.ts      # Vite + proxy config
│   ├── server.js           # Production server
│   ├── package.json
│   └── Dockerfile
└── .github/
    ├── workflows/
    │   ├── ci.yml          # CI pipeline
    │   └── deploy.yml      # Azure deployment
    ├── copilot-instructions.md
    └── prompts/            # Copilot prompt files
```

## 🛠️ Development

### Running Standalone

**API only:**

```bash
cd api
uv sync
uv run uvicorn app.main:app --reload
```

**Web only** (requires API running):

```bash
cd web
npm install
npm run dev
```

### Testing

```bash
# API tests
cd api
uv run pytest

# Web type checking
cd web
npm run typecheck
```

### Linting

```bash
# API
cd api
uv run ruff check app/
uv run mypy app/

# Web
cd web
npm run lint
```

## 🚢 Deployment

### Deploy to Azure (Local)

```bash
# One-command deployment to Azure Container Apps
aspire deploy
```

This will:

1. Publish the FastAPI API through Aspire's Python hosting integration
2. Build and publish the React web container
3. Deploy to Azure Container Apps
4. Configure environment variables and networking

### GitHub Actions Deployment

Waypoint includes a deployment workflow (`.github/workflows/deploy.yml`) that deploys using Aspire. The public repository currently treats this as an advanced path while end-to-end deployment validation continues.

**Automated Setup** (recommended):

```bash
# Linux/macOS - derives resource names from repo name
./setup-azure.sh caldova/waypoint

# Or with custom app name and location
./setup-azure.sh caldova/waypoint waypoint westus2

# Windows PowerShell
.\setup-azure.ps1 -GitHubRepo "caldova/waypoint"

# Or with custom app name and location
.\setup-azure.ps1 -GitHubRepo "caldova/waypoint" -AppName "waypoint" -Location "westus2"
```

The script automatically derives resource names from the app name:

- **Resource Group**: `<app-name>-rg`
- **App Registration**: `<app-name>-deploy`

This script will:

1. Create an Azure AD app registration with federated credentials
2. Create a service principal with Contributor role
3. Configure all required GitHub repository secrets

**Prerequisites for setup script:**

- [Azure CLI](https://docs.microsoft.com/cli/azure/install-azure-cli) - logged in with `az login`
- [GitHub CLI](https://cli.github.com/) - logged in with `gh auth login`

<details>
<summary><strong>Manual Setup</strong> (if you prefer not to use the script)</summary>

**Required Secrets** (configure in GitHub repository settings):

| Secret | Description |
| ------ | ----------- |
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_LOCATION` | Azure region (e.g., `eastus`) |
| `AZURE_RESOURCE_GROUP` | Target resource group name |
| `POSTGRES_ADMIN_PASSWORD` | Azure PostgreSQL administrator password used only for server bootstrap |
| `POSTGRES_APP_PASSWORD` | Password for the least-privilege Azure PostgreSQL application role |
| `WAYPOINT_API_KEYS` | Optional semicolon-separated scoped API keys in `label:key:role,role` format |
| `HORIZONDB_ADMIN_PASSWORD` | HorizonDB administrator password used only for cluster bootstrap |
| `HORIZONDB_APP_PASSWORD` | Password for the least-privilege `waypoint_app` database role |

**Required Variables**:

| Variable | Description |
| -------- | ----------- |
| `WAYPOINT_CORS_ALLOWED_ORIGINS` | Comma-separated production web origins allowed to call the API |
| `WAYPOINT_MSAL_ENABLED` | `true` to require Microsoft Entra bearer tokens |
| `WAYPOINT_MSAL_TENANT_ID` | Microsoft Entra tenant ID |
| `WAYPOINT_MSAL_CLIENT_ID` | Waypoint API/application client ID |
| `WAYPOINT_MSAL_API_SCOPE` | API scope accepted by the backend |
| `WAYPOINT_MSAL_REDIRECT_URI` | Web login redirect origin/URI. If unset, the deploy workflow derives `https://<web-fqdn>` after Aspire creates the Web Container App and best-effort registers the matching `/auth/msal/callback` SPA redirect URI on the app registration. |
| `WAYPOINT_MSAL_API_VALIDATION_ENABLED` | `true` to validate API bearer tokens |
| `WAYPOINT_LOG_ANALYTICS_WORKSPACE_NAME` | Log Analytics workspace used for deploy summaries |
| `WAYPOINT_LOG_ANALYTICS_RESOURCE_GROUP` | Resource group containing the workspace, when different from `AZURE_RESOURCE_GROUP` |
| `WAYPOINT_API_KEY_AUTH_ENABLED` | Set to `true` only when scoped API-key fallback is intentionally enabled |
| `WAYPOINT_POSTGRES_SERVER_NAME` | Azure PostgreSQL server name; defaults to `waypoint-postgres` |
| `WAYPOINT_POSTGRES_PROVISION_ENABLED` | Internal deploy flag set by the manual `provision_database` input; normal app deploys leave PostgreSQL provisioning off |
| `WAYPOINT_POSTGRES_ADMIN_LOGIN` | Azure PostgreSQL bootstrap administrator login; defaults to `waypointadmin` |
| `WAYPOINT_POSTGRES_APP_USER` | Least-privilege application database role; defaults to `waypoint_app` |
| `WAYPOINT_POSTGRES_DATABASE_NAME` | Application database name; defaults to `waypoint` |
| `WAYPOINT_POSTGRES_SKU_NAME` | Azure PostgreSQL SKU; defaults to `Standard_B1ms` |
| `WAYPOINT_POSTGRES_SKU_TIER` | Azure PostgreSQL SKU tier; defaults to `Burstable` |
| `WAYPOINT_POSTGRES_STORAGE_SIZE_GB` | Provisioned storage; defaults to `32` |
| `WAYPOINT_POSTGRES_BACKUP_RETENTION_DAYS` | Backup retention; defaults to `7` |
| `WAYPOINT_POSTGRES_FIREWALL_RULES_JSON` | Explicit IPv4 allow rules for Azure PostgreSQL as a JSON array |
| `WAYPOINT_HORIZONDB_PROVISION_ENABLED` | Internal deploy flag set by the manual workflow input; normal push deploys leave paid HorizonDB provisioning off |
| `WAYPOINT_HORIZONDB_CLUSTER_NAME` | HorizonDB cluster name; defaults to `waypoint-horizondb` |
| `WAYPOINT_HORIZONDB_ADMIN_LOGIN` | HorizonDB bootstrap administrator login; defaults to `waypointadmin` |
| `WAYPOINT_HORIZONDB_APP_USER` | Least-privilege application database role; defaults to `waypoint_app` |
| `WAYPOINT_HORIZONDB_DATABASE_NAME` | Application database name; defaults to `waypoint` |
| `WAYPOINT_HORIZONDB_VCORES` | vCores per HorizonDB replica; defaults to `2` |
| `WAYPOINT_HORIZONDB_REPLICA_COUNT` | Readable high availability replicas; defaults to `1` |
| `WAYPOINT_HORIZONDB_ZONE_PLACEMENT_POLICY` | `BestEffort` or `Strict`; defaults to `BestEffort` |
| `WAYPOINT_HORIZONDB_FIREWALL_RULES_JSON` | Explicit IPv4 allow rules for HorizonDB as a JSON array |

Waypoint publish mode defaults to the existing Azure PostgreSQL Flexible Server for lower-cost persistent demos. Routine app deploys do not run the PostgreSQL Bicep module or reconcile firewall rules; use the manual `provision_database` input only when creating or intentionally updating database infrastructure. The manual deploy workflow can still select HorizonDB, which provisions through `infra/horizondb.bicep` only when both `provision_database` and `provision_horizondb` are enabled. Local Aspire run mode uses an Aspire-hosted PostgreSQL database as the HorizonDB-compatible stand-in. Production should use the least-privilege app role in `APP_DATABASE_CONNECTION`; keep admin credentials only for initial bootstrap and password rotation.

Production API-key authentication is disabled unless `WAYPOINT_API_KEY_AUTH_ENABLED=true` is set. When enabled, keep `WAYPOINT_API_KEYS` scoped, labeled, and minimal; use `reader` for read-only consumers and `admin` only for bootstrap/import operations such as loading Ledgerfield seed data.

**Setup OIDC Authentication**:

```bash
# Create a service principal with federated credentials
az ad app create --display-name "waypoint-deploy"
az ad sp create --id <APP_ID>

# Create federated credential for GitHub Actions
az ad app federated-credential create \
  --id <APP_ID> \
  --parameters '{
    "name": "github-deploy",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:caldova/waypoint:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Grant permissions to the subscription/resource group
az role assignment create \
  --assignee <APP_ID> \
  --role "Contributor" \
  --scope /subscriptions/<SUBSCRIPTION_ID>
```

</details>

**Trigger deployment**:

- Push to `main` branch (auto-deploys on changes to api/, web/, apphost.cs)
- Manual trigger via GitHub Actions UI

**Verified deployment notes**:

- Routine app-only deploys should use `database_provider=azure-postgres`, `provision_database=false`, and `provision_horizondb=false`. This skips database Bicep/firewall reconciliation and has been verified end-to-end against the live Waypoint deployment.
- Use `provision_database=true` only for intentional database infrastructure changes. PostgreSQL firewall allowlist reconciliation can take many minutes because each `aca-egress-*` rule is tracked as an Azure deployment operation.
- Browser/MSAL account and token state is stored in origin-wide browser storage so Teams/Outlook invoice deep links that open new tabs can reuse an existing Waypoint sign-in. Waypoint does not use auth cookies for this browser flow, so SameSite/Secure cookie settings are not involved; the deep-link host must match the origin where the user signed in. Signing out clears the cached MSAL account.
- Browser/MSAL or Azure CLI token acquisition can open hidden login/device-code windows. If an auth check appears stalled, check hidden browser windows and taskbar prompts before assuming the API or workflow is hung.
- The deployed security/data path has been verified: unauthenticated `/api/invoices` and `/api/invoice-decisions` return `401`, while an authenticated Entra bearer token can read `/api/user/me`, `/api/invoice-decisions`, and invoice detail data.

### Manual Web Docker Build

```bash
# Build Web with version
docker build --build-arg BUILD_VERSION=$(git rev-parse --short HEAD) -t my-web ./web
```

## 🔧 Configuration

### Environment Variables

The API uses `APP_` prefixed environment variables (managed by Aspire):

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `APP_DATABASE_CONNECTION` | Database connection string | `""` |
| `APP_DATABASE_NAME` | Database name | `"StarterDB"` |
| `APP_STORAGE_CONNECTION` | Azure Storage connection | `""` |
| `APP_FOUNDRY_ENDPOINT` | Azure AI Foundry endpoint | `""` |

### Adding Azure Services

See [AGENTS.md](AGENTS.md) for instructions on adding:

- Azure Cosmos DB
- Azure Blob Storage
- Azure AI Foundry

## � Documentation

| Document                                                      | Purpose                                  | Audience                                             |
| ------------------------------------------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, data flows, module patterns | Developers understanding the codebase |
| [AGENTS.md](AGENTS.md) | Extension guide, common tasks, troubleshooting | AI agents and developers extending Waypoint |
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | Coding standards and conventions | AI coding assistants |

## �📚 Learn More

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Router v7](https://reactrouter.com/)
- [.NET Aspire](https://learn.microsoft.com/dotnet/aspire/)
- [OpenTelemetry](https://opentelemetry.io/)
- [Tailwind CSS](https://tailwindcss.com/)

## 📄 License

MIT
