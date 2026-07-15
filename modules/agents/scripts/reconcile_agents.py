"""Reconcile the Foundry project's agents against what this repo declares.

Computes the set of agent names the repo is *supposed* to manage — every
deployable folder under ``agents/``, whether it deploys as a hosted container
or as a prompt agent — then lists the agents that actually exist in the
Foundry project and reports (``--dry-run``, the default) or deletes
(``--apply``) any that the repo no longer declares.

It then does the same for the *tool connections* those agents bind: an agent's
remote MCP "tools" are project connections (type ``RemoteTool``) that are NOT
removed when the agent is deleted. A RemoteTool connection is pruned only when
it is referenced nowhere in the repo (agents/infra/scripts) AND is not bound by
any currently live agent — so an out-of-band/manual attachment is never deleted.
Only ``RemoteTool`` connections are ever considered — infra-provisioned
connections (AppInsights, ContainerRegistry, storage, search, Bing grounding)
are never touched. ``--skip-connections`` opts out.

This is how retired agents (e.g. a hosted ``*-expert`` that became a prompt
agent, or a renamed/removed agent) and their leftover tool connections get
pruned from Foundry so the project does not accumulate drift after a deploy.

Deployed agent name == folder name for BOTH deployment styles:
  * hosted agents keep folder == agent.yaml:name == azure.yaml service key
    (repo convention), and ``azd deploy <folder>`` registers ``<folder>``.
  * prompt agents are validated by deploy_prompt_agents.py to require
    ``metadata.forge.deployment.prompt.agentName == <folder>``.
So the repo's expected Foundry-agent-name set is exactly the set of
deployable folder names.

Usage:
    python scripts/reconcile_agents.py --dry-run     # report only (default)
    python scripts/reconcile_agents.py --apply       # actually delete drift
    python scripts/reconcile_agents.py --protect a,b # never delete a or b

Auth: uses DefaultAzureCredential (az login / OIDC / managed identity) to get
an https://ai.azure.com/.default token, same as the other Foundry scripts.

Safety:
  * NEVER deletes when the computed expected set is empty (a discovery failure
    must not nuke the whole project). Exits non-zero instead.
  * Only deletes agents whose names are NOT in the expected set AND not in the
    ``--protect`` list.
  * ``--dry-run`` is the default; deletion requires explicit ``--apply``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import requests
from azure.identity import DefaultAzureCredential
from prompty import load as load_prompty

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"

AI_SCOPE = "https://ai.azure.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
FOUNDRY_API_VERSION = "2025-11-15-preview"
# Control-plane api-version for deleting a project connection (the data-plane
# /connections endpoint rejects DELETE with 405; connections are ARM resources).
ARM_API_VERSION = "2025-06-01"
FOUNDRY_FEATURES = "AgentEndpoints=V1Preview,HostedAgents=V1Preview"

# Only connections of these data-plane types are ever considered for pruning —
# they are the agent "tools" (remote MCP servers). Infra-provisioned connections
# (AppInsights, ContainerRegistry, AzureStorageAccount, CognitiveSearch,
# GroundingWithBingSearch, ...) are a different type and are NEVER touched.
PRUNABLE_CONNECTION_TYPES = {"RemoteTool"}

# A RemoteTool connection is considered "still referenced" (NOT orphaned) if its
# name appears as a literal anywhere in these repo trees — agent prompt.md
# toolBindings, hosted toolbox.py, infra Bicep, or helper scripts. This mirrors
# how connection ownership is declared across both deployment styles without
# brittle per-style metadata parsing.
_CORPUS_DIRS = ("agents", "infra", "scripts")
_CORPUS_SUFFIXES = (".md", ".yaml", ".yml", ".py", ".bicep", ".json", ".env")

# Same required-file gate discover_agents.py uses to decide a folder is a real,
# deployable hosted agent (half-scaffolded folders are not "managed").
HOSTED_REQUIRED_FILES: tuple[str | tuple[str, ...], ...] = (
    "agent.yaml",
    ("requirements.txt", "pyproject.toml"),
    "Dockerfile",
)


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _prompt_metadata(path: Path) -> dict[str, Any]:
    prompt = path / "prompt.md"
    if not prompt.is_file():
        return {}
    loaded = load_prompty(prompt)
    return loaded.metadata if isinstance(loaded.metadata, dict) else {}


def _is_hosted_deployable(path: Path) -> bool:
    for entry in HOSTED_REQUIRED_FILES:
        if isinstance(entry, tuple):
            if not any((path / f).is_file() for f in entry):
                return False
        elif not (path / entry).is_file():
            return False
    deployment = _prompt_metadata(path).get("forge", {}).get("deployment", {})
    hosted = deployment.get("hosted", {})
    if isinstance(hosted, dict) and hosted.get("enabled") is False:
        return False
    return True


def _is_prompt_deployable(path: Path) -> bool:
    deployment = _prompt_metadata(path).get("forge", {}).get("deployment", {})
    prompt = deployment.get("prompt", {})
    return isinstance(prompt, dict) and prompt.get("enabled") is True


def expected_agent_names() -> set[str]:
    """Every folder under agents/ that deploys as a hosted OR prompt agent."""
    if not AGENTS_DIR.is_dir():
        return set()
    expected: set[str] = set()
    for p in sorted(AGENTS_DIR.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        if _is_hosted_deployable(p) or _is_prompt_deployable(p):
            expected.add(p.name)
    return expected


def list_foundry_agents(endpoint: str, token: str) -> list[str]:
    """Return every agent name currently registered in the Foundry project.

    The Foundry ``/agents`` list endpoint follows the OpenAI list convention:
    the page of objects is under ``data`` (NOT ``value``), and pagination is
    cursor-based via ``has_more`` + ``last_id`` (passed back as ``after``), NOT
    an absolute ``nextLink``. Reading ``value`` returns an empty list, which
    silently looks like "no agents / no drift" — so be explicit about ``data``.
    """
    base = f"{endpoint.rstrip('/')}/agents"
    headers = {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": FOUNDRY_FEATURES,
    }
    params: dict[str, str] = {"api-version": FOUNDRY_API_VERSION, "limit": "100"}
    names: list[str] = []
    while True:
        resp = requests.get(base, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = body.get("data") or body.get("value") or []
        else:
            items = []
        for item in items:
            name = item.get("name") or item.get("id") if isinstance(item, dict) else None
            if name:
                names.append(name)
        if not (isinstance(body, dict) and body.get("has_more") and body.get("last_id")):
            break
        params["after"] = str(body["last_id"])
    return names


def live_connection_bindings(
    endpoint: str, token: str, agent_names: list[str]
) -> set[str]:
    """Connection names bound as tools by any *currently live* agent.

    A connection can be attached to an agent out-of-band (manually, or by an
    agent deployed outside this repo), in which case it won't appear in the
    repo corpus. Reading each live agent's latest tool definition gives a
    second, authoritative "is this in use" signal so reconcile never prunes a
    connection a real agent still binds.
    """
    bound: set[str] = set()
    headers = {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": FOUNDRY_FEATURES,
    }
    for name in agent_names:
        url = f"{endpoint.rstrip('/')}/agents/{name}?api-version={FOUNDRY_API_VERSION}"
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        data = resp.json()
        versions = data.get("versions") if isinstance(data, dict) else None
        latest = versions.get("latest", {}) if isinstance(versions, dict) else {}
        definition = latest.get("definition", {}) if isinstance(latest, dict) else {}
        for tool in definition.get("tools", []) or []:
            if not isinstance(tool, dict):
                continue
            for key in ("project_connection_id", "connection_id", "projectConnectionId"):
                value = tool.get(key)
                if value:
                    bound.add(str(value))
    return bound


def delete_foundry_agent(endpoint: str, name: str, token: str) -> None:
    url = (
        f"{endpoint.rstrip('/')}/agents/{name}"
        f"?api-version={FOUNDRY_API_VERSION}&force=true"
    )
    resp = requests.delete(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Foundry-Features": FOUNDRY_FEATURES,
        },
        timeout=60,
    )
    # 200/202/204 = deleted; 404 = already gone (idempotent).
    if resp.status_code not in (200, 202, 204, 404):
        raise RuntimeError(
            f"DELETE {name} failed: HTTP {resp.status_code} {resp.text[:300]}"
        )


def _repo_corpus() -> str:
    """Concatenated text of repo files that may reference a tool connection."""
    parts: list[str] = []
    for d in _CORPUS_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in _CORPUS_SUFFIXES:
                try:
                    parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(parts)


def list_connections(endpoint: str, token: str) -> list[dict[str, Any]]:
    """Return project connections as dicts with name, type and ARM id.

    Same OpenAI list shape as /agents: items under ``data`` with cursor
    pagination (``has_more`` + ``last_id`` -> ``after``). Each item carries a
    top-level ``type`` (e.g. RemoteTool, CognitiveSearch) and an ``id`` that is
    the connection's ARM resource id (used for control-plane deletion).
    """
    base = f"{endpoint.rstrip('/')}/connections"
    headers = {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": FOUNDRY_FEATURES,
    }
    params: dict[str, str] = {"api-version": FOUNDRY_API_VERSION, "limit": "100"}
    out: list[dict[str, Any]] = []
    while True:
        resp = requests.get(base, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = body.get("data") or body.get("value") or []
        else:
            items = []
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                out.append(
                    {
                        "name": item["name"],
                        "type": item.get("type"),
                        "id": item.get("id"),
                    }
                )
        if not (isinstance(body, dict) and body.get("has_more") and body.get("last_id")):
            break
        params["after"] = str(body["last_id"])
    return out


def delete_connection(arm_id: str, arm_token: str) -> None:
    """Delete a project connection via the ARM control plane."""
    url = f"https://management.azure.com{arm_id}?api-version={ARM_API_VERSION}"
    resp = requests.delete(
        url, headers={"Authorization": f"Bearer {arm_token}"}, timeout=60
    )
    if resp.status_code not in (200, 202, 204, 404):
        raise RuntimeError(
            f"DELETE connection failed: HTTP {resp.status_code} {resp.text[:300]}"
        )


def _reconcile_connections(
    endpoint: str,
    ai_token: str,
    *,
    apply: bool,
    protect: set[str],
    credential: DefaultAzureCredential,
    live_agents: list[str],
) -> int:
    """Report (or, with ``apply``, delete) orphaned RemoteTool connections.

    A connection is an orphan only when ALL of these hold:
      * its type is in PRUNABLE_CONNECTION_TYPES (RemoteTool — an agent tool),
      * it is not in the ``protect`` allowlist,
      * its name appears nowhere in the repo corpus (agents/infra/scripts), AND
      * it is not bound as a tool by any *currently live* agent.

    The last condition is a safety net against pruning a connection that a real
    agent still uses even though the repo doesn't declare it (e.g. an
    out-of-band/manual attachment). Returns 0 on success/clean, 1 on a delete
    failure or a safety abort.
    """
    try:
        connections = list_connections(endpoint, ai_token)
    except requests.HTTPError as exc:  # pragma: no cover - network
        print(f"::error::Failed to list project connections: {exc}", file=sys.stderr)
        return 1

    prunable = [c for c in connections if c.get("type") in PRUNABLE_CONNECTION_TYPES]
    print(
        f"Tool connections ({len(prunable)} prunable / {len(connections)} total): "
        f"{', '.join(sorted(c['name'] for c in prunable)) or '(none)'}"
    )
    if not prunable:
        return 0

    corpus = _repo_corpus()
    if not corpus.strip():
        print(
            "::warning::Repo corpus is empty — skipping connection pruning to avoid "
            "deleting referenced tools on a read failure.",
            file=sys.stderr,
        )
        return 0

    try:
        bound = live_connection_bindings(endpoint, ai_token, live_agents)
    except requests.HTTPError as exc:  # pragma: no cover - network
        print(
            f"::warning::Could not read live agent tool bindings ({exc}); skipping "
            "connection pruning to stay safe.",
            file=sys.stderr,
        )
        return 0
    if bound:
        print(f"Bound by a live agent: {', '.join(sorted(bound))}")

    orphans = [
        c
        for c in prunable
        if c["name"] not in protect
        and c["name"] not in corpus
        and c["name"] not in bound
    ]
    if protect:
        print(f"Protected connections (never deleted): {', '.join(sorted(protect))}")
    if not orphans:
        print(
            "::notice::No connection drift — every tool connection is still "
            "referenced by the repo. Nothing to prune."
        )
        return 0

    verb = "Deleting" if apply else "Would delete (dry-run)"
    print(f"::warning::{len(orphans)} orphaned tool connection(s) detected.")
    arm_token: str | None = None
    failures = 0
    for conn in orphans:
        print(f"  {verb} connection: {conn['name']} (type={conn.get('type')})")
        if not apply:
            continue
        arm_id = conn.get("id")
        if not arm_id:
            failures += 1
            print(
                f"::error::Cannot delete connection {conn['name']}: no ARM id in listing.",
                file=sys.stderr,
            )
            continue
        if arm_token is None:
            arm_token = credential.get_token(ARM_SCOPE).token
        try:
            delete_connection(arm_id, arm_token)
            print(f"    deleted connection {conn['name']}")
        except Exception as exc:  # pragma: no cover - network
            failures += 1
            print(
                f"::error::Failed to delete connection {conn['name']}: {exc}",
                file=sys.stderr,
            )

    if not apply:
        print(
            "::notice::Dry-run only. Re-run with --apply (or set the prune input) to "
            "delete the above connection(s)."
        )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-endpoint",
        default=_first_env(
            "PROJECT_ENDPOINT",
            "AZURE_AI_PROJECT_ENDPOINT",
            "FOUNDRY_PROJECT_ENDPOINT",
        ),
        help="Foundry project endpoint (or PROJECT_ENDPOINT/AZURE_AI_PROJECT_ENDPOINT/FOUNDRY_PROJECT_ENDPOINT).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report drift without deleting (default).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete unmanaged agents.",
    )
    parser.add_argument(
        "--protect",
        default="",
        help="Comma-separated agent names to never delete, even if unmanaged.",
    )
    parser.add_argument(
        "--skip-connections",
        action="store_true",
        help="Only reconcile agents; do not touch orphaned tool connections.",
    )
    parser.add_argument(
        "--protect-connections",
        default="",
        help="Comma-separated connection names to never delete, even if orphaned.",
    )
    args = parser.parse_args()

    if not args.project_endpoint:
        print(
            "::error::--project-endpoint or PROJECT_ENDPOINT/AZURE_AI_PROJECT_ENDPOINT/FOUNDRY_PROJECT_ENDPOINT is required",
            file=sys.stderr,
        )
        return 2

    apply = bool(args.apply)
    protect = {p.strip() for p in args.protect.split(",") if p.strip()}
    protect_connections = {
        p.strip() for p in args.protect_connections.split(",") if p.strip()
    }

    expected = expected_agent_names()
    if not expected:
        print(
            "::error::Computed expected-agent set is EMPTY — refusing to reconcile "
            "(this would look like every agent is drift). Check the agents/ folder.",
            file=sys.stderr,
        )
        return 1
    print(f"Repo-managed agents ({len(expected)}): {', '.join(sorted(expected))}")

    credential = DefaultAzureCredential()
    token = credential.get_token(AI_SCOPE).token
    try:
        actual = list_foundry_agents(args.project_endpoint, token)
    except requests.HTTPError as exc:  # pragma: no cover - network
        print(f"::error::Failed to list Foundry agents: {exc}", file=sys.stderr)
        return 1
    print(f"Foundry agents ({len(actual)}): {', '.join(sorted(actual)) or '(none)'}")

    unmanaged = sorted(set(actual) - expected - protect)
    if protect:
        print(f"Protected (never deleted): {', '.join(sorted(protect))}")

    exit_code = 0
    if not unmanaged:
        print("::notice::No drift — every Foundry agent is repo-managed. Nothing to prune.")
    else:
        verb = "Deleting" if apply else "Would delete (dry-run)"
        print(f"::warning::{len(unmanaged)} unmanaged agent(s) detected.")
        failures = 0
        for name in unmanaged:
            print(f"  {verb}: {name}")
            if apply:
                try:
                    delete_foundry_agent(args.project_endpoint, name, token)
                    print(f"    deleted {name}")
                except Exception as exc:  # pragma: no cover - network
                    failures += 1
                    print(f"::error::Failed to delete {name}: {exc}", file=sys.stderr)

        if not apply:
            print(
                "::notice::Dry-run only. Re-run with --apply (or set the prune input) to delete the above."
            )
        if failures:
            exit_code = 1

    if not args.skip_connections:
        conn_code = _reconcile_connections(
            args.project_endpoint,
            token,
            apply=apply,
            protect=protect_connections,
            credential=credential,
            live_agents=actual,
        )
        exit_code = exit_code or conn_code

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
