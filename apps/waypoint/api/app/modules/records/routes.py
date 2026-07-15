"""API routes for domain source records."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ...common.auth import UserContext, require_admin, require_reader, require_writer
from ...common.database import get_waypoint_repository
from ...common.onelake import OneLakeClient, get_onelake_client
from ...common.repository import WaypointRepository
from ...common.settings import Settings, get_settings
from ...common.tracer import trace_span
from .schemas import (
    AuditEvent,
    ContractDocumentDetail,
    EvidenceReference,
    FindingValidation,
    FindingValidationCreate,
    Invoice,
    InvoiceDecision,
    InvoiceDetail,
    LedgerfieldSeedImport,
    PolicyDetail,
    ReconciliationFinding,
    Scenario,
    SeedImportResult,
    Supplier,
)
from .service import WaypointService

router = APIRouter(prefix="", tags=["records"])


def get_records_onelake_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OneLakeClient:
    return get_onelake_client(settings)


async def get_records_service(
    repository: Annotated[WaypointRepository, Depends(get_waypoint_repository)],
    onelake: Annotated[OneLakeClient, Depends(get_records_onelake_client)],
) -> WaypointService:
    return WaypointService(repository, onelake)


@router.get("/suppliers", response_model=list[Supplier])
async def list_suppliers(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> list[Supplier]:
    with trace_span("list_suppliers_endpoint"):
        return await service.list_suppliers()


@router.get("/scenarios", response_model=list[Scenario])
async def list_scenarios(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> list[Scenario]:
    with trace_span("list_scenarios_endpoint"):
        return await service.list_scenarios()


@router.get("/invoices", response_model=list[Invoice])
async def list_invoices(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
    supplier_id: str | None = Query(default=None),
    invoice_number: str | None = Query(
        default=None,
        description=(
            "Exact invoice-number lookup; combine with supplier_id for a stable business key."
        ),
    ),
) -> list[Invoice]:
    with trace_span(
        "list_invoices_endpoint",
        attributes={"supplier_id": supplier_id or "", "invoice_number": invoice_number or ""},
    ):
        return await service.list_invoices(supplier_id, invoice_number)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> InvoiceDetail:
    with trace_span("get_invoice_endpoint", attributes={"invoice_id": invoice_id}):
        invoice = await service.get_invoice_detail(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")
        return invoice


@router.get("/invoice-decisions", response_model=list[InvoiceDecision])
async def list_invoice_decisions(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> list[InvoiceDecision]:
    with trace_span("list_invoice_decisions_endpoint"):
        return await service.list_invoice_decisions()


@router.get("/findings", response_model=list[ReconciliationFinding])
async def list_findings(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
    invoice_id: str | None = Query(default=None),
) -> list[ReconciliationFinding]:
    with trace_span("list_findings_endpoint", attributes={"invoice_id": invoice_id or ""}):
        return await service.list_findings(invoice_id)


@router.get("/findings/{finding_id}/validations", response_model=list[FindingValidation])
async def list_finding_validations(
    finding_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> list[FindingValidation]:
    with trace_span("list_finding_validations_endpoint", attributes={"finding_id": finding_id}):
        try:
            return await service.list_finding_validations(finding_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/findings/{finding_id}/validations",
    response_model=FindingValidation,
    status_code=201,
)
async def create_finding_validation(
    finding_id: str,
    validation: FindingValidationCreate,
    user: Annotated[UserContext, Depends(require_writer)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> FindingValidation:
    with trace_span("create_finding_validation_endpoint", attributes={"finding_id": finding_id}):
        try:
            return await service.create_finding_validation(
                finding_id,
                validation,
                actor=_actor(user),
                auth_source=user.source,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/evidence", response_model=list[EvidenceReference])
async def list_evidence(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
    invoice_id: str | None = Query(default=None),
    finding_id: str | None = Query(default=None),
) -> list[EvidenceReference]:
    with trace_span(
        "list_evidence_endpoint",
        attributes={"invoice_id": invoice_id or "", "finding_id": finding_id or ""},
    ):
        return await service.list_evidence(invoice_id, finding_id)


@router.get("/contract-documents/{document_id}", response_model=ContractDocumentDetail)
async def get_contract_document(
    document_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
    include_content: bool = Query(
        default=False,
        description="Resolve and include the full document text from the corpus lake",
    ),
) -> ContractDocumentDetail:
    with trace_span(
        "get_contract_document_endpoint",
        attributes={"document_id": document_id, "include_content": include_content},
    ):
        document = await service.get_contract_document_detail(document_id, include_content)
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Contract document '{document_id}' not found",
            )
        return document


@router.get("/policies/{policy_id}", response_model=PolicyDetail)
async def get_policy(
    policy_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
    include_content: bool = Query(
        default=False,
        description="Resolve and include the full policy text from the corpus lake",
    ),
) -> PolicyDetail:
    with trace_span(
        "get_policy_endpoint",
        attributes={"policy_id": policy_id, "include_content": include_content},
    ):
        policy = await service.get_policy_detail(policy_id, include_content)
        if not policy:
            raise HTTPException(status_code=404, detail=f"Policy '{policy_id}' not found")
        return policy


@router.get("/invoices/{invoice_id}/pdf")
async def get_invoice_pdf(
    invoice_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> Response:
    """Stream an invoice's source PDF inline for the in-app document viewer.

    Returns ``404`` when the invoice or its PDF reference is unknown, and ``409`` when the
    PDF exists as a reference but its bytes are not resolvable in this environment (for
    example when OneLake is not configured) so the client can render a fallback.
    """

    with trace_span(
        "get_invoice_pdf_endpoint",
        attributes={"invoice_id": invoice_id},
    ):
        resolved = await service.get_invoice_pdf(invoice_id)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"Invoice '{invoice_id}' has no PDF document",
            )
        if resolved.data is None:
            raise HTTPException(
                status_code=409,
                detail="Invoice PDF is not available in this environment",
            )
        return Response(
            content=resolved.data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{invoice_id}.pdf"',
                "Cache-Control": "private, max-age=300",
            },
        )


@router.get("/audit/events", response_model=list[AuditEvent])
async def list_audit_events(
    _user: Annotated[UserContext, Depends(require_admin)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> list[AuditEvent]:
    with trace_span("list_audit_events_endpoint"):
        return await service.list_audit_events()


@router.post("/admin/seed/ledgerfield", response_model=SeedImportResult)
async def import_ledgerfield_seed(
    seed: LedgerfieldSeedImport,
    _user: Annotated[UserContext, Depends(require_admin)],
    service: Annotated[WaypointService, Depends(get_records_service)],
) -> SeedImportResult:
    with trace_span("import_ledgerfield_seed_endpoint", attributes={"source": seed.source}):
        try:
            return await service.import_ledgerfield_seed(seed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def _actor(user: UserContext) -> str:
    assert user.email is not None
    return user.email
