"""Postgres schema, seed, and live-data append helpers."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .paths import data_path, read_json, repo_root


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledgerfield_seed_metadata (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS suppliers (
    id text PRIMARY KEY,
    name text NOT NULL,
    supplier_type text NOT NULL,
    region text NOT NULL,
    specialty text NOT NULL,
    contract_model text NOT NULL,
    invoice_categories jsonb NOT NULL,
    reconciliation_focus jsonb NOT NULL,
    risk_examples jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id text PRIMARY KEY,
    supplier_id text NOT NULL REFERENCES suppliers(id),
    issue_category text NOT NULL,
    future_invoice_line text NOT NULL,
    expected_decision_state text NOT NULL,
    evidence_refs jsonb NOT NULL,
    recommended_action text NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id text PRIMARY KEY,
    supplier_id text REFERENCES suppliers(id),
    document_type text NOT NULL,
    title text NOT NULL,
    source_path text NOT NULL,
    generated_path text
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id text PRIMARY KEY,
    supplier_id text NOT NULL REFERENCES suppliers(id),
    supplier_name text NOT NULL,
    invoice_date date NOT NULL,
    purchase_order_id text NOT NULL,
    total_amount numeric(14, 2) NOT NULL,
    currency text NOT NULL,
    generation_batch text NOT NULL DEFAULT 'base',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    line_id text PRIMARY KEY,
    invoice_id text NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
    line_type text NOT NULL,
    description text NOT NULL,
    reference_id text NOT NULL,
    quantity numeric(14, 4) NOT NULL,
    unit_price numeric(14, 4) NOT NULL,
    amount numeric(14, 2) NOT NULL,
    expected_decision_state text NOT NULL,
    scenario_id text REFERENCES scenarios(scenario_id),
    issue_category text
);
"""


def database_url(explicit_url: str | None = None) -> str:
    """Resolve the target Postgres connection string."""

    url = explicit_url or os.getenv("LEDGERFIELD_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError(
            "Postgres connection string is required. Pass --dsn or set "
            "LEDGERFIELD_DATABASE_URL/DATABASE_URL."
        )
    return url


def connect(dsn: str):
    """Open a psycopg connection, importing the dependency only for DB commands."""

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("Install the project dependencies before using database commands.") from exc

    return psycopg.connect(dsn)


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)
    conn.commit()


def get_status(dsn: str) -> dict[str, Any]:
    """Return row counts and whether the database appears to need seed data."""

    with connect(dsn) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.suppliers'), to_regclass('public.invoices')")
            suppliers_table, invoices_table = cursor.fetchone()
            if not suppliers_table or not invoices_table:
                return {
                    "schema_exists": False,
                    "supplier_count": 0,
                    "invoice_count": 0,
                    "invoice_line_count": 0,
                    "needs_seed": True,
                }

            cursor.execute("SELECT count(*) FROM suppliers")
            supplier_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM invoices")
            invoice_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM invoice_lines")
            invoice_line_count = cursor.fetchone()[0]

    return {
        "schema_exists": True,
        "supplier_count": supplier_count,
        "invoice_count": invoice_count,
        "invoice_line_count": invoice_line_count,
        "needs_seed": supplier_count == 0 or invoice_count == 0,
    }


def seed_database(
    dsn: str,
    root: Path | None = None,
    *,
    if_needed: bool = False,
    append_cycles: int = 0,
) -> dict[str, Any]:
    """Create schema, seed canonical data, and optionally append live-looking invoices."""

    root = root or repo_root()
    if if_needed and not get_status(dsn)["needs_seed"]:
        appended = append_live_invoices(dsn, root, append_cycles) if append_cycles else 0
        status = get_status(dsn)
        status["base_seeded"] = False
        status["appended_invoices"] = appended
        return status

    with connect(dsn) as conn:
        ensure_schema(conn)
        _seed_suppliers(conn, root)
        _seed_scenarios(conn, root)
        _seed_documents(conn, root)
        _seed_invoices(conn, root)
        _set_metadata(conn, "base_seed_version", "1")
        conn.commit()

    appended = append_live_invoices(dsn, root, append_cycles) if append_cycles else 0
    status = get_status(dsn)
    status["base_seeded"] = True
    status["appended_invoices"] = appended
    return status


def append_live_invoices(dsn: str, root: Path | None = None, cycles: int = 1) -> int:
    """Append derived invoices so the database looks like it is receiving new activity."""

    if cycles < 1:
        return 0

    root = root or repo_root()
    invoice_doc = read_json(data_path(root) / "invoices" / "supplier-invoices.json")
    inserted = 0

    with connect(dsn) as conn:
        ensure_schema(conn)
        next_counter = _next_live_counter(conn)
        for offset in range(cycles):
            sequence = next_counter + offset
            batch = f"live-{sequence:03d}"
            for invoice in invoice_doc["invoices"]:
                derived = _derive_live_invoice(invoice, invoice_doc["currency"], sequence, batch)
                _upsert_invoice(conn, derived)
                inserted += 1
            _set_metadata(conn, "live_batch_counter", str(sequence))
        conn.commit()

    return inserted


def _seed_suppliers(conn: Any, root: Path) -> None:
    from psycopg.types.json import Jsonb

    suppliers = read_json(data_path(root) / "suppliers" / "suppliers.json")
    with conn.cursor() as cursor:
        for supplier in suppliers:
            cursor.execute(
                """
                INSERT INTO suppliers (
                    id, name, supplier_type, region, specialty, contract_model,
                    invoice_categories, reconciliation_focus, risk_examples
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    supplier_type = EXCLUDED.supplier_type,
                    region = EXCLUDED.region,
                    specialty = EXCLUDED.specialty,
                    contract_model = EXCLUDED.contract_model,
                    invoice_categories = EXCLUDED.invoice_categories,
                    reconciliation_focus = EXCLUDED.reconciliation_focus,
                    risk_examples = EXCLUDED.risk_examples
                """,
                (
                    supplier["id"],
                    supplier["name"],
                    supplier["supplier_type"],
                    supplier["region"],
                    supplier["specialty"],
                    supplier["contract_model"],
                    Jsonb(supplier["invoice_categories"]),
                    Jsonb(supplier["reconciliation_focus"]),
                    Jsonb(supplier["risk_examples"]),
                ),
            )


def _seed_scenarios(conn: Any, root: Path) -> None:
    from psycopg.types.json import Jsonb

    scenarios = read_json(data_path(root) / "scenarios" / "invoice-assurance-scenarios.json")[
        "scenarios"
    ]
    with conn.cursor() as cursor:
        for scenario in scenarios:
            cursor.execute(
                """
                INSERT INTO scenarios (
                    scenario_id, supplier_id, issue_category, future_invoice_line,
                    expected_decision_state, evidence_refs, recommended_action
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (scenario_id) DO UPDATE SET
                    supplier_id = EXCLUDED.supplier_id,
                    issue_category = EXCLUDED.issue_category,
                    future_invoice_line = EXCLUDED.future_invoice_line,
                    expected_decision_state = EXCLUDED.expected_decision_state,
                    evidence_refs = EXCLUDED.evidence_refs,
                    recommended_action = EXCLUDED.recommended_action
                """,
                (
                    scenario["scenario_id"],
                    scenario["supplier_id"],
                    scenario["issue_category"],
                    scenario["future_invoice_line"],
                    scenario["expected_decision_state"],
                    Jsonb(scenario["evidence_refs"]),
                    scenario["recommended_action"],
                ),
            )


def _seed_documents(conn: Any, root: Path) -> None:
    documents: list[dict[str, str | None]] = []
    for path in sorted((data_path(root) / "contracts" / "source-markdown").glob("*.md")):
        supplier_id = path.name.split("-")[0] + "-" + path.name.split("-")[1]
        documents.append(
            {
                "document_id": path.stem,
                "supplier_id": supplier_id,
                "document_type": "contract",
                "title": path.stem.replace("-", " ").title(),
                "source_path": path.relative_to(root).as_posix(),
                "generated_path": f"data/contracts/docx/{path.stem}.docx",
            }
        )
    for path in sorted((data_path(root) / "policies" / "source-markdown").glob("*.md")):
        documents.append(
            {
                "document_id": path.stem,
                "supplier_id": None,
                "document_type": "policy",
                "title": path.stem.replace("-", " ").title(),
                "source_path": path.relative_to(root).as_posix(),
                "generated_path": f"data/policies/docx/{path.stem}.docx",
            }
        )

    with conn.cursor() as cursor:
        for document in documents:
            cursor.execute(
                """
                INSERT INTO documents (
                    document_id, supplier_id, document_type, title, source_path, generated_path
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id) DO UPDATE SET
                    supplier_id = EXCLUDED.supplier_id,
                    document_type = EXCLUDED.document_type,
                    title = EXCLUDED.title,
                    source_path = EXCLUDED.source_path,
                    generated_path = EXCLUDED.generated_path
                """,
                (
                    document["document_id"],
                    document["supplier_id"],
                    document["document_type"],
                    document["title"],
                    document["source_path"],
                    document["generated_path"],
                ),
            )


def _seed_invoices(conn: Any, root: Path) -> None:
    invoice_doc = read_json(data_path(root) / "invoices" / "supplier-invoices.json")
    for invoice in invoice_doc["invoices"]:
        invoice = {**invoice, "currency": invoice_doc["currency"], "generation_batch": "base"}
        _upsert_invoice(conn, invoice)


def _upsert_invoice(conn: Any, invoice: dict[str, Any]) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO invoices (
                invoice_id, supplier_id, supplier_name, invoice_date, purchase_order_id,
                total_amount, currency, generation_batch
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (invoice_id) DO UPDATE SET
                supplier_id = EXCLUDED.supplier_id,
                supplier_name = EXCLUDED.supplier_name,
                invoice_date = EXCLUDED.invoice_date,
                purchase_order_id = EXCLUDED.purchase_order_id,
                total_amount = EXCLUDED.total_amount,
                currency = EXCLUDED.currency,
                generation_batch = EXCLUDED.generation_batch
            """,
            (
                invoice["invoice_id"],
                invoice["supplier_id"],
                invoice["supplier_name"],
                invoice["invoice_date"],
                invoice["purchase_order_id"],
                Decimal(str(invoice["total_amount"])),
                invoice["currency"],
                invoice.get("generation_batch", "base"),
            ),
        )
        for line in invoice["lines"]:
            cursor.execute(
                """
                INSERT INTO invoice_lines (
                    line_id, invoice_id, line_type, description, reference_id, quantity,
                    unit_price, amount, expected_decision_state, scenario_id, issue_category
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (line_id) DO UPDATE SET
                    invoice_id = EXCLUDED.invoice_id,
                    line_type = EXCLUDED.line_type,
                    description = EXCLUDED.description,
                    reference_id = EXCLUDED.reference_id,
                    quantity = EXCLUDED.quantity,
                    unit_price = EXCLUDED.unit_price,
                    amount = EXCLUDED.amount,
                    expected_decision_state = EXCLUDED.expected_decision_state,
                    scenario_id = EXCLUDED.scenario_id,
                    issue_category = EXCLUDED.issue_category
                """,
                (
                    line["line_id"],
                    invoice["invoice_id"],
                    line["line_type"],
                    line["description"],
                    line["reference_id"],
                    Decimal(str(line["quantity"])),
                    Decimal(str(line["unit_price"])),
                    Decimal(str(line["amount"])),
                    line["expected_decision_state"],
                    line.get("scenario_id"),
                    line.get("issue_category"),
                ),
            )


def _derive_live_invoice(
    invoice: dict[str, Any],
    currency: str,
    sequence: int,
    batch: str,
) -> dict[str, Any]:
    multiplier = Decimal("1") + (Decimal(sequence % 7) * Decimal("0.0075"))
    invoice_date = date.fromisoformat(invoice["invoice_date"]) + timedelta(days=sequence * 7)
    derived_lines: list[dict[str, Any]] = []
    total = Decimal("0")

    for line in invoice["lines"]:
        amount = (Decimal(str(line["amount"])) * multiplier).quantize(Decimal("0.01"))
        unit_price = (Decimal(str(line["unit_price"])) * multiplier).quantize(Decimal("0.0001"))
        total += amount
        derived_lines.append(
            {
                **line,
                "line_id": f"{line['line_id']}-LIVE-{sequence:03d}",
                "reference_id": f"{line['reference_id']}-LIVE-{sequence:03d}",
                "unit_price": str(unit_price),
                "amount": str(amount),
            }
        )

    return {
        **invoice,
        "invoice_id": f"{invoice['invoice_id']}-LIVE-{sequence:03d}",
        "invoice_date": invoice_date.isoformat(),
        "purchase_order_id": f"{invoice['purchase_order_id']}-LIVE-{sequence:03d}",
        "total_amount": str(total.quantize(Decimal("0.01"))),
        "currency": currency,
        "generation_batch": batch,
        "lines": derived_lines,
    }


def _next_live_counter(conn: Any) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT value FROM ledgerfield_seed_metadata WHERE key = 'live_batch_counter'")
        row = cursor.fetchone()
        return int(row[0]) + 1 if row else 1


def _set_metadata(conn: Any, key: str, value: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ledgerfield_seed_metadata (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, value),
        )


def to_pretty_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
