"""Generate a Waypoint-compatible seed payload from Ledgerfield corpus sources."""

from __future__ import annotations

import json
import os
import re
import warnings
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .paths import data_path, read_json, repo_root

WAYPOINT_SEED_RELATIVE_PATH = Path("waypoint") / "waypoint-seed.json"
WAYPOINT_DECISIONS_RELATIVE_PATH = Path("waypoint") / "invoice-decisions.json"
WAYPOINT_SEED_SCHEMA_VERSION = "1.0"
ALLOWED_FINDING_STATUSES = {"open", "review", "approved", "recover", "escalate", "closed"}

SCENARIO_DESCRIPTIONS = {
    "wps-surge-capacity": (
        "Surge capacity and overtime premiums that require approved schedule and PO changes."
    ),
    "wps-ip-sensitive-work": (
        "IP-sensitive tooling, packaging, and tech-transfer work requiring legal or IP review."
    ),
    "wps-quality-release": (
        "QA release, deviation, hold, and protocol evidence needed before quality charges are paid."
    ),
    "wps-matched-production": (
        "Matched production, rate-card, conversion, and cleaning charges that should approve cleanly."
    ),
    "wps-api-handling": (
        "Sensitive API handling fees that must be tied to approved batch authorization."
    ),
    "wps-approved-surge": "Documented surge approval cases where expedited work is payable.",
    "wps-unmatched-invoice": "Invoice lines missing PO, batch, or operational authorization.",
    "wps-duplicate-submission": "Duplicate invoice submissions across supplier channels.",
    "wps-storage-condition": "Storage surcharges requiring approved temperature-control evidence.",
}

POLICY_INVOICE_RECONCILIATION = "policy-invoice-reconciliation-policy"
POLICY_DISPUTE_RECOVERY = "policy-invoice-dispute-and-recovery-procedure"
POLICY_QUALITY_RELEASE = "policy-quality-release-billability-policy"

POLICY_IDS_BY_INVOICE = {
    "INV-2026-08034": [POLICY_QUALITY_RELEASE, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08055": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08102": [POLICY_QUALITY_RELEASE, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08140": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08177": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08201": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08244": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08273": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08305": [POLICY_QUALITY_RELEASE],
    "INV-2026-08338": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08392": [POLICY_QUALITY_RELEASE],
    "INV-2026-08411": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08462": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08507": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08549": [POLICY_INVOICE_RECONCILIATION],
    "INV-2026-08602": [POLICY_INVOICE_RECONCILIATION, POLICY_DISPUTE_RECOVERY],
    "INV-2026-08644": [POLICY_QUALITY_RELEASE],
    "INV-2026-08690": [POLICY_QUALITY_RELEASE, POLICY_DISPUTE_RECOVERY],
}

BASIS_SUMMARIES_BY_INVOICE = {
    "INV-2026-08034": "BluePeak capacity agreement (contamination and deviation costs) + quality release and dispute policies.",
    "INV-2026-08055": "MSA/IP-sensitive work terms + reconciliation and dispute policies.",
    "INV-2026-08102": "Quality agreement + quality release and recovery policy.",
    "INV-2026-08140": "Supplier rate card + invoice reconciliation policy satisfied.",
    "INV-2026-08177": "API supply agreement + sensitive-material authorization policy.",
    "INV-2026-08201": "Packaging agreement + documented surge approval policy satisfied.",
    "INV-2026-08244": "Packaging agreement + IP-sensitive changeover approval policy.",
    "INV-2026-08273": "Manufacturing SOW + minimum PO and batch evidence policy.",
    "INV-2026-08305": "Testing SOW + quality protocol policy satisfied.",
    "INV-2026-08338": "Tech-transfer SOW + IP/legal escalation policy.",
    "INV-2026-08392": "Sterilization agreement + quality release timing policy.",
    "INV-2026-08411": "Supplier rate-card minimum + invoice reconciliation policy satisfied.",
    "INV-2026-08462": "Logistics agreement + duplicate submission recovery policy.",
    "INV-2026-08507": "Clinical supply storage terms + minimum evidence policy.",
    "INV-2026-08549": "Regional manufacturing rate table + reconciliation policy satisfied.",
    "INV-2026-08602": "Manufacturing SOW + overtime and surge approval policies.",
    "INV-2026-08644": "Manufacturing SOW cleaning terms + quality batch-plan policy satisfied.",
    "INV-2026-08690": "Quality agreement + hold-window and dispute policies.",
}


def generate_waypoint_seed(
    root: Path | None = None,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Generate the Waypoint seed payload and write it to disk."""

    root = root or repo_root()
    payload = build_waypoint_seed(root)
    validate_waypoint_seed(payload)

    output_path = output_path or data_path(root) / WAYPOINT_SEED_RELATIVE_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    counts = waypoint_seed_counts(payload)
    counts["output_path"] = str(output_path)
    return counts


def build_waypoint_seed(root: Path | None = None) -> dict[str, Any]:
    """Build a Waypoint-compatible seed import payload without writing it."""

    root = root or repo_root()
    data_root = data_path(root)
    generated_at = _generated_at(data_root)
    decisions = read_json(data_root / WAYPOINT_DECISIONS_RELATIVE_PATH)["decisions"]
    suppliers = read_json(data_root / "suppliers" / "suppliers.json")

    contract_documents = _contract_documents(data_root)
    contract_ids_by_supplier = _contract_ids_by_supplier(contract_documents)
    policies = _policies(data_root)
    scenarios = _waypoint_scenarios(decisions, generated_at)

    invoices = []
    findings = []
    evidence = []
    for index, decision in enumerate(decisions):
        invoice = _invoice(decision, suppliers, generated_at, index)
        finding = _finding(decision, contract_ids_by_supplier)
        invoice_evidence = _evidence(decision, finding["id"], invoice["id"])
        invoice_lines = [_decision_line(decision, invoice["id"]), _supporting_line(decision, invoice["id"])]

        invoice["lines"] = invoice_lines
        invoice["findings"] = [finding]
        invoice["evidence"] = invoice_evidence
        finding["evidence_ids"] = [item["id"] for item in invoice_evidence]

        invoices.append(invoice)
        findings.append(finding)
        evidence.extend(invoice_evidence)

    return {
        "schema_version": WAYPOINT_SEED_SCHEMA_VERSION,
        "suppliers": [_supplier(item) for item in suppliers],
        "contract_documents": contract_documents,
        "policies": policies,
        "scenarios": scenarios,
        "invoices": invoices,
        "findings": findings,
        "evidence": evidence,
    }


def validate_waypoint_seed(payload: dict[str, Any]) -> None:
    """Validate the shape and references Waypoint expects for seed import."""

    required_top_level = {
        "schema_version",
        "suppliers",
        "contract_documents",
        "policies",
        "scenarios",
        "invoices",
        "findings",
        "evidence",
    }
    missing = required_top_level - payload.keys()
    if missing:
        raise ValueError(f"Waypoint seed payload is missing top-level keys: {sorted(missing)}")
    if payload["schema_version"] != WAYPOINT_SEED_SCHEMA_VERSION:
        raise ValueError(
            "Waypoint seed payload has unsupported schema version: "
            f"{payload['schema_version']!r}"
        )

    suppliers = {item["id"] for item in payload["suppliers"]}
    scenarios = {item["id"] for item in payload["scenarios"]}
    invoices = {item["id"] for item in payload["invoices"]}
    findings = {item["id"] for item in payload["findings"]}
    evidence = {item["id"] for item in payload["evidence"]}
    contract_documents = {item["id"] for item in payload["contract_documents"]}
    policies = {item["id"] for item in payload["policies"]}

    for invoice in payload["invoices"]:
        _require_keys(
            invoice,
            {
                "id",
                "supplier_id",
                "scenario_id",
                "invoice_number",
                "invoice_date",
                "due_date",
                "status",
                "currency",
                "total_amount",
                "html_uri",
                "pdf_uri",
                "metadata",
                "lines",
                "findings",
                "evidence",
            },
            f"invoice {invoice.get('id')}",
        )
        if invoice["supplier_id"] not in suppliers:
            raise ValueError(f"Invoice {invoice['id']} references unknown supplier.")
        if invoice["scenario_id"] not in scenarios:
            raise ValueError(f"Invoice {invoice['id']} references unknown scenario.")
        line_total = sum(Decimal(str(line["amount"])) for line in invoice["lines"])
        if line_total != Decimal(str(invoice["total_amount"])):
            raise ValueError(f"Invoice {invoice['id']} line totals do not reconcile.")

    for finding in payload["findings"]:
        _require_keys(
            finding,
            {
                "id",
                "invoice_id",
                "scenario_id",
                "category",
                "severity",
                "status",
                "summary",
                "overpayment_amount",
                "evidence_ids",
                "contract_document_ids",
                "policy_ids",
                "metadata",
            },
            f"finding {finding.get('id')}",
        )
        if finding["status"] not in ALLOWED_FINDING_STATUSES:
            raise ValueError(f"Finding {finding['id']} has invalid status {finding['status']}.")
        if finding["invoice_id"] not in invoices:
            raise ValueError(f"Finding {finding['id']} references unknown invoice.")
        if finding["scenario_id"] not in scenarios:
            raise ValueError(f"Finding {finding['id']} references unknown scenario.")
        for evidence_id in finding["evidence_ids"]:
            if evidence_id not in evidence:
                raise ValueError(f"Finding {finding['id']} references unknown evidence.")
        for contract_document_id in finding["contract_document_ids"]:
            if contract_document_id not in contract_documents:
                raise ValueError(
                    f"Finding {finding['id']} references unknown contract document."
                )
        for policy_id in finding["policy_ids"]:
            if policy_id not in policies:
                raise ValueError(f"Finding {finding['id']} references unknown policy.")

    for item in payload["evidence"]:
        _require_keys(
            item,
            {"id", "title", "evidence_type", "invoice_id", "finding_id", "uri", "excerpt", "metadata"},
            f"evidence {item.get('id')}",
        )
        if item["invoice_id"] not in invoices:
            raise ValueError(f"Evidence {item['id']} references unknown invoice.")
        if item["finding_id"] not in findings:
            raise ValueError(f"Evidence {item['id']} references unknown finding.")


def waypoint_seed_counts(payload: dict[str, Any]) -> dict[str, int]:
    """Return counts for the generated Waypoint seed payload."""

    return {
        "suppliers": len(payload["suppliers"]),
        "contract_documents": len(payload["contract_documents"]),
        "policies": len(payload["policies"]),
        "scenarios": len(payload["scenarios"]),
        "invoices": len(payload["invoices"]),
        "invoice_lines": sum(len(invoice["lines"]) for invoice in payload["invoices"]),
        "findings": len(payload["findings"]),
        "evidence": len(payload["evidence"]),
        "findings_with_contract_refs": sum(
            1 for finding in payload["findings"] if finding["contract_document_ids"]
        ),
        "findings_with_policy_refs": sum(
            1 for finding in payload["findings"] if finding["policy_ids"]
        ),
        "findings_with_both_refs": sum(
            1
            for finding in payload["findings"]
            if finding["contract_document_ids"] and finding["policy_ids"]
        ),
    }


def _supplier(supplier: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": supplier["id"],
        "name": supplier["name"],
        "status": "active",
        "category": supplier["supplier_type"],
        "metadata": {
            "region": supplier["region"],
            "specialty": supplier["specialty"],
            "contract_model": supplier["contract_model"],
            "invoice_categories": supplier["invoice_categories"],
            "reconciliation_focus": supplier["reconciliation_focus"],
            "risk_examples": supplier["risk_examples"],
        },
    }


def _contract_documents(data_root: Path) -> list[dict[str, Any]]:
    documents = []
    for path in sorted((data_root / "contracts" / "source-markdown").glob("*.md")):
        supplier_id = path.name[:7]
        documents.append(
            {
                "id": f"contract-{path.stem}",
                "supplier_id": supplier_id,
                "title": _markdown_title(path),
                "document_type": "contract",
                "effective_date": "2026-01-01",
                "uri": _uri(path, data_root),
                "metadata": {
                    "source_format": "markdown",
                    "generated_docx_uri": f"data/contracts/docx/{path.with_suffix('.docx').name}",
                },
            }
        )
    return documents


def _policies(data_root: Path) -> list[dict[str, Any]]:
    policies = []
    for path in sorted((data_root / "policies" / "source-markdown").glob("*.md")):
        policies.append(
            {
                "id": f"policy-{path.stem}",
                "name": _markdown_title(path),
                "description": f"Ledgerfield policy evidence source generated from {path.name}.",
                "severity": "medium",
                "metadata": {
                    "uri": _uri(path, data_root),
                    "source_format": "markdown",
                    "generated_docx_uri": f"data/policies/docx/{path.with_suffix('.docx').name}",
                },
            }
        )
    return policies


def _contract_ids_by_supplier(
    contract_documents: list[dict[str, Any]],
) -> dict[str, list[str]]:
    contract_ids: dict[str, list[str]] = {}
    for document in contract_documents:
        contract_ids.setdefault(document["supplier_id"], []).append(document["id"])
    return contract_ids


def _waypoint_scenarios(decisions: list[dict[str, Any]], generated_at: str) -> list[dict[str, Any]]:
    scenario_ids = sorted({decision["scenario_id"] for decision in decisions})
    return [
        {
            "id": scenario_id,
            "name": _title_from_slug(scenario_id.removeprefix("wps-")),
            "description": SCENARIO_DESCRIPTIONS.get(
                scenario_id,
                f"Waypoint invoice assurance scenario group {scenario_id}.",
            ),
            "generated_at": generated_at,
            "source_uri": f"data/waypoint/invoice-decisions.json#/{scenario_id}",
            "metadata": {
                "decision_count": sum(1 for decision in decisions if decision["scenario_id"] == scenario_id),
                "source": "ledgerfield",
            },
        }
        for scenario_id in scenario_ids
    ]


def _invoice(
    decision: dict[str, Any],
    suppliers: list[dict[str, Any]],
    generated_at: str,
    index: int,
) -> dict[str, Any]:
    invoice_date = date(2026, 8, 3) + timedelta(days=index * 3)
    supplier = next(item for item in suppliers if item["id"] == decision["supplier_id"])
    invoice_id = decision["invoice_id"]
    artifact_name = f"{decision['supplier_id']}-{invoice_id.lower()}"
    return {
        "id": invoice_id,
        "supplier_id": decision["supplier_id"],
        "scenario_id": decision["scenario_id"],
        "invoice_number": invoice_id,
        "invoice_date": invoice_date.isoformat(),
        "due_date": (invoice_date + timedelta(days=45)).isoformat(),
        "status": "approved" if decision["status"] == "approved" else "open",
        "currency": "USD",
        "total_amount": _decimal_string(decision["invoice_amount"]),
        "html_uri": f"data/waypoint/invoices/html/{artifact_name}.html",
        "pdf_uri": f"data/waypoint/invoices/pdf/{artifact_name}.pdf",
        "metadata": {
            "supplier_name": supplier["name"],
            "generated_at": generated_at,
            "artifact_uri_status": "synthesized",
            "source_decision_uri": f"data/waypoint/invoice-decisions.json#/{invoice_id}",
        },
    }


def _decision_line(decision: dict[str, Any], invoice_id: str) -> dict[str, Any]:
    line = decision["line"]
    return {
        "id": f"{invoice_id}-L1",
        "invoice_id": invoice_id,
        "description": line["description"],
        "quantity": _decimal_string(line["quantity"]),
        "unit_price": _decimal_string(line["unit_price"]),
        "amount": _decimal_string(Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"]))),
        "sku": line["sku"],
        "purchase_order": line["purchase_order"],
        "metadata": {
            "line_role": "decision_line",
            "category": decision["category"],
            "source_status": decision["status"],
        },
    }


def _supporting_line(decision: dict[str, Any], invoice_id: str) -> dict[str, Any]:
    primary_amount = Decimal(str(decision["line"]["quantity"])) * Decimal(
        str(decision["line"]["unit_price"])
    )
    amount = Decimal(str(decision["invoice_amount"])) - primary_amount
    return {
        "id": f"{invoice_id}-L2",
        "invoice_id": invoice_id,
        "description": "Matched base manufacturing, packaging, quality, or logistics services",
        "quantity": "1",
        "unit_price": _decimal_string(amount),
        "amount": _decimal_string(amount),
        "sku": "BASE-SERVICE",
        "purchase_order": decision["line"]["purchase_order"] or "PO-PENDING-REVIEW",
        "metadata": {
            "line_role": "supporting_matched_line",
            "reconciliation_state": "matched",
        },
    }


def _finding(
    decision: dict[str, Any],
    contract_ids_by_supplier: dict[str, list[str]],
) -> dict[str, Any]:
    invoice_id = decision["invoice_id"]
    contract_document_ids = contract_ids_by_supplier.get(decision["supplier_id"], [])
    policy_ids = POLICY_IDS_BY_INVOICE.get(invoice_id, [])
    return {
        "id": f"finding-{invoice_id.lower()}",
        "invoice_id": invoice_id,
        "scenario_id": decision["scenario_id"],
        "category": _slug(decision["category"]),
        "severity": decision["severity"],
        "status": decision["status"],
        "summary": decision["summary"],
        "overpayment_amount": _decimal_string(decision["overpayment_amount"]),
        "evidence_ids": [],
        "contract_document_ids": contract_document_ids,
        "policy_ids": policy_ids,
        "metadata": {
            "basis_summary": BASIS_SUMMARIES_BY_INVOICE[invoice_id],
            "display_category": decision["category"],
            "source_labels": decision["sources"],
            "source_decision_uri": f"data/waypoint/invoice-decisions.json#/{invoice_id}",
        },
    }


def _evidence(decision: dict[str, Any], finding_id: str, invoice_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"ev-{invoice_id.lower()}-{index:02d}",
            "title": f"{source} evidence for {invoice_id}",
            "evidence_type": _evidence_type(source),
            "invoice_id": invoice_id,
            "finding_id": finding_id,
            "uri": _evidence_uri(source, decision),
            "excerpt": _evidence_excerpt(source, decision),
            "metadata": {
                "source_label": source,
                "supplier_id": decision["supplier_id"],
                "scenario_id": decision["scenario_id"],
            },
        }
        for index, source in enumerate(decision["sources"], start=1)
    ]


def _evidence_type(source: str) -> str:
    source_lower = source.lower()
    if "pdf" in source_lower:
        return "invoice_pdf"
    if "po" in source_lower:
        return "purchase_order"
    if "msa" in source_lower or "contract" in source_lower or "rate table" in source_lower:
        return "contract"
    if "email" in source_lower:
        return "email"
    if "portal" in source_lower or "edi" in source_lower:
        return "supplier_submission"
    return _slug(source)


def _evidence_uri(source: str, decision: dict[str, Any]) -> str:
    source_lower = source.lower()
    if "pdf" in source_lower:
        artifact_name = f"{decision['supplier_id']}-{decision['invoice_id'].lower()}"
        return f"data/waypoint/invoices/pdf/{artifact_name}.pdf"
    if "msa" in source_lower or "contract" in source_lower or "rate table" in source_lower:
        return f"data/contracts/source-markdown/{decision['supplier_id']}-*"
    return f"ledgerfield://operational-evidence/{decision['invoice_id']}/{_slug(source)}"


def _evidence_excerpt(source: str, decision: dict[str, Any]) -> str:
    return f"{source} supports review of: {decision['summary']}"


def _markdown_title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return _title_from_slug(path.stem)


def _uri(path: Path, data_root: Path) -> str:
    return str(Path("data") / path.relative_to(data_root)).replace("\\", "/")


def _generated_at(data_root: Path) -> str:
    """Resolve the seed's `generated_at` timestamp deterministically.

    Resolution order:
    1. `LEDGERFIELD_SEED_GENERATED_AT` env override (lets CI pin the value explicitly).
    2. The committed `generated_at` in `invoice-decisions.json` (the normal source of truth).
    3. Wall-clock time, only when `LEDGERFIELD_SEED_ALLOW_WALLCLOCK=1` is set.

    Falling back to wall-clock time silently would make the seed non-reproducible, so it is
    refused by default to keep CI drift checks meaningful.
    """

    override = os.environ.get("LEDGERFIELD_SEED_GENERATED_AT")
    if override:
        return override.strip()

    value = read_json(data_root / WAYPOINT_DECISIONS_RELATIVE_PATH).get("generated_at")
    if value:
        return str(value)

    if os.environ.get("LEDGERFIELD_SEED_ALLOW_WALLCLOCK") == "1":
        warnings.warn(
            "invoice-decisions.json has no 'generated_at'; using wall-clock time. "
            "The generated Waypoint seed will not be reproducible.",
            stacklevel=2,
        )
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    raise ValueError(
        "invoice-decisions.json is missing 'generated_at' and no "
        "LEDGERFIELD_SEED_GENERATED_AT override was provided. Set a pinned timestamp to keep "
        "the Waypoint seed reproducible, or set LEDGERFIELD_SEED_ALLOW_WALLCLOCK=1 to opt into "
        "non-deterministic wall-clock time."
    )


def _require_keys(item: dict[str, Any], keys: set[str], label: str) -> None:
    missing = keys - item.keys()
    if missing:
        raise ValueError(f"{label} is missing keys: {sorted(missing)}")


def _title_from_slug(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").title()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _decimal_string(value: Any) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"
