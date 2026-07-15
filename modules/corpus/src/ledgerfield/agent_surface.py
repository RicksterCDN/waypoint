"""Agent-facing discovery and bootstrap helpers for Ledgerfield."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .docx_docs import generate_docx_documents
from .invoice_docs import generate_invoice_documents, inspect_invoice_pdfs
from .paths import data_path, repo_root
from .waypoint_seed import generate_waypoint_seed


def agent_manifest() -> dict[str, Any]:
    """Return stable commands and endpoints for consuming apps and agents."""

    endpoints = {
        "preview": "/preview",
        "manifest": "/api/agent/manifest",
        "doctor": "/api/agent/doctor",
        "bootstrap": "/api/agent/bootstrap",
        "invoices": "/api/invoices",
        "invoice_html": "/api/invoices/{invoice_id}/html",
        "invoice_pdf": "/api/invoices/{invoice_id}/pdf",
        "db_status": "/api/db/status",
        "db_seed": "/api/db/seed",
        "db_append": "/api/db/append",
    }
    return {
        "name": "ledgerfield",
        "purpose": "Canonical Waypoint demo corpus, artifact generator, and local seed database tooling.",
        "source_of_truth": {
            "suppliers": "data/suppliers/suppliers.json",
            "invoices": "data/invoices/supplier-invoices.json",
            "scenarios": "data/scenarios/invoice-assurance-scenarios.json",
            "contracts": "data/contracts/source-markdown",
            "policies": "data/policies/source-markdown",
        },
        "commands": {
            "setup": "uv run ledgerfield setup",
            "generate_all": "uv run ledgerfield generate-all",
            "generate_waypoint_seed": "uv run ledgerfield generate-waypoint-seed",
            "doctor": "uv run ledgerfield doctor",
            "serve": "uv run ledgerfield serve",
            "seed_db": "uv run ledgerfield db seed --if-needed --append-cycles 1",
        },
        "endpoints": endpoints,
        "api": endpoints,
        "generated_artifacts": {
            "invoice_html": "data/invoices/html",
            "invoice_pdf": "data/invoices/pdf",
            "contract_docx": "data/contracts/docx",
            "policy_docx": "data/policies/docx",
            "waypoint_seed": "data/waypoint/waypoint-seed.json",
        },
    }


def artifact_status(root: Path | None = None) -> dict[str, Any]:
    """Return source and generated artifact counts for validation and agents."""

    root = root or repo_root()
    data_root = data_path(root)
    pdfs = inspect_invoice_pdfs(root)
    return {
        "sources": {
            "suppliers": len(_glob(data_root / "suppliers", "*.json")),
            "contract_markdown": len(_glob(data_root / "contracts" / "source-markdown", "*.md")),
            "policy_markdown": len(_glob(data_root / "policies" / "source-markdown", "*.md")),
            "scenario_files": len(_glob(data_root / "scenarios", "*.json")),
            "invoice_files": len(_glob(data_root / "invoices", "supplier-invoices.json")),
            "waypoint_decision_files": len(_glob(data_root / "waypoint", "invoice-decisions.json")),
        },
        "generated": {
            "invoice_html": len(_glob(data_root / "invoices" / "html", "sup-*.html")),
            "invoice_pdf": len(pdfs),
            "contract_docx": len(_glob(data_root / "contracts" / "docx", "*.docx")),
            "policy_docx": len(_glob(data_root / "policies" / "docx", "*.docx")),
            "waypoint_seed": len(_glob(data_root / "waypoint", "waypoint-seed.json")),
        },
        "pdfs": [
            {
                "path": str(info.path),
                "page_count": info.page_count,
                "media_boxes": list(info.media_boxes),
                "is_letter": info.is_letter,
            }
            for info in pdfs
        ],
    }


def generate_all_artifacts(root: Path | None = None) -> dict[str, Any]:
    """Generate all local demo artifacts from canonical sources."""

    root = root or repo_root()
    invoices = generate_invoice_documents(root, output="both")
    docx = generate_docx_documents(root)
    waypoint_seed = generate_waypoint_seed(root)
    return {
        "invoice_documents": len(invoices),
        "docx_documents": len(docx),
        "waypoint_seed": waypoint_seed,
        "status": artifact_status(root),
    }


def _glob(folder: Path, pattern: str) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob(pattern))
