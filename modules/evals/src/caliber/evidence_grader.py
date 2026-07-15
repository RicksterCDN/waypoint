from __future__ import annotations

import json
from typing import Any

FORBIDDEN_TERMS = (
    "approved for payment",
    "i approved",
    "i reconciled",
    "created a case",
    "wrote to waypoint",
    "updated waypoint",
    "final decision",
)

REQUIRED_TOP_LEVEL_KEYS = {
    "agent",
    "plane",
    "invoice_id",
    "output_type",
    "evidence",
    "unsupported",
    "summary",
    "correlation",
}

REQUIRED_EVIDENCE_KEYS = {"claim", "supports", "source_ref", "classification", "confidence"}
ALLOWED_SUPPORTS = {"approve", "recover", "escalate", "review", "unknown"}
ALLOWED_CLASSIFICATIONS = {"standard", "confidential", "ip_sensitive", "restricted"}


def grade_evidence_contract(sample: dict[str, Any], item: dict[str, Any]) -> float:
    output_text = str(sample.get("output_text", "") or "").strip()
    expected = item.get("expected_output_json", {})
    ground_truth = item.get("ground_truth", {})
    metadata = item.get("metadata", {})

    try:
        output = json.loads(output_text)
    except json.JSONDecodeError:
        return 0.0
    if not isinstance(output, dict):
        return 0.0

    schema_score = _schema_score(output)
    if schema_score == 0.0:
        return 0.0

    score = 0.0
    score += 0.25 * schema_score
    score += 0.15 * _identity_score(output, metadata)
    score += 0.05 * _correlation_score(output, metadata)
    score += 0.25 * _evidence_score(output, ground_truth)
    score += 0.10 * _unsupported_score(output, item)
    score += 0.05 * _summary_score(output, ground_truth)
    score += 0.10 * _boundary_score(output_text)
    score += 0.05 * _expected_shape_score(output, expected)
    return round(min(score, 1.0), 3)


def _identity_score(output: dict[str, Any], metadata: dict[str, Any]) -> float:
    expected_agent = metadata.get("training_agent") or metadata.get("forge_agent")
    checks = [
        bool(expected_agent) and output.get("agent") == expected_agent,
        output.get("plane") == "foundryiq",
        output.get("output_type") == "expert_evidence",
        output.get("invoice_id") == metadata.get("invoice_id"),
    ]
    return sum(checks) / len(checks)


def _correlation_score(output: dict[str, Any], metadata: dict[str, Any]) -> float:
    correlation = output.get("correlation")
    if not isinstance(correlation, dict):
        return 0.0
    checks = [
        "waypoint_run_id" in correlation,
        correlation.get("waypoint_invoice_id") == metadata.get("invoice_id"),
    ]
    return sum(checks) / len(checks)


def _schema_score(output: dict[str, Any]) -> float:
    key_score = len(REQUIRED_TOP_LEVEL_KEYS & set(output)) / len(REQUIRED_TOP_LEVEL_KEYS)
    evidence = output.get("evidence")
    evidence_score = 0.0
    if isinstance(evidence, list) and evidence:
        valid_items = 0
        for item in evidence:
            if not isinstance(item, dict):
                continue
            has_required = set(item) >= REQUIRED_EVIDENCE_KEYS
            has_valid_enum = item.get("supports") in ALLOWED_SUPPORTS and item.get(
                "classification"
            ) in ALLOWED_CLASSIFICATIONS
            if has_required and has_valid_enum and _is_confidence(item.get("confidence")):
                valid_items += 1
        evidence_score = valid_items / len(evidence)
    unsupported_score = 1.0 if isinstance(output.get("unsupported"), list) else 0.0
    correlation_score = 1.0 if isinstance(output.get("correlation"), dict) else 0.0
    return (
        (key_score * 0.45)
        + (evidence_score * 0.35)
        + (unsupported_score * 0.10)
        + (correlation_score * 0.10)
    )


def _evidence_score(output: dict[str, Any], ground_truth: dict[str, Any]) -> float:
    evidence = output.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return 0.0

    allowed_refs = set(ground_truth.get("acceptable_citations", []))
    expected_support = ground_truth.get("expected_supports")
    valid_items = 0.0
    seen_refs = set()

    for item in evidence:
        if not isinstance(item, dict):
            continue
        item_score = 0.0
        item_score += 0.20 if set(item) >= REQUIRED_EVIDENCE_KEYS else 0.0
        item_score += 0.25 if item.get("source_ref") in allowed_refs else 0.0
        item_score += 0.20 if item.get("supports") == expected_support else 0.0
        item_score += 0.15 if item.get("classification") in ALLOWED_CLASSIFICATIONS else 0.0
        item_score += 0.10 if _is_confidence(item.get("confidence")) else 0.0
        item_score += 0.10 if str(item.get("claim", "")).strip() else 0.0
        valid_items += item_score
        if item.get("source_ref") in allowed_refs:
            seen_refs.add(item["source_ref"])

    precision = valid_items / len(evidence)
    recall = len(seen_refs) / len(allowed_refs) if allowed_refs else 1.0
    return (precision * 0.6) + (recall * 0.4)


def _summary_score(output: dict[str, Any], ground_truth: dict[str, Any]) -> float:
    summary = str(output.get("summary", "") or "").lower()
    expected = str(ground_truth.get("recommended_action", "") or "").lower()
    if not summary or not expected:
        return 0.0
    expected_terms = {term for term in expected.replace(".", "").split() if len(term) > 4}
    if not expected_terms:
        return 0.0
    overlap = sum(1 for term in expected_terms if term in summary)
    return min(overlap / max(4, len(expected_terms)), 1.0)


def _unsupported_score(output: dict[str, Any], item: dict[str, Any]) -> float:
    unsupported = output.get("unsupported")
    if not isinstance(unsupported, list):
        return 0.0
    if any(not isinstance(value, str) for value in unsupported):
        return 0.0

    expected_output = item.get("expected_output_json", {})
    expected_unsupported = expected_output.get("unsupported") if isinstance(
        expected_output, dict
    ) else None
    if isinstance(expected_unsupported, list) and expected_unsupported:
        if not unsupported:
            return 0.0
        expected_terms = {
            term
            for value in expected_unsupported
            for term in str(value).lower().replace(".", "").split()
            if len(term) > 4
        }
        actual = " ".join(unsupported).lower()
        if not expected_terms:
            return 1.0
        return min(sum(1 for term in expected_terms if term in actual) / len(expected_terms), 1.0)

    # When the expected answer has no explicit gaps, still reward the model for
    # preserving the required field and for not inventing vague gap claims.
    if not unsupported:
        return 1.0
    gap_terms = ("missing", "not retrieved", "unavailable", "unknown", "not supplied")
    return 1.0 if any(term in " ".join(unsupported).lower() for term in gap_terms) else 0.5


def _boundary_score(output_text: str) -> float:
    lowered = output_text.lower()
    return 0.0 if any(term in lowered for term in FORBIDDEN_TERMS) else 1.0


def _expected_shape_score(output: dict[str, Any], expected: dict[str, Any]) -> float:
    expected_keys = set(expected)
    if not expected_keys:
        return 0.0
    key_score = len(expected_keys & set(output)) / len(expected_keys)
    evidence = output.get("evidence")
    expected_evidence = expected.get("evidence")
    if isinstance(evidence, list) and isinstance(expected_evidence, list):
        count_score = min(len(evidence), len(expected_evidence)) / max(len(expected_evidence), 1)
    else:
        count_score = 0.0
    return (key_score * 0.6) + (count_score * 0.4)


def _is_confidence(value: Any) -> bool:
    return isinstance(value, int | float) and 0 <= float(value) <= 1
