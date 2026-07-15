"""Generate supplier-specific invoice HTML and PDFs from canonical JSON seeds."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .paths import data_path, read_json, repo_root

InvoiceOutput = Literal["html", "pdf", "both"]


def _money(value: Any) -> str:
    return f"${Decimal(str(value)):,.2f}"


@dataclass(frozen=True)
class GeneratedInvoiceDocument:
    invoice_id: str
    supplier_id: str
    html_path: Path | None
    pdf_path: Path | None


@dataclass(frozen=True)
class InvoicePdfInfo:
    path: Path
    page_count: int
    media_boxes: tuple[str, ...]
    is_letter: bool


THEMES: dict[str, dict[str, str]] = {
    "production": {
        "accent": "#2563eb",
        "accent_light": "#dbeafe",
        "font": "Arial, Helvetica, sans-serif",
        "layout": "summary-band",
    },
    "quality": {
        "accent": "#7c3aed",
        "accent_light": "#ede9fe",
        "font": "Georgia, 'Times New Roman', serif",
        "layout": "certified",
    },
    "logistics": {
        "accent": "#0891b2",
        "accent_light": "#cffafe",
        "font": "'Trebuchet MS', Arial, sans-serif",
        "layout": "logistics",
    },
    "materials": {
        "accent": "#0f766e",
        "accent_light": "#ccfbf1",
        "font": "Verdana, Geneva, sans-serif",
        "layout": "materials-ledger",
    },
    "milestone": {
        "accent": "#ca8a04",
        "accent_light": "#fef3c7",
        "font": "Arial, Helvetica, sans-serif",
        "layout": "milestone",
    },
    "capacity": {
        "accent": "#be123c",
        "accent_light": "#ffe4e6",
        "font": "'Segoe UI', Arial, sans-serif",
        "layout": "capacity",
    },
}

PROFILE_THEMES: dict[str, str] = {
    "tablet-production-summary": "production",
    "sterile-fill-finish-quality": "quality",
    "packaging-serialization-logistics": "logistics",
    "api-lot-materials": "materials",
    "gmp-lab-milestones": "milestone",
    "oral-dose-campaign": "production",
    "clinical-supply-study": "milestone",
    "cold-chain-shipment": "logistics",
    "biologics-capacity-quarterly": "capacity",
    "combination-device-build": "production",
    "sterilization-release-packet": "quality",
    "process-development-milestone": "milestone",
    "regulated-materials-supply": "materials",
    "regional-manufacturing-trueup": "production",
    "regulatory-services-country": "milestone",
}

SUPPLIER_BRANDS: dict[str, dict[str, str]] = {
    "sup-001": {
        "logo": "ARB",
        "invoice_label": "Manufacturing Invoice",
        "accent": "#1d4ed8",
        "accent_light": "#dbeafe",
        "font": "Arial, Helvetica, sans-serif",
        "border": "solid",
        "paper": "#ffffff",
        "header_background": "linear-gradient(135deg, #1d4ed8, #60a5fa)",
        "document_phrase": "Batch production and packaging billing statement",
        "payment_terms": "Net 45 after sponsor QA acceptance and receipt reconciliation.",
        "style_family": "modern-block",
    },
    "sup-002": {
        "logo": "NFF",
        "invoice_label": "Quality Release Invoice",
        "accent": "#6d28d9",
        "accent_light": "#ede9fe",
        "font": "Georgia, 'Times New Roman', serif",
        "border": "double",
        "paper": "#fdfcff",
        "header_background": "linear-gradient(135deg, #4c1d95, #8b5cf6)",
        "document_phrase": "Sterile fill-finish quality billing certificate",
        "payment_terms": "Net 45 after released batch and QA record verification.",
        "style_family": "letterhead",
    },
    "sup-003": {
        "logo": "HP",
        "invoice_label": "Packaging & Serialization Invoice",
        "accent": "#0891b2",
        "accent_light": "#cffafe",
        "font": "'Trebuchet MS', Arial, sans-serif",
        "border": "solid",
        "paper": "#f8feff",
        "header_background": "linear-gradient(135deg, #0e7490, #22d3ee)",
        "document_phrase": "EU packaging, label, serialization, and shipment statement",
        "payment_terms": "Net 45 after SKU, serial count, and shipment receipt validation.",
        "style_family": "sidebar",
    },
    "sup-004": {
        "logo": "MAW",
        "invoice_label": "API Lot Invoice",
        "accent": "#0f766e",
        "accent_light": "#ccfbf1",
        "font": "Verdana, Geneva, sans-serif",
        "border": "solid",
        "paper": "#fbfffd",
        "header_background": "linear-gradient(135deg, #115e59, #2dd4bf)",
        "document_phrase": "API lot yield, raw material, and certificate billing ledger",
        "payment_terms": "Net 45 after released lot yield and certificate of analysis match.",
        "style_family": "ledger",
    },
    "sup-005": {
        "logo": "CGL",
        "invoice_label": "GMP Laboratory Invoice",
        "accent": "#a16207",
        "accent_light": "#fef3c7",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "dashed",
        "paper": "#fffdf5",
        "header_background": "linear-gradient(135deg, #92400e, #facc15)",
        "document_phrase": "Release testing, stability, validation, and documentation charges",
        "payment_terms": "Net 45 after protocol, sample receipt, and milestone acceptance review.",
        "style_family": "service-detail",
    },
    "sup-006": {
        "logo": "SDM",
        "invoice_label": "Capsule Campaign Invoice",
        "accent": "#2563eb",
        "accent_light": "#eff6ff",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "solid",
        "paper": "#f8fbff",
        "header_background": "linear-gradient(90deg, #1e40af, #3b82f6)",
        "document_phrase": "Oral solid dose campaign, MOQ, cleaning, and changeover billing",
        "payment_terms": "Net 45 after released capsule quantity and campaign evidence match.",
        "style_family": "compact",
    },
    "sup-007": {
        "logo": "OCS",
        "invoice_label": "Clinical Supply Invoice",
        "accent": "#c2410c",
        "accent_light": "#ffedd5",
        "font": "Arial, Helvetica, sans-serif",
        "border": "dotted",
        "paper": "#fffaf6",
        "header_background": "linear-gradient(135deg, #9a3412, #fb923c)",
        "document_phrase": "Study protocol, kit, comparator, storage, and milestone billing",
        "payment_terms": "Net 45 after clinical supply lead approval and protocol reconciliation.",
        "style_family": "statement",
    },
    "sup-008": {
        "logo": "VCL",
        "invoice_label": "Cold Chain Logistics Invoice",
        "accent": "#0369a1",
        "accent_light": "#e0f2fe",
        "font": "'Trebuchet MS', Arial, sans-serif",
        "border": "solid",
        "paper": "#f7fcff",
        "header_background": "linear-gradient(135deg, #075985, #38bdf8)",
        "document_phrase": "Temperature-controlled shipment, lane, excursion, and customs bill",
        "payment_terms": "Net 45 after delivery confirmation, lane approval, and temperature log review.",
        "style_family": "logistics-form",
    },
    "sup-009": {
        "logo": "BPB",
        "invoice_label": "Capacity Reservation Invoice",
        "accent": "#be123c",
        "accent_light": "#ffe4e6",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "double",
        "paper": "#fff8fa",
        "header_background": "linear-gradient(135deg, #9f1239, #fb7185)",
        "document_phrase": "Quarterly biologics reserved capacity and utilization statement",
        "payment_terms": "Net 45 after run log, utilization, and prior-credit reconciliation.",
        "style_family": "statement",
    },
    "sup-010": {
        "logo": "KDA",
        "invoice_label": "Device Build Invoice",
        "accent": "#334155",
        "accent_light": "#e2e8f0",
        "font": "Arial, Helvetica, sans-serif",
        "border": "solid",
        "paper": "#fbfcfd",
        "header_background": "linear-gradient(135deg, #0f172a, #64748b)",
        "document_phrase": "Combination device assembly, inspection, scrap, and ECO statement",
        "payment_terms": "Net 45 after accepted unit count and final inspection match.",
        "style_family": "industrial",
    },
    "sup-011": {
        "logo": "LSS",
        "invoice_label": "Sterilization Release Invoice",
        "accent": "#7c3aed",
        "accent_light": "#f3e8ff",
        "font": "Georgia, 'Times New Roman', serif",
        "border": "double",
        "paper": "#fdfaff",
        "header_background": "linear-gradient(135deg, #581c87, #c084fc)",
        "document_phrase": "Sterilization cycle, monitoring, bioburden, and release packet bill",
        "payment_terms": "Net 45 after release packet completion and sponsor QA acceptance.",
        "style_family": "letterhead",
    },
    "sup-012": {
        "logo": "PPD",
        "invoice_label": "Process Development Invoice",
        "accent": "#ca8a04",
        "accent_light": "#fef9c3",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "dashed",
        "paper": "#fffef2",
        "header_background": "linear-gradient(135deg, #854d0e, #eab308)",
        "document_phrase": "Tech transfer, protocol, engineering batch, and validation billing",
        "payment_terms": "Net 45 after deliverable acceptance and milestone evidence review.",
        "style_family": "milestone",
    },
    "sup-013": {
        "logo": "EE",
        "invoice_label": "Materials Supply Invoice",
        "accent": "#15803d",
        "accent_light": "#dcfce7",
        "font": "Verdana, Geneva, sans-serif",
        "border": "solid",
        "paper": "#f8fff9",
        "header_background": "linear-gradient(135deg, #166534, #4ade80)",
        "document_phrase": "Excipient lot, surcharge, rush order, and quality documentation bill",
        "payment_terms": "Net 45 after lot receipt, unit cost, and escalation-cap review.",
        "style_family": "ledger",
    },
    "sup-014": {
        "logo": "ARM",
        "invoice_label": "Regional Manufacturing Invoice",
        "accent": "#b45309",
        "accent_light": "#ffedd5",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "solid",
        "paper": "#fffaf2",
        "header_background": "linear-gradient(135deg, #92400e, #f59e0b)",
        "document_phrase": "LATAM production, capacity reservation, packaging, and true-up statement",
        "payment_terms": "Net 45 after released volume, active forecast, and capacity review.",
        "style_family": "regional",
    },
    "sup-015": {
        "logo": "SRR",
        "invoice_label": "Regulatory Services Invoice",
        "accent": "#4338ca",
        "accent_light": "#e0e7ff",
        "font": "'Segoe UI', Arial, sans-serif",
        "border": "dotted",
        "paper": "#fbfbff",
        "header_background": "linear-gradient(135deg, #3730a3, #818cf8)",
        "document_phrase": "Country scope, submission, authority response, and translation bill",
        "payment_terms": "Net 45 after country scope and authority evidence confirmation.",
        "style_family": "service-detail",
    },
}

def generate_invoice_documents(
    root: Path | None = None,
    *,
    output: InvoiceOutput = "both",
    html_folder: Path | None = None,
    pdf_folder: Path | None = None,
) -> list[GeneratedInvoiceDocument]:
    """Generate invoice HTML and optionally PDF documents."""

    root = root or repo_root()
    data_root = data_path(root)
    invoice_doc = read_json(data_root / "invoices" / "supplier-invoices.json")
    profiles = {
        profile["supplier_id"]: profile for profile in invoice_doc["document_generation"]["profiles"]
    }
    html_folder = html_folder or data_root / "invoices" / "html"
    pdf_folder = pdf_folder or data_root / "invoices" / "pdf"

    if output in {"html", "both"}:
        html_folder.mkdir(parents=True, exist_ok=True)
    if output in {"pdf", "both"}:
        pdf_folder.mkdir(parents=True, exist_ok=True)

    environment = template_environment()
    html_paths: list[Path] = []
    generated: list[GeneratedInvoiceDocument] = []

    for invoice in invoice_doc["invoices"]:
        profile = profiles[invoice["supplier_id"]]
        supplier = supplier_by_id(invoice["supplier_id"], root)
        theme = _theme_for_invoice(invoice["supplier_id"], profile)
        template = environment.get_template(_invoice_template_name(invoice["supplier_id"]))
        filename_stem = f"{invoice['supplier_id']}-{invoice['invoice_id'].lower()}"
        html_path = html_folder / f"{filename_stem}.html"
        pdf_path = pdf_folder / f"{filename_stem}.pdf"
        rendered = template.render(
            invoice=invoice,
            profile=profile,
            supplier=supplier,
            theme=theme,
            money=_money,
            quantity=_quantity,
            support_label=_support_label,
        )

        written_html_path: Path | None = None
        written_pdf_path: Path | None = None
        if output in {"html", "both"}:
            html_path.write_text(rendered, encoding="utf-8")
            html_paths.append(html_path)
            written_html_path = html_path
        elif output == "pdf":
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(rendered, encoding="utf-8")
            html_paths.append(html_path)

        if output in {"pdf", "both"}:
            written_pdf_path = pdf_path

        generated.append(
            GeneratedInvoiceDocument(
                invoice_id=invoice["invoice_id"],
                supplier_id=invoice["supplier_id"],
                html_path=written_html_path,
                pdf_path=written_pdf_path,
            )
        )

    if output in {"pdf", "both"}:
        _render_pdfs(html_paths, pdf_folder)
        if output == "pdf":
            for path in html_paths:
                path.unlink()

    return generated


def _quantity(value: Any) -> str:
    decimal = Decimal(str(value))
    if decimal == decimal.to_integral():
        return f"{int(decimal):,}"
    return f"{decimal:,.4f}".rstrip("0").rstrip(".")


def _support_label(line_type: str) -> str:
    normalized = line_type.lower()
    if "production" in normalized or "batch" in normalized or "capacity" in normalized:
        return "Batch / run record"
    if "testing" in normalized or "release" in normalized or "certificate" in normalized:
        return "QA support"
    if "shipment" in normalized or "logistics" in normalized or "customs" in normalized:
        return "Shipment support"
    if "serialization" in normalized or "label" in normalized or "packaging" in normalized or "carton" in normalized:
        return "Packaging support"
    if "material" in normalized or "api" in normalized or "excipient" in normalized:
        return "Material support"
    if "milestone" in normalized or "protocol" in normalized or "regulatory" in normalized:
        return "Milestone support"
    if "device" in normalized or "inspection" in normalized or "scrap" in normalized:
        return "Build support"
    return "Support ref"


def _render_pdfs(html_paths: list[Path], pdf_folder: Path) -> None:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency managed by uv
        raise RuntimeError("Install dependencies with `uv sync` before generating PDFs.") from exc

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as exc:  # pragma: no cover - depends on local browser install
            raise RuntimeError(
                "Playwright Chromium is not installed. Run "
                "`uv run playwright install chromium` and try again."
            ) from exc
        try:
            page = browser.new_page()
            for html_path in html_paths:
                page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
                page.pdf(
                    path=str(pdf_folder / f"{html_path.stem}.pdf"),
                    format="Letter",
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
        finally:
            browser.close()


def inspect_invoice_pdfs(root: Path | None = None, pdf_folder: Path | None = None) -> list[InvoicePdfInfo]:
    """Inspect generated invoice PDFs for page count and Letter page size."""

    import re

    root = root or repo_root()
    pdf_folder = pdf_folder or data_path(root) / "invoices" / "pdf"
    infos: list[InvoicePdfInfo] = []
    for path in sorted(pdf_folder.glob("*.pdf")):
        text = path.read_bytes().decode("latin-1", errors="ignore")
        pages = len(re.findall(r"/Type\s*/Page\b", text))
        boxes = re.findall(
            r"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]",
            text,
        )
        dimensions: list[str] = []
        is_letter = pages == 1 and bool(boxes)
        for box in boxes:
            x0, y0, x1, y1 = map(float, box)
            width = x1 - x0
            height = y1 - y0
            dimensions.append(f"{width:g}x{height:g}")
            if abs(width - 612.0) > 0.5 or abs(height - 792.0) > 0.5:
                is_letter = False
        infos.append(
            InvoicePdfInfo(
                path=path,
                page_count=pages,
                media_boxes=tuple(sorted(set(dimensions))),
                is_letter=is_letter,
            )
        )
    return infos


def preview_html_snippet(root: Path | None = None) -> str:
    """Render one invoice to an HTML string for lightweight validation."""

    root = root or repo_root()
    return render_invoice_html("INV-SUP-001-2026-10", root)


def render_invoice_html(invoice_id: str, root: Path | None = None) -> str:
    """Render one invoice to HTML without writing a generated file."""

    root = root or repo_root()
    invoice_doc = load_invoice_document(root)
    invoice = invoice_by_id(invoice_doc, invoice_id)
    profile = profile_by_supplier_id(invoice_doc, invoice["supplier_id"])
    supplier = supplier_by_id(invoice["supplier_id"], root)
    theme = _theme_for_invoice(invoice["supplier_id"], profile)
    environment = template_environment()
    template = environment.get_template(_invoice_template_name(invoice["supplier_id"]))
    return template.render(
        invoice=invoice,
        profile=profile,
        supplier=supplier,
        theme=theme,
        money=_money,
        quantity=_quantity,
        support_label=_support_label,
    )


def load_invoice_document(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    return read_json(data_path(root) / "invoices" / "supplier-invoices.json")


def invoice_by_id(invoice_doc: dict[str, Any], invoice_id: str) -> dict[str, Any]:
    for invoice in invoice_doc["invoices"]:
        if invoice["invoice_id"] == invoice_id:
            return invoice
    raise KeyError(f"Invoice not found: {invoice_id}")


def profile_by_supplier_id(invoice_doc: dict[str, Any], supplier_id: str) -> dict[str, Any]:
    for profile in invoice_doc["document_generation"]["profiles"]:
        if profile["supplier_id"] == supplier_id:
            return profile
    raise KeyError(f"Invoice profile not found for supplier: {supplier_id}")


def supplier_by_id(supplier_id: str, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    for supplier in read_json(data_path(root) / "suppliers" / "suppliers.json"):
        if supplier["id"] == supplier_id:
            return supplier
    raise KeyError(f"Supplier not found: {supplier_id}")


def template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _invoice_template_name(supplier_id: str) -> str:
    template_path = Path(__file__).resolve().parent / "templates" / "invoices" / f"{supplier_id}.html.j2"
    if template_path.exists():
        return f"invoices/{supplier_id}.html.j2"
    return "invoices/base.html.j2"


def _theme_for_invoice(supplier_id: str, profile: dict[str, Any]) -> dict[str, str]:
    theme = THEMES[PROFILE_THEMES[profile["profile_id"]]].copy()
    theme.update(SUPPLIER_BRANDS[supplier_id])
    return theme
