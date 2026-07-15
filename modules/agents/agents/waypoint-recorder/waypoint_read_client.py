"""Read client for the Waypoint corpus API.

The waypoint_recorder uses this to *ground* the governed write deterministically: before it
persists a decision it re-fetches the invoice's reconciliation findings and evidence
references from the seeded ledgerfield corpus. That lets the persisted run /
recommendation carry the real money-at-risk, the real evidence reference IDs, and a
fan-out trail even when the model's narrated numbers drift.

Auth mirrors waypoint_write_client: a scope (Entra) yields a bearer token via
DefaultAzureCredential; an empty scope (local dev) sends no Authorization header and
the API falls back to the local-dev reader identity.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from telemetry import set_span_attribute, trace_span

logger = logging.getLogger("waypoint_recorder.waypoint_read_client")

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
AGENT_ROOT = os.path.dirname(__file__)
ENV_PATH = os.path.join(AGENT_ROOT, ".env")


@dataclass(frozen=True)
class WaypointReadConfig:
    api_base_url: str
    api_scope: str | None
    api_key: str | None
    verify_ssl: bool

    @classmethod
    def try_from_env(cls) -> "WaypointReadConfig | None":
        load_dotenv(ENV_PATH, override=False)
        base_url = _usable_env("WAYPOINT_API_BASE_URL")
        if not base_url:
            return None
        # Accept either a bare host (waypoint's api_fqdn output is the Container App
        # ingress FQDN, no scheme) or a full URL; the client composes requests as
        # f"{base}{path}", which needs a scheme. Default to https when none is given.
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        return cls(
            api_base_url=base_url.rstrip("/"),
            api_scope=_usable_env("WAYPOINT_API_SCOPE"),
            api_key=_usable_env("WAYPOINT_API_KEY"),
            verify_ssl=_env_bool("WAYPOINT_API_VERIFY_SSL", default=True),
        )


class WaypointReadClient:
    """Minimal GET wrapper over the Waypoint corpus API."""

    def __init__(self, config: WaypointReadConfig | None = None, timeout: float = 30.0) -> None:
        resolved = config or WaypointReadConfig.try_from_env()
        if resolved is None:
            raise EnvironmentError("WAYPOINT_API_BASE_URL is not set; cannot read corpus.")
        self._config = resolved
        self._timeout = timeout
        self._credential: DefaultAzureCredential | None = None

    # ── corpus reads ─────────────────────────────────────────────────────────

    def get_invoice_detail(self, invoice_id: str) -> dict[str, Any] | None:
        value = self._get(f"/api/invoices/{invoice_id}")
        return value if isinstance(value, dict) else None

    def list_invoices(self) -> list[dict[str, Any]]:
        return _as_dicts(self._get("/api/invoices"))

    def list_findings(self, invoice_id: str) -> list[dict[str, Any]]:
        return _as_dicts(self._get("/api/findings", params={"invoice_id": invoice_id}))

    def list_evidence(self, invoice_id: str) -> list[dict[str, Any]]:
        return _as_dicts(self._get("/api/evidence", params={"invoice_id": invoice_id}))

    def resolve_invoice(self, invoice_ref: str) -> dict[str, Any] | None:
        """Resolve an invoice by canonical id, falling back to invoice_number match.

        AssuranceOrchestrator/waypoint_recorder prompts sometimes carry the human invoice number
        (INV-2026-08034) instead of Waypoint's canonical id (inv-2026-08034); the
        write API resolves only by canonical id, so normalize here before writing.
        """
        ref = (invoice_ref or "").strip()
        if not ref:
            return None
        detail = self.get_invoice_detail(ref)
        if detail:
            return detail
        target = ref.lower()
        for invoice in self.list_invoices():
            if str(invoice.get("id", "")).lower() == target or (
                str(invoice.get("invoice_number", "")).lower() == target
            ):
                resolved = self.get_invoice_detail(str(invoice.get("id")))
                if resolved:
                    return resolved
        return None

    # ── transport ────────────────────────────────────────────────────────────

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> JsonValue:
        with trace_span(
            "waypoint_recorder.waypoint.read",
            {
                "gen_ai.agent.name": "waypoint-recorder",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "waypoint_read",
                "http.request.method": "GET",
                "url.path": path,
                "forge.waypoint.params": params,
                "forge.rft.agent": "waypoint_recorder",
                "forge.rft.task_family": "invoice_assurance_decision",
            },
        ) as span:
            headers = {"Accept": "application/json"}
            # Prefer agent-identity bearer when a scope is configured; only fall
            # back to the API key when no scope is set (Waypoint rejects x-api-key
            # when server-side key auth is disabled).
            token = self._token() if self._config.api_scope else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            elif self._config.api_key:
                headers["x-api-key"] = self._config.api_key
            with httpx.Client(timeout=self._timeout, verify=self._config.verify_ssl) as client:
                response = client.get(self._url(path), headers=headers, params=params)
            set_span_attribute(span, "http.response.status_code", response.status_code)
            if response.status_code == 404:
                return None
            if response.is_error:
                raise RuntimeError(
                    f"Waypoint GET {path} failed with HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            if not response.content:
                return None
            return response.json()

    def _token(self) -> str | None:
        if not self._config.api_scope:
            return None
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential.get_token(self._config.api_scope).token

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        base = self._config.api_base_url
        if base.endswith("/api") and path.startswith("/api/"):
            return f"{base}{path[4:]}"
        return f"{base}{path}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _as_dicts(value: JsonValue) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


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
