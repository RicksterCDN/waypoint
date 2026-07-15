"""Upload the Ledgerfield corpus into a Waypoint Microsoft Fabric OneLake lakehouse.

This is the *writer* side of the contract Waypoint's ``api/app/common/onelake.py`` reads against:

* **Files** (unstructured documents) are written to ``{lakehouse}.Lakehouse/Files/corpus/...``
  via the ADLS Gen2 (``azure-storage-file-datalake``) API against the OneLake DFS endpoint, using
  ``DefaultAzureCredential`` so the same code path works for a CI OIDC identity and a developer's
  ``az login``. The path construction mirrors Waypoint's reader exactly so document resolution
  (which keys off the URI *basename*) lines up.
* **Tables** (Delta format) are written to ``{lakehouse}.Lakehouse/Tables/{table}`` with the
  ``deltalake`` (delta-rs) library against the OneLake ``abfss://`` endpoint, using a bearer token
  from ``DefaultAzureCredential``. Columns mirror Waypoint's ``records/schemas.py`` so a future
  Fabric SQL agent can query them.

Unlike the Waypoint reader (which degrades gracefully to URI-only metadata), this uploader is a
deploy step: any failure raises so the CLI exits non-zero and the umbrella pipeline stops.

Heavy Azure/Delta dependencies are imported lazily inside the functions that need them, so the
document-generation and database CLI commands keep working without them installed.
"""

from __future__ import annotations

import html
import logging
import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .paths import data_path, repo_root
from .waypoint_seed import build_waypoint_seed, validate_waypoint_seed

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"
DEFAULT_CORPUS_PREFIX = "Files/corpus"

# Token scope for delta-rs writes against OneLake. The deploy identity (a Fabric workspace Member)
# presents this storage-plane token; OneLake honors it for Tables writes.
_DELTA_TOKEN_SCOPE = "https://storage.azure.com/.default"

# Token scope for the Fabric control-plane REST API (used to resolve a lakehouse display name to
# its GUID). The deploy identity is a workspace Member, which can list lakehouses.
_FABRIC_API_SCOPE = "https://api.fabric.microsoft.com/.default"
_FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_guid(value: str) -> bool:
    return bool(_GUID_RE.match(value.strip()))


def _resolve_lakehouse_segment(workspace: str, lakehouse: str) -> str:
    """Return the OneLake path segment for the lakehouse.

    OneLake DFS/abfss paths accept either friendly names (``<name>.Lakehouse``) or bare GUIDs.
    Many tenants disable friendly-name support (the DFS endpoint then returns
    ``FriendlyNameSupportDisabled``), so we resolve the lakehouse to its GUID and use the bare
    GUID segment, which is accepted regardless of the tenant setting.

    Resolution order:
      1. If ``lakehouse`` is already a GUID, use it as-is.
      2. Otherwise look it up by ``displayName`` via the Fabric REST API (requires ``workspace``
         to be a GUID, which it is when provisioned by Waypoint).
      3. If lookup fails, fall back to the friendly ``<name>.Lakehouse`` segment so tenants that
         still allow friendly names keep working (with a warning).
    """

    if _is_guid(lakehouse):
        return lakehouse

    if _is_guid(workspace):
        try:
            lakehouse_id = _lookup_lakehouse_id(workspace, lakehouse)
        except Exception as exc:  # pragma: no cover - network/permission failures
            logger.warning(
                "Could not resolve lakehouse '%s' to a GUID via Fabric REST (%s); "
                "falling back to friendly-name path.",
                lakehouse,
                exc,
            )
            lakehouse_id = None
        if lakehouse_id:
            logger.info("Resolved lakehouse '%s' -> %s", lakehouse, lakehouse_id)
            return lakehouse_id
    else:
        logger.warning(
            "Workspace '%s' is not a GUID; cannot resolve lakehouse GUID via Fabric REST. "
            "Falling back to friendly-name path.",
            workspace,
        )

    return f"{lakehouse}.Lakehouse"


def _lookup_lakehouse_id(workspace: str, lakehouse: str) -> str | None:
    """Look up a lakehouse GUID by display name within a workspace (Fabric REST)."""

    import json
    import urllib.request

    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(_FABRIC_API_SCOPE).token
    headers = {"Authorization": f"Bearer {token}"}
    url: str | None = f"{_FABRIC_API_BASE}/workspaces/{workspace}/lakehouses"
    target = lakehouse.strip().casefold()

    while url:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - trusted Fabric URL
            body = json.loads(resp.read().decode("utf-8"))
        for item in body.get("value", []):
            if str(item.get("displayName", "")).strip().casefold() == target:
                return item.get("id")
        url = body.get("continuationUri") or None

    return None

# Money/quantity precision for Delta numeric columns. Wide enough for any seed value; a Fabric SQL
# agent can cast down as needed.
_DECIMAL_PRECISION = 20
_DECIMAL_SCALE = 4


@dataclass(frozen=True)
class FileUpload:
    """One document to place in the lakehouse ``Files`` area."""

    data: bytes
    # Lakehouse-relative destination directory under the corpus prefix, e.g. ``contracts``.
    subdir: str
    name: str


@dataclass
class UploadSummary:
    """Counts returned to the CLI for human/agent-readable logging."""

    workspace: str
    lakehouse: str
    corpus_prefix: str
    files: dict[str, int] = field(default_factory=dict)
    tables: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "lakehouse": self.lakehouse,
            "corpus_prefix": self.corpus_prefix,
            "files": dict(sorted(self.files.items())),
            "files_total": sum(self.files.values()),
            "tables": dict(sorted(self.tables.items())),
            "tables_total": sum(self.tables.values()),
        }


# --------------------------------------------------------------------------------------
# Artifact discovery (on-disk -> Files/corpus mapping)
# --------------------------------------------------------------------------------------

# Each entry maps an on-disk source folder + glob to a lakehouse ``Files/corpus`` subdirectory.
# Filenames are preserved verbatim so they match the basenames Waypoint's reader resolves against.
# Invoice HTML/PDF are intentionally *not* here: the canonical invoice set Waypoint imports comes
# from the Waypoint seed (``invoice-decisions.json``), whose ids/basenames differ from the
# ``supplier-invoices.json`` set that ``generate-invoices`` renders. Invoice documents are instead
# rendered from the seed payload (see ``_render_invoice_files``) so their basenames line up.
_FILE_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("contracts/source-markdown", "*.md", "contracts"),
    ("contracts/docx", "*.docx", "contracts"),
    ("policies/source-markdown", "*.md", "policies"),
    ("policies/docx", "*.docx", "policies"),
)


def _collect_document_files(data_root: Path) -> Iterator[FileUpload]:
    for relative_folder, pattern, subdir in _FILE_SOURCES:
        folder = data_root / relative_folder
        if not folder.exists():
            logger.warning("OneLake upload: source folder missing, skipping: %s", folder)
            continue
        for path in sorted(folder.glob(pattern)):
            yield FileUpload(data=path.read_bytes(), subdir=subdir, name=path.name)


def _seed_file(data_root: Path, payload: dict[str, Any]) -> FileUpload:
    import json

    seed_path = data_root / "waypoint" / "waypoint-seed.json"
    if seed_path.exists():
        data = seed_path.read_bytes()
    else:
        data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    return FileUpload(data=data, subdir="seed", name="waypoint-seed.json")


def _evidence_files(payload: dict[str, Any]) -> Iterator[FileUpload]:
    """Synthesize one Markdown document per evidence reference.

    Ledgerfield evidence is metadata + an excerpt rather than an on-disk file, and its seed URIs
    are not basename-unique. We synthesize ``evidence/{evidence_id}.md`` (ids are unique) so the
    lake has resolvable evidence documents alongside the ``evidence_references`` Delta table.
    """

    for item in payload.get("evidence", []):
        metadata = item.get("metadata", {})
        lines = [
            f"# {item['title']}",
            "",
            f"- Evidence ID: {item['id']}",
            f"- Type: {item.get('evidence_type', 'document')}",
            f"- Invoice: {item.get('invoice_id', '')}",
            f"- Finding: {item.get('finding_id', '')}",
            f"- Source URI: {item.get('uri', '')}",
            f"- Source label: {metadata.get('source_label', '')}",
            f"- Supplier: {metadata.get('supplier_id', '')}",
            f"- Scenario: {metadata.get('scenario_id', '')}",
            "",
            "## Excerpt",
            "",
            item.get("excerpt", ""),
            "",
        ]
        data = ("\n".join(lines)).encode("utf-8")
        yield FileUpload(data=data, subdir="evidence", name=f"{item['id']}.md")


def _basename(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _money(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"${Decimal(str(value)):,.2f}"


def _invoice_html(invoice: dict[str, Any]) -> str:
    """Render a self-contained, supplier-facing invoice document from a seed invoice.

    Uses only invoice header + line items + total. Decision/finding state is deliberately excluded
    to keep the rendered document supplier-facing-safe.
    """

    metadata = invoice.get("metadata", {})
    supplier_name = metadata.get("supplier_name", invoice.get("supplier_id", ""))
    rows = []
    for line in invoice.get("lines", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(line.get('description', '')))}</td>"
            f"<td>{html.escape(str(line.get('sku') or ''))}</td>"
            f"<td>{html.escape(str(line.get('purchase_order') or ''))}</td>"
            f"<td class='num'>{html.escape(str(line.get('quantity', '')))}</td>"
            f"<td class='num'>{_money(line.get('unit_price'))}</td>"
            f"<td class='num'>{_money(line.get('amount'))}</td>"
            "</tr>"
        )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(invoice['invoice_number'])}</title>"
        "<style>"
        "body{font-family:Aptos,Segoe UI,Arial,sans-serif;color:#1b1b1b;margin:48px;font-size:12px}"
        "h1{font-size:22px;margin:0 0 4px}.muted{color:#555}"
        ".head{display:flex;justify-content:space-between;margin-bottom:24px}"
        "table{width:100%;border-collapse:collapse;margin-top:16px}"
        "th,td{border-bottom:1px solid #ddd;padding:8px;text-align:left}"
        ".num{text-align:right}tfoot td{font-weight:bold;border-top:2px solid #333}"
        "</style></head><body>"
        "<div class='head'><div>"
        f"<h1>Invoice {html.escape(invoice['invoice_number'])}</h1>"
        f"<div class='muted'>{html.escape(supplier_name)} &middot; "
        f"{html.escape(invoice.get('supplier_id',''))}</div>"
        "</div><div class='muted'>"
        f"Invoice date: {html.escape(str(invoice.get('invoice_date') or ''))}<br>"
        f"Due date: {html.escape(str(invoice.get('due_date') or ''))}<br>"
        f"Status: {html.escape(str(invoice.get('status') or ''))}"
        "</div></div>"
        "<table><thead><tr>"
        "<th>Description</th><th>SKU</th><th>PO</th>"
        "<th class='num'>Qty</th><th class='num'>Unit price</th><th class='num'>Amount</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody><tfoot><tr>"
        f"<td colspan='5'>Total ({html.escape(invoice.get('currency','USD'))})</td>"
        f"<td class='num'>{_money(invoice.get('total_amount'))}</td>"
        "</tr></tfoot></table></body></html>"
    )


def _render_invoice_files(payload: dict[str, Any], *, include_pdf: bool) -> list[FileUpload]:
    """Render invoice HTML (and optionally PDF) from the seed, basename-matched to seed URIs."""

    from .invoice_docs import _render_pdfs

    invoices = payload["invoices"]
    uploads: list[FileUpload] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        html_dir = tmp_path / "html"
        pdf_dir = tmp_path / "pdf"
        html_dir.mkdir()
        pdf_dir.mkdir()

        html_paths: list[Path] = []
        for invoice in invoices:
            name = _basename(invoice.get("html_uri")) or f"{invoice['id'].lower()}.html"
            html_path = html_dir / name
            html_path.write_text(_invoice_html(invoice), encoding="utf-8")
            html_paths.append(html_path)
            uploads.append(
                FileUpload(
                    data=html_path.read_bytes(), subdir="invoices/html", name=name
                )
            )

        if include_pdf:
            _render_pdfs(html_paths, pdf_dir)
            for invoice in invoices:
                pdf_name = _basename(invoice.get("pdf_uri")) or f"{invoice['id'].lower()}.pdf"
                # _render_pdfs writes "{html stem}.pdf"; map by html basename stem.
                html_name = _basename(invoice.get("html_uri")) or f"{invoice['id'].lower()}.html"
                rendered = pdf_dir / f"{Path(html_name).stem}.pdf"
                uploads.append(
                    FileUpload(
                        data=rendered.read_bytes(), subdir="invoices/pdf", name=pdf_name
                    )
                )

    return uploads


# --------------------------------------------------------------------------------------
# Files upload (ADLS Gen2 over the OneLake DFS endpoint)
# --------------------------------------------------------------------------------------


def _file_system_client(account_url: str, workspace: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    service_client = DataLakeServiceClient(
        account_url=account_url,
        credential=DefaultAzureCredential(),
    )
    return service_client.get_file_system_client(workspace)


def _ensure_directory(filesystem, path: str, created: set[str]) -> None:
    import contextlib

    from azure.core.exceptions import ResourceExistsError

    if path in created:
        return
    with contextlib.suppress(ResourceExistsError):
        filesystem.get_directory_client(path).create_directory()
    created.add(path)


def _upload_files(
    filesystem,
    lakehouse_segment: str,
    corpus_prefix: str,
    uploads: Iterable[FileUpload],
) -> dict[str, int]:
    prefix = corpus_prefix.strip("/")
    counts: dict[str, int] = {}
    created_dirs: set[str] = set()

    for upload in uploads:
        directory = f"{lakehouse_segment}/{prefix}/{upload.subdir}".rstrip("/")
        _ensure_directory(filesystem, directory, created_dirs)
        file_path = f"{directory}/{upload.name}"
        file_client = filesystem.get_file_client(file_path)
        file_client.upload_data(upload.data, overwrite=True)
        counts[upload.subdir] = counts.get(upload.subdir, 0) + 1
        logger.info("Uploaded Files/%s/%s/%s", prefix, upload.subdir, upload.name)

    return counts


# --------------------------------------------------------------------------------------
# Delta tables (delta-rs over the OneLake abfss endpoint)
# --------------------------------------------------------------------------------------


def _decimal(value: Any):
    if value is None or value == "":
        return None
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-_DECIMAL_SCALE))


def _date(value: Any):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _build_tables(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``{table_name: pyarrow.Table}`` mirroring Waypoint's record schemas."""

    import pyarrow as pa

    money = pa.decimal128(_DECIMAL_PRECISION, _DECIMAL_SCALE)
    str_list = pa.list_(pa.string())

    invoices = payload["invoices"]

    invoices_table = pa.table(
        {
            "id": [i["id"] for i in invoices],
            "supplier_id": [i["supplier_id"] for i in invoices],
            "scenario_id": [i.get("scenario_id") for i in invoices],
            "invoice_number": [i["invoice_number"] for i in invoices],
            "invoice_date": pa.array([_date(i.get("invoice_date")) for i in invoices], pa.date32()),
            "status": [i.get("status") for i in invoices],
            "currency": [i.get("currency") for i in invoices],
            "total_amount": pa.array([_decimal(i.get("total_amount")) for i in invoices], money),
            "html_uri": [i.get("html_uri") for i in invoices],
            "pdf_uri": [i.get("pdf_uri") for i in invoices],
        }
    )

    lines = [line for invoice in invoices for line in invoice.get("lines", [])]
    invoice_lines_table = pa.table(
        {
            "id": [line["id"] for line in lines],
            "invoice_id": [line["invoice_id"] for line in lines],
            "description": [line.get("description") for line in lines],
            "quantity": pa.array([_decimal(line.get("quantity")) for line in lines], money),
            "unit_price": pa.array([_decimal(line.get("unit_price")) for line in lines], money),
            "amount": pa.array([_decimal(line.get("amount")) for line in lines], money),
            "sku": [line.get("sku") for line in lines],
            "purchase_order": [line.get("purchase_order") for line in lines],
        }
    )

    findings = payload["findings"]
    findings_table = pa.table(
        {
            "id": [f["id"] for f in findings],
            "invoice_id": [f["invoice_id"] for f in findings],
            "scenario_id": [f.get("scenario_id") for f in findings],
            "category": [f.get("category") for f in findings],
            "severity": [f.get("severity") for f in findings],
            "status": [f.get("status") for f in findings],
            "summary": [f.get("summary") for f in findings],
            "overpayment_amount": pa.array(
                [_decimal(f.get("overpayment_amount")) for f in findings], money
            ),
            "contract_document_ids": pa.array(
                [f.get("contract_document_ids", []) for f in findings], str_list
            ),
            "policy_ids": pa.array([f.get("policy_ids", []) for f in findings], str_list),
            "evidence_ids": pa.array([f.get("evidence_ids", []) for f in findings], str_list),
            "basis_summary": [f.get("metadata", {}).get("basis_summary") for f in findings],
        }
    )

    suppliers = payload["suppliers"]
    suppliers_table = pa.table(
        {
            "id": [s["id"] for s in suppliers],
            "name": [s.get("name") for s in suppliers],
            "status": [s.get("status", "active") for s in suppliers],
            "category": [s.get("category", "contract-manufacturer") for s in suppliers],
        }
    )

    contracts = payload["contract_documents"]
    contract_documents_table = pa.table(
        {
            "id": [c["id"] for c in contracts],
            "supplier_id": [c["supplier_id"] for c in contracts],
            "title": [c.get("title") for c in contracts],
            "document_type": [c.get("document_type", "contract") for c in contracts],
            "effective_date": pa.array(
                [_date(c.get("effective_date")) for c in contracts], pa.date32()
            ),
            "uri": [c.get("uri") for c in contracts],
        }
    )

    policies = payload["policies"]
    policies_table = pa.table(
        {
            "id": [p["id"] for p in policies],
            "name": [p.get("name") for p in policies],
            "description": [p.get("description", "") for p in policies],
            "severity": [p.get("severity", "medium") for p in policies],
        }
    )

    evidence = payload["evidence"]
    evidence_table = pa.table(
        {
            "id": [e["id"] for e in evidence],
            "title": [e.get("title") for e in evidence],
            "evidence_type": [e.get("evidence_type", "document") for e in evidence],
            "invoice_id": [e.get("invoice_id") for e in evidence],
            "finding_id": [e.get("finding_id") for e in evidence],
            "uri": [e.get("uri") for e in evidence],
            "excerpt": [e.get("excerpt") for e in evidence],
        }
    )

    return {
        "invoices": invoices_table,
        "invoice_lines": invoice_lines_table,
        "reconciliation_findings": findings_table,
        "suppliers": suppliers_table,
        "contract_documents": contract_documents_table,
        "policies": policies_table,
        "evidence_references": evidence_table,
    }


def _write_tables(
    account_url: str,
    workspace: str,
    lakehouse_segment: str,
    tables: dict[str, Any],
) -> dict[str, int]:
    from azure.identity import DefaultAzureCredential
    from deltalake import write_deltalake

    token = DefaultAzureCredential().get_token(_DELTA_TOKEN_SCOPE).token
    storage_options = {"bearer_token": token, "use_fabric_endpoint": "true"}

    # OneLake abfss host is fixed; the configurable account_url applies to the DFS Files endpoint.
    host = "onelake.dfs.fabric.microsoft.com"
    counts: dict[str, int] = {}
    for name, table in tables.items():
        table_uri = f"abfss://{workspace}@{host}/{lakehouse_segment}/Tables/{name}"
        write_deltalake(
            table_uri,
            table,
            mode="overwrite",
            schema_mode="overwrite",
            storage_options=storage_options,
        )
        counts[name] = table.num_rows
        logger.info("Wrote Delta table Tables/%s (%d rows)", name, table.num_rows)
    return counts


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


def _regenerate_artifacts(root: Path) -> None:
    """Deterministically (re)generate the contract/policy DOCX + seed before upload.

    Invoice documents are rendered from the seed at upload time (see ``_render_invoice_files``),
    not via ``generate-invoices``, because that command renders a different invoice set than the
    one the Waypoint seed references.
    """

    from .docx_docs import generate_docx_documents
    from .waypoint_seed import generate_waypoint_seed

    generate_docx_documents(root)
    generate_waypoint_seed(root)


def upload_corpus(
    root: Path | None = None,
    *,
    workspace: str,
    lakehouse: str,
    account_url: str = DEFAULT_ACCOUNT_URL,
    corpus_prefix: str = DEFAULT_CORPUS_PREFIX,
    upload_files: bool = True,
    write_tables: bool = True,
    regenerate: bool = True,
    include_invoice_pdf: bool = True,
) -> dict[str, Any]:
    """Upload the corpus to OneLake and return a summary of what was written.

    Raises on any failure so callers exit non-zero. ``workspace`` and ``lakehouse`` are required.
    """

    if not workspace or not lakehouse:
        raise ValueError("Both --workspace and --lakehouse (or their env vars) are required.")

    root = root or repo_root()
    data_root = data_path(root)

    if regenerate:
        logger.info("Regenerating corpus artifacts before upload.")
        _regenerate_artifacts(root)

    payload = build_waypoint_seed(root)
    validate_waypoint_seed(payload)

    summary = UploadSummary(
        workspace=workspace, lakehouse=lakehouse, corpus_prefix=corpus_prefix
    )

    # Resolve the lakehouse to a path segment (GUID where possible) so OneLake paths work even
    # in tenants where friendly-name support is disabled on the DFS endpoint.
    lakehouse_segment = _resolve_lakehouse_segment(workspace, lakehouse)

    if upload_files:
        filesystem = _file_system_client(account_url, workspace)
        documents = list(_collect_document_files(data_root))
        documents.append(_seed_file(data_root, payload))
        documents.extend(_evidence_files(payload))
        documents.extend(_render_invoice_files(payload, include_pdf=include_invoice_pdf))
        summary.files = _upload_files(filesystem, lakehouse_segment, corpus_prefix, documents)

    if write_tables:
        tables = _build_tables(payload)
        summary.tables = _write_tables(account_url, workspace, lakehouse_segment, tables)

    return summary.as_dict()
