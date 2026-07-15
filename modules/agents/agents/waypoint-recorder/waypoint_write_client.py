"""Governed write client for the Waypoint API.

The waypoint_recorder is the ONLY agent in the pipeline allowed to write to Waypoint. It
opens a run anchor, opens/locates an assurance case, and stages a recommendation and
optional draft for an invoice — always carrying Waypoint correlation IDs so the
business decision links back to Foundry / App Insights telemetry.

Auth:
- Deployed: the agent's per-instance managed identity (DefaultAzureCredential),
  granted the Waypoint writer app role.
- Local: DefaultAzureCredential also resolves an `az login` developer identity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from telemetry import set_span_attribute, trace_span

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
AGENT_ROOT = os.path.dirname(__file__)
ENV_PATH = os.path.join(AGENT_ROOT, ".env")

# Closed vocabularies mirrored from the Waypoint API (cases schemas).
DECISIONS = {"approve", "recover", "escalate", "review"}
DRAFT_TYPES = {"supplier_dispute", "escalation_packet", "approval_summary"}
CLASSIFICATIONS = {"standard", "confidential", "ip_sensitive", "restricted"}


@dataclass(frozen=True)
class WaypointWriteConfig:
    api_base_url: str
    api_scope: str | None
    api_key: str | None
    verify_ssl: bool
    agent_name: str
    app_insights_operation_id: str | None

    @classmethod
    def try_from_env(cls) -> "WaypointWriteConfig | None":
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
            agent_name=_usable_env("AGENT_NAME") or "waypoint-recorder",
            app_insights_operation_id=_usable_env("APP_INSIGHTS_OPERATION_ID"),
        )


class WaypointWriteClient:
    """Minimal write wrapper over the governed Waypoint API."""

    def __init__(self, config: WaypointWriteConfig | None = None, timeout: float = 30.0) -> None:
        resolved = config or WaypointWriteConfig.try_from_env()
        if resolved is None:
            raise EnvironmentError(
                "WAYPOINT_API_BASE_URL is not set. Configure the Waypoint API base URL "
                "(and WAYPOINT_API_SCOPE for Entra auth) before writing."
            )
        self._config = resolved
        self._timeout = timeout
        self._credential: DefaultAzureCredential | None = None

    # ── write operations ────────────────────────────────────────────────────

    def open_run(
        self,
        name: str,
        *,
        case_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        foundry_conversation_id: str | None = None,
        app_insights_operation_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JsonValue:
        # Carry the caller-supplied App Insights operation id (the orchestrator's
        # per-invoice correlation id) when present so the run anchor is enriched at
        # early-open time; fall back to the recorder's env-configured value. This is
        # what lets Waypoint backfill null op-ids from the enriched open (W3) and
        # stops runs from orphaning with a null correlation id.
        operation_id = app_insights_operation_id or self._config.app_insights_operation_id
        body: dict[str, Any] = {
            "name": name,
            "case_id": case_id,
            "foundry_agent_name": self._config.agent_name,
            "foundry_conversation_id": foundry_conversation_id,
            "app_insights_operation_id": operation_id,
            "metadata": metadata or {},
        }
        if status is not None:
            body["status"] = status
        if summary is not None:
            body["summary"] = summary
        # Optional dedupe key: the Waypoint API returns the SAME run for a repeated
        # idempotency_key, so retries / duplicate opens don't create parallel active
        # runs. A pre-extension server silently ignores this unknown field.
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        return self._post("/api/runs", body)

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        foundry_agent_name: str | None = None,
        foundry_conversation_id: str | None = None,
        foundry_response_id: str | None = None,
        app_insights_operation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JsonValue:
        """PATCH a run anchor to advance its lifecycle (e.g. running -> completed/failed).

        Only the provided fields are sent. Idempotent on the Waypoint side. If the PATCH
        route is not deployed yet (HTTP 404/405), `_patch` degrades to a no-op so an early
        forge deploy can't break the pipeline.
        """
        body: dict[str, Any] = {}
        if status is not None:
            body["status"] = status
        if summary is not None:
            body["summary"] = summary
        if foundry_agent_name is not None:
            body["foundry_agent_name"] = foundry_agent_name
        if foundry_conversation_id is not None:
            body["foundry_conversation_id"] = foundry_conversation_id
        if foundry_response_id is not None:
            body["foundry_response_id"] = foundry_response_id
        if app_insights_operation_id is not None:
            body["app_insights_operation_id"] = app_insights_operation_id
        if metadata is not None:
            body["metadata"] = metadata
        return self._patch(f"/api/runs/{run_id}", body)

    def create_case(
        self,
        invoice_id: str,
        *,
        finding_id: str | None = None,
        title: str | None = None,
        summary: str = "",
        classification: str = "standard",
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JsonValue:
        body = {
            "invoice_id": invoice_id,
            "finding_id": finding_id,
            "title": title,
            "summary": summary,
            "classification": _classification(classification),
            "metadata": metadata or {},
        }
        # Optional dedupe key: the Waypoint API returns the SAME case for a repeated
        # idempotency_key, so the orchestrator's early-open and the recorder's final
        # write resolve to one case (no duplicates). Pre-extension servers ignore it.
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        return self._post("/api/cases", body)

    def open_assurance_run(
        self,
        invoice_ref: str,
        *,
        operation_id: str,
        status: str = "running",
        case_summary: str = "",
        run_summary: str | None = None,
        classification: str = "standard",
        case_metadata: dict[str, Any] | None = None,
        run_metadata: dict[str, Any] | None = None,
        foundry_conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently open (or re-anchor) a case + run for one invoice execution.

        Uses the shared idempotency key scheme so the orchestrator's early-open and the
        recorder's final write resolve to the SAME case and run:
          - case key: ``assurance-case:{invoice_ref}:{operation_id}``
          - run key:  ``assurance:{invoice_ref}:{operation_id}``
        ``invoice_ref`` MUST be the same stable invoice reference both stages agree on
        (the orchestrator's fan-out ``invoice_id``), NOT a grounded/canonical value that
        may differ between stages. ``case_id`` is set at open time because it is not
        PATCHable afterwards. Returns the resolved correlation ids + keys.

        ``operation_id`` is also threaded onto the run as ``app_insights_operation_id``
        (on both the open POST and the status-advancing PATCH) so the run anchor carries
        the orchestrator's correlation id at early-open time. Combined with the stable
        ``name = "assurance:{invoice_ref}"``, Waypoint's server-side reuse (W2) resolves
        repeated same-invoice opens to one active run and can backfill null op-ids (W3).
        """
        case_key = assurance_case_key(invoice_ref, operation_id)
        run_key = assurance_run_key(invoice_ref, operation_id)
        case = self.create_case(
            invoice_ref,
            summary=case_summary,
            classification=classification,
            idempotency_key=case_key,
            metadata=case_metadata or {},
        )
        case_id = case.get("id") if isinstance(case, dict) else None
        run = self.open_run(
            name=f"assurance:{invoice_ref}",
            case_id=case_id,
            status=status,
            summary=run_summary or f"assurance:{invoice_ref} ({status})",
            app_insights_operation_id=operation_id,
            foundry_conversation_id=foundry_conversation_id,
            idempotency_key=run_key,
            metadata=run_metadata or {},
        )
        run_id = run.get("id") if isinstance(run, dict) else None
        # Enforce the requested status. An idempotent re-open returns the EXISTING run
        # unchanged, so a run previously enrolled as "pending" won't flip to "running"
        # from the POST alone — advance it with a PATCH when the status differs. Harmless
        # (and skipped) for a freshly created run that already has the requested status.
        current_status = run.get("status") if isinstance(run, dict) else None
        if run_id and current_status and current_status != status:
            self.update_run(
                run_id,
                status=status,
                summary=run_summary or f"assurance:{invoice_ref} ({status})",
                app_insights_operation_id=operation_id,
            )
        return {
            "case_id": case_id,
            "run_id": run_id,
            "case_key": case_key,
            "run_key": run_key,
        }

    def create_recommendation(
        self,
        case_id: str,
        *,
        decision: str,
        reasoning: str,
        confidence: float = 0.0,
        money_at_risk: float = 0.0,
        evidence_ids: list[str] | None = None,
        proposed_next_actions: list[str] | None = None,
        foundry_response_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JsonValue:
        body = {
            "decision": _decision(decision),
            "reasoning": reasoning,
            "confidence": _clamp_unit(confidence),
            "money_at_risk": str(money_at_risk),
            "evidence_ids": evidence_ids or [],
            "proposed_next_actions": proposed_next_actions or [],
            "foundry_response_id": foundry_response_id,
            "metadata": metadata or {},
        }
        return self._post(f"/api/cases/{case_id}/recommendations", body)

    def create_draft(
        self,
        case_id: str,
        *,
        draft_type: str,
        title: str,
        body_text: str,
        source_recommendation_id: str | None = None,
        classification: str = "standard",
        metadata: dict[str, Any] | None = None,
    ) -> JsonValue:
        body = {
            "draft_type": _draft_type(draft_type),
            "title": title,
            "body": body_text,
            "source_recommendation_id": source_recommendation_id,
            "classification": _classification(classification),
            "metadata": metadata or {},
        }
        return self._post(f"/api/cases/{case_id}/drafts", body)

    # ── transport ───────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict[str, Any]) -> JsonValue:
        with trace_span(
            "waypoint_recorder.waypoint.write",
            {
                "gen_ai.agent.name": "waypoint-recorder",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "waypoint_write",
                "http.request.method": "POST",
                "url.path": path,
                "forge.waypoint.invoice_id": body.get("invoice_id"),
                "forge.waypoint.case_id": body.get("case_id"),
                "forge.waypoint.decision": body.get("decision"),
                "forge.rft.agent": "waypoint_recorder",
                "forge.rft.task_family": "invoice_assurance_decision",
            },
        ) as span:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            # Prefer agent-identity bearer when a scope is configured; only fall
            # back to the API key when no scope is set (Waypoint rejects x-api-key
            # when server-side key auth is disabled).
            token = self._token() if self._config.api_scope else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            elif self._config.api_key:
                headers["x-api-key"] = self._config.api_key
            with httpx.Client(timeout=self._timeout, verify=self._config.verify_ssl) as client:
                response = client.post(self._url(path), headers=headers, json=body)
            set_span_attribute(span, "http.response.status_code", response.status_code)
            if response.is_error:
                detail = response.text[:500]
                raise RuntimeError(
                    f"Waypoint POST {path} failed with HTTP {response.status_code}: {detail}"
                )
            if not response.content:
                return None
            payload = response.json()
            set_span_attribute(span, "forge.waypoint.entity_id", payload.get("id") if isinstance(payload, dict) else None)
            return payload

    def _patch(self, path: str, body: dict[str, Any]) -> JsonValue:
        with trace_span(
            "waypoint_recorder.waypoint.write",
            {
                "gen_ai.agent.name": "waypoint-recorder",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "waypoint_write",
                "http.request.method": "PATCH",
                "url.path": path,
                "forge.waypoint.status": body.get("status"),
                "forge.rft.agent": "waypoint_recorder",
                "forge.rft.task_family": "invoice_assurance_decision",
            },
        ) as span:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            # Prefer agent-identity bearer when a scope is configured; only fall
            # back to the API key when no scope is set (Waypoint rejects x-api-key
            # when server-side key auth is disabled).
            token = self._token() if self._config.api_scope else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            elif self._config.api_key:
                headers["x-api-key"] = self._config.api_key
            with httpx.Client(timeout=self._timeout, verify=self._config.verify_ssl) as client:
                response = client.patch(self._url(path), headers=headers, json=body)
            set_span_attribute(span, "http.response.status_code", response.status_code)
            # Graceful degradation: if the PATCH route isn't deployed yet (older Waypoint
            # API), treat it as a no-op instead of failing the whole assurance write. The
            # run still exists from open_run; it just won't advance its lifecycle until the
            # Waypoint API extension lands. No forge redeploy needed once it does.
            if response.status_code in (404, 405):
                set_span_attribute(span, "forge.waypoint.patch_unsupported", True)
                return None
            if response.is_error:
                detail = response.text[:500]
                raise RuntimeError(
                    f"Waypoint PATCH {path} failed with HTTP {response.status_code}: {detail}"
                )
            if not response.content:
                return None
            payload = response.json()
            set_span_attribute(span, "forge.waypoint.entity_id", payload.get("id") if isinstance(payload, dict) else None)
            return payload

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


def is_waypoint_configured() -> bool:
    return WaypointWriteConfig.try_from_env() is not None


# ── idempotency keys ─────────────────────────────────────────────────────────
# The orchestrator (early-open) and the recorder (final write) must derive the
# SAME keys so a run/case opened at fan-out start is reused at completion. Keep
# these formats identical to assurance_orchestrator.expert_clients (which
# replicates them, since the two agents are separate Python packages).


def assurance_run_key(invoice_ref: str, operation_id: str) -> str:
    return f"assurance:{invoice_ref}:{operation_id}"


def assurance_case_key(invoice_ref: str, operation_id: str) -> str:
    return f"assurance-case:{invoice_ref}:{operation_id}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _decision(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}, got {value!r}")
    return cleaned


def _draft_type(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in DRAFT_TYPES:
        raise ValueError(f"draft_type must be one of {sorted(DRAFT_TYPES)}, got {value!r}")
    return cleaned


def _classification(value: str) -> str:
    cleaned = (value or "standard").strip().lower()
    return cleaned if cleaned in CLASSIFICATIONS else "standard"


def _clamp_unit(value: float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    number = max(0.0, min(1.0, number))
    return str(number)


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
