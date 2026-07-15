"""Read-only client for the Waypoint testing API.

AssuranceOrchestrator intentionally starts with delegated-user testing through Azure CLI
credentials. Production Forge agents should move to client credentials or app
roles after Waypoint exposes that authorization path.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from telemetry import set_span_attribute, trace_span


JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
AGENT_ROOT = os.path.dirname(__file__)
ENV_PATH = os.path.join(AGENT_ROOT, ".env")


@dataclass(frozen=True)
class WaypointConfig:
    api_base_url: str
    api_scope: str | None
    api_key: str | None
    verify_ssl: bool

    @classmethod
    def from_env(cls) -> "WaypointConfig":
        load_dotenv(ENV_PATH, override=False)
        return cls(
            api_base_url=_required_env("WAYPOINT_API_BASE_URL").rstrip("/"),
            api_scope=_usable_env("WAYPOINT_API_SCOPE"),
            api_key=_usable_env("WAYPOINT_API_KEY"),
            verify_ssl=_env_bool("WAYPOINT_API_VERIFY_SSL", default=True),
        )

    @classmethod
    def try_from_env(cls) -> "WaypointConfig | None":
        load_dotenv(ENV_PATH, override=False)
        base_url = _usable_env("WAYPOINT_API_BASE_URL")
        scope = _usable_env("WAYPOINT_API_SCOPE")
        if not base_url:
            return None
        return cls(
            api_base_url=base_url.rstrip("/"),
            api_scope=scope,
            api_key=_usable_env("WAYPOINT_API_KEY"),
            verify_ssl=_env_bool("WAYPOINT_API_VERIFY_SSL", default=True),
        )


class WaypointReadOnlyClient:
    """Read-only Waypoint API wrapper."""

    def __init__(self, config: WaypointConfig | None = None, timeout: float = 30.0) -> None:
        self._config = config or WaypointConfig.from_env()
        self._timeout = timeout

    def get_work(self) -> JsonValue:
        return self._get("/api/work")

    def get_action_types(self) -> JsonValue:
        return self._get("/api/actions/types")

    def get_runs(self) -> JsonValue:
        return self._get("/api/runs")

    def get_cases(self) -> JsonValue:
        return self._get("/api/cases")

    def get_case(self, case_id: str) -> JsonValue:
        return self._get(f"/api/cases/{case_id}")

    def get_invoices(self, supplier_id: str | None = None) -> JsonValue:
        params = {"supplier_id": supplier_id} if supplier_id else None
        return self._get("/api/invoices", params=params)

    def get_invoice(self, invoice_id: str) -> JsonValue:
        return self._get(f"/api/invoices/{invoice_id}")

    def get_invoice_context(self, invoice_id: str) -> JsonValue:
        return self._get(f"/api/invoices/{invoice_id}/context")

    def get_invoice_decisions(self) -> JsonValue:
        return self._get("/api/invoice-decisions")

    def get_findings(self, invoice_id: str | None = None) -> JsonValue:
        params = {"invoice_id": invoice_id} if invoice_id else None
        return self._get("/api/findings", params=params)

    def get_evidence(
        self,
        invoice_id: str | None = None,
        finding_id: str | None = None,
    ) -> JsonValue:
        params = {
            key: value
            for key, value in {
                "invoice_id": invoice_id,
                "finding_id": finding_id,
            }.items()
            if value
        }
        return self._get("/api/evidence", params=params or None)

    def get_contract_document(self, document_id: str) -> JsonValue:
        return self._get(f"/api/contract-documents/{document_id}")

    def get_policy(self, policy_id: str) -> JsonValue:
        return self._get(f"/api/policies/{policy_id}")

    def _get(self, path: str, params: dict[str, str] | None = None) -> JsonValue:
        with trace_span(
            "assurance_orchestrator.waypoint.read",
            {
                "gen_ai.agent.name": "assurance-orchestrator",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "waypoint_read",
                "http.request.method": "GET",
                "url.path": path,
                "forge.waypoint.params": params,
                "forge.rft.agent": "assurance_orchestrator",
                "forge.rft.task_family": "invoice_assurance_read",
            },
        ) as span:
            headers = {"Accept": "application/json"}
            # Prefer agent-identity bearer whenever a scope is configured; only
            # fall back to the API key when no scope is set. Waypoint rejects
            # x-api-key when server-side key auth is disabled, so a stray key must
            # never shadow the bearer token.
            token = self._get_token() if self._config.api_scope else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            elif self._config.api_key:
                headers["x-api-key"] = self._config.api_key
            with httpx.Client(timeout=self._timeout, verify=self._config.verify_ssl) as client:
                response = client.get(self._url(path), headers=headers, params=params)
            set_span_attribute(span, "http.response.status_code", response.status_code)
            if response.is_error:
                body = response.text[:500]
                raise RuntimeError(
                    f"Waypoint GET {path} failed with HTTP {response.status_code}: {body}"
                )
            if not response.content:
                return None
            return response.json()

    def _get_token(self) -> str | None:
        if not self._config.api_scope:
            return None
        if not self._config.api_scope.endswith("/.default"):
            return _get_azure_cli_scope_token(self._config.api_scope)

        credential = DefaultAzureCredential()
        try:
            token = credential.get_token(self._config.api_scope)
            return token.token
        finally:
            credential.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        base = self._config.api_base_url
        if base.endswith("/api") and path.startswith("/api/"):
            return f"{base}{path[4:]}"
        return f"{base}{path}"


def is_waypoint_configured() -> bool:
    return WaypointConfig.try_from_env() is not None


def _required_env(name: str) -> str:
    value = _usable_env(name)
    if value:
        return value
    raise EnvironmentError(
        f"{name} is not set. Configure WAYPOINT_API_BASE_URL for Waypoint "
        "integration testing. Set WAYPOINT_API_SCOPE only when the target API "
        "requires delegated Entra auth."
    )


def _usable_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        return None
    if value.startswith("{{") or value.startswith("${"):
        return None
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    value = _usable_env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_azure_cli_scope_token(scope: str) -> str:
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise RuntimeError(
            "Azure CLI is required for delegated Waypoint testing but was not found."
        )
    try:
        completed = subprocess.run(
            [
                az,
                "account",
                "get-access-token",
                "--scope",
                scope,
                "--output",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        message = (error.stderr or error.stdout or "").strip()
        raise RuntimeError(f"Azure CLI failed to acquire a Waypoint token: {message}") from error

    payload = json.loads(completed.stdout)
    token = payload.get("accessToken")
    if not token:
        raise RuntimeError("Azure CLI token response did not include accessToken.")
    return str(token)
