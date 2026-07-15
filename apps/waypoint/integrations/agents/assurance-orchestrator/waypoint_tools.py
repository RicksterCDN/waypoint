"""Read-only Waypoint function tools for AssuranceOrchestrator."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from agent_framework import FunctionTool

from assurance_workflow import run_assurance_orchestrator_invoice_assurance_async
from run_harness import run_invoice_assurance_and_finalize
from waypoint_client import WaypointReadOnlyClient, is_waypoint_configured

logger = logging.getLogger("assurance_orchestrator.waypoint_tools")


def waypoint_get_work() -> str:
    """Read the Waypoint work queue from GET /api/work."""
    return _json({"work": _client().get_work(), "correlation": _empty_correlation()})


def waypoint_get_action_types() -> str:
    """Read Waypoint's closed action vocabulary from GET /api/actions/types."""
    return _run_tool(
        "waypoint_get_action_types",
        lambda: {"action_types": _client().get_action_types(), "correlation": _empty_correlation()},
    )


def waypoint_get_runs() -> str:
    """Read Waypoint agent run anchors from GET /api/runs."""
    return _json({"runs": _client().get_runs(), "correlation": _empty_correlation()})


def waypoint_get_cases() -> str:
    """Read assurance cases from GET /api/cases."""
    return _json({"cases": _client().get_cases(), "correlation": _empty_correlation()})


def waypoint_get_invoice(invoice_id: str) -> str:
    """Read one invoice detail record from GET /api/invoices/{id}.

    Args:
        invoice_id: Waypoint invoice id.
    """
    clean_invoice_id = invoice_id.strip()
    return _json(
        {
            "invoice": _client().get_invoice(clean_invoice_id),
            "correlation": {**_empty_correlation(), "waypoint_invoice_id": clean_invoice_id},
        }
    )


def waypoint_get_invoice_context(invoice_id: str) -> str:
    """Read Waypoint's agent-friendly invoice context bundle.

    Args:
        invoice_id: Waypoint invoice id for GET /api/invoices/{id}/context.
    """
    clean_invoice_id = invoice_id.strip()
    return _json(
        {
            "context": _client().get_invoice_context(clean_invoice_id),
            "correlation": {**_empty_correlation(), "waypoint_invoice_id": clean_invoice_id},
        }
    )


def waypoint_get_invoice_decisions() -> str:
    """Read invoice decision summaries from GET /api/invoice-decisions."""
    return _json(
        {
            "invoice_decisions": _client().get_invoice_decisions(),
            "correlation": _empty_correlation(),
        }
    )


def waypoint_get_findings(invoice_id: str | None = None) -> str:
    """Read reconciliation findings.

    Args:
        invoice_id: Optional invoice id filter for GET /api/findings.
    """
    clean_invoice_id = invoice_id.strip() if invoice_id else None
    return _json(
        {
            "findings": _client().get_findings(clean_invoice_id),
            "correlation": {
                **_empty_correlation(),
                "waypoint_invoice_id": clean_invoice_id,
            },
        }
    )


def waypoint_get_evidence(
    invoice_id: str | None = None,
    finding_id: str | None = None,
) -> str:
    """Read evidence references.

    Args:
        invoice_id: Optional invoice id filter for GET /api/evidence.
        finding_id: Optional finding id filter for GET /api/evidence.
    """
    clean_invoice_id = invoice_id.strip() if invoice_id else None
    clean_finding_id = finding_id.strip() if finding_id else None
    return _json(
        {
            "evidence": _client().get_evidence(clean_invoice_id, clean_finding_id),
            "correlation": {
                **_empty_correlation(),
                "waypoint_invoice_id": clean_invoice_id,
                "waypoint_finding_id": clean_finding_id,
            },
        }
    )


def waypoint_get_contract_document(document_id: str) -> str:
    """Read a contract document from GET /api/contract-documents/{id}.

    Args:
        document_id: Waypoint contract document id.
    """
    clean_document_id = document_id.strip()
    return _json(
        {
            "contract_document": _client().get_contract_document(clean_document_id),
            "correlation": {
                **_empty_correlation(),
                "waypoint_contract_document_id": clean_document_id,
            },
        }
    )


def waypoint_get_policy(policy_id: str) -> str:
    """Read a policy from GET /api/policies/{id}.

    Args:
        policy_id: Waypoint policy id.
    """
    clean_policy_id = policy_id.strip()
    return _json(
        {
            "policy": _client().get_policy(clean_policy_id),
            "correlation": {**_empty_correlation(), "waypoint_policy_id": clean_policy_id},
        }
    )


def waypoint_invoice_assurance_scout(invoice_id: str | None = None, limit: int = 5) -> str:
    """Run a read-only invoice assurance scout pass.

    The scout discovers work, reads invoice domain truth, summarizes evidence
    and action options, and prepares run metadata locally. It never creates
    cases, stages approvals, authorizes actions, or POSTs run metadata.

    Args:
        invoice_id: Optional invoice id to focus the scout on.
        limit: Maximum number of work items to inspect when invoice_id is not
            supplied. Values outside 1-25 are clamped.
    """
    client = _client()
    work = _as_list(client.get_work())
    action_types = _as_list(client.get_action_types())
    runs = _as_list(client.get_runs())
    limit = max(1, min(int(limit), 25))

    selected_work = _select_work(work, invoice_id.strip() if invoice_id else None, limit)
    invoice_ids = _unique(
        item.get("invoice_id")
        for item in selected_work
        if isinstance(item, dict) and item.get("invoice_id")
    )
    if invoice_id and invoice_id.strip() not in invoice_ids:
        invoice_ids.append(invoice_id.strip())

    invoice_summaries = []
    waypoint_case_ids: list[str] = []
    for selected_invoice_id in invoice_ids:
        context = client.get_invoice_context(selected_invoice_id)
        if isinstance(context, dict):
            invoice_summaries.append(_summarize_invoice_context(context))
            waypoint_case_ids.extend(
                case["id"]
                for case in _as_list(context.get("cases"))
                if isinstance(case, dict) and case.get("id")
            )

    case_ids = _unique(waypoint_case_ids)
    return _json(
        {
            "mode": "read_only_invoice_assurance_scout",
            "read_only": True,
            "side_effects_performed": False,
            "prohibited_operations": [
                "POST /api/cases",
                "POST /api/cases/{id}/recommendations",
                "POST /api/cases/{id}/drafts",
                "POST /api/cases/{id}/actions",
                "POST /api/cases/{id}/approvals",
                "POST /api/actions/{id}/authorize",
                "POST /api/runs",
            ],
            "work_queue": {
                "total_items_seen": len(work),
                "selected_items": [_summarize_work_item(item) for item in selected_work],
            },
            "invoice_truth": invoice_summaries,
            "action_options": [_summarize_action_type(action) for action in action_types],
            "existing_runs": [_summarize_run(run) for run in runs[:10]],
            "prepared_run_metadata": {
                "name": "assurance-orchestrator-read-only-invoice-assurance-scout",
                "foundry_agent_name": "assurance-orchestrator",
                "case_ids": case_ids,
                "metadata": {
                    "integration_phase": "delegated-user-testing",
                    "read_only": True,
                    "source": "forge.assurance_orchestrator.waypoint_invoice_assurance_scout",
                },
            },
            "correlation": {
                "waypoint_case_id": case_ids[0] if case_ids else None,
                "waypoint_run_id": None,
                "waypoint_action_id": None,
            },
            "next_safe_step": (
                "Review the summary with a human. Do not stage approvals or "
                "authorize actions from this scout pass."
            ),
        }
    )


async def assurance_orchestrator_run_invoice_assurance_workflow(
    request_json: str | None = None,
    invoice_id: str | None = None,
    pdf_uri: str | None = None,
    pdf_base64: str | None = None,
    limit: int = 1,
) -> str:
    """Run the read-only AssuranceOrchestrator invoice assurance workflow.

    This is AssuranceOrchestrator's explicit "Run mode" entrypoint for Responses and Activity
    Protocol invocations. It accepts either a structured request JSON payload or
    a single invoice id. A missing invoice id causes the workflow to select up to
    ``limit`` items from Waypoint's work queue. The workflow performs no writes
    and returns a Waypoint write-plan preview only.

    Args:
        request_json: Optional JSON object containing ``assurance_orchestrator_request`` or a
            direct run manifest with ``mode``, ``request_type``, ``items``, and
            ``constraints`` fields.
        invoice_id: Optional single invoice id to run.
        pdf_uri: Optional public or service-accessible PDF URL to analyze with
            Content Understanding.
        pdf_base64: Optional raw base64 PDF bytes to analyze with Content
            Understanding. Data URL prefixes are accepted and stripped.
        limit: Maximum number of Waypoint work queue items to run when no
            invoice id is supplied. Values outside 1-25 are clamped.
    """
    if (pdf_uri or pdf_base64) and not request_json:
        request_json = _json(
            {
                "invoice_id": invoice_id.strip() if invoice_id else None,
                "pdf_uri": pdf_uri.strip() if pdf_uri else None,
                "pdf_base64": pdf_base64.strip() if pdf_base64 else None,
            }
        )
    result = await run_assurance_orchestrator_invoice_assurance_async(
        request_json,
        invoice_id=invoice_id.strip() if invoice_id else None,
        limit=max(1, min(int(limit), 25)),
    )
    return _json(result)


async def run_invoice_assurance(
    request_json: str | None = None,
    invoice_id: str | None = None,
    pdf_uri: str | None = None,
    pdf_base64: str | None = None,
    limit: int = 1,
) -> str:
    """Run invoice assurance end-to-end for an invoice and finalize it on Waypoint.

    This is Assurance Orchestrator's ONE run-mode entrypoint. It deterministically drives the
    whole lifecycle in code — do NOT try to orchestrate experts or the waypoint-recorder
    yourself; a single call to this tool does all of it:

    1. Opens the invoice's assurance run as `running`.
    2. Fans out to the domain experts IN PARALLEL and reconciles their evidence (bounded by a
       30-minute runtime budget).
    3. ALWAYS finalizes: on success it hands the fused evidence to the waypoint-recorder, which
       applies policy and performs the governed Waypoint write (the run flips to `completed`);
       on timeout or error it marks the run `failed` so it never orphans at `running`.

    Degraded expert lanes are tolerated — the run still finalizes (the recorder decides
    review/escalate). The waypoint-recorder remains the only agent that writes to Waypoint.

    Args:
        request_json: Optional JSON run manifest (``assurance_orchestrator_request`` or a direct
            manifest with ``mode``/``items``/``constraints``).
        invoice_id: Optional single invoice id to run.
        pdf_uri: Optional PDF URL to analyze with Content Understanding.
        pdf_base64: Optional raw base64 PDF bytes (data URL prefixes are stripped).
        limit: Max Waypoint work-queue items to run when no invoice id is supplied (clamped 1-25).
    """
    result = await run_invoice_assurance_and_finalize(
        request_json=request_json,
        invoice_id=invoice_id.strip() if invoice_id else None,
        pdf_uri=pdf_uri,
        pdf_base64=pdf_base64,
        limit=limit,
    )
    return _json(result)


def build_waypoint_tools() -> list[FunctionTool]:
    if not is_waypoint_configured():
        return []
    return [
        FunctionTool(func=waypoint_get_work, name="waypoint_get_work"),
        FunctionTool(func=waypoint_get_action_types, name="waypoint_get_action_types"),
        FunctionTool(func=waypoint_get_runs, name="waypoint_get_runs"),
        FunctionTool(func=waypoint_get_cases, name="waypoint_get_cases"),
        FunctionTool(func=waypoint_get_invoice, name="waypoint_get_invoice"),
        FunctionTool(func=waypoint_get_invoice_context, name="waypoint_get_invoice_context"),
        FunctionTool(func=waypoint_get_invoice_decisions, name="waypoint_get_invoice_decisions"),
        FunctionTool(func=waypoint_get_findings, name="waypoint_get_findings"),
        FunctionTool(func=waypoint_get_evidence, name="waypoint_get_evidence"),
        FunctionTool(func=waypoint_get_contract_document, name="waypoint_get_contract_document"),
        FunctionTool(func=waypoint_get_policy, name="waypoint_get_policy"),
        FunctionTool(
            func=waypoint_invoice_assurance_scout,
            name="waypoint_invoice_assurance_scout",
        ),
        FunctionTool(
            func=assurance_orchestrator_run_invoice_assurance_workflow,
            name="assurance_orchestrator_run_invoice_assurance_workflow",
        ),
        FunctionTool(
            func=run_invoice_assurance,
            name="run_invoice_assurance",
        ),
    ]


def _client() -> WaypointReadOnlyClient:
    return WaypointReadOnlyClient()


def _run_tool(name: str, get_value) -> str:
    logger.warning("%s started", name)
    try:
        result = _json(get_value())
    except Exception:
        logger.exception("%s failed", name)
        raise
    logger.warning("%s succeeded with %s characters", name, len(result))
    return result


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _select_work(
    work: list[Any],
    invoice_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    items = [item for item in work if isinstance(item, dict)]
    if invoice_id:
        return [item for item in items if item.get("invoice_id") == invoice_id]
    return items[:limit]


def _summarize_work_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": item.get("invoice_id"),
        "invoice_number": item.get("invoice_number"),
        "supplier_name": item.get("supplier_name"),
        "finding_id": item.get("finding_id"),
        "waypoint_case_id": item.get("case_id"),
        "severity": item.get("severity"),
        "status": item.get("status"),
        "category": item.get("category"),
        "money_at_risk": item.get("money_at_risk"),
        "classification": item.get("classification"),
        "summary": item.get("summary"),
    }


def _summarize_invoice_context(context: dict[str, Any]) -> dict[str, Any]:
    invoice = context.get("invoice") if isinstance(context.get("invoice"), dict) else {}
    findings = _as_list(invoice.get("findings"))
    evidence = _as_list(invoice.get("evidence"))
    return {
        "invoice": {
            "id": invoice.get("id"),
            "invoice_number": invoice.get("invoice_number"),
            "supplier_id": invoice.get("supplier_id"),
            "supplier_name": _nested(invoice, "supplier", "name"),
            "status": invoice.get("status"),
            "currency": invoice.get("currency"),
            "total_amount": invoice.get("total_amount"),
            "line_count": len(_as_list(invoice.get("lines"))),
        },
        "findings": [_summarize_finding(finding) for finding in findings if isinstance(finding, dict)],
        "evidence": [_summarize_evidence(item) for item in evidence if isinstance(item, dict)],
        "contract_documents": [
            _summarize_contract(document)
            for document in _as_list(context.get("contract_documents"))
            if isinstance(document, dict)
        ],
        "policies": [
            _summarize_policy(policy)
            for policy in _as_list(context.get("policies"))
            if isinstance(policy, dict)
        ],
        "cases": [
            _summarize_case(case)
            for case in _as_list(context.get("cases"))
            if isinstance(case, dict)
        ],
        "allowed_action_ids": [
            action.get("id")
            for action in _as_list(context.get("allowed_actions"))
            if isinstance(action, dict)
        ],
        "redactions": context.get("redactions", []),
        "metadata": context.get("metadata", {}),
    }


def _summarize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": finding.get("id"),
        "severity": finding.get("severity"),
        "status": finding.get("status"),
        "category": finding.get("category"),
        "summary": finding.get("summary"),
        "overpayment_amount": finding.get("overpayment_amount"),
        "basis_summary": finding.get("basis_summary"),
        "evidence_ids": finding.get("evidence_ids", []),
        "contract_document_ids": finding.get("contract_document_ids", []),
        "policy_ids": finding.get("policy_ids", []),
    }


def _summarize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": evidence.get("id"),
        "title": evidence.get("title"),
        "evidence_type": evidence.get("evidence_type"),
        "invoice_id": evidence.get("invoice_id"),
        "finding_id": evidence.get("finding_id"),
        "excerpt": evidence.get("excerpt"),
        "classification": _nested(evidence, "metadata", "classification"),
    }


def _summarize_contract(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "supplier_id": document.get("supplier_id"),
        "title": document.get("title"),
        "document_type": document.get("document_type"),
        "effective_date": document.get("effective_date"),
    }


def _summarize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": policy.get("id"),
        "name": policy.get("name"),
        "description": policy.get("description"),
        "severity": policy.get("severity"),
    }


def _summarize_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "waypoint_case_id": case.get("id"),
        "invoice_id": case.get("invoice_id"),
        "finding_id": case.get("finding_id"),
        "title": case.get("title"),
        "status": case.get("status"),
        "classification": case.get("classification"),
    }


def _summarize_action_type(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {}
    return {
        "id": action.get("id"),
        "name": action.get("name"),
        "description": action.get("description"),
        "required_role": action.get("required_role"),
        "approval_gate": action.get("approval_gate"),
        "external_side_effect": action.get("external_side_effect"),
    }


def _summarize_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    return {
        "waypoint_run_id": run.get("id"),
        "waypoint_case_id": run.get("case_id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "foundry_agent_name": run.get("foundry_agent_name"),
        "foundry_conversation_id": run.get("foundry_conversation_id"),
        "app_insights_operation_id": run.get("app_insights_operation_id"),
        "summary": run.get("summary"),
    }


def _empty_correlation() -> dict[str, None]:
    return {
        "waypoint_case_id": None,
        "waypoint_run_id": None,
        "waypoint_action_id": None,
    }


def _unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
