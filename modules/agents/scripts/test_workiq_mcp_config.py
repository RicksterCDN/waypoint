#!/usr/bin/env python3
"""Validate WorkIQ MCP configuration guards.

The current tenant-supported WorkIQ Email/Teams endpoints are not confirmed yet.
This check prevents the old placeholder SharePoint MCP URL from being treated as
a real tool because it returns HTTP 400 and crashes the hosted agent during MCP
context setup.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_SHAREPOINT_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_SharePointRemoteServer"


def main() -> None:
    module = _load_workiq_main()

    with _patched_env(
        WORKIQ_EMAIL_MCP_SERVER_URL=None,
        WORKIQ_TEAMS_MCP_SERVER_URL=None,
        WORKIQ_SHAREPOINT_MCP_SERVER_URL=None,
        WORKIQ_MCP_SERVER_URL=LEGACY_SHAREPOINT_URL,
    ):
        assert module._resolve_mcp_url(module.MCP_TOOLS[2]) is None

    with _patched_env(WORKIQ_EMAIL_MCP_SERVER_URL="https://example.invalid/email-mcp"):
        assert module._resolve_mcp_url(module.MCP_TOOLS[0]) == "https://example.invalid/email-mcp"

    with _patched_env(
        WORKIQ_SHAREPOINT_MCP_SERVER_URL="https://example.invalid/sharepoint-mcp",
        WORKIQ_MCP_SERVER_URL=LEGACY_SHAREPOINT_URL,
    ):
        assert module._resolve_mcp_url(module.MCP_TOOLS[2]) == "https://example.invalid/sharepoint-mcp"

    deploy_text = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    params_text = (REPO_ROOT / "infra" / "main.parameters.json").read_text(encoding="utf-8")
    assert LEGACY_SHAREPOINT_URL not in deploy_text
    assert LEGACY_SHAREPOINT_URL not in params_text

    print("workiq MCP configuration checks passed")


def _load_workiq_main() -> Any:
    _install_import_stubs()
    path = REPO_ROOT / "agents" / "collaboration-evidence-expert" / "main.py"
    spec = importlib.util.spec_from_file_location("workiq_main", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _install_import_stubs() -> None:
    if "activity_protocol" not in sys.modules:
        activity_protocol = types.ModuleType("activity_protocol")
        activity_protocol.mount_activity_protocol = lambda *args, **kwargs: None
        sys.modules["activity_protocol"] = activity_protocol
    if "agent_framework" not in sys.modules:
        agent_framework = types.ModuleType("agent_framework")

        class Agent:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class MCPStreamableHTTPTool:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        agent_framework.Agent = Agent
        agent_framework.MCPStreamableHTTPTool = MCPStreamableHTTPTool
        sys.modules["agent_framework"] = agent_framework
    if "agent_framework.foundry" not in sys.modules:
        foundry = types.ModuleType("agent_framework.foundry")
        foundry.FoundryChatClient = object
        sys.modules["agent_framework.foundry"] = foundry
    if "agent_framework_foundry_hosting" not in sys.modules:
        hosting = types.ModuleType("agent_framework_foundry_hosting")
        hosting.ResponsesHostServer = object
        sys.modules["agent_framework_foundry_hosting"] = hosting
    if "azure.identity" not in sys.modules:
        azure = sys.modules.setdefault("azure", types.ModuleType("azure"))
        identity = types.ModuleType("azure.identity")
        identity.DefaultAzureCredential = object
        identity.get_bearer_token_provider = lambda *args, **kwargs: lambda: ""
        azure.identity = identity
        sys.modules["azure.identity"] = identity
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")

        class AsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class Auth:
            pass

        httpx.AsyncClient = AsyncClient
        httpx.Auth = Auth
        sys.modules["httpx"] = httpx
    if "telemetry" not in sys.modules:
        telemetry = types.ModuleType("telemetry")
        telemetry.agent_startup_attributes = lambda *args, **kwargs: {}
        telemetry.trace_span = lambda *args, **kwargs: _NullContext()
        sys.modules["telemetry"] = telemetry


class _NullContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _patched_env:
    def __init__(self, **values: str | None) -> None:
        self._values = values
        self._old: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._values.items():
            self._old[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
