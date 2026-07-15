# Fix CI Failure

Help diagnose and fix a CI workflow failure.

## Request

Analyze the CI failure and provide a fix. Common issues include:

1. **Python errors** - mypy type errors, ruff lint errors, pytest failures
2. **TypeScript errors** - Type checking failures in web/
3. **Missing files** - Files not tracked due to .gitignore patterns
4. **Dependency issues** - Missing or incompatible packages

## Diagnostic Steps

1. Check the GitHub Actions logs for the specific error message
2. Reproduce locally:
   - `cd api && uv run pytest` for test failures
   - `cd api && uv run ruff check app/` for lint errors
   - `cd api && uv run mypy app/` for type errors
   - `cd web && npm run typecheck` for TypeScript errors

3. Check git status for untracked files that should be committed
4. Review `.gitignore` for overly broad patterns

## Common Fixes

| Error Type                | Common Fix                                 |
| ------------------------- | ------------------------------------------ |
| mypy Callable error       | Use `from collections.abc import Callable` |
| ruff import order         | Move `from __future__ import` to top       |
| TypeScript file not found | Check `.gitignore` isn't excluding it      |
| pytest async error        | Add `@pytest.mark.asyncio` decorator       |

## Output

Provide the specific fix needed with file path and code changes.
