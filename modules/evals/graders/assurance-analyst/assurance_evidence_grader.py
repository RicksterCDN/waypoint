from __future__ import annotations

from typing import Any

from caliber.evidence_grader import grade_evidence_contract


def grade(sample: dict[str, Any], item: dict[str, Any]) -> float:
    return grade_evidence_contract(sample, item)
