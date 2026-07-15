# Security policy

## Reporting security issues

Please do not open public issues for suspected vulnerabilities. Report security issues through the repository owner's preferred private security reporting channel.

## Reference app scope

Waypoint is a reference application for a fictional company and synthetic data set. It is not a supported Microsoft product or a production-ready security baseline.

Before adapting this code for production, review:

- Authentication and authorization configuration.
- Secret storage and rotation.
- Network exposure and CORS policy.
- Tenant, subscription, and managed-identity permissions.
- Agent tool permissions and write boundaries.
- Audit, logging, and data-retention policies.

## Secrets

Do not commit secrets, tenant-specific credentials, generated tokens, private endpoints, uploaded-file IDs, or raw run artifacts. Use environment variables, Key Vault, GitHub secrets, or another approved secret store.
