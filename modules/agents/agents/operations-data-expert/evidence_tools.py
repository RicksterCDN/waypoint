"""FabricIQ structured-data grounding — dual path.

operations-data-expert grounds on the LIVE Waypoint operational core — the
mirrored operational Postgres tables ``suppliers``, ``invoices``,
``invoice_lines`` and ``reconciliation_findings`` only. Contracts, policies and
emailed invoices are owned by contract-policy-expert (FoundryIQ) and never enter
this plane.

There are two grounding paths, because the Fabric data agent tool authenticates
on-behalf-of (OBO) the signed-in user and does NOT support service-principal /
managed-identity auth:

1. INTERACTIVE (a user identity is present — Foundry Playground, Microsoft 365
   AI Teammate): the published Waypoint Fabric data agent (WaypointDataAgent),
   reached through the ``waypoint-data-agent-connection`` ``MicrosoftFabric``
   project connection (AAD/OBO). Rich natural-language grounding over the
   WaypointIQ Direct Lake semantic model. Implemented as a Foundry-native
   ``MicrosoftFabricPreviewTool`` bound directly to the agent.

2. HEADLESS (no user identity — e.g. the assurance-orchestrator fan-out): a
   deterministic direct read of the mirrored DB's SQL analytics endpoint over
   TDS using the Forge PROJECT managed identity (AAD token audience
   ``https://database.windows.net/.default``). Parameterized ``SELECT``s against
   ONLY the four allow-listed tables. Implemented as the local
   ``gather_fabric_evidence`` function tool.

Both paths emit the shared evidence contract with ``fabric://`` source refs
limited to the four tables. When neither path can ground, the tool returns an
honest, repairable empty contract.

``agent-framework-foundry==1.4.0`` does not ship the
``FoundryChatClient.get_fabric_tool`` factory, so the interactive tool is built
directly from ``azure-ai-projects`` models (the same object the factory
returns).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import struct
from typing import Any

from agent_framework import FunctionTool, tool

logger = logging.getLogger("fabriciq.evidence_tools")

PLANE = "fabriciq"
AGENT = "operations-data-expert"

DEFAULT_CONNECTION_NAME = "waypoint-data-agent-connection"
# Fabric materializes the mirrored Postgres `public` schema on the SQL analytics
# endpoint as `_public` (T-SQL reserves the bare word `public`). Identifiers are
# always bracketed below, but the default/fallback must match what Fabric exposes.
DEFAULT_SCHEMA = "_public"

# Strict allow-list. These are the ONLY tables this agent may read. They are
# compile-time constants (never derived from model/user input), so no other
# table — and nothing outside the operational core — can be queried.
ALLOWED_TABLES = ("suppliers", "invoices", "invoice_lines", "reconciliation_findings")

# ODBC attribute id for an AAD access token (SQL_COPT_SS_ACCESS_TOKEN).
_SQL_COPT_SS_ACCESS_TOKEN = 1256
_DB_TOKEN_SCOPE = "https://database.windows.net/.default"

# Candidate column names used to resolve rows without hard-coding the exact
# mirrored schema. Only the first matching column present on a table is used.
_INVOICE_ID_COLUMNS = ("invoice_number", "invoice_id", "number", "id")
_INVOICE_LINE_FK_COLUMNS = ("invoice_id", "invoice", "invoice_number")
_SUPPLIER_FK_COLUMNS = ("supplier_id", "supplier", "vendor_id")
_FINDING_FK_COLUMNS = ("invoice_id", "invoice", "invoice_number")
_SUPPLIER_PK_COLUMNS = ("id", "supplier_id")
_AMOUNT_COLUMNS = (
    "overpayment_amount",
    "variance_amount",
    "recoverable_amount",
    "amount",
    "difference",
)


def _usable(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith("${") or value.startswith("{{"):
        return None
    return value


def _connection_name() -> str | None:
    return _usable(os.environ.get("FABRIC_DATA_AGENT_CONNECTION_NAME")) or DEFAULT_CONNECTION_NAME


def build_headless_evidence_tools() -> list:
    """Return the deterministic headless grounding tool(s).

    This is the ``gather_fabric_evidence`` function tool: it self-guards and
    degrades to an empty contract when the mirrored SQL endpoint is not
    configured or reachable. It runs as the project managed identity and is safe
    on the headless Responses path (no signed-in user), so it is attached to
    every agent surface.
    """
    return [tool(gather_fabric_evidence)]


def build_interactive_fabric_tools(credential=None, project_endpoint: str | None = None) -> list:
    """Return the interactive/OBO Fabric data-agent tool(s), if resolvable.

    The ``MicrosoftFabricPreviewTool`` grounds on-behalf-of the *signed-in user*
    (OBO identity passthrough). It MUST NOT be attached to the headless Responses
    surface: with no user token present the Foundry backend rejects the turn with
    an internal server error. Attach these only to the Teammate/Copilot surface
    (``/api/messages``), where a delegated user token exists.

    Returns an empty list when the Fabric connection cannot be resolved so the
    caller can attach unconditionally.
    """
    fabric_tool = _build_fabric_dataagent_tool(credential, project_endpoint)
    return [fabric_tool] if fabric_tool is not None else []


def build_evidence_tools(credential=None, project_endpoint: str | None = None) -> list:
    """Backwards-compatible combined tool list (headless + interactive).

    Prefer :func:`build_headless_evidence_tools` and
    :func:`build_interactive_fabric_tools` so the OBO Fabric tool can be scoped
    to the Teammate surface only. Retained for callers that want both.
    """
    return build_interactive_fabric_tools(credential, project_endpoint) + build_headless_evidence_tools()


def _build_fabric_dataagent_tool(credential, project_endpoint: str | None):
    """Interactive/OBO path: MicrosoftFabricPreviewTool over the project connection."""
    connection_name = _connection_name()
    project_endpoint = project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not connection_name or not project_endpoint or credential is None:
        logger.info(
            "FabricIQ data-agent (interactive) grounding disabled "
            "(connection=%s, project_endpoint=%s).",
            connection_name,
            bool(project_endpoint),
        )
        return None

    try:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import (
            FabricDataAgentToolParameters,
            MicrosoftFabricPreviewTool,
            ToolProjectConnection,
        )
    except ImportError:  # pragma: no cover - azure-ai-projects present in container
        logger.warning("azure-ai-projects not installed; interactive FabricIQ path disabled.")
        return None

    try:
        project = AIProjectClient(endpoint=project_endpoint, credential=credential)
        connection_id = project.connections.get(connection_name).id
    except Exception:  # pragma: no cover - transient/config failure must not crash startup
        logger.warning(
            "Could not resolve Fabric connection '%s'; interactive FabricIQ path disabled.",
            connection_name,
            exc_info=True,
        )
        return None

    logger.info(
        "FabricIQ interactive grounding enabled via connection '%s' (%s).",
        connection_name,
        connection_id,
    )
    return MicrosoftFabricPreviewTool(
        fabric_dataagent_preview=FabricDataAgentToolParameters(
            project_connections=[ToolProjectConnection(project_connection_id=connection_id)],
        )
    )


def gather_fabric_evidence(invoice_id: str = "", question: str = "") -> str:
    """Deterministically read the mirrored operational core for one invoice.

    Headless (no user identity) grounding path. Connects to the mirrored DB SQL
    analytics endpoint with the Forge project managed identity and reads ONLY the
    four allow-listed operational tables (suppliers, invoices, invoice_lines,
    reconciliation_findings). Returns the shared evidence contract with
    ``fabric://`` source refs. Never touches contracts, policies, or emailed
    invoices.

    Args:
        invoice_id: The invoice id or invoice number under assurance review.
        question: Optional focused structured-data question (advisory only).
    """
    reader = _MirroredSqlReader.try_from_env()
    if reader is None:
        return _contract(
            invoice_id,
            [],
            "FabricIQ mirrored-SQL read is not configured "
            "(FABRIC_MIRRORED_SQL_ENDPOINT / FABRIC_MIRRORED_DATABASE unset).",
        )

    try:
        return reader.evidence_for_invoice(invoice_id)
    except Exception as exc:  # pragma: no cover - degrade gracefully on any read failure
        # Surface the concrete failure (pyodbc error text + registered ODBC
        # drivers) directly in the log message: the telemetry exporter otherwise
        # flattens the chained cause and we lose the discriminating detail (e.g.
        # IM002 driver-not-found vs 18456 login-denied).
        logger.warning(
            "FabricIQ mirrored-SQL read failed for '%s': %s: %s "
            "(presented principal: %s; registered ODBC drivers: %s)",
            invoice_id,
            type(exc).__name__,
            exc,
            reader._last_principal or "<token not acquired>",
            _available_odbc_drivers(),
            exc_info=True,
        )
        return _contract(
            invoice_id,
            [],
            "FabricIQ mirrored-SQL read was unavailable for this invoice; no operational "
            "evidence could be grounded.",
        )


def _available_odbc_drivers() -> str:
    """Best-effort list of ODBC drivers visible to pyodbc (diagnostic only)."""
    try:
        import pyodbc

        return ", ".join(pyodbc.drivers()) or "<none>"
    except Exception as exc:  # pragma: no cover - pyodbc/driver-manager missing
        return f"<pyodbc unavailable: {type(exc).__name__}: {exc}>"


def _principal_from_token(token: str) -> str:
    """Decode the non-secret identity claims from an AAD access token.

    Returns a compact ``appid=… oid=… aud=… tid=…`` string so we can confirm
    WHICH principal the container actually presents to the SQL endpoint (e.g. the
    granted Forge project MI vs. a hosting/compute managed identity). Only
    identifier claims are surfaced — never the token/signature.
    """
    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        return f"<undecodable token claims: {type(exc).__name__}>"
    parts = []
    for key in ("appid", "azp", "oid", "aud", "tid", "idtyp", "xms_mirid"):
        value = claims.get(key)
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts) or "<no identity claims>"


# ── mirrored SQL analytics endpoint reader (headless, project-MI) ─────────────


class _MirroredSqlReader:
    def __init__(self, server: str, database: str, schema: str) -> None:
        self._server = server
        self._database = database
        self._schema = schema
        self._credential = None
        self._columns: dict[str, list[str]] = {}
        self._last_principal: str | None = None

    @classmethod
    def try_from_env(cls) -> "_MirroredSqlReader | None":
        server = _usable(os.environ.get("FABRIC_MIRRORED_SQL_ENDPOINT"))
        database = _usable(os.environ.get("FABRIC_MIRRORED_DATABASE"))
        if not server or not database:
            return None
        schema = _usable(os.environ.get("FABRIC_MIRRORED_SCHEMA")) or DEFAULT_SCHEMA
        return cls(server, database, schema)

    # -- connection --

    def _access_token_struct(self) -> bytes:
        from azure.identity import DefaultAzureCredential

        if self._credential is None:
            self._credential = DefaultAzureCredential()
        token = self._credential.get_token(_DB_TOKEN_SCOPE).token
        # Record + log which principal we actually present to the SQL endpoint.
        # A mismatch here (hosting/compute MI instead of the granted Forge project
        # MI) is the difference between a clean read and an 18456 login denial.
        self._last_principal = _principal_from_token(token)
        logger.info(
            "FabricIQ SQL reader presenting principal to %s: %s",
            self._server,
            self._last_principal,
        )
        encoded = token.encode("utf-16-le")
        return struct.pack("<I", len(encoded)) + encoded

    def _connect(self):
        import pyodbc

        connection_string = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={self._server},1433;"
            f"Database={self._database};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )
        return pyodbc.connect(
            connection_string,
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: self._access_token_struct()},
            timeout=30,
        )

    # -- schema introspection (allow-listed tables only) --

    def _table_columns(self, cursor, table: str) -> list[str]:
        assert table in ALLOWED_TABLES
        if table not in self._columns:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                self._schema,
                table,
            )
            self._columns[table] = [str(row[0]) for row in cursor.fetchall()]
        return self._columns[table]

    def _pick(self, columns: list[str], candidates: tuple[str, ...]) -> str | None:
        lowered = {c.lower(): c for c in columns}
        for candidate in candidates:
            if candidate.lower() in lowered:
                return lowered[candidate.lower()]
        return None

    def _select_rows(self, cursor, table: str, where_col: str, value: Any) -> list[dict[str, Any]]:
        assert table in ALLOWED_TABLES
        # table/schema/where_col are validated identifiers (constants or
        # INFORMATION_SCHEMA-derived); the value is always parameterized.
        cursor.execute(
            f'SELECT * FROM [{self._schema}].[{table}] WHERE [{where_col}] = ?',
            value,
        )
        column_names = [c[0] for c in cursor.description]
        return [dict(zip(column_names, row)) for row in cursor.fetchall()]

    # -- evidence assembly --

    def evidence_for_invoice(self, invoice_id: str) -> str:
        invoice_id = (invoice_id or "").strip()
        if not invoice_id:
            return _contract(invoice_id, [], "No invoice id was provided to the FabricIQ read.")

        with self._connect() as conn:
            cursor = conn.cursor()

            invoice_cols = self._table_columns(cursor, "invoices")
            id_col = self._pick(invoice_cols, _INVOICE_ID_COLUMNS)
            invoice_row: dict[str, Any] | None = None
            if id_col is not None:
                for candidate in _INVOICE_ID_COLUMNS:
                    col = self._pick(invoice_cols, (candidate,))
                    if not col:
                        continue
                    rows = self._select_rows(cursor, "invoices", col, invoice_id)
                    if rows:
                        invoice_row = rows[0]
                        id_col = col
                        break

            if not invoice_row:
                return _contract(
                    invoice_id,
                    [],
                    f"No invoice matched '{invoice_id}' in the mirrored operational core.",
                )

            invoice_pk = invoice_row.get(id_col, invoice_id)
            evidence: list[dict[str, Any]] = []

            evidence.append(
                {
                    "claim": _row_claim("invoice", invoice_row),
                    "supports": "review",
                    "source_ref": f"fabric://{self._schema}.invoices/{invoice_pk}",
                    "classification": "confidential",
                    "confidence": 0.9,
                }
            )

            # supplier (parent of the invoice)
            supplier_fk = self._pick(invoice_cols, _SUPPLIER_FK_COLUMNS)
            if supplier_fk and invoice_row.get(supplier_fk) is not None:
                supplier_cols = self._table_columns(cursor, "suppliers")
                supplier_pk_col = self._pick(supplier_cols, _SUPPLIER_PK_COLUMNS)
                if supplier_pk_col:
                    for supplier in self._select_rows(
                        cursor, "suppliers", supplier_pk_col, invoice_row[supplier_fk]
                    ):
                        pk = supplier.get(supplier_pk_col)
                        evidence.append(
                            {
                                "claim": _row_claim("supplier", supplier),
                                "supports": "review",
                                "source_ref": f"fabric://{self._schema}.suppliers/{pk}",
                                "classification": "confidential",
                                "confidence": 0.85,
                            }
                        )

            # invoice_lines (children of the invoice)
            line_cols = self._table_columns(cursor, "invoice_lines")
            line_fk = self._pick(line_cols, _INVOICE_LINE_FK_COLUMNS)
            line_pk_col = self._pick(line_cols, ("id", "line_id", "invoice_line_id"))
            if line_fk:
                for line in self._select_rows(cursor, "invoice_lines", line_fk, invoice_pk):
                    pk = line.get(line_pk_col) if line_pk_col else "line"
                    evidence.append(
                        {
                            "claim": _row_claim("invoice line", line),
                            "supports": "review",
                            "source_ref": f"fabric://{self._schema}.invoice_lines/{pk}",
                            "classification": "confidential",
                            "confidence": 0.85,
                        }
                    )

            # reconciliation_findings (drive the supports signal)
            finding_cols = self._table_columns(cursor, "reconciliation_findings")
            finding_fk = self._pick(finding_cols, _FINDING_FK_COLUMNS)
            finding_pk_col = self._pick(finding_cols, ("id", "finding_id"))
            amount_col = self._pick(finding_cols, _AMOUNT_COLUMNS)
            if finding_fk:
                for finding in self._select_rows(
                    cursor, "reconciliation_findings", finding_fk, invoice_pk
                ):
                    pk = finding.get(finding_pk_col) if finding_pk_col else "finding"
                    amount = _to_float(finding.get(amount_col)) if amount_col else 0.0
                    supports = "recover" if amount > 0 else "review"
                    evidence.append(
                        {
                            "claim": _row_claim("reconciliation finding", finding),
                            "supports": supports,
                            "source_ref": (
                                f"fabric://{self._schema}.reconciliation_findings/{pk}"
                            ),
                            "classification": "confidential",
                            "confidence": 0.9,
                        }
                    )

        summary = (
            f"Grounded {len(evidence)} operational-core record(s) for invoice "
            f"'{invoice_id}' from the mirrored suppliers/invoices/invoice_lines/"
            "reconciliation_findings tables."
        )
        return _contract(str(invoice_pk), evidence, summary)


# ── helpers ──────────────────────────────────────────────────────────────────


def _row_claim(kind: str, row: dict[str, Any]) -> str:
    parts = []
    for key, value in row.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
        if len(parts) >= 6:
            break
    detail = ", ".join(parts) if parts else "no fields"
    return f"Operational {kind} record: {detail}."


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _contract(invoice_id: str, evidence: list[dict[str, Any]], summary: str) -> str:
    return json.dumps(
        {
            "agent": AGENT,
            "plane": PLANE,
            "invoice_id": invoice_id,
            "output_type": "expert_evidence",
            "evidence": evidence,
            "unsupported": [],
            "summary": summary,
            "correlation": {"waypoint_run_id": None, "waypoint_invoice_id": invoice_id},
        },
        ensure_ascii=False,
        default=str,
    )
