---
applyTo: "src/**/*.py,pyproject.toml"
---

Ledgerfield Python tooling is managed with `uv`.

Use the existing `ledgerfield` CLI and FastAPI structure. Keep generated artifacts out of source control, and prefer reusable helpers over one-off scripts.

Validate Python changes with:

```powershell
uv run python -m compileall -q src
uv run ledgerfield generate-invoices --format html
uv run ledgerfield doctor
```

Prefer shared helpers in `src/ledgerfield/agent_surface.py` when adding new CLI/API operations that should be available to both humans and consuming agents.
