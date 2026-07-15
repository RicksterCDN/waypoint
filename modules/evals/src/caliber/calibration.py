from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .grading import load_grader, require_score_range
from .schemas import read_jsonl, validate_jsonl

DEFAULT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)


def calibrate_grader(
    *,
    dataset_path: Path,
    outputs_path: Path,
    grader_path: Path,
    thresholds: list[float] | None = None,
) -> dict[str, Any]:
    dataset = validate_jsonl(dataset_path)
    if not outputs_path.exists():
        raise ValueError(f"outputs file does not exist: {outputs_path}")
    if outputs_path.suffix != ".jsonl":
        raise ValueError(f"outputs file must be JSONL: {outputs_path}")

    selected_thresholds = thresholds or list(DEFAULT_THRESHOLDS)
    for threshold in selected_thresholds:
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError(f"threshold must be between 0.0 and 1.0: {threshold}")

    rows = read_jsonl(dataset_path)
    outputs = read_jsonl(outputs_path)
    grader = load_grader(grader_path)
    dataset_by_id = {str(row.get("id")): row for row in rows if row.get("id")}

    scored = []
    unmatched_outputs = []
    for index, output_row in enumerate(outputs):
        item = _match_dataset_row(
            output_row=output_row,
            output_index=index,
            dataset_rows=rows,
            dataset_by_id=dataset_by_id,
        )
        if item is None:
            unmatched_outputs.append(_output_identifier(output_row, index))
            continue
        output_text = _extract_output_text(output_row)
        score = require_score_range(float(grader({"output_text": output_text}, item)))
        scored.append(
            {
                "id": item.get("id"),
                "output_id": _output_identifier(output_row, index),
                "score": score,
                "has_output_text": bool(output_text.strip()),
            }
        )

    scores = [item["score"] for item in scored]
    pass_rates = {
        str(threshold): _pass_rate(scores=scores, threshold=threshold)
        for threshold in selected_thresholds
    }
    recommended_thresholds = [
        threshold
        for threshold in selected_thresholds
        if 0.25 <= (1.0 - pass_rates[str(threshold)]) <= 0.50
    ]

    return {
        "ready": bool(scored) and not unmatched_outputs,
        "dataset": dataset,
        "outputs": {"path": str(outputs_path), "rows": len(outputs)},
        "grader": {"path": str(grader_path), "bytes": grader_path.stat().st_size},
        "scored_rows": len(scored),
        "unmatched_outputs": unmatched_outputs,
        "score_summary": _score_summary(scores),
        "pass_rates": pass_rates,
        "recommended_thresholds": recommended_thresholds,
        "recommendation": _recommendation(scored, unmatched_outputs, recommended_thresholds),
    }


def _match_dataset_row(
    *,
    output_row: dict[str, Any],
    output_index: int,
    dataset_rows: list[dict[str, Any]],
    dataset_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    output_id = output_row.get("id")
    if output_id is not None:
        match = dataset_by_id.get(str(output_id))
        if match is not None:
            return match
    if output_index < len(dataset_rows):
        return dataset_rows[output_index]
    return None


def _extract_output_text(output_row: dict[str, Any]) -> str:
    output_text = output_row.get("output_text")
    if isinstance(output_text, str):
        return output_text

    final_message = output_row.get("final_assistant_message")
    if isinstance(final_message, dict):
        content = final_message.get("content")
        if isinstance(content, str):
            return _string_content_text(content)
        if isinstance(content, list):
            return "".join(_content_part_text(part) for part in content)

    sample = output_row.get("sample")
    if isinstance(sample, dict):
        sample_output = sample.get("output")
        if isinstance(sample_output, list):
            assistant_messages = [
                message
                for message in sample_output
                if isinstance(message, dict) and message.get("role") == "assistant"
            ]
            if assistant_messages:
                return _message_content_text(assistant_messages[-1])
    return ""


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return _string_content_text(content)
    if isinstance(content, list):
        return "".join(_content_part_text(part) for part in content)
    return ""


def _string_content_text(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return ""
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return content
    if isinstance(decoded, list):
        return "".join(_content_part_text(part) for part in decoded)
    if isinstance(decoded, dict):
        return _content_part_text(decoded)
    return content


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return ""
    text = part.get("text")
    if isinstance(text, str):
        return text
    if isinstance(part.get("content"), str):
        return part["content"]
    return ""


def _output_identifier(output_row: dict[str, Any], index: int) -> str:
    for key in ("id", "output_id", "datasource_item_id"):
        value = output_row.get(key)
        if value is not None:
            return str(value)
    return f"output-index:{index}"


def _pass_rate(*, scores: list[float], threshold: float) -> float:
    if not scores:
        return 0.0
    return round(sum(1 for score in scores if score >= threshold) / len(scores), 3)


def _score_summary(scores: list[float]) -> dict[str, Any]:
    if not scores:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": min(scores),
        "max": max(scores),
        "avg": round(sum(scores) / len(scores), 3),
    }


def _recommendation(
    scored: list[dict[str, Any]],
    unmatched_outputs: list[str],
    recommended_thresholds: list[float],
) -> str:
    if unmatched_outputs:
        return "Resolve unmatched outputs before using calibration results."
    if not scored:
        return "No outputs were scored; export or capture model outputs first."
    if recommended_thresholds:
        return (
            "Use a threshold in recommended_thresholds for a 25-50% baseline failure "
            "rate, then inspect failed rows before RFT submission."
        )
    return (
        "No tested threshold produced a 25-50% baseline failure rate; revise the "
        "grader, dataset, or thresholds before RFT submission."
    )
