# Deployment tooling

This folder contains the deployment orchestration for the Caldova Waypoint reference environment. It wires together the Waypoint app, synthetic corpus, agent fleet, Microsoft Fabric/OneLake resources, Foundry resources, Key Vault, MSAL/OIDC setup, seed import, and post-deploy agent/app configuration.

> [!NOTE]
> The current GitHub Actions entry point is the root workflow at `.github/workflows/deploy.yml`. It deploys the Waypoint app plus the full hosted agent suite, while the Assurance Orchestrator fan-out starts with only WebIQ (`market-evidence-expert`) and FoundryIQ (`contract-policy-expert`) enabled. Treat the scripts in this folder as lower-level building blocks and compatibility helpers for broader deployment work.

> [!IMPORTANT]
> Deployment is the main remaining public-readiness caveat. The scripts and workflow contracts are included so readers can inspect the intended production-style shape, but the full cloud path is still being exercised end-to-end. Treat this folder as advanced until `docs/status.md` says deployment validation is complete.

## What the deployment path is intended to do

The deployment flow is staged so each step can be validated independently:

1. Discover or create the Azure deployment identity and resource anchors.
2. Preflight the target environment and decide which stages need to run.
3. Provision Key Vault secrets for Waypoint API keys and database credentials.
4. Ensure the Waypoint MSAL application registration and app roles.
5. Generate and publish the Waypoint seed from the corpus module.
6. Deploy the Caldova Forge agent fleet.
7. Deploy the Waypoint app runtime.
8. Provision or reuse Fabric/OneLake corpus storage.
9. Upload corpus and contracts knowledge-base content.
10. Import the Waypoint seed and wire deployed endpoints back into agents.
11. Record state so later runs can skip unchanged stages safely.

## Prerequisites

Cloud deployment requires:

- Azure CLI authenticated with permission to create resource groups, app registrations, service principals, role assignments, Key Vault, Azure Container Apps, Azure Database for PostgreSQL, Log Analytics, and Application Insights resources.
- GitHub CLI authenticated to the target repository when bootstrapping OIDC settings.
- Azure AI Foundry access for hosted agents.
- Microsoft Fabric workspace/capacity permissions for OneLake corpus provisioning.
- Microsoft 365 permissions for any live Teams or admin-center publishing paths.
- `uv`, Node.js/npm, .NET SDK, Aspire CLI, Bash, and `azd` for local helper paths.

Do not put tenant IDs, subscription IDs, private endpoints, uploaded-file IDs, tokens, or secrets into docs, issues, commits, or sample outputs.

## Local static checks

From the repository root:

```powershell
bash -n tools/deploy/scripts/*.sh
```

This only checks shell syntax. It does not prove cloud deployment.

## Environment file

Copy `.env.example` to `.env` only for local deployment-helper experiments. The canonical public path should prefer GitHub Actions with OIDC configuration and secrets/variables managed outside the repository.

The example file intentionally leaves real Azure identity values blank and documents which values are computed at runtime. Keep it that way.

## Current caveats

- Some script and tag names still preserve compatibility terms so existing behavior can be verified before resource names are changed.
- The scripts assume a configured Azure tenant and may require admin approval for Graph/MSAL operations.
- Fabric, Foundry, Microsoft 365, and Azure AI Search setup is environment-specific.
- A full rerunnable deployment must be validated before this folder should be described as production-ready.

See `docs/status.md` for the current public-readiness status.
