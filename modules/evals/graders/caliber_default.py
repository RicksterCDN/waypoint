from __future__ import annotations

from typing import Any


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    """Minimal exact-match grader for early Caliber smoke tests."""
    expected = item.get("expected", {})
    expected_text = str(expected.get("text", "")).strip().lower()
    output_text = str(sample.get("output_text", "")).strip().lower()
    if not expected_text:
        return 0.0
    return 1.0 if expected_text in output_text else 0.0
