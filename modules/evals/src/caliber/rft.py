from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .schemas import read_jsonl, validate_jsonl


def build_rft_plan(
    train_path: Path,
    validation_path: Path,
    grader_path: Path,
    base_model: str,
    suffix: str | None = None,
    project_endpoint: str | None = None,
) -> dict[str, Any]:
    train = validate_jsonl(train_path)
    validation = validate_jsonl(validation_path)
    if not grader_path.exists():
        raise ValueError(f"grader file does not exist: {grader_path}")
    if grader_path.suffix != ".py":
        raise ValueError(f"grader must be a Python file: {grader_path}")

    endpoint = project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_AI_PROJECT_ENDPOINT"
    )
    return {
        "ready_to_submit": bool(endpoint),
        "base_model": base_model,
        "suffix": suffix or "caliber-rft",
        "goal": "cost_optimization_after_agent_optimizer",
        "project_endpoint_configured": bool(endpoint),
        "train": train,
        "validation": validation,
        "grader": {"path": str(grader_path), "bytes": grader_path.stat().st_size},
        "next_step": (
            "Submit through Foundry only after optimizer establishes the quality target and "
            "reviewed datasets/graders confirm the cheaper model can preserve it."
            if endpoint
            else "Set FOUNDRY_PROJECT_ENDPOINT before submitting a real Foundry job."
        ),
    }


def package_rft_assets(
    *,
    train_path: Path,
    validation_path: Path,
    grader_path: Path,
    out_dir: Path,
    agent: str,
    base_model: str,
    suffix: str | None = None,
    optimizer_job_id: str | None = None,
    optimizer_candidate_id: str | None = None,
) -> dict[str, Any]:
    """Package reviewed Caliber rows into Foundry RFT-ready local artifacts."""

    if not agent.strip():
        raise ValueError("agent is required")

    train = _package_split(source_path=train_path, split="train")
    validation = _package_split(source_path=validation_path, split="validation")

    if not grader_path.exists():
        raise ValueError(f"grader file does not exist: {grader_path}")
    if grader_path.suffix != ".py":
        raise ValueError(f"grader must be a Python file: {grader_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    train_out = out_dir / f"{agent}-rft-train.jsonl"
    validation_out = out_dir / f"{agent}-rft-validation.jsonl"
    grader_out = out_dir / f"{agent}-rft-grader.py"
    job_spec_out = out_dir / f"{agent}-rft-job.dry-run.json"
    manifest_out = out_dir / "manifest.json"

    _write_jsonl(train_out, train["rows"])
    _write_jsonl(validation_out, validation["rows"])
    grader_source = _self_contained_grader_source(grader_path)
    grader_out.write_text(grader_source, encoding="utf-8")

    resolved_suffix = suffix or f"{agent}-cost"
    job_spec = {
        "status": "dry_run_not_submitted",
        "model": base_model,
        "suffix": resolved_suffix,
        "training_file": "<upload " + str(train_out) + ">",
        "validation_file": "<upload " + str(validation_out) + ">",
        "method": {
            "type": "reinforcement",
            "reinforcement": {
                "grader": {
                    "type": "python",
                    "name": f"{_safe_name(agent)}_evidence_grader",
                    "source_file": str(grader_out),
                },
            },
        },
    }
    job_spec_out.write_text(json.dumps(job_spec, indent=2, sort_keys=True), encoding="utf-8")

    if optimizer_candidate_id:
        next_step = (
            "Use the selected optimizer candidate as the gold quality target, confirm the "
            "RFT base model is supported, then submit live RFT only after approval."
        )
    else:
        next_step = (
            "Wait for Agent Optimizer to finish, apply the selected candidate after review, "
            "rerun the calibration eval, then recalibrate this grader before live RFT submit."
        )

    manifest = {
        "agent": agent,
        "base_model": base_model,
        "suffix": resolved_suffix,
        "goal": "cost_optimization_after_agent_optimizer",
        "ready_for_live_submit": False,
        "blocked_until": _rft_submission_gates(
            optimizer_job_id=optimizer_job_id,
            optimizer_candidate_id=optimizer_candidate_id,
        ),
        "optimizer_job_id": optimizer_job_id,
        "optimizer_candidate_id": optimizer_candidate_id,
        "gold_standard": (
            {
                "source": "agent_optimizer_candidate",
                "optimizer_job_id": optimizer_job_id,
                "optimizer_candidate_id": optimizer_candidate_id,
            }
            if optimizer_job_id or optimizer_candidate_id
            else None
        ),
        "source": {
            "train": str(train_path),
            "validation": str(validation_path),
            "grader": str(grader_path),
        },
        "artifacts": {
            "train": {"path": str(train_out), "rows": train["count"]},
            "validation": {"path": str(validation_out), "rows": validation["count"]},
            "grader": {"path": str(grader_out), "bytes": grader_out.stat().st_size},
            "job_spec": str(job_spec_out),
        },
        "checks": [
            _check("train_rft_format", train["count"] > 0, "Training rows end with user turns."),
            _check(
                "validation_rft_format",
                validation["count"] > 0,
                "Validation rows end with user turns.",
            ),
            _check(
                "self_contained_grader",
                "from caliber" not in grader_source,
                "Packaged grader does not import Caliber package modules.",
            ),
            _check(
                "strict_evidence_schema_gate",
                "REQUIRED_TOP_LEVEL_KEYS" in grader_source
                and "REQUIRED_EVIDENCE_KEYS" in grader_source,
                (
                    "Grader rewards the strict expert_evidence JSON contract and evidence-item "
                    "metadata surfaced by optimizer runs."
                ),
            ),
            _check(
                "submission_gate",
                False,
                "Live submission remains gated on optimizer completion, eval comparison, "
                "RFT base-model support, and explicit spend approval.",
            ),
        ],
        "next_step": next_step,
    }
    manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_rft_status(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {
            "status": "missing",
            "path": str(state_path),
            "message": "No local RFT job metadata file exists.",
        }
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid RFT state file: {state_path}") from exc
    return {"status": "found", "path": str(state_path), "job": data}


def _package_split(*, source_path: Path, split: str) -> dict[str, Any]:
    validate_jsonl(source_path)
    rows = [
        _to_rft_row(row, source_path=source_path, line_number=index, split=split)
        for index, row in enumerate(read_jsonl(source_path), 1)
    ]
    return {"count": len(rows), "rows": rows}


def _to_rft_row(
    row: dict[str, Any],
    *,
    source_path: Path,
    line_number: int,
    split: str,
) -> dict[str, Any]:
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{source_path}:{line_number}: messages must be a non-empty list")
    if messages[-1].get("role") != "user":
        raise ValueError(f"{source_path}:{line_number}: RFT row must end with a user message")
    if any(message.get("role") == "assistant" for message in messages):
        raise ValueError(
            f"{source_path}:{line_number}: RFT package rows must not include assistant messages"
        )

    expected = row.get("expected", {})
    expected_text = expected.get("text") if isinstance(expected, dict) else None
    packaged = {
        "messages": messages,
        "expected_output_json": row.get("expected_output_json", {}),
        "ground_truth": row.get("ground_truth", {}),
        "metadata": {
            **dict(row.get("metadata", {})),
            "caliber_source_id": row.get("id"),
            "caliber_rft_split": split,
        },
    }
    if row.get("id") is not None:
        packaged["id"] = row["id"]
    if expected_text is not None:
        packaged["reference_output"] = expected_text
    if "retrieved_context" in row:
        packaged["retrieved_context"] = row["retrieved_context"]
    if "expected_tools" in row:
        packaged["expected_tools"] = row["expected_tools"]
    return packaged


def _self_contained_grader_source(grader_path: Path) -> str:
    source = grader_path.read_text(encoding="utf-8")
    if "from caliber.evidence_grader import grade_evidence_contract" not in source:
        return source

    evidence_source = (Path(__file__).parent / "evidence_grader.py").read_text(encoding="utf-8")
    return (
        evidence_source
        + "\n\n"
        + "def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:\n"
        + "    return grade_evidence_contract(sample, item)\n"
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8-sig",
    )


def _check(name: str, ok: bool, message: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message}


def _rft_submission_gates(
    *,
    optimizer_job_id: str | None,
    optimizer_candidate_id: str | None,
) -> list[str]:
    if optimizer_candidate_id:
        return [
            "optimizer candidate is selected as the gold quality target",
            "gold candidate outputs are calibrated with the RFT grader",
            "RFT base model support is verified",
            "live RFT spend is explicitly approved",
        ]
    if optimizer_job_id:
        return [
            "optimizer job completes or recoverable candidate artifacts are selected",
            "optimizer candidate is selected as the gold quality target",
            "optimized hosted agent passes the calibration eval",
            "RFT grader threshold is recalibrated against optimized outputs",
        ]
    return [
        "optimizer job completes",
        "optimizer candidate is selected and applied after review",
        "optimized hosted agent passes the calibration eval",
        "RFT grader threshold is recalibrated against optimized outputs",
    ]


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")
