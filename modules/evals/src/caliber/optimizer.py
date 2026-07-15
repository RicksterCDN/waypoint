from __future__ import annotations

from pathlib import Path
from typing import Any

OPTIMIZER_PACKAGE = "azure-ai-agentserver-optimization"
REQUIRED_CONTRACT_KEYS = (
    "agent",
    "plane",
    "output_type",
    "correlation",
    "evidence",
    "unsupported",
    "summary",
)


def build_optimizer_plan(
    *,
    forge_path: Path,
    agent: str,
    dataset_path: Path | None = None,
    eval_config_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect a Forge checkout and plan pre-RFT agent optimization steps."""

    root = forge_path.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Forge path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Forge path is not a directory: {root}")
    if not agent.strip():
        raise ValueError("agent is required")

    azure_yaml = root / "azure.yaml"
    agent_root = root / "agents" / agent
    agent_yaml = agent_root / "agent.yaml"
    main_py = agent_root / "main.py"
    requirements = agent_root / "requirements.txt"
    prompt = agent_root / "prompt.md"
    baseline_dir = agent_root / ".agent_configs" / "baseline"

    if not agent_root.exists():
        raise ValueError(f"agent root does not exist: {agent_root}")

    azure_text = _read_text(azure_yaml)
    agent_yaml_text = _read_text(agent_yaml)
    main_text = _read_text(main_py)
    requirements_text = _read_text(requirements)
    prompt_text = _read_text(prompt)

    hosted_service = (
        azure_yaml.exists()
        and f"{agent}:" in azure_text
        and "host: azure.ai.agent" in azure_text
    )
    hosted_agent = "kind: hosted" in agent_yaml_text
    optimizer_wired = "load_config" in main_text and OPTIMIZER_PACKAGE in requirements_text
    baseline_ready = all(
        path.exists()
        for path in (
            baseline_dir / "metadata.yaml",
            baseline_dir / "instructions.md",
        )
    )
    prompt_contract = _prompt_contract_status(prompt_text)
    eval_config_status = _file_status(eval_config_path) if eval_config_path else None
    dataset_status = _file_status(dataset_path) if dataset_path else None

    optimizer_ready = hosted_service and hosted_agent and optimizer_wired and baseline_ready
    prompt_repair_ready = prompt.exists()

    checks = [
        _check(
            "azd_hosted_service",
            hosted_service,
            "Forge azure.yaml exposes the agent as an azure.ai.agent service.",
        ),
        _check("hosted_agent_yaml", hosted_agent, "agent.yaml declares kind: hosted."),
        _check(
            "optimizer_sdk_wiring",
            optimizer_wired,
            "main.py uses load_config and requirements include azure-ai-agentserver-optimization.",
        ),
        _check(
            "baseline_config",
            baseline_ready,
            ".agent_configs/baseline contains metadata.yaml and instructions.md.",
        ),
        _check(
            "prompt_contract",
            prompt_contract["aligned"],
            "prompt.md explicitly requires the strict Caliber evidence JSON contract.",
        ),
    ]

    return {
        "agent": agent,
        "forge_path": str(root),
        "agent_root": str(agent_root),
        "dataset": dataset_status,
        "eval_config": eval_config_status,
        "routes": {
            "agent_optimizer": {
                "applicable": hosted_service and hosted_agent,
                "ready": optimizer_ready,
                "why": (
                    "Use Agent Optimizer to improve hosted-agent instructions and non-model assets "
                    "before spending model fine-tuning budget."
                ),
                "commands": [
                    (
                        f"azd ai agent optimize {agent} "
                        "--config <reviewed-eval-yaml> "
                        "--eval-model <existing-eval-model-deployment> "
                        "--optimize-model <existing-optimization-model-deployment> "
                        "--target instruction "
                        "--no-wait --no-prompt --output json"
                    ),
                    "azd ai agent optimize status <operation-id> --watch",
                    "azd ai agent optimize apply --candidate <candidate-id>",
                ],
            },
            "prompt_agent_repair": {
                "applicable": prompt_repair_ready,
                "ready": prompt_contract["aligned"],
                "why": (
                    "Fix the prompt contract first so eval failures measure grounded reasoning "
                    "instead of response-shape drift."
                ),
                "required_contract_keys": list(REQUIRED_CONTRACT_KEYS),
            },
        },
        "checks": checks,
        "prompt_contract": prompt_contract,
        "next_steps": _next_steps(
            optimizer_ready=optimizer_ready,
            hosted_applicable=hosted_service and hosted_agent,
            optimizer_wired=optimizer_wired,
            baseline_ready=baseline_ready,
            prompt_contract_aligned=prompt_contract["aligned"],
        ),
    }


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _file_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def _check(name: str, ok: bool, message: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message}


def _prompt_contract_status(prompt_text: str) -> dict[str, Any]:
    present = [key for key in REQUIRED_CONTRACT_KEYS if key in prompt_text]
    missing = [key for key in REQUIRED_CONTRACT_KEYS if key not in prompt_text]
    has_json_instruction = "json" in prompt_text.lower()
    aligned = not missing and has_json_instruction
    return {
        "aligned": aligned,
        "present_keys": present,
        "missing_keys": missing,
        "has_json_instruction": has_json_instruction,
        "recommended_change": (
            "Replace or gate the demo text response shape with a strict JSON evidence contract "
            "for assurance-evidence tasks, while preserving concise status answers for pure "
            "Waypoint status questions."
        ),
    }


def _next_steps(
    *,
    optimizer_ready: bool,
    hosted_applicable: bool,
    optimizer_wired: bool,
    baseline_ready: bool,
    prompt_contract_aligned: bool,
) -> list[str]:
    steps: list[str] = []
    if not prompt_contract_aligned:
        steps.append(
            "Patch the Forge hosted agent prompt.md to emit the strict Caliber evidence JSON "
            "contract for invoice-assurance evidence questions."
        )
    if not hosted_applicable:
        steps.append(
            "Use a Forge checkout that includes the hosted azure.ai.agent service for this "
            "candidate before submitting an Agent Optimizer job."
        )
    if hosted_applicable and not optimizer_wired:
        steps.append(
            "Scaffold Agent Optimizer wiring in Forge: add azure-ai-agentserver-optimization "
            "and call load_config() in main.py."
        )
    if hosted_applicable and not baseline_ready:
        steps.append(
            "Create the Forge hosted agent .agent_configs\\baseline\\metadata.yaml and "
            "instructions.md from the reviewed prompt baseline."
        )
    if optimizer_ready:
        steps.append(
            "Run azd ai agent optimize with a reviewed eval.yaml and apply the selected "
            "candidate locally for source review before deploy."
        )
    steps.append(
        "Rerun the contract-grounding rubric and Caliber grader calibration after optimizer. "
        "Use RFT afterward to preserve optimized behavior on a cheaper model."
    )
    return steps
