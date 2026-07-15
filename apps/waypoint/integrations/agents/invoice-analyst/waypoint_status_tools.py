"""Read-only Waypoint status tools for the invoice-analyst agent.

People can ask "what's the status of our runs?" and the analyst answers from the
governed Waypoint API. It is strictly read-only.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from agent_framework import FunctionTool, tool
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from telemetry import set_span_attribute, trace_span

logger = logging.getLogger("invoice_analyst.waypoint_status_tools")

AGENT_ROOT = os.path.dirname(__file__)
ENV_PATH = os.path.join(AGENT_ROOT, ".env")


@dataclass(frozen=True)
class StatusConfig:
    api_base_url: str
    api_scope: str | None
    api_key: str | None
    verify_ssl: bool

    @classmethod
    def try_from_env(cls) -> "StatusConfig | None":
        load_dotenv(ENV_PATH, override=False)
        base_url = _usable_env("WAYPOINT_API_BASE_URL")
        if not base_url:
            return None
        return cls(
            api_base_url=base_url.rstrip("/"),
            api_scope=_usable_env("WAYPOINT_API_SCOPE"),
            api_key=_usable_env("WAYPOINT_API_KEY"),
            verify_ssl=_env_bool("WAYPOINT_API_VERIFY_SSL", default=True),
        )


class WaypointStatusClient:
    def __init__(self, config: StatusConfig | None = None, timeout: float = 30.0) -> None:
        resolved = config or StatusConfig.try_from_env()
        if resolved is None:
            raise EnvironmentError("WAYPOINT_API_BASE_URL is not set.")
        self._config = resolved
        self._timeout = timeout
        self._credential: DefaultAzureCredential | None = None

    def get_runs(self, case_id: str | None = None) -> Any:
        params = {"case_id": case_id} if case_id else None
        return self._get("/api/runs", params=params)

    def get_cases(self) -> Any:
        return self._get("/api/cases")

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        with trace_span(
            "invoice_analyst.waypoint.read",
            {
                "gen_ai.agent.name": "invoice-analyst",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "waypoint_status_read",
                "http.request.method": "GET",
                "url.path": path,
                "forge.waypoint.params": params,
                "forge.rft.agent": "invoice-analyst",
                "forge.rft.task_family": "invoice_assurance_analysis",
            },
        ) as span:
            headers = {"Accept": "application/json"}
            if self._config.api_key:
                headers["x-api-key"] = self._config.api_key
            else:
                token = self._token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            with httpx.Client(timeout=self._timeout, verify=self._config.verify_ssl) as client:
                response = client.get(self._url(path), headers=headers, params=params)
            set_span_attribute(span, "http.response.status_code", response.status_code)
            if response.is_error:
                raise RuntimeError(
                    f"Waypoint GET {path} failed with HTTP {response.status_code}: {response.text[:300]}"
                )
            return response.json() if response.content else None

    def _token(self) -> str | None:
        if not self._config.api_scope:
            return None
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential.get_token(self._config.api_scope).token

    def _url(self, path: str) -> str:
        base = self._config.api_base_url
        if base.endswith("/api") and path.startswith("/api/"):
            return f"{base}{path[4:]}"
        return f"{base}{path}"


def is_waypoint_configured() -> bool:
    return StatusConfig.try_from_env() is not None


def waypoint_run_status(case_id: str = "") -> str:
    """Report Waypoint agent run status, optionally filtered to one case_id."""
    if not is_waypoint_configured():
        return json.dumps({"ok": False, "error": "Waypoint is not configured."})
    try:
        runs = WaypointStatusClient().get_runs(case_id=case_id.strip() or None)
    except Exception as exc:  # surface a clean message for the chat surface
        logger.exception("waypoint_run_status failed")
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, "runs": runs}, ensure_ascii=False)


def waypoint_case_overview() -> str:
    """Report a high-level overview of Waypoint assurance cases."""
    if not is_waypoint_configured():
        return json.dumps({"ok": False, "error": "Waypoint is not configured."})
    try:
        cases = WaypointStatusClient().get_cases()
    except Exception as exc:
        logger.exception("waypoint_case_overview failed")
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, "cases": cases}, ensure_ascii=False)


def build_waypoint_status_tools() -> list[FunctionTool]:
    """Return the analyst's read-only status tools (empty when Waypoint is unconfigured)."""
    if not is_waypoint_configured():
        logger.info("Waypoint not configured; status tools disabled.")
        return []
    return [tool(waypoint_run_status), tool(waypoint_case_overview)]


def _usable_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value or value.startswith("{{") or value.startswith("${"):
        return None
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    value = _usable_env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
