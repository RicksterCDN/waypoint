"""Targeted telemetry checks for contract-policy-expert evidence gathering.

Run from the repo root:

    python scripts/test_foundryiq_telemetry.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    _install_import_stubs()
    foundry = _load_module(
        "foundryiq_evidence_tools_telemetry",
        REPO_ROOT / "agents" / "contract-policy-expert" / "evidence_tools.py",
    )
    foundry._CorpusClient.try_from_env = classmethod(lambda cls: _FakeFoundryCorpusClient())
    registered_tools = foundry.build_evidence_tools()
    assert len(registered_tools) == 1
    assert registered_tools[0].__name__ == "gather_contract_policy_evidence"

    payload = json.loads(foundry.gather_foundry_evidence("INV-2026-08338"))
    assert payload["agent"] == "contract-policy-expert"
    assert payload["plane"] == "foundryiq"
    assert payload["invoice_id"] == "inv-2026-08338"
    assert payload["evidence"]

    telemetry = sys.modules["telemetry"]
    spans = {span.name: span.attributes for span in telemetry.CAPTURED_SPANS}
    expected_tools = {
        "execute_tool gather_contract_policy_evidence": "gather_contract_policy_evidence",
        "execute_tool resolve_invoice_record": "resolve_invoice_record",
        "execute_tool load_reconciliation_findings": "load_reconciliation_findings",
        "execute_tool interpret_recovery_need": "interpret_recovery_need",
        "execute_tool select_contract_evidence": "select_contract_evidence",
        "execute_tool select_policy_evidence": "select_policy_evidence",
        "execute_tool assemble_recovery_evidence": "assemble_recovery_evidence",
        "execute_tool validate_evidence_shape": "validate_evidence_shape",
    }
    for span_name, tool_name in expected_tools.items():
        attrs = spans[span_name]
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == tool_name
        assert attrs["gen_ai.tool.call.arguments"]
        assert attrs["gen_ai.tool.call.result"]

    assert spans["execute_tool resolve_invoice_record"]["gen_ai.tool.type"] == "datastore"
    assert spans["execute_tool select_contract_evidence"]["gen_ai.tool.type"] == "datastore"
    assert spans["execute_tool select_policy_evidence"]["gen_ai.data_source.id"] == "waypoint"

    telemetry.CAPTURED_SPANS.clear()
    foundry._CorpusClient.try_from_env = classmethod(lambda cls: _UnauthorizedFoundryCorpusClient())
    payload = json.loads(foundry.gather_foundry_evidence("INV-2026-08273"))
    assert payload["evidence"] == []
    unauthorized_spans = {span.name: span.attributes for span in telemetry.CAPTURED_SPANS}
    assert unauthorized_spans["execute_tool resolve_invoice_record"]["lookup.status"] == "unauthorized"
    assert (
        unauthorized_spans["execute_tool gather_contract_policy_evidence"]["gen_ai.tool.call.result"]["status"]
        == "unauthorized"
    )

    telemetry.CAPTURED_SPANS.clear()
    foundry._CorpusClient.try_from_env = classmethod(lambda cls: _EvidenceGapFoundryCorpusClient())
    payload = json.loads(foundry.gather_foundry_evidence("INV-2026-08339"))
    assert payload["evidence"] == []
    gap_spans = {span.name: span.attributes for span in telemetry.CAPTURED_SPANS}
    assert gap_spans["execute_tool gather_contract_policy_evidence"]["gen_ai.tool.call.result"]["status"] == "evidence_gap"
    print("foundryiq telemetry tests passed")


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _install_import_stubs() -> None:
    agent_framework = types.ModuleType("agent_framework")
    agent_framework.FunctionTool = object
    agent_framework.tool = lambda func: func
    sys.modules["agent_framework"] = agent_framework

    azure = sys.modules.setdefault("azure", types.ModuleType("azure"))
    identity = types.ModuleType("azure.identity")

    class DefaultAzureCredential:
        def get_token(self, scope: str) -> Any:
            return types.SimpleNamespace(token="")

    identity.DefaultAzureCredential = DefaultAzureCredential
    azure.identity = identity
    sys.modules["azure.identity"] = identity

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

    httpx = types.ModuleType("httpx")
    httpx.Client = object
    sys.modules["httpx"] = httpx

    telemetry = types.ModuleType("telemetry")
    telemetry.CAPTURED_SPANS = []

    class _CapturedSpan:
        def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
            self.name = name
            self.attributes = dict(attributes or {})

        def set_attribute(self, name: str, value: Any) -> None:
            self.attributes[name] = value

        def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
            pass

    @contextmanager
    def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Any:
        span = _CapturedSpan(name, attributes)
        telemetry.CAPTURED_SPANS.append(span)
        yield span

    telemetry.trace_span = trace_span
    telemetry.set_span_attribute = lambda span, name, value: span.set_attribute(name, value)
    telemetry.add_span_event = lambda span, name, attributes=None: span.add_event(name, attributes)
    sys.modules["telemetry"] = telemetry


class _FakeFoundryCorpusClient:
    last_error_kind = None

    def resolve_invoice(self, ref: str) -> dict[str, Any]:
        return {
            "id": "inv-2026-08338",
            "invoice_number": "INV-2026-08338",
            "supplier_name": "Contoso Services",
            "status": "under_review",
            "currency": "USD",
            "total_amount": 1234.56,
        }

    def list_findings(self, invoice_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "finding-1",
                "category": "rate_overage",
                "overpayment_amount": 200,
                "contract_document_ids": ["contract-ms-2026"],
                "policy_ids": ["policy-billable-expenses"],
            }
        ]

    def get_contract_document(self, document_id: str) -> dict[str, Any] | None:
        return {
            "id": document_id,
            "slug": "master-services-2026",
            "title": "Master Services Agreement",
            "document_type": "msa",
            "body": "not safe for telemetry",
        }

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        return {
            "id": policy_id,
            "slug": "billable-expenses",
            "name": "Billable Expenses",
            "description": "Travel must be preapproved.",
            "text": "not safe for telemetry",
        }


class _UnauthorizedFoundryCorpusClient:
    last_error_kind = "unauthorized"

    def resolve_invoice(self, ref: str) -> None:
        return None


class _EvidenceGapFoundryCorpusClient:
    last_error_kind = None

    def resolve_invoice(self, ref: str) -> dict[str, Any]:
        return {
            "id": "inv-2026-08339",
            "invoice_number": "INV-2026-08339",
            "supplier_name": "Contoso Services",
            "status": "under_review",
        }

    def list_findings(self, invoice_id: str) -> list[dict[str, Any]]:
        return []

    def get_contract_document(self, document_id: str) -> None:
        return None

    def get_policy(self, policy_id: str) -> None:
        return None


if __name__ == "__main__":
    main()
