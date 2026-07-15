# Compatibility names

Waypoint now uses public-facing repository names such as `apps/waypoint`, `modules/corpus`, `modules/agents`, `modules/evals`, and `modules/optimization`.

Some code-level names are intentionally still compatibility names. They remain because they are import paths, CLI entry points, package metadata, workflow contracts, or generated artifact references that other modules still depend on.

| Compatibility name | Where it appears | Public module |
| --- | --- | --- |
| `ledgerfield` | Python package/imports for corpus and Waypoint seed generation | `modules/corpus` |
| `caliber` | Evaluation CLI and Python package | `modules/evals` |
| `Forge` / `Caldova Forge` | Agent fleet identity and Foundry-facing docs/scripts | `modules/agents` |
| `keystone` | Some deployment resource and workflow history | `tools/deploy` |

The current rule is to preserve functionality first and rename only when the replacement has a verified migration path. Public docs should describe the module role first and mention the compatibility name only when it is required to run a command or understand an import.
