# Consolidation notes

This document tracks internal consolidation context while the public README stays focused on Caldova Waypoint as a reference app.

## Current status

The first consolidation pass imported tracked source from the original project family into the public-facing repository structure:

| Original source | Current home |
| --- | --- |
| Legacy Waypoint runtime | `apps/waypoint/` |
| Ledgerfield demo data and generation tooling | `modules/corpus/` |
| Caldova Forge agent fleet | `modules/agents/` |
| Caliber evaluation tooling | `modules/evals/` |
| Caliber optimization and RFT materials | `modules/optimization/` |
| Keystone deployment orchestration | `tools/deploy/` |

Compatibility names are being kept where they protect functionality. Some CLIs, package internals, docs, and historical runbooks may still use their original names until replacement paths and public docs are stable.

## Consolidation principles

- Keep Waypoint as the product identity and Caldova as the demo company identity.
- Preserve behavior and contracts before renaming internals.
- Keep generated run/eval/RFT outputs, tenant-specific IDs, endpoints, uploaded-file IDs, and secrets out of git.
- Treat the Waypoint API/OpenAPI/app-role contract and corpus seed schema as product contracts.
- Keep live service wiring behind explicit environment configuration until local and staged validation pass.
- Optimize for a repository that feels real while still teaching the architecture clearly.
