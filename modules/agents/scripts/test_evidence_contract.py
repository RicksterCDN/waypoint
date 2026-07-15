"""Lightweight checks for the shared IQ evidence contract.

Run from the repo root:

    python scripts/test_evidence_contract.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from run_local_prompt_agents import AGENTS, _response_contract


REPO_ROOT = Path(__file__).resolve().parent.parent
PLANES = {
    "workiq": "collaboration-evidence-expert",
    "webiq": "market-evidence-expert",
    "foundryiq": "contract-policy-expert",
    "fabriciq": "operations-data-expert",
}
SUPPORTS = {"approve", "recover", "escalate", "review", "unknown"}
CLASSIFICATIONS = {"standard", "confidential", "ip_sensitive", "restricted"}


def main() -> None:
    for plane, agent in PLANES.items():
        payload = _response_contract(plane, "Invoice id: INV-2026-08034.")
        validate_contract(payload, expected_agent=agent, expected_plane=plane)

    with _without_waypoint_env():
        fabric = _load_module(
            "fabriciq_evidence_tools",
            REPO_ROOT / "agents" / "operations-data-expert" / "evidence_tools.py",
        )
        foundry = _load_module(
            "foundryiq_evidence_tools",
            REPO_ROOT / "agents" / "contract-policy-expert" / "evidence_tools.py",
        )
        fabric_stub = json.loads(fabric.gather_fabric_evidence("INV-2026-08034"))
        validate_contract(
            fabric_stub,
            expected_agent="operations-data-expert",
            expected_plane="fabriciq",
        )
        assert fabric_stub["evidence"] == []
        assert "temporarily stubbed" in fabric_stub["summary"]
        assert "was queried" in fabric_stub["summary"]
        validate_contract(
            json.loads(foundry.gather_foundry_evidence("INV-2026-08034")),
            expected_agent="contract-policy-expert",
            expected_plane="foundryiq",
        )
        validate_foundryiq_toolbox()

    for plane, agent in PLANES.items():
        prompt = REPO_ROOT / "agents" / agent / "prompt.md"
        validate_prompt_contract(prompt, expected_agent=agent, expected_plane=plane)

    print("all evidence contract tests passed")


def validate_contract(payload: dict[str, Any], *, expected_agent: str, expected_plane: str) -> None:
    assert payload["agent"] == expected_agent
    assert payload["plane"] == expected_plane
    assert isinstance(payload["invoice_id"], str)
    assert isinstance(payload["summary"], str) and payload["summary"].strip()
    assert isinstance(payload["evidence"], list)

    correlation = payload.get("correlation")
    assert isinstance(correlation, dict)
    assert "waypoint_run_id" in correlation
    assert correlation.get("waypoint_invoice_id") == payload["invoice_id"]

    for item in payload["evidence"]:
        assert isinstance(item, dict)
        assert isinstance(item.get("claim"), str) and item["claim"].strip()
        assert item.get("supports") in SUPPORTS
        assert isinstance(item.get("source_ref"), str) and item["source_ref"].strip()
        assert item.get("classification") in CLASSIFICATIONS
        assert isinstance(item.get("confidence"), int | float)
        assert 0.0 <= float(item["confidence"]) <= 1.0


def validate_prompt_contract(path: Path, *, expected_agent: str, expected_plane: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert f'"agent": "{expected_agent}"' in text
    assert f'"plane": "{expected_plane}"' in text
    for field in (
        '"invoice_id"',
        '"evidence"',
        '"claim"',
        '"supports"',
        '"source_ref"',
        '"classification"',
        '"confidence"',
        '"summary"',
        '"correlation"',
    ):
        assert field in text, f"{path} is missing {field}"


def validate_foundryiq_toolbox() -> None:
    old = os.environ.get("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME")
    os.environ["AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME"] = "kb-mcp-connection"
    try:
        toolbox = _load_module(
            "foundryiq_toolbox",
            REPO_ROOT / "agents" / "contract-policy-expert" / "toolbox.py",
        )
    finally:
        if old is None:
            os.environ.pop("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME", None)
        else:
            os.environ["AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME"] = old

    required_env = getattr(toolbox, "REQUIRED_ENV", {})
    assert required_env.get("knowledge_base") == ("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME",)
    labels = {getattr(tool, "server_label", "") for tool in toolbox.TOOLBOX}
    assert "knowledge_base" in labels


def _load_module(name: str, path: Path) -> Any:
    _install_import_stubs()
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
    if "agent_framework" not in sys.modules:
        agent_framework = types.ModuleType("agent_framework")
        agent_framework.FunctionTool = object
        agent_framework.tool = lambda func: func
        sys.modules["agent_framework"] = agent_framework
    if "azure.identity" not in sys.modules:
        azure = sys.modules.setdefault("azure", types.ModuleType("azure"))
        identity = types.ModuleType("azure.identity")

        class DefaultAzureCredential:
            def get_token(self, scope: str) -> Any:
                return types.SimpleNamespace(token="")

        identity.DefaultAzureCredential = DefaultAzureCredential
        azure.identity = identity
        sys.modules["azure.identity"] = identity
    if "azure.ai.projects.models" not in sys.modules:
        azure = sys.modules.setdefault("azure", types.ModuleType("azure"))
        ai = sys.modules.setdefault("azure.ai", types.ModuleType("azure.ai"))
        projects = sys.modules.setdefault("azure.ai.projects", types.ModuleType("azure.ai.projects"))
        models = types.ModuleType("azure.ai.projects.models")

        class MCPTool:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        models.MCPTool = MCPTool
        projects.models = models
        ai.projects = projects
        azure.ai = ai
        sys.modules["azure.ai.projects.models"] = models
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")
        httpx.Client = object
        sys.modules["httpx"] = httpx
    if "telemetry" not in sys.modules:
        telemetry = types.ModuleType("telemetry")

        @contextmanager
        def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Any:
            yield types.SimpleNamespace(set_attribute=lambda *args, **kwargs: None)

        telemetry.trace_span = trace_span
        telemetry.rft_reference_attributes = lambda **kwargs: {}
        telemetry.set_span_attribute = lambda *args, **kwargs: None
        telemetry.add_span_event = lambda *args, **kwargs: None
        sys.modules["telemetry"] = telemetry


class _without_waypoint_env:
    def __enter__(self) -> None:
        self._old = {
            key: os.environ.get(key)
            for key in ("WAYPOINT_API_BASE_URL", "WAYPOINT_API_SCOPE", "WAYPOINT_API_KEY")
        }
        for key in self._old:
            os.environ.pop(key, None)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
