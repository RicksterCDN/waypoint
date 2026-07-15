"""Code-owned run lifecycle for the hosted Assurance Orchestrator (F1).

The deployed orchestrator used to run assurance as a chain of MODEL-decided tool calls:
``open_run`` (running) -> sequential ``consult_*`` -> ``handoff_to_waypoint_recorder``. If the
long LLM turn was cut off after the open but before the handoff, the run orphaned at
``running`` — nothing guaranteed the finalize.

This harness makes the lifecycle DETERMINISTIC, per the pipeline docs ("keep side-effect
delivery in deterministic code, not model-selected tool calls"; "fan out with asyncio.gather";
"final synthesis tool-free and evidence-only"). It:

1. Opens each known invoice's run ``running`` up front (a run to finalize + a visible window).
2. Runs the existing read-only ``assurance_workflow`` under the ``max_runtime_minutes`` bound
   (parallel ``asyncio.gather`` fan-out already lives there).
3. In a ``try/finally`` ALWAYS finalizes: on success it deterministically hands each invoice's
   fused evidence to the waypoint-recorder (which applies policy and performs the governed
   write, PATCHing the shared run to ``completed``); on timeout/exception it marks each opened
   run ``failed`` with a reason — never leaving it ``running``.
4. Preserves the partial-evidence policy: degraded lanes still finalize (recorder decides
   review/escalate); only when EVERY lane failed AND there is no Waypoint context does it skip
   the write and report ``inconclusive``.
5. Routes the terminal recorder write through the durable finalize outbox (F3) so a transient
   recorder failure retries instead of orphaning.

The waypoint-recorder remains the ONLY agent that writes to Waypoint; this harness only moves
WHO triggers it (deterministic code, in a ``finally``) — not who writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from assurance_workflow import normalize_workflow_request, run_assurance_orchestrator_invoice_assurance_async
from expert_clients import finalize_run_failed, handoff_to_waypoint_recorder, open_run_for_invoice, resolve_run_identity
from finalize_outbox import FinalizeOutbox

logger = logging.getLogger("assurance_orchestrator.run_harness")

# Injection seams (overridable in tests). Real implementations are blocking HTTP round-trips,
# so the harness calls them via asyncio.to_thread.
WorkflowRunner = Callable[[str | None, str | None, int], Awaitable[dict[str, Any]]]
RecorderHandoff = Callable[[str], str]
RunOpener = Callable[[str], None]
FailFinalizer = Callable[[str, str], Any]


async def _default_workflow_runner(request_json: str | None, invoice_id: str | None, limit: int) -> dict[str, Any]:
    return await run_assurance_orchestrator_invoice_assurance_async(
        request_json,
        invoice_id=invoice_id,
        limit=limit,
    )


def _known_invoice_ids(request_json: str | None, invoice_id: str | None) -> list[str]:
    """Best-effort resolve the invoice ids we can open BEFORE running the workflow.

    Covers the common hosted trigger (an explicit invoice) and inline run manifests. When the
    run selects work from Waypoint's queue instead, ids are unknown up front — those runs are
    only opened by the workflow's own discovery, so a pre-run crash can't orphan them.
    """
    ids: list[str] = []
    if invoice_id and invoice_id.strip():
        ids.append(invoice_id.strip())
    try:
        request = normalize_workflow_request(request_json, invoice_id=invoice_id)
    except Exception:  # a malformed request is surfaced by the workflow itself later
        return _dedupe(ids)
    for item in request.items:
        if item.invoice_id and item.invoice_id.strip():
            ids.append(item.invoice_id.strip())
    return _dedupe(ids)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _resp_ok(response: str | None) -> bool:
    if not response:
        return False
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return False
    return bool(isinstance(payload, dict) and payload.get("ok"))


def _validators_for(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in result.get("validators", []) if isinstance(v, dict)]


def _invoice_ids_from_result(result: dict[str, Any], known: list[str]) -> list[str]:
    ids = list(known)
    for judgement in result.get("judgements", []):
        if isinstance(judgement, dict) and judgement.get("invoice_id"):
            ids.append(str(judgement["invoice_id"]).strip())
    for target in result.get("targets", []):
        if isinstance(target, dict) and target.get("invoice_id"):
            ids.append(str(target["invoice_id"]).strip())
    return _dedupe([i for i in ids if i])


def _has_context(invoice_id: str, result: dict[str, Any]) -> bool:
    for context in result.get("invoice_contexts", []):
        if not isinstance(context, dict):
            continue
        ctx_id = str(context.get("invoice_id") or context.get("id") or "").strip()
        if ctx_id and ctx_id == invoice_id:
            payload = context.get("context", context)
            return bool(payload)
    return False


def _every_lane_failed(validators: list[dict[str, Any]]) -> bool:
    if not validators:
        return True
    return all(v.get("status") == "failed" for v in validators)


def _bundle_for_invoice(invoice_id: str, result: dict[str, Any]) -> dict[str, Any]:
    validators = _validators_for(result)
    degraded = [
        v.get("validator_id")
        for v in validators
        if v.get("status") == "failed" or v.get("output_quality") in {"malformed", "no_evidence"}
    ]
    judgement = next(
        (j for j in result.get("judgements", []) if isinstance(j, dict) and str(j.get("invoice_id")).strip() == invoice_id),
        None,
    )
    return {
        "invoice_id": invoice_id,
        "workflow_status": result.get("status"),
        "judgement": judgement,
        "validators": validators,
        "deterministic_checks": result.get("deterministic_checks"),
        "write_plan_preview": result.get("write_plan"),
        "degraded_experts": [d for d in degraded if d],
    }


async def run_invoice_assurance_and_finalize(
    request_json: str | None = None,
    invoice_id: str | None = None,
    pdf_uri: str | None = None,
    pdf_base64: str | None = None,
    limit: int = 1,
    *,
    workflow_runner: WorkflowRunner | None = None,
    recorder_handoff: RecorderHandoff | None = None,
    run_opener: RunOpener | None = None,
    fail_finalizer: FailFinalizer | None = None,
    outbox: FinalizeOutbox | None = None,
    drain_outbox: bool = True,
) -> dict[str, Any]:
    """Run invoice assurance end-to-end with a guaranteed terminal finalize."""
    workflow_runner = workflow_runner or _default_workflow_runner
    recorder_handoff = recorder_handoff or handoff_to_waypoint_recorder
    run_opener = run_opener or open_run_for_invoice
    fail_finalizer = fail_finalizer or finalize_run_failed
    if outbox is None:
        outbox = FinalizeOutbox.from_env()

    if (pdf_uri or pdf_base64) and not request_json:
        request_json = json.dumps(
            {
                "invoice_id": invoice_id.strip() if invoice_id else None,
                "pdf_uri": pdf_uri.strip() if pdf_uri else None,
                "pdf_base64": pdf_base64.strip() if pdf_base64 else None,
            }
        )
    clamped_limit = max(1, min(int(limit), 25))

    finalize: dict[str, Any] = {
        "performed": False,
        "handed_off": [],
        "failed": [],
        "inconclusive": [],
        "outboxed": [],
        "drained": None,
    }

    # Retry any recorder writes stranded by a previous run's transient failure.
    if outbox is not None and drain_outbox:
        def _deliver(bundle_json: str) -> bool:
            try:
                return _resp_ok(recorder_handoff(bundle_json))
            except Exception:
                logger.warning("outbox delivery attempt failed", exc_info=True)
                return False

        try:
            finalize["drained"] = await asyncio.to_thread(outbox.drain, _deliver)
        except Exception:  # draining is best-effort and must never fail the new run
            logger.warning("finalize outbox drain failed", exc_info=True)

    known = _known_invoice_ids(request_json, invoice_id)
    for inv in known:
        try:
            await asyncio.to_thread(run_opener, inv)
        except Exception:
            logger.warning("early-open failed for %s", inv, exc_info=True)

    try:
        result = await workflow_runner(request_json, invoice_id, clamped_limit)
    except TimeoutError as exc:
        reason = f"timeout: assurance exceeded its runtime budget ({type(exc).__name__})"
        await _fail_all(known, reason, fail_finalizer, finalize)
        return _failed_envelope(known, reason, finalize)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        await _fail_all(known, reason, fail_finalizer, finalize)
        return _failed_envelope(known, reason, finalize)

    if not isinstance(result, dict):
        reason = f"workflow returned unexpected result type {type(result).__name__}"
        await _fail_all(known, reason, fail_finalizer, finalize)
        return _failed_envelope(known, reason, finalize)

    await _finalize_from_result(result, known, recorder_handoff, fail_finalizer, outbox, finalize)
    result["finalize"] = finalize
    return result


async def _fail_all(
    invoice_ids: list[str],
    reason: str,
    fail_finalizer: FailFinalizer,
    finalize: dict[str, Any],
) -> None:
    for inv in invoice_ids:
        try:
            await asyncio.to_thread(fail_finalizer, inv, reason)
            finalize["performed"] = True
        except Exception:
            logger.warning("finalize-failed for %s did not complete", inv, exc_info=True)
        finalize["failed"].append(inv)


def _failed_envelope(invoice_ids: list[str], reason: str, finalize: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "run",
        "request_type": "invoice_assurance",
        "status": "failed",
        "error": reason,
        "invoice_ids": invoice_ids,
        "finalize": finalize,
        "side_effects_performed": finalize.get("performed", False),
    }


async def _finalize_from_result(
    result: dict[str, Any],
    known: list[str],
    recorder_handoff: RecorderHandoff,
    fail_finalizer: FailFinalizer,
    outbox: FinalizeOutbox | None,
    finalize: dict[str, Any],
) -> None:
    invoice_ids = _invoice_ids_from_result(result, known)
    validators = _validators_for(result)
    for inv in invoice_ids:
        # Partial-evidence policy: only skip the write when there is truly nothing to act on.
        if _every_lane_failed(validators) and not _has_context(inv, result):
            reason = "inconclusive: every expert lane failed and no Waypoint context was available"
            try:
                await asyncio.to_thread(fail_finalizer, inv, reason)
                finalize["performed"] = True
            except Exception:
                logger.warning("inconclusive finalize-failed for %s did not complete", inv, exc_info=True)
            finalize["inconclusive"].append(inv)
            continue

        bundle = _bundle_for_invoice(inv, result)
        bundle["operation_id"] = resolve_run_identity(inv)
        bundle_json = json.dumps(bundle, ensure_ascii=False)

        entry_id = outbox.enqueue(inv, bundle_json, operation_id=bundle["operation_id"]) if outbox else None
        try:
            response = await asyncio.to_thread(recorder_handoff, bundle_json)
        except Exception as exc:
            response = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        if _resp_ok(response):
            if outbox and entry_id:
                outbox.mark_done(entry_id)
            finalize["performed"] = True
            finalize["handed_off"].append(inv)
        else:
            if outbox and entry_id:
                outbox.mark_failed(entry_id, response or "recorder finalize returned not-ok")
                finalize["outboxed"].append(inv)
            else:
                # No durable outbox to retry through — flip the run failed so it never orphans.
                try:
                    await asyncio.to_thread(fail_finalizer, inv, "recorder finalize did not succeed")
                    finalize["performed"] = True
                except Exception:
                    logger.warning("fallback finalize-failed for %s did not complete", inv, exc_info=True)
                finalize["failed"].append(inv)
