from __future__ import annotations

from pathlib import Path
from typing import Any

from .grading import load_grader, require_score_range
from .schemas import read_jsonl, validate_jsonl


def build_eval_plan(
    dataset_path: Path,
    grader_path: Path,
    model: str,
    limit: int = 0,
) -> dict[str, Any]:
    dataset = validate_jsonl(dataset_path)
    grader = load_grader(grader_path)
    rows = read_jsonl(dataset_path)
    selected = rows[:limit] if limit > 0 else rows

    grader_self_check_scores = []
    for row in selected[:3]:
        expected_text = str(row.get("expected", {}).get("text", ""))
        score = require_score_range(
            float(grader({"output_text": expected_text, "output_tools": []}, row))
        )
        grader_self_check_scores.append(score)

    return {
        "ready_to_run": True,
        "model": model,
        "dataset": dataset,
        "grader": {"path": str(grader_path), "bytes": grader_path.stat().st_size},
        "limit": limit,
        "grader_self_check_scores": grader_self_check_scores,
        "next_step": "Wire this plan to a local or hosted Forge agent invocation target.",
    }
