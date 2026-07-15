"""Validate and print the Forge prompt-agent deployment plan.

The plan is derived from each agent's canonical `prompt.md` file. It may be
empty: the current evidence experts deploy as hosted agents, but the validator
is kept so future prompt agents can opt in with metadata.forge.deployment.prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from prompty import load as load_prompty


REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the prompt-agent plan and print a human summary.",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include prompt.md files whose metadata.forge.deployment.prompt.enabled is false.",
    )
    args = parser.parse_args()

    errors: list[str] = []
    plan = build_plan(include_disabled=args.include_disabled, errors=errors)
    if errors:
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        return 1

    if args.check:
        print(f"Validated {len(plan)} prompt-agent deployment(s)")
        for item in plan:
            print(
                f"- {item['agentName']} <- {item['promptPath']} "
                f"(model={item['model']}, hosted={item['hostedEnabled']})"
            )
    else:
        print(json.dumps(plan, indent=2))
    return 0


def build_plan(*, include_disabled: bool, errors: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for prompt_path in sorted(AGENTS_DIR.glob("*/prompt.md")):
        folder = prompt_path.parent.name
        try:
            prompt = load_prompty(prompt_path)
        except Exception as exc:  # noqa: BLE001 - fail validation with file context.
            errors.append(f"{prompt_path}: Prompty load failed: {exc}")
            continue

        metadata = prompt.metadata if isinstance(prompt.metadata, dict) else {}
        forge = metadata.get("forge", {})
        deployment = forge.get("deployment", {})
        prompt_deploy = deployment.get("prompt", {})
        hosted_deploy = deployment.get("hosted", {})
        prompt_enabled = bool(prompt_deploy.get("enabled"))
        if not prompt_enabled and not include_disabled:
            continue

        agent_name = prompt_deploy.get("agentName")
        hosted_enabled = hosted_deploy.get("enabled", True)
        model_id = getattr(prompt.model, "id", None)
        instructions = prompt.instructions or ""

        if prompt.name != folder:
            errors.append(f"{prompt_path}: name must match folder {folder!r}, got {prompt.name!r}")
        if prompt_enabled and agent_name != prompt.name:
            errors.append(
                f"{prompt_path}: metadata.forge.deployment.prompt.agentName must be "
                f"{prompt.name!r} for in-place replacement, got {agent_name!r}"
            )
        if prompt_enabled and hosted_enabled is not False:
            errors.append(
                f"{prompt_path}: prompt replacements must set "
                "metadata.forge.deployment.hosted.enabled=false"
            )
        if not model_id:
            errors.append(f"{prompt_path}: model.id is required")
        if not instructions.strip():
            errors.append(f"{prompt_path}: prompt body/instructions are required")

        result.append(
            {
                "folder": folder,
                "agentName": agent_name,
                "displayName": prompt.display_name,
                "description": prompt.description,
                "model": model_id,
                "apiType": getattr(prompt.model, "api_type", None),
                "temperature": getattr(prompt.model.options, "temperature", None),
                "promptPath": str(prompt_path.relative_to(REPO_ROOT)),
                "hostedEnabled": hosted_enabled,
                "promptEnabled": prompt_enabled,
                "tools": [getattr(tool, "name", "") for tool in prompt.tools],
                "smokeInvoiceId": forge.get("smoke", {}).get("invoiceId"),
            }
        )

    return result


if __name__ == "__main__":
    sys.exit(main())
