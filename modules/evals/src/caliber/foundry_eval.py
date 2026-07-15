from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_eval_output_items(
    *,
    project_endpoint: str,
    eval_id: str,
    run_id: str,
    out_path: Path,
    limit: int = 100,
) -> dict[str, Any]:
    if not project_endpoint.strip():
        raise ValueError("project endpoint is required")
    if not eval_id.strip():
        raise ValueError("eval id is required")
    if not run_id.strip():
        raise ValueError("run id is required")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise ValueError("azure-ai-projects and azure-identity are required") from exc

    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )
    openai_client = client.get_openai_client()

    rows = []
    for item in openai_client.evals.runs.output_items.list(
        run_id,
        eval_id=eval_id,
        limit=limit,
    ):
        rows.append(_output_item_row(item))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "path": str(out_path),
        "rows": len(rows),
        "eval_id": eval_id,
        "run_id": run_id,
        "project_endpoint": project_endpoint,
        "calibration_note": (
            "Use final_assistant_message as the graded output. Use tool_messages as "
            "retrieval trace/evidence context, not as final answer text."
        ),
    }


def _output_item_row(item: Any) -> dict[str, Any]:
    data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
    sample = data.get("sample") or {}
    output = sample.get("output") or []
    assistant_messages = [
        message
        for message in output
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    tool_messages = [
        message for message in output if isinstance(message, dict) and message.get("role") == "tool"
    ]
    return {
        "id": (data.get("datasource_item") or {}).get("id"),
        "input": sample.get("input"),
        "final_assistant_message": assistant_messages[-1] if assistant_messages else None,
        "tool_messages": tool_messages,
        "results": data.get("results"),
        "usage": data.get("usage"),
    }
