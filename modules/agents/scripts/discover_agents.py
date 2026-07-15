"""Print a JSON list of hosted-agent folder names under agents/, suitable for a
GitHub Actions matrix.

Usage:
    python scripts/discover_agents.py                 # all agents
    python scripts/discover_agents.py --changed-since <git-ref>
                                                       # only agents whose
                                                       # folder changed since
                                                       # the ref, OR all agents
                                                       # if any shared file
                                                       # (infra/, scripts/,
                                                       # azure.yaml, workflow)
                                                       # changed.

The folder name `_template` and any folder starting with `.` is ignored.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from prompty import load as load_prompty

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"
# Files whose changes force a redeploy of EVERY agent. Keep this set as
# narrow as possible — anything listed here turns single-agent PRs into
# all-agent deploys, which causes cross-team conflicts on the shared
# Foundry env.
#
# Intentionally NOT in this set:
#   - scripts/**            - tooling that runs at CI time. Script edits do
#                             not change container images. The next deploy
#                             of each agent picks up the new script.
#   - infra/**              - the provision job has its own infra-change
#                             detector in .github/workflows/deploy.yml.
#                             Changing bicep does not by itself require
#                             re-pushing every agent container.
#   - .github/workflows/**  - workflow logic changes that require a full
#                             redeploy can be triggered explicitly via
#                             workflow_dispatch (which deploys all agents
#                             when no `agent` input is given).
#
# Only `azure.yaml` remains because adding/removing services there genuinely
# affects every agent — azd reads the full services list at deploy time.
SHARED_PATHS = ("azure.yaml",)

# Files an agent folder must contain to be considered deployable. Folders
# missing any of these are skipped (with a warning to stderr) so half-scaffolded
# agents don't break the deploy matrix.
#
# Each entry is either a single filename (must exist) or a tuple of
# alternatives (at least one must exist). `requirements.txt` OR
# `pyproject.toml` are both valid Python dependency manifests — hopper
# uses pyproject.toml + uv.lock, the other agents use requirements.txt.
# Each entry is either a single filename (must exist) or a tuple of
# alternatives (at least one must exist).
# - `requirements.txt` OR `pyproject.toml`: both are valid Python dependency
#   manifests. hopper / release-captain / etc. use pyproject.toml + uv.lock.
# - `main.py` OR `src/<name>/__main__.py`: agents migrated to the Carson-style
#   package structure (e.g. agents/eliza/src/eliza/__main__.py) no longer have
#   a top-level main.py. Either form is valid.
REQUIRED_FILES: tuple[str | tuple[str, ...], ...] = (
    "agent.yaml",
    ("requirements.txt", "pyproject.toml"),
    "Dockerfile",
)


def _is_valid_agent(path: Path) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for entry in REQUIRED_FILES:
        if isinstance(entry, tuple):
            if not any((path / f).is_file() for f in entry):
                missing.append(" or ".join(entry))
        else:
            if not (path / entry).is_file():
                missing.append(entry)
    return (not missing, missing)


def _prompt_metadata(path: Path) -> dict[str, Any]:
    prompt = path / "prompt.md"
    if not prompt.is_file():
        return {}
    loaded = load_prompty(prompt)
    return loaded.metadata if isinstance(loaded.metadata, dict) else {}


def _hosted_deployment_enabled(path: Path) -> bool:
    data = _prompt_metadata(path)
    deployment = data.get("forge", {}).get("deployment", {})
    hosted = deployment.get("hosted", {})
    if isinstance(hosted, dict) and hosted.get("enabled") is False:
        return False
    return True


def _all_agents() -> list[str]:
    if not AGENTS_DIR.is_dir():
        return []
    valid: list[str] = []
    for p in sorted(AGENTS_DIR.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        ok, missing = _is_valid_agent(p)
        if ok:
            if _hosted_deployment_enabled(p):
                valid.append(p.name)
            else:
                print(
                    f"::notice::Skipping agents/{p.name} - hosted deployment disabled in prompt.md",
                    file=sys.stderr,
                )
        else:
            print(
                f"::warning::Skipping agents/{p.name} - missing required file(s): "
                f"{', '.join(missing)}",
                file=sys.stderr,
            )
    return valid


def _changed_files(ref: str) -> list[str]:
    # Three-dot syntax = diff vs the merge-base of `ref` and HEAD. Works even
    # when HEAD is a PR merge commit whose first parent is the base branch.
    cmd = ["git", "diff", "--name-only", f"{ref}...HEAD"]
    print(f"discover_agents: running `{' '.join(cmd)}`", file=sys.stderr)
    out = subprocess.check_output(cmd, cwd=REPO_ROOT, text=True)
    files = [line.strip() for line in out.splitlines() if line.strip()]
    print(
        f"discover_agents: {len(files)} changed file(s) since {ref}:",
        file=sys.stderr,
    )
    for f in files:
        print(f"  - {f}", file=sys.stderr)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-since",
        default=None,
        help="Git ref to diff against (e.g. HEAD~1 or a SHA)",
    )
    parser.add_argument(
        "--fallback",
        choices=("all", "fail"),
        default="fail",
        help=(
            "What to do when --changed-since cannot be resolved (ref not "
            "in local history, git diff fails, etc.). 'fail' (default) "
            "exits non-zero so CI surfaces the problem instead of silently "
            "fanning out to every agent. 'all' restores the old behavior."
        ),
    )
    args = parser.parse_args()

    all_agents = _all_agents()

    if not args.changed_since:
        result = all_agents
    else:
        try:
            files = _changed_files(args.changed_since)
        except subprocess.CalledProcessError as exc:
            msg = (
                f"discover_agents: `git diff --name-only "
                f"{args.changed_since}...HEAD` failed (exit "
                f"{exc.returncode}). Likely a shallow checkout or an "
                f"unreachable base SHA."
            )
            if args.fallback == "all":
                print(
                    f"::warning::{msg} Falling back to deploying every agent.",
                    file=sys.stderr,
                )
                files = []
                result = all_agents
            else:
                print(
                    f"::error::{msg} Re-run with --fallback all if you "
                    "intentionally want to deploy every agent.",
                    file=sys.stderr,
                )
                return 1
        else:
            shared_changed = [f for f in files if f.startswith(SHARED_PATHS)]
            if shared_changed:
                print(
                    f"discover_agents: shared file(s) changed — fanning out "
                    f"to every agent: {shared_changed}",
                    file=sys.stderr,
                )
                result = all_agents
            else:
                changed = {
                    f.split("/", 2)[1]
                    for f in files
                    if f.startswith("agents/") and len(f.split("/", 2)) >= 3
                }
                # Intersect with all_agents so we never schedule a folder that
                # failed the required-files check above.
                result = sorted(a for a in all_agents if a in changed)

    print(f"discover_agents: result = {result}", file=sys.stderr)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
