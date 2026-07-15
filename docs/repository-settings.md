# Repository settings

These settings are recommended before treating Caldova Waypoint as a public reference repository.

## Description and topics

Suggested description:

> Caldova Waypoint: a reference invoice assurance app with governed agents, synthetic corpus, evals, optimization, and deployment automation.

Suggested topics:

`ai-agents`, `fastapi`, `react-router`, `aspire`, `azure-ai-foundry`, `evaluation`, `synthetic-data`, `invoice-assurance`, `reference-application`

## Branch and pull request policy

- Require pull request review before merging to `main`.
- Require the root CI workflow to pass.
- Require signed commits if the project policy needs provenance enforcement.
- Keep secret scanning and push protection enabled.

## Security and issue policy

- Enable private vulnerability reporting if available.
- Use the issue templates for public bugs, docs, and deployment readiness.
- Close or edit issues that accidentally include secrets, tenant IDs, private endpoints, or uploaded-file IDs.

## Public status

Keep `docs/status.md` current. It should continue to state that the local validation path is exercised and that cloud deployment validation is still being completed until the full deployment path has been proven end-to-end.
