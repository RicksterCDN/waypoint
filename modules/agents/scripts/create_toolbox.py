"""Create or update the Foundry toolbox for a given agent.

Usage:
    python scripts/create_toolbox.py --agent lovelace

The script imports `agents/<agent>/toolbox.py`, reads the `TOOLBOX` list (and
optional `DESCRIPTION`) from it, then:
  1. Creates a new immutable toolbox version named `<agent>-tools`
  2. Promotes that version to `default_version`
  3. Prints `TOOLBOX_MCP_ENDPOINT=<url>` to stdout (and to $GITHUB_OUTPUT
     as `toolbox_endpoint=<url>`) so the deploy step can wire it into the
     agent container.

Idempotent: each run creates a new version. Any agent pointed at the toolbox
MCP endpoint picks up the new tool list on its next session start.

Required env vars:
    FOUNDRY_PROJECT_ENDPOINT  e.g. https://<acct>.services.ai.azure.com/api/projects/<proj>
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

REPO_ROOT = Path(__file__).resolve().parent.parent


def _toolbox_ops(project):
    """Return the toolboxes operations group across azure-ai-projects releases.

    The toolbox API graduated from `project.beta.toolboxes` (older preview
    SDKs) to the GA `project.toolboxes` (>=2.1.0). Prefer GA; fall back to
    beta so the script keeps working on whichever release CI happens to pin.
    Returns None when neither is present.
    """
    ga = getattr(project, "toolboxes", None)
    if ga is not None:
        return ga
    return getattr(getattr(project, "beta", None), "toolboxes", None)


# Flat Responses-tool type -> its Toolbox-tool counterpart. The GA
# `create_version(tools=...)` API expects `ToolboxTool` variants, but each
# agent's toolbox.py builds the flat tool models (MCPTool, WebSearchTool,
# CodeInterpreterTool) shared with the Responses agents. The two families are
# field-for-field identical apart from the `type` discriminator, so we convert
# by dict round-trip. Import lazily/tolerantly so a missing model on an older
# SDK degrades to "pass the flat tool through" rather than crashing at import.
def _toolbox_tool_map() -> dict:
    from azure.ai.projects import models as _m

    mapping = {}
    for type_key, cls_name in (
        ("mcp", "MCPToolboxTool"),
        ("web_search", "WebSearchToolboxTool"),
        ("code_interpreter", "CodeInterpreterToolboxTool"),
    ):
        cls = getattr(_m, cls_name, None)
        if cls is not None:
            mapping[type_key] = cls
    return mapping


def _to_toolbox_tool(tool):
    """Coerce a flat Responses tool (or dict) into a `ToolboxTool` model.

    Idempotent: an object that is already a ToolboxTool (or an unrecognized
    type on an older SDK) is returned unchanged so create_version can still
    attempt to serialize it.
    """
    try:
        from azure.ai.projects.models import ToolboxTool
    except Exception:  # pragma: no cover - very old SDK without ToolboxTool
        return tool
    if isinstance(tool, ToolboxTool):
        return tool
    try:
        data = dict(tool)
    except Exception:
        return tool
    ttype = data.pop("type", None)
    box_cls = _toolbox_tool_map().get(ttype)
    if box_cls is None:
        # Unknown/unmapped tool type — hand it back and let the SDK decide.
        return tool
    return box_cls(**data)


def _load_agent_toolbox(agent: str):
    """Dynamically import agents/<agent>/toolbox.py and return (tools, description, required_env)."""
    path = REPO_ROOT / "agents" / agent / "toolbox.py"
    if not path.is_file():
        print(f"::error::No toolbox.py found at {path}", file=sys.stderr)
        sys.exit(1)

    spec = importlib.util.spec_from_file_location(f"agents.{agent}.toolbox", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    tools = getattr(module, "TOOLBOX", None)
    if tools is None:
        print(f"::error::{path} must define a `TOOLBOX` list", file=sys.stderr)
        sys.exit(1)

    description = getattr(module, "DESCRIPTION", f"Tools for the {agent} agent")
    # Optional dict: {tool_label: (env_var_a, env_var_b, ...)}. At least one
    # of each tuple must be set in the environment, else we refuse to bump
    # the toolbox (see _check_required_env).
    required_env = getattr(module, "REQUIRED_ENV", {})
    return tools, description, required_env


def _check_required_env(
    agent: str,
    required_env: dict,
    allow_missing: bool,
) -> set[str]:
    """Return the set of tool labels whose REQUIRED_ENV is unsatisfied.

    This used to abort outright on unsatisfied env, but that wrongly failed
    deploys that wouldn't actually drop any tool (e.g. first-time create,
    or the agent's previous version also lacked github). We now just
    *compute* the unsatisfied set and let the caller compare it to the
    real diff against the live toolbox.
    """
    unsatisfied: set[str] = set()
    for label, env_vars in required_env.items():
        if not any(os.environ.get(v) for v in env_vars):
            unsatisfied.add(label)
    if unsatisfied and allow_missing:
        print(
            f"::warning::Agent '{agent}' has unsatisfied REQUIRED_ENV groups "
            f"for tools: {sorted(unsatisfied)}. Continuing because "
            "--allow-missing-tools was passed.",
            file=sys.stderr,
        )
    return unsatisfied


def _tool_label(tool: object) -> str | None:
    """Best-effort: pull a stable label out of an SDK tool object or dict."""
    for attr in ("server_label", "name", "label"):
        val = getattr(tool, attr, None)
        if val:
            return str(val)
    if isinstance(tool, dict):
        for key in ("server_label", "name", "label"):
            if tool.get(key):
                return str(tool[key])
    return None


def _existing_tool_labels(project, toolbox_name: str) -> set[str] | None:
    """Return the set of tool labels in the current default version of the
    toolbox, or None when we couldn't determine it (toolbox doesn't exist
    yet, unexpected SDK shape, transient error)."""
    # Tolerate SDK variations across azure-ai-projects preview releases:
    # the toolbox + version accessors have moved between attributes.
    tb_ops = _toolbox_ops(project)
    if tb_ops is None:
        return None
    try:
        tb = tb_ops.get(toolbox_name)
    except Exception:  # pragma: no cover - toolbox not yet created
        return None
    default_version = (
        getattr(tb, "default_version", None)
        or (tb.get("default_version") if isinstance(tb, dict) else None)
    )
    if not default_version:
        return None

    version_obj = None
    for fetcher in (
        lambda: tb_ops.get_version(toolbox_name, default_version),
        lambda: tb_ops.versions.get(toolbox_name, default_version),
    ):
        try:
            version_obj = fetcher()
            break
        except Exception:
            continue
    if version_obj is None:
        return None

    tools = (
        getattr(version_obj, "tools", None)
        or (version_obj.get("tools") if isinstance(version_obj, dict) else None)
    )
    if not tools:
        return set()
    labels: set[str] = set()
    for t in tools:
        lbl = _tool_label(t)
        if lbl:
            labels.add(lbl)
    return labels


def _decide_bump_action(
    agent: str,
    toolbox_name: str,
    new_tools: list,
    existing_labels: set[str] | None,
    unsatisfied: set[str],
    allow_missing: bool,
) -> str:
    """Decide whether to create a new toolbox version, skip the bump and
    keep the current default version intact, or hard-fail.

    Returns one of:
      - "proceed" — bump the toolbox version as normal.
      - "skip"    — the new tool set is a subset of the current default and
                    every dropped tool is reproducible from env vars that
                    just happen to be unset in this environment (e.g. a CI
                    run without a developer's PAT). Keep the existing
                    version as the default so we don't wipe tools that
                    were wired up in a previous run.

    Hard-fails (sys.exit(2)) when the bump would drop a tool that is NOT
    explained by missing env — that's a deliberate developer change and
    should be opt-in via --allow-missing-tools.
    """
    if existing_labels is None:
        # First-time create or unknown SDK shape. Fall back to the simpler
        # "is REQUIRED_ENV satisfied?" check so we don't silently regress.
        if unsatisfied and not allow_missing:
            print(
                f"::error::Toolbox '{toolbox_name}' state could not be read; "
                f"REQUIRED_ENV groups are unsatisfied for: {sorted(unsatisfied)}. "
                "Set the corresponding env vars OR pass --allow-missing-tools.",
                file=sys.stderr,
            )
            sys.exit(2)
        return "proceed"

    new_labels = {l for l in (_tool_label(t) for t in new_tools) if l}
    would_drop = existing_labels - new_labels
    if not would_drop:
        return "proceed"  # New version is a superset; safe to bump.

    if allow_missing:
        print(
            f"::warning::New toolbox version for '{agent}' would drop tools: "
            f"{sorted(would_drop)}. Continuing because --allow-missing-tools "
            "was passed.",
            file=sys.stderr,
        )
        return "proceed"

    # Are ALL of the dropped tools explainable by unsatisfied REQUIRED_ENV?
    # If so, this is a "CI without the developer's PAT" run — keep the
    # current default version instead of bumping to a subset.
    if would_drop.issubset(unsatisfied):
        print(
            f"::notice::Skipping toolbox bump for '{toolbox_name}': the new "
            f"tool set is missing env-reproducible tools {sorted(would_drop)} "
            f"(declared in agents/{agent}/toolbox.py REQUIRED_ENV). Keeping "
            "the existing default version so previously-wired tools are not "
            "wiped. Set the missing env vars to deploy a new version, or "
            "pass --allow-missing-tools to intentionally remove them.",
            file=sys.stderr,
        )
        return "skip"

    # Genuine drop — the developer removed a tool from toolbox.py that
    # isn't env-gated. Require an explicit opt-in.
    msg_lines = [
        f"Refusing to bump '{toolbox_name}': would drop tools that are in the "
        f"current default version: {sorted(would_drop)}.",
    ]
    fixable = would_drop & set(unsatisfied)
    if fixable:
        msg_lines.append(
            "  These are reproducible if you set the corresponding REQUIRED_ENV "
            f"vars (declared in agents/{agent}/toolbox.py): {sorted(fixable)}"
        )
    unexplained = would_drop - set(unsatisfied)
    if unexplained:
        msg_lines.append(
            f"  These are NOT covered by REQUIRED_ENV: {sorted(unexplained)}. "
            "Either declare them in REQUIRED_ENV or pass --allow-missing-tools."
        )
    print(f"::error::{chr(10).join(msg_lines)}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, help="Agent folder name under agents/")
    parser.add_argument(
        "--toolbox-name",
        default=None,
        help="Toolbox name (default: <agent>-tools)",
    )
    parser.add_argument(
        "--allow-missing-tools",
        action="store_true",
        help=(
            "Allow creating a toolbox version even when REQUIRED_ENV groups "
            "are unsatisfied. Without this flag the script refuses to bump "
            "the toolbox so a missing PAT/secret can't silently delete a "
            "tool that the previous version had."
        ),
    )
    args = parser.parse_args()

    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        print("::error::FOUNDRY_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 1

    toolbox_name = args.toolbox_name or f"{args.agent}-tools"
    tools, description, required_env = _load_agent_toolbox(args.agent)
    unsatisfied = _check_required_env(
        args.agent, required_env, args.allow_missing_tools
    )

    project = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )

    existing_labels = _existing_tool_labels(project, toolbox_name)
    action = _decide_bump_action(
        agent=args.agent,
        toolbox_name=toolbox_name,
        new_tools=tools,
        existing_labels=existing_labels,
        unsatisfied=unsatisfied,
        allow_missing=args.allow_missing_tools,
    )

    endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
    )

    if action == "skip":
        # Don't create a new version; the existing default keeps serving
        # the env-reproducible tools that this run can't recreate. The MCP
        # endpoint URL is name-based so it points at the same toolbox.
        print(
            f"Toolbox '{toolbox_name}' left at its current default version."
        )
    else:
        tb_ops = _toolbox_ops(project)
        if tb_ops is None:
            print(
                "::error::This azure-ai-projects release exposes neither "
                "`project.toolboxes` (GA) nor `project.beta.toolboxes`; "
                "cannot create the toolbox version.",
                file=sys.stderr,
            )
            return 1

        # GA create_version(tools=...) expects ToolboxTool models; agents'
        # toolbox.py builds the flat Responses tool models. Convert here so a
        # single toolbox.py stays the source of truth for both agent shapes.
        toolbox_tools = [_to_toolbox_tool(t) for t in tools]

        version = tb_ops.create_version(
            toolbox_name,
            description=description,
            tools=toolbox_tools,
        )
        print(f"Created toolbox version: {toolbox_name} v{version.version}")

        tb_ops.update(
            toolbox_name,
            default_version=version.version,
        )
        print(f"Promoted v{version.version} to default")

    print(f"TOOLBOX_MCP_ENDPOINT={endpoint}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"toolbox_endpoint={endpoint}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
