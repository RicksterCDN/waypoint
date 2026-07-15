"""FastAPI preview and generation API for Ledgerfield."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from .agent_surface import agent_manifest, artifact_status, generate_all_artifacts
from .db import append_live_invoices, database_url, get_status, seed_database
from .docx_docs import generate_docx_documents
from .invoice_docs import (
    generate_invoice_documents,
    invoice_by_id,
    load_invoice_document,
    profile_by_supplier_id,
    render_invoice_html,
    template_environment,
)
from .paths import data_path, read_json

app = FastAPI(
    title="Ledgerfield API",
    description="Preview and generate Waypoint demo invoices, documents, and Postgres seed data.",
    version="0.1.0",
)


@app.get("/health", response_class=HTMLResponse)
async def health() -> str:
    return "Healthy"


@app.get("/preview", response_class=HTMLResponse)
async def preview() -> str:
    invoice_doc = load_invoice_document()
    invoices = [_invoice_preview_item(invoice_doc, invoice) for invoice in invoice_doc["invoices"]]
    template = template_environment().get_template("preview.html.j2")
    return template.render(invoices=invoices)


@app.get("/api/suppliers")
async def list_suppliers() -> list[dict[str, Any]]:
    return read_json(data_path() / "suppliers" / "suppliers.json")


@app.get("/api/suppliers/{supplier_id}")
async def get_supplier(supplier_id: str) -> dict[str, Any]:
    for supplier in read_json(data_path() / "suppliers" / "suppliers.json"):
        if supplier["id"] == supplier_id:
            return supplier
    raise HTTPException(status_code=404, detail=f"Supplier not found: {supplier_id}")


@app.get("/api/invoices")
async def list_invoices() -> list[dict[str, Any]]:
    invoice_doc = load_invoice_document()
    return [_invoice_preview_item(invoice_doc, invoice) for invoice in invoice_doc["invoices"]]


@app.get("/api/invoices/{invoice_id}")
async def get_invoice(invoice_id: str) -> dict[str, Any]:
    try:
        return invoice_by_id(load_invoice_document(), invoice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/invoices/{invoice_id}/html", response_class=HTMLResponse)
async def get_invoice_html(invoice_id: str) -> str:
    try:
        return render_invoice_html(invoice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/invoices/{invoice_id}/pdf")
async def get_invoice_pdf(invoice_id: str) -> FileResponse:
    invoice_doc = load_invoice_document()
    try:
        invoice = invoice_by_id(invoice_doc, invoice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    pdf_path = data_path() / "invoices" / "pdf" / f"{invoice['supplier_id']}-{invoice_id.lower()}.pdf"
    if not pdf_path.exists():
        try:
            await run_in_threadpool(generate_invoice_documents, output="pdf")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)


@app.post("/api/generation/invoices/html")
async def generate_invoice_html_files() -> dict[str, Any]:
    generated = await run_in_threadpool(generate_invoice_documents, output="html")
    return {
        "generated": len(generated),
        "html": [str(item.html_path) for item in generated if item.html_path],
    }


@app.post("/api/generation/invoices/pdf")
async def generate_invoice_pdf_files() -> dict[str, Any]:
    generated = await run_in_threadpool(generate_invoice_documents, output="pdf")
    return {
        "generated": len(generated),
        "pdf": [str(item.pdf_path) for item in generated if item.pdf_path],
    }


@app.post("/api/generation/docx")
async def generate_docx_files() -> dict[str, Any]:
    generated = await run_in_threadpool(generate_docx_documents)
    return {
        "generated": len(generated),
        "docx": [str(item.output_path) for item in generated],
    }


@app.post("/api/generation/all")
async def generate_all() -> dict[str, Any]:
    return await run_in_threadpool(generate_all_artifacts)


@app.get("/api/agent/manifest")
async def get_agent_manifest() -> dict[str, Any]:
    return agent_manifest()


@app.get("/api/agent/doctor")
async def get_agent_doctor() -> dict[str, Any]:
    return artifact_status()


@app.post("/api/agent/bootstrap")
async def agent_bootstrap(
    artifacts: bool = True,
    seed_db: bool = False,
    append_cycles: int = 0,
    dsn: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ready", "manifest": agent_manifest()}
    if artifacts:
        result["status"] = "artifacts_generated"
        result["artifacts"] = await run_in_threadpool(generate_all_artifacts)
    else:
        result["artifacts"] = artifact_status()
    if seed_db:
        result["database"] = seed_database(
            database_url(dsn),
            if_needed=True,
            append_cycles=append_cycles,
        )
        result["status"] = "database_seeded"
    return result


@app.get("/api/db/status")
async def db_status(dsn: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    return get_status(database_url(dsn))


@app.post("/api/db/seed")
async def db_seed(
    dsn: Annotated[str | None, Query()] = None,
    if_needed: bool = True,
    append_cycles: int = 0,
) -> dict[str, Any]:
    return seed_database(
        database_url(dsn),
        if_needed=if_needed,
        append_cycles=append_cycles,
    )


@app.post("/api/db/append")
async def db_append(
    dsn: Annotated[str | None, Query()] = None,
    cycles: int = 1,
) -> dict[str, Any]:
    resolved_dsn = database_url(dsn)
    appended = append_live_invoices(resolved_dsn, cycles=cycles)
    status = get_status(resolved_dsn)
    status["appended_invoices"] = appended
    return status


def _invoice_preview_item(
    invoice_doc: dict[str, Any],
    invoice: dict[str, Any],
) -> dict[str, Any]:
    profile = profile_by_supplier_id(invoice_doc, invoice["supplier_id"])
    return {
        "invoice_id": invoice["invoice_id"],
        "supplier_id": invoice["supplier_id"],
        "supplier_name": invoice["supplier_name"],
        "invoice_date": invoice["invoice_date"],
        "total_amount": invoice["total_amount"],
        "line_count": len(invoice["lines"]),
        "profile_id": profile["profile_id"],
        "template": f"src/ledgerfield/templates/invoices/{invoice['supplier_id']}.html.j2",
    }
