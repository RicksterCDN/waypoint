# Contributing

Waypoint is currently being prepared as a public reference application. Contributions should preserve working behavior while making the repository easier to understand.

## Guidelines

- Keep Waypoint as the product identity and Caldova as the fictional company identity.
- Prefer clear public names over internal codenames.
- Preserve compatibility before removing old paths, package names, or CLIs.
- Keep generated run/eval/RFT outputs and tenant-specific artifacts out of git.
- Add or update validation when changing app, corpus, agent, eval, optimization, or deploy behavior.
- Document public-facing concepts in `docs/` or the relevant module README.

## Validation

Use the smallest relevant checks for your change. See `docs/getting-started.md` for the current local validation commands.
