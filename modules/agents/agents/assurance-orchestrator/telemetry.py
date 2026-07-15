"""OpenTelemetry helpers for Forge hosted agents.

The hosted-agent runtime owns exporter configuration. These helpers only create
spans and attributes that are useful both for App Insights diagnostics and for
later trace-to-RFT dataset harvesting.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

_SENSITIVE_ATTRIBUTE_PARTS = (
    "auth",
    "bearer",
    "connection",
    "credential",
    "key",
    "password",
    "private",
    "secret",
    "session",
    "token",
)


def _is_sensitive_attribute(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SENSITIVE_ATTRIBUTE_PARTS)


def _include_rft_content() -> bool:
    return os.getenv("FORGE_TRACE_RFT_CONTENT", "").strip().lower() in {"1", "true", "yes", "on"}


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def set_span_attribute(span: Span, name: str, value: Any) -> None:
    if value is None:
        return
    if _is_sensitive_attribute(name):
        span.set_attribute(name, "**********")
        return
    if isinstance(value, (str, bool, int, float)):
        span.set_attribute(name, value)
        return
    span.set_attribute(name, json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def set_span_attributes(span: Span, attributes: dict[str, Any] | None) -> None:
    for name, value in (attributes or {}).items():
        set_span_attribute(span, name, value)


def mark_span_ok(span: Span) -> None:
    span.set_status(Status(StatusCode.OK))


def mark_span_error(span: Span, error: BaseException) -> None:
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, str(error)))
    set_span_attribute(span, "exception.type", f"{type(error).__module__}.{type(error).__name__}")
    set_span_attribute(span, "exception.message", str(error))


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    started = time.perf_counter()
    with trace.get_tracer("forge.hosted_agents").start_as_current_span(name) as span:
        set_span_attributes(span, attributes)
        try:
            yield span
            mark_span_ok(span)
        except Exception as error:
            mark_span_error(span, error)
            raise
        finally:
            set_span_attribute(span, "duration_ms", int((time.perf_counter() - started) * 1000))


def agent_startup_attributes(agent_name: str, task_family: str, model: str | None = None) -> dict[str, Any]:
    return {
        "gen_ai.agent.name": agent_name,
        "gen_ai.operation.name": "hosted_agent_startup",
        "gen_ai.request.model": model,
        "forge.agent.name": agent_name,
        "forge.rft.agent": agent_name,
        "forge.rft.task_family": task_family,
        "forge.rft.capture.version": "1",
    }


def rft_reference_attributes(
    *,
    agent_name: str,
    task_family: str,
    scenario_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    expected_tools: list[str] | None = None,
    expected_output: Any | None = None,
    grader_reference: Any | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "forge.rft.capture.version": "1",
        "forge.rft.agent": agent_name,
        "forge.rft.task_family": task_family,
        "forge.rft.scenario_id": scenario_id,
    }
    if messages is not None:
        attrs["forge.rft.messages.count"] = len(messages)
        attrs["forge.rft.messages.hash"] = stable_hash(messages)
        if _include_rft_content():
            attrs["gen_ai.input.messages"] = messages
    if expected_tools is not None:
        attrs["forge.rft.expected_tools"] = expected_tools
    if expected_output is not None:
        attrs["forge.rft.expected_output.hash"] = stable_hash(expected_output)
        if _include_rft_content():
            attrs["forge.rft.expected_output"] = expected_output
    if grader_reference is not None:
        attrs["forge.rft.grader_reference.hash"] = stable_hash(grader_reference)
        if _include_rft_content():
            attrs["forge.rft.grader_reference"] = grader_reference
    return attrs
