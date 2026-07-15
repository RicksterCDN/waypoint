"""FoundryIQ grounding tool.

Grounds the FoundryIQ expert against the seeded Waypoint corpus: the governing
knowledge for an invoice — the contract documents and policies referenced by its
reconciliation findings. Returns the shared evidence contract for the waypoint_recorder.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from agent_framework import FunctionTool, tool
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from telemetry import add_span_event, set_span_attribute, trace_span

logger = logging.getLogger("foundryiq.evidence_tools")

PLANE = "foundryiq"
AGENT = "contract-policy-expert"
AGENT_ROOT = os.path.dirname(__file__)
DATA_SOURCE = "waypoint"
EVIDENCE_SCHEMA = "invoice_evidence_contract.v1"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"


def gather_contract_policy_evidence(invoice_id: str = "") -> str:
    """Retrieve grounded contract/policy knowledge for an invoice from the Foundry corpus.

    Args:
        invoice_id: The invoice id or invoice number under assurance review.
    """
    return _gather_contract_policy_evidence(invoice_id)


def gather_foundry_evidence(invoice_id: str = "") -> str:
    """Compatibility alias for older callers. The model-facing tool uses the business name."""
    return _gather_contract_policy_evidence(invoice_id)


def _gather_contract_policy_evidence(invoice_id: str = "") -> str:
    requested_invoice = invoice_id.strip()
    with trace_span(
        "execute_tool gather_contract_policy_evidence",
        {
            "gen_ai.agent.name": AGENT,
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "gather_contract_policy_evidence",
            "gen_ai.tool.type": "function",
            TOOL_CALL_ARGUMENTS: {"invoice_id": requested_invoice},
            **_business_step("contract_policy_evidence_gathering"),
            "invoice.id": requested_invoice or None,
            "evidence.source": "contract-policy-corpus",
        },
    ) as span:
        client = _CorpusClient.try_from_env()
        if client is None:
            set_span_attribute(span, "evidence.status", "not_configured")
            set_span_attribute(span, TOOL_CALL_RESULT, _gather_result_summary("not_configured", invoice_id, 0, 0, 0))
            return _contract(invoice_id, [], "FoundryIQ corpus is not configured (WAYPOINT_API_BASE_URL unset).")

        with trace_span(
            "execute_tool resolve_invoice_record",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "resolve_invoice_record",
                "gen_ai.tool.type": "datastore",
                TOOL_CALL_ARGUMENTS: {"invoice_id": requested_invoice},
                **_business_step("invoice_resolution"),
                "gen_ai.data_source.id": DATA_SOURCE,
                "invoice.id": requested_invoice or None,
                "lookup.target": "invoice",
            },
        ) as resolve_span:
            detail = client.resolve_invoice(invoice_id)
            lookup_status = _lookup_status(client, detail)
            set_span_attribute(resolve_span, "lookup.status", lookup_status)
            set_span_attribute(resolve_span, TOOL_CALL_RESULT, _invoice_result_summary(detail, lookup_status))

        if not detail:
            evidence_status = "unauthorized" if lookup_status == "unauthorized" else "not_found"
            set_span_attribute(span, "evidence.status", evidence_status)
            set_span_attribute(span, TOOL_CALL_RESULT, _gather_result_summary(evidence_status, invoice_id, 0, 0, 0))
            return _contract(invoice_id, [], f"No invoice matched '{invoice_id}' in the Foundry knowledge corpus.")

        canonical = str(detail.get("id") or invoice_id)
        set_span_attribute(span, "invoice.id", canonical)

        with trace_span(
            "execute_tool load_reconciliation_findings",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "load_reconciliation_findings",
                "gen_ai.tool.type": "datastore",
                TOOL_CALL_ARGUMENTS: {"invoice_id": canonical},
                **_business_step("reconciliation_review"),
                "gen_ai.data_source.id": DATA_SOURCE,
                "invoice.id": canonical,
                "lookup.target": "reconciliation_findings",
            },
        ) as findings_span:
            findings = client.list_findings(canonical) or _as_list(detail.get("findings"))
            set_span_attribute(findings_span, "findings.count", len(findings))
            set_span_attribute(findings_span, TOOL_CALL_RESULT, _findings_result_summary(findings))

        with trace_span(
            "execute_tool interpret_recovery_need",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.agent.name": AGENT,
                "gen_ai.tool.name": "interpret_recovery_need",
                "gen_ai.tool.type": "function",
                TOOL_CALL_ARGUMENTS: _interpret_arguments_summary(findings),
                **_business_step("recovery_interpretation"),
                "invoice.id": canonical,
            },
        ) as decision_span:
            recovery_count = sum(1 for finding in findings if _float(finding.get("overpayment_amount")) > 0)
            review_count = len(findings) - recovery_count
            set_span_attribute(decision_span, "findings.overpayment.count", recovery_count)
            set_span_attribute(decision_span, "findings.review.count", review_count)
            set_span_attribute(decision_span, "decision.recovery_needed", recovery_count > 0)
            set_span_attribute(
                decision_span,
                TOOL_CALL_RESULT,
                {
                    "recovery_needed": recovery_count > 0,
                    "findings_count": len(findings),
                    "recoverable_findings_count": recovery_count,
                    "review_findings_count": review_count,
                    "categories": _finding_categories(findings),
                },
            )

        evidence: list[dict[str, Any]] = []
        evidence_specs: list[dict[str, str]] = []
        seen_docs: set[str] = set()
        seen_policies: set[str] = set()
        for finding in findings:
            supports = "recover" if _float(finding.get("overpayment_amount")) > 0 else "review"
            for doc_id in _str_list(finding.get("contract_document_ids")):
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                evidence_specs.append({"kind": "contract_document", "source_ref": doc_id, "supports": supports})
            for policy_id in _str_list(finding.get("policy_ids")):
                if policy_id in seen_policies:
                    continue
                seen_policies.add(policy_id)
                evidence_specs.append({"kind": "policy", "source_ref": policy_id, "supports": supports})

        contract_documents: dict[str, dict[str, Any] | None] = {}
        with trace_span(
            "execute_tool select_contract_evidence",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "select_contract_evidence",
                "gen_ai.tool.type": "datastore",
                TOOL_CALL_ARGUMENTS: {"source_refs": _limited(sorted(seen_docs)), "requested_count": len(seen_docs)},
                **_business_step("contract_evidence_selection"),
                "gen_ai.data_source.id": DATA_SOURCE,
                "invoice.id": canonical,
                "lookup.target": "contract_documents",
                "contract_documents.requested.count": len(seen_docs),
            },
        ) as docs_span:
            for doc_id in seen_docs:
                doc = client.get_contract_document(doc_id)
                contract_documents[doc_id] = doc
                add_span_event(
                    docs_span,
                    "source_selected",
                    {
                        "source.type": "contract_document",
                        "source.ref": doc_id,
                        "lookup.status": "found" if doc else "missing",
                    },
                )
            set_span_attribute(docs_span, "contract_documents.count", sum(1 for doc in contract_documents.values() if doc))
            set_span_attribute(docs_span, "source_refs.count", len(seen_docs))
            set_span_attribute(docs_span, TOOL_CALL_RESULT, _contract_documents_result_summary(contract_documents))

        policies: dict[str, dict[str, Any] | None] = {}
        with trace_span(
            "execute_tool select_policy_evidence",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "select_policy_evidence",
                "gen_ai.tool.type": "datastore",
                TOOL_CALL_ARGUMENTS: {"source_refs": _limited(sorted(seen_policies)), "requested_count": len(seen_policies)},
                **_business_step("policy_evidence_selection"),
                "gen_ai.data_source.id": DATA_SOURCE,
                "invoice.id": canonical,
                "lookup.target": "policies",
                "policies.requested.count": len(seen_policies),
            },
        ) as policies_span:
            for policy_id in seen_policies:
                policy = client.get_policy(policy_id)
                policies[policy_id] = policy
                add_span_event(
                    policies_span,
                    "source_selected",
                    {
                        "source.type": "policy",
                        "source.ref": policy_id,
                        "lookup.status": "found" if policy else "missing",
                    },
                )
            set_span_attribute(policies_span, "policies.count", sum(1 for policy in policies.values() if policy))
            set_span_attribute(policies_span, "source_refs.count", len(seen_policies))
            set_span_attribute(policies_span, TOOL_CALL_RESULT, _policies_result_summary(policies))

        with trace_span(
            "execute_tool assemble_recovery_evidence",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.agent.name": AGENT,
                "gen_ai.tool.name": "assemble_recovery_evidence",
                "gen_ai.tool.type": "function",
                TOOL_CALL_ARGUMENTS: {
                    "evidence_specs_count": len(evidence_specs),
                    "contract_document_refs": _limited(sorted(seen_docs)),
                    "policy_refs": _limited(sorted(seen_policies)),
                },
                **_business_step("recovery_evidence_assembly"),
                "invoice.id": canonical,
            },
        ) as assembly_span:
            for spec in evidence_specs:
                source_ref = spec["source_ref"]
                supports = spec["supports"]
                if spec["kind"] == "contract_document":
                    doc = contract_documents.get(source_ref)
                    title = str(doc.get("title")) if doc else source_ref
                    doc_type = str(doc.get("document_type")) if doc else "contract"
                    evidence.append(
                        {
                            "claim": f"Governing {doc_type} '{title}' applies to this charge.",
                            "supports": supports,
                            "source_ref": source_ref,
                            "classification": "confidential",
                            "confidence": 0.85,
                        }
                    )
                    continue
                policy = policies.get(source_ref)
                name = str(policy.get("name")) if policy else source_ref
                desc = str(policy.get("description")) if policy else ""
                evidence.append(
                    {
                        "claim": f"Policy '{name}' governs billability: {desc}".strip(),
                        "supports": supports,
                        "source_ref": source_ref,
                        "classification": "standard",
                        "confidence": 0.85,
                    }
                )
            set_span_attribute(assembly_span, "evidence.claims.count", len(evidence))
            set_span_attribute(assembly_span, "evidence.claims.support_recover", sum(1 for item in evidence if item.get("supports") == "recover"))
            set_span_attribute(assembly_span, "evidence.claims.support_review", sum(1 for item in evidence if item.get("supports") == "review"))
            set_span_attribute(assembly_span, "evidence.sources.unique.count", len(seen_docs) + len(seen_policies))
            set_span_attribute(assembly_span, TOOL_CALL_RESULT, _evidence_claims_result_summary(evidence))

        with trace_span(
            "execute_tool validate_evidence_shape",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.agent.name": AGENT,
                "gen_ai.tool.name": "validate_evidence_shape",
                "gen_ai.tool.type": "function",
                TOOL_CALL_ARGUMENTS: {
                    "schema": EVIDENCE_SCHEMA,
                    "evidence_claims_count": len(evidence),
                },
                **_business_step("evidence_shape_validation"),
                "invoice.id": canonical,
                "evidence.contract.schema": EVIDENCE_SCHEMA,
            },
        ) as validation_span:
            claims_with_source_ref = sum(1 for item in evidence if item.get("source_ref"))
            contract_valid = claims_with_source_ref == len(evidence)
            set_span_attribute(validation_span, "evidence.contract.valid", contract_valid)
            set_span_attribute(validation_span, "evidence.claims.with_source_ref", claims_with_source_ref)
            set_span_attribute(
                validation_span,
                TOOL_CALL_RESULT,
                {
                    "valid": contract_valid,
                    "claims_count": len(evidence),
                    "claims_with_source_ref": claims_with_source_ref,
                    "schema": EVIDENCE_SCHEMA,
                },
            )

        summary = (
            f"{len(seen_docs)} contract document(s) and {len(seen_policies)} policy(ies) govern this invoice's findings."
            if evidence
            else "No governing contract or policy references found for this invoice."
        )
        evidence_status = "completed" if evidence else "evidence_gap"
        set_span_attribute(span, "evidence.status", evidence_status)
        set_span_attribute(span, "evidence.claims.count", len(evidence))
        set_span_attribute(span, "evidence.contract_documents.count", len(seen_docs))
        set_span_attribute(span, "evidence.policies.count", len(seen_policies))
        set_span_attribute(
            span,
            TOOL_CALL_RESULT,
            _gather_result_summary(evidence_status, canonical, len(evidence), len(seen_docs), len(seen_policies)),
        )
        return _contract(canonical, evidence, summary)


def build_evidence_tools() -> list[FunctionTool]:
    """Return the grounding tool even when the corpus endpoint is not configured.

    The tool itself returns an explicit not-configured evidence contract. Keeping
    it registered prevents the model from implying retrieval ran when no corpus
    endpoint is available.
    """
    if not _CorpusClient.try_from_env():
        logger.info("Waypoint corpus not configured; %s grounding tool will report not_configured.", AGENT)
    return [tool(gather_contract_policy_evidence)]


def _business_step(review_stage: str) -> dict[str, Any]:
    return {
        "business_process": "contract_policy_invoice_review",
        "review_stage": review_stage,
        "intelligence_type": "deterministic_business_logic",
        "llm_involved": False,
    }


def _gather_result_summary(status: str, invoice_id: str, evidence_count: int, contract_document_count: int, policy_count: int) -> dict[str, Any]:
    return {
        "status": status,
        "invoice_id": invoice_id,
        "evidence_claims_count": evidence_count,
        "contract_documents_count": contract_document_count,
        "policies_count": policy_count,
    }


def _invoice_result_summary(detail: dict[str, Any] | None, status: str) -> dict[str, Any]:
    if not detail:
        return {"status": status}
    return {
        "status": status,
        "invoice": _compact_invoice(detail),
        "findings_count": len(_as_list(detail.get("findings"))),
        "line_items_count": len(_as_list(detail.get("line_items"))),
    }


def _compact_invoice(detail: dict[str, Any]) -> dict[str, Any]:
    return _compact_dict(
        detail,
        (
            "id",
            "invoice_number",
            "supplier_id",
            "supplier_name",
            "status",
            "currency",
            "total_amount",
            "invoice_total",
        ),
    )


def _findings_result_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "findings_count": len(findings),
        "categories": _finding_categories(findings),
        "recoverable_findings_count": sum(1 for finding in findings if _float(finding.get("overpayment_amount")) > 0),
        "contract_document_refs": _limited(sorted({ref for finding in findings for ref in _str_list(finding.get("contract_document_ids"))})),
        "policy_refs": _limited(sorted({ref for finding in findings for ref in _str_list(finding.get("policy_ids"))})),
    }


def _interpret_arguments_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "findings_count": len(findings),
        "categories": _finding_categories(findings),
    }


def _contract_documents_result_summary(documents: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    found = {doc_id: doc for doc_id, doc in documents.items() if doc}
    missing = sorted(doc_id for doc_id, doc in documents.items() if not doc)
    return {
        "found_count": len(found),
        "missing_count": len(missing),
        "documents": [
            _compact_dict(doc or {"id": doc_id}, ("id", "document_id", "slug", "title", "document_type"))
            for doc_id, doc in sorted(found.items())
        ][:20],
        "missing_source_refs": _limited(missing),
    }


def _policies_result_summary(policies: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    found = {policy_id: policy for policy_id, policy in policies.items() if policy}
    missing = sorted(policy_id for policy_id, policy in policies.items() if not policy)
    return {
        "found_count": len(found),
        "missing_count": len(missing),
        "policies": [
            _compact_dict(policy or {"id": policy_id}, ("id", "policy_id", "slug", "name", "policy_type"))
            for policy_id, policy in sorted(found.items())
        ][:20],
        "missing_source_refs": _limited(missing),
    }


def _evidence_claims_result_summary(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "claims_count": len(evidence),
        "supports": _counts(item.get("supports") for item in evidence),
        "classifications": _counts(item.get("classification") for item in evidence),
        "source_refs": _limited(sorted(str(item.get("source_ref")) for item in evidence if item.get("source_ref"))),
    }


# ── corpus client (shared shape across experts) ───────────────────────────────


class _CorpusClient:
    def __init__(self, base_url: str, scope: str | None, verify: bool, api_key: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._scope = scope
        self._verify = verify
        self._api_key = api_key
        self._credential: DefaultAzureCredential | None = None
        self.last_error_kind: str | None = None

    @classmethod
    def try_from_env(cls) -> "_CorpusClient | None":
        load_dotenv(os.path.join(AGENT_ROOT, ".env"), override=False)
        base = _usable_env("WAYPOINT_API_BASE_URL")
        if not base:
            return None
        return cls(base, _usable_env("WAYPOINT_API_SCOPE"), _env_bool("WAYPOINT_API_VERIFY_SSL", True), _usable_env("WAYPOINT_API_KEY"))

    def resolve_invoice(self, ref: str) -> dict[str, Any] | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        detail = self._get(f"/api/invoices/{ref}")
        if isinstance(detail, dict):
            return detail
        target = ref.lower()
        for inv in _as_list(self._get("/api/invoices")):
            if str(inv.get("id", "")).lower() == target or str(inv.get("invoice_number", "")).lower() == target:
                resolved = self._get(f"/api/invoices/{inv.get('id')}")
                return resolved if isinstance(resolved, dict) else None
        return None

    def list_findings(self, invoice_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get("/api/findings", {"invoice_id": invoice_id}))

    def list_evidence(self, invoice_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get("/api/evidence", {"invoice_id": invoice_id}))

    def get_contract_document(self, document_id: str) -> dict[str, Any] | None:
        value = self._get(f"/api/contract-documents/{document_id}")
        return value if isinstance(value, dict) else None

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        value = self._get(f"/api/policies/{policy_id}")
        return value if isinstance(value, dict) else None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.last_error_kind = None
        headers = {"Accept": "application/json"}
        if self._scope:
            if self._credential is None:
                self._credential = DefaultAzureCredential()
            headers["Authorization"] = f"Bearer {self._credential.get_token(self._scope).token}"
        elif self._api_key:
            headers["x-api-key"] = self._api_key
        url = f"{self._base}{path[4:]}" if self._base.endswith("/api") and path.startswith("/api/") else f"{self._base}{path}"
        try:
            with httpx.Client(timeout=30.0, verify=self._verify) as client:
                response = client.get(url, headers=headers, params=params)
        except Exception:
            self.last_error_kind = "transport_error"
            logger.warning("corpus GET %s failed", path, exc_info=True)
            return None
        if response.status_code in {401, 403}:
            self.last_error_kind = "unauthorized"
            return None
        if response.status_code == 404:
            self.last_error_kind = "not_found"
            return None
        if response.is_error:
            self.last_error_kind = "http_error"
            return None
        if not response.content:
            return None
        return response.json()


# ── helpers ──────────────────────────────────────────────────────────────────


def _contract(invoice_id: str, evidence: list[dict[str, Any]], summary: str) -> str:
    return json.dumps(
        {
            "agent": AGENT,
            "plane": PLANE,
            "invoice_id": invoice_id,
            "evidence": evidence,
            "summary": summary,
            "correlation": {"waypoint_run_id": None, "waypoint_invoice_id": invoice_id},
        },
        ensure_ascii=False,
    )


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _finding_categories(findings: list[dict[str, Any]]) -> dict[str, int]:
    categories = _counts(
        finding.get("category")
        or finding.get("finding_type")
        or finding.get("type")
        or finding.get("reason_code")
        for finding in findings
    )
    return categories or {"uncategorized": len(findings)}


def _lookup_status(client: "_CorpusClient", detail: dict[str, Any] | None) -> str:
    if detail:
        return "found"
    if client.last_error_kind == "unauthorized":
        return "unauthorized"
    return "not_found"


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unspecified").strip() or "unspecified"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _compact_dict(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item is None or isinstance(item, (dict, list)):
            continue
        text = str(item)
        summary[key] = text[:120] if len(text) > 120 else item
    return summary


def _limited(values: list[str], limit: int = 20) -> list[str]:
    return values[:limit]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _usable_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value or value.startswith("{{") or value.startswith("${"):
        return None
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = _usable_env(name)
    return value.strip().lower() in {"1", "true", "yes", "on"} if value is not None else default
