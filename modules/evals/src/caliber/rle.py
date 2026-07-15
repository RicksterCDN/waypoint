from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .schemas import validate_jsonl


def build_rle_plan(
    *,
    agent: str,
    environment: str,
    train_path: Path,
    validation_path: Path,
    eval_path: Path,
    grader_path: Path,
    base_model: str,
    frontier_model: str,
    fine_tuned_model: str,
    project_endpoint: str | None = None,
    tool_url: str | None = None,
    plain_reinforcement: bool = False,
) -> dict[str, Any]:
    """Build an offline Deploy -> Observe -> Learn plan for an RFT environment."""

    if not agent.strip():
        raise ValueError("agent is required")
    if not environment.strip():
        raise ValueError("environment is required")

    train = validate_jsonl(train_path)
    validation = validate_jsonl(validation_path)
    eval_dataset = validate_jsonl(eval_path)
    if not grader_path.exists():
        raise ValueError(f"grader file does not exist: {grader_path}")
    if grader_path.suffix != ".py":
        raise ValueError(f"grader must be a Python file: {grader_path}")

    endpoint = project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_AI_PROJECT_ENDPOINT"
    )
    tools_endpoint = (tool_url or os.environ.get("TOOL_URL") or "").rstrip("/")
    mode = "plain-reinforcement" if plain_reinforcement else "tool-augmented-rft"

    checks = [
        _check("agent", True, f"Target agent is {agent}."),
        _check("environment", True, f"Environment is {environment}."),
        _check("project_endpoint", bool(endpoint), "Foundry project endpoint is configured."),
        _check("train_dataset", train["rows"] > 0, "Training dataset has rows."),
        _check("validation_dataset", validation["rows"] > 0, "Validation dataset has rows."),
        _check("eval_dataset", eval_dataset["rows"] > 0, "Held-out eval dataset has rows."),
        _check("grader", True, "Python reward grader exists."),
    ]
    if plain_reinforcement:
        checks.append(
            _check(
                "training_tools",
                True,
                "Plain reinforcement selected; training-time tools are not required.",
            )
        )
    else:
        checks.append(
            _check(
                "training_tools",
                bool(tools_endpoint),
                "Tool URL is configured for tool-augmented RFT.",
            )
        )

    ready_to_observe = eval_dataset["rows"] > 0
    ready_to_learn = bool(endpoint) and all(item["ok"] for item in checks)

    return {
        "ready": ready_to_observe and ready_to_learn,
        "agent": agent,
        "environment": environment,
        "mode": mode,
        "models": {
            "frontier": frontier_model,
            "base": base_model,
            "fine_tuned": fine_tuned_model,
        },
        "datasets": {
            "train": train,
            "validation": validation,
            "eval": eval_dataset,
        },
        "grader": {"path": str(grader_path), "bytes": grader_path.stat().st_size},
        "connections": {
            "project_endpoint_configured": bool(endpoint),
            "tool_url_configured": bool(tools_endpoint),
        },
        "checks": checks,
        "workflow": [
            {
                "stage": "deploy",
                "ready": bool(endpoint),
                "next_step": f"Deploy or select the {agent} agent in {environment}.",
            },
            {
                "stage": "observe",
                "ready": ready_to_observe,
                "next_step": (
                    f"Run a baseline eval for {frontier_model} and {base_model} against "
                    f"{eval_path}."
                ),
            },
            {
                "stage": "learn",
                "ready": ready_to_learn,
                "next_step": (
                    "Submit a Foundry reinforcement fine-tuning job only after baseline evals, "
                    "dataset review, and grader calibration are approved."
                ),
            },
        ],
        "outputs": {
            "eval_results": f"runs/eval-results/{agent}/",
            "rft_state": ".rft_job.json",
            "generated": "outputs/",
        },
        "next_step": _next_step(
            ready_to_learn,
            bool(endpoint),
            plain_reinforcement,
            bool(tools_endpoint),
        ),
    }


def _check(name: str, ok: bool, message: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message}


def _next_step(
    ready_to_learn: bool,
    endpoint_configured: bool,
    plain_reinforcement: bool,
    tool_url_configured: bool,
) -> str:
    if ready_to_learn:
        return "Run baseline evals, then review whether RFT submission is warranted."
    if not endpoint_configured:
        return "Set FOUNDRY_PROJECT_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT before live RFT work."
    if not plain_reinforcement and not tool_url_configured:
        return "Set TOOL_URL or use --plain-reinforcement for reward-only RFT planning."
    return "Review failed checks before running a live reinforcement fine-tuning workflow."
