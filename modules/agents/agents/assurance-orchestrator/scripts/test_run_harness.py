"""Unit tests for the code-owned run harness + durable finalize outbox (F1/F3).

Run with:

    cd agents/assurance-orchestrator
    python -m unittest scripts.test_run_harness
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_harness
from assurance_workflow import RetryPolicy, WorkTarget, fan_out_validators
from finalize_outbox import FinalizeOutbox

ALL_EXPERTS_ENABLED_ENV = {
    "ASSURANCE_ORCHESTRATOR_WORKIQ_ENABLED": "true",
    "ASSURANCE_ORCHESTRATOR_WEBIQ_ENABLED": "true",
    "ASSURANCE_ORCHESTRATOR_FOUNDRYIQ_ENABLED": "true",
    "ASSURANCE_ORCHESTRATOR_FABRICIQ_ENABLED": "true",
}


def _result(
    invoice_id: str = "INV-1",
    *,
    status: str = "completed",
    validators: list[dict[str, Any]] | None = None,
    contexts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A workflow-result stub shaped like assurance_workflow.WorkflowRunResult."""
    if validators is None:
        validators = [{"validator_id": "webiq", "status": "completed", "output_quality": "structured"}]
    if contexts is None:
        contexts = [{"invoice_id": invoice_id, "context": {"invoice": {"id": invoice_id}}}]
    return {
        "mode": "run",
        "request_type": "invoice_assurance",
        "status": status,
        "run": {"run_id": "run-1"},
        "targets": [{"invoice_id": invoice_id}],
        "documents": [],
        "invoice_contexts": contexts,
        "deterministic_checks": [{"invoice_id": invoice_id, "status": "matched"}],
        "validators": validators,
        "judgements": [{"invoice_id": invoice_id, "status": "clean"}],
        "write_plan": {"read_only": True},
        "auth_context": {},
        "steps": [],
        "side_effects_performed": False,
    }


class RecorderSpy:
    """Records handoff/fail calls and returns a scripted recorder response."""

    def __init__(self, handoff_response: str | Callable[[str], str] = '{"ok": true}') -> None:
        self.handoffs: list[dict[str, Any]] = []
        self.fails: list[tuple[str, str]] = []
        self.opens: list[str] = []
        self._handoff_response = handoff_response

    def handoff(self, bundle_json: str) -> str:
        bundle = json.loads(bundle_json)
        self.handoffs.append(bundle)
        if callable(self._handoff_response):
            return self._handoff_response(bundle_json)
        return self._handoff_response

    def fail(self, invoice_id: str, reason: str) -> str:
        self.fails.append((invoice_id, reason))
        return '{"ok": true}'

    def open(self, invoice_id: str) -> None:
        self.opens.append(invoice_id)


def _run(coro):
    return asyncio.run(coro)


class RunHarnessFinalizeTests(unittest.TestCase):
    def test_success_hands_off_exactly_once_with_shared_operation_id(self) -> None:
        spy = RecorderSpy()
        with patch.object(run_harness, "resolve_run_identity", return_value="op-shared-123"):
            result = _run(
                run_harness.run_invoice_assurance_and_finalize(
                    invoice_id="INV-1",
                    workflow_runner=lambda rj, iv, lim: _async(_result("INV-1")),
                    recorder_handoff=spy.handoff,
                    run_opener=spy.open,
                    fail_finalizer=spy.fail,
                    outbox=None,
                )
            )
        self.assertEqual(len(spy.handoffs), 1)
        self.assertEqual(spy.handoffs[0]["invoice_id"], "INV-1")
        self.assertEqual(spy.handoffs[0]["operation_id"], "op-shared-123")
        self.assertEqual(spy.fails, [])
        self.assertEqual(result["finalize"]["handed_off"], ["INV-1"])
        self.assertTrue(result["finalize"]["performed"])
        self.assertEqual(spy.opens, ["INV-1"])

    def test_timeout_finalizes_failed_never_running(self) -> None:
        spy = RecorderSpy()

        async def _boom(rj, iv, lim):
            raise TimeoutError("budget exceeded")

        result = _run(
            run_harness.run_invoice_assurance_and_finalize(
                invoice_id="INV-9",
                workflow_runner=_boom,
                recorder_handoff=spy.handoff,
                run_opener=spy.open,
                fail_finalizer=spy.fail,
                outbox=None,
            )
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("timeout", result["error"].lower())
        self.assertEqual(spy.handoffs, [])  # never handed off
        self.assertEqual([inv for inv, _ in spy.fails], ["INV-9"])  # finalized failed
        self.assertIn("INV-9", result["finalize"]["failed"])
        self.assertTrue(result["finalize"]["performed"])

    def test_generic_exception_finalizes_failed(self) -> None:
        spy = RecorderSpy()

        async def _boom(rj, iv, lim):
            raise RuntimeError("expert exploded")

        result = _run(
            run_harness.run_invoice_assurance_and_finalize(
                invoice_id="INV-7",
                workflow_runner=_boom,
                recorder_handoff=spy.handoff,
                run_opener=spy.open,
                fail_finalizer=spy.fail,
                outbox=None,
            )
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("RuntimeError", result["error"])
        self.assertEqual([inv for inv, _ in spy.fails], ["INV-7"])

    def test_all_lanes_failed_no_context_is_inconclusive_no_write(self) -> None:
        spy = RecorderSpy()
        dead = [
            {"validator_id": vid, "status": "failed", "output_quality": "no_evidence"}
            for vid in ("webiq", "fabriciq", "workiq", "foundryiq")
        ]
        result = _run(
            run_harness.run_invoice_assurance_and_finalize(
                invoice_id="INV-2",
                workflow_runner=lambda rj, iv, lim: _async(
                    _result("INV-2", status="partial", validators=dead, contexts=[])
                ),
                recorder_handoff=spy.handoff,
                run_opener=spy.open,
                fail_finalizer=spy.fail,
                outbox=None,
            )
        )
        self.assertEqual(spy.handoffs, [])  # no governed write
        self.assertIn("INV-2", result["finalize"]["inconclusive"])
        self.assertEqual([inv for inv, _ in spy.fails], ["INV-2"])  # recorded failed, not running

    def test_degraded_lane_with_context_still_hands_off(self) -> None:
        spy = RecorderSpy()
        mixed = [
            {"validator_id": "webiq", "status": "completed", "output_quality": "structured"},
            {"validator_id": "fabriciq", "status": "failed", "output_quality": "no_evidence"},
        ]
        result = _run(
            run_harness.run_invoice_assurance_and_finalize(
                invoice_id="INV-3",
                workflow_runner=lambda rj, iv, lim: _async(_result("INV-3", status="partial", validators=mixed)),
                recorder_handoff=spy.handoff,
                run_opener=spy.open,
                fail_finalizer=spy.fail,
                outbox=None,
            )
        )
        self.assertEqual(len(spy.handoffs), 1)
        self.assertIn("fabriciq", spy.handoffs[0]["degraded_experts"])
        self.assertEqual(result["finalize"]["handed_off"], ["INV-3"])


class RunHarnessOutboxTests(unittest.TestCase):
    def test_transient_recorder_failure_is_outboxed_then_drained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox = FinalizeOutbox(tmp)

            # First run: recorder returns not-ok -> entry stays pending in the outbox.
            spy_fail = RecorderSpy(handoff_response='{"ok": false, "error": "503"}')
            first = _run(
                run_harness.run_invoice_assurance_and_finalize(
                    invoice_id="INV-5",
                    workflow_runner=lambda rj, iv, lim: _async(_result("INV-5")),
                    recorder_handoff=spy_fail.handoff,
                    run_opener=spy_fail.open,
                    fail_finalizer=spy_fail.fail,
                    outbox=outbox,
                    drain_outbox=False,
                )
            )
            self.assertIn("INV-5", first["finalize"]["outboxed"])
            self.assertEqual(spy_fail.fails, [])  # not flipped failed — outbox owns retry
            self.assertEqual(len(outbox.pending()), 1)

            # Second run start drains the pending entry with a now-healthy recorder.
            spy_ok = RecorderSpy(handoff_response='{"ok": true}')
            _run(
                run_harness.run_invoice_assurance_and_finalize(
                    invoice_id="INV-6",
                    workflow_runner=lambda rj, iv, lim: _async(_result("INV-6")),
                    recorder_handoff=spy_ok.handoff,
                    run_opener=spy_ok.open,
                    fail_finalizer=spy_ok.fail,
                    outbox=outbox,
                    drain_outbox=True,
                )
            )
            # The stranded INV-5 bundle was redelivered during the drain.
            self.assertTrue(any(b["invoice_id"] == "INV-5" for b in spy_ok.handoffs))
            self.assertEqual(len(outbox.pending()), 0)

    def test_no_outbox_flips_failed_when_recorder_not_ok(self) -> None:
        spy = RecorderSpy(handoff_response='{"ok": false}')
        result = _run(
            run_harness.run_invoice_assurance_and_finalize(
                invoice_id="INV-8",
                workflow_runner=lambda rj, iv, lim: _async(_result("INV-8")),
                recorder_handoff=spy.handoff,
                run_opener=spy.open,
                fail_finalizer=spy.fail,
                outbox=None,
            )
        )
        self.assertEqual([inv for inv, _ in spy.fails], ["INV-8"])
        self.assertIn("INV-8", result["finalize"]["failed"])


class FanOutParallelismTests(unittest.TestCase):
    def test_fan_out_runs_validators_concurrently(self) -> None:
        """The four expert lanes must overlap (asyncio.gather), not run serially."""
        concurrency = {"current": 0, "peak": 0}

        class BarrierExpertClient:
            async def resolve_tool_name(self, validator_id: str) -> str:
                return f"{validator_id}_validate"

            async def call_validator(self, validator_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
                concurrency["current"] += 1
                concurrency["peak"] = max(concurrency["peak"], concurrency["current"])
                await asyncio.sleep(0.05)  # hold the lane open so peers overlap
                concurrency["current"] -= 1
                return {"structuredContent": {"status": "completed", "summary": f"{validator_id} ok"}}

        targets = [WorkTarget(invoice_id="INV-1", finding_id=None, waypoint_case_id=None, summary=None)]
        contexts = [{"invoice_id": "INV-1", "invoice": {"id": "INV-1"}}]
        checks = [{"invoice_id": "INV-1", "status": "matched"}]

        with patch.dict("os.environ", ALL_EXPERTS_ENABLED_ENV, clear=False):
            started = time.perf_counter()
            validators = _run(
                fan_out_validators(
                    targets,
                    contexts,
                    checks,
                    expert_client=BarrierExpertClient(),
                    retry_policy=RetryPolicy(),
                    steps=[],
                )
            )
            elapsed = time.perf_counter() - started

        self.assertEqual(len(validators), 4)
        self.assertGreaterEqual(concurrency["peak"], 2)  # lanes overlapped
        self.assertLess(elapsed, 0.05 * 4)  # total ~= max(lane), not the serial sum


async def _async(value: Any) -> Any:
    return value


if __name__ == "__main__":
    unittest.main()
