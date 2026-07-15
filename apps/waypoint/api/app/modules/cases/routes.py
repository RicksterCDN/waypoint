"""API routes for governed assurance cases."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...common.auth import UserContext, require_admin, require_reader, require_writer
from ...common.database import get_waypoint_repository
from ...common.repository import WaypointRepository
from ...common.tracer import trace_span
from .schemas import (
    ActionType,
    AssuranceCase,
    AssuranceCaseCreate,
    AuthorizedIntent,
    AuthorizeIntentCreate,
    CaseApproval,
    CaseApprovalCreate,
    CaseDraft,
    CaseDraftCreate,
    CaseRecommendation,
    CaseRecommendationCreate,
    DecisionAuditEvent,
    InvoiceAssurance,
    ProposedAction,
    ProposedActionCreate,
)
from .service import CasesService

router = APIRouter(prefix="", tags=["cases"])


async def get_cases_service(
    repository: Annotated[WaypointRepository, Depends(get_waypoint_repository)],
) -> CasesService:
    return CasesService(repository)


@router.get("/actions/types", response_model=list[ActionType])
async def list_action_types(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> list[ActionType]:
    with trace_span("list_action_types_endpoint"):
        return await service.list_action_types()


@router.get("/cases", response_model=list[AssuranceCase])
async def list_cases(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
    invoice_id: str | None = Query(default=None),
    finding_id: str | None = Query(default=None),
) -> list[AssuranceCase]:
    with trace_span(
        "list_cases_endpoint",
        attributes={"invoice_id": invoice_id or "", "finding_id": finding_id or ""},
    ):
        return await service.list_cases(invoice_id=invoice_id, finding_id=finding_id)


@router.get("/invoices/{invoice_id}/assurance", response_model=InvoiceAssurance)
async def get_invoice_assurance(
    invoice_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> InvoiceAssurance:
    with trace_span("get_invoice_assurance_endpoint", attributes={"invoice_id": invoice_id}):
        return await service.get_invoice_assurance(invoice_id)


@router.post("/cases", response_model=AssuranceCase, status_code=201)
async def create_case(
    case_create: AssuranceCaseCreate,
    user: Annotated[UserContext, Depends(require_writer)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> AssuranceCase:
    with trace_span("create_case_endpoint"):
        try:
            return await service.create_case(
                case_create,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cases/{case_id}", response_model=AssuranceCase)
async def get_case(
    case_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> AssuranceCase:
    with trace_span("get_case_endpoint", attributes={"case_id": case_id}):
        assurance_case = await service.get_case(case_id)
        if not assurance_case:
            raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
        return assurance_case


@router.get("/cases/{case_id}/recommendations", response_model=list[CaseRecommendation])
async def list_case_recommendations(
    case_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> list[CaseRecommendation]:
    with trace_span("list_case_recommendations_endpoint", attributes={"case_id": case_id}):
        return await service.list_case_recommendations(case_id)


@router.get("/cases/{case_id}/drafts", response_model=list[CaseDraft])
async def list_case_drafts(
    case_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> list[CaseDraft]:
    with trace_span("list_case_drafts_endpoint", attributes={"case_id": case_id}):
        return await service.list_case_drafts(case_id)


@router.post("/cases/{case_id}/recommendations", response_model=CaseRecommendation, status_code=201)
async def create_case_recommendation(
    case_id: str,
    recommendation: CaseRecommendationCreate,
    user: Annotated[UserContext, Depends(require_writer)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> CaseRecommendation:
    with trace_span("create_case_recommendation_endpoint", attributes={"case_id": case_id}):
        try:
            return await service.create_case_recommendation(
                case_id,
                recommendation,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cases/{case_id}/drafts", response_model=CaseDraft, status_code=201)
async def create_case_draft(
    case_id: str,
    draft: CaseDraftCreate,
    user: Annotated[UserContext, Depends(require_writer)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> CaseDraft:
    with trace_span("create_case_draft_endpoint", attributes={"case_id": case_id}):
        try:
            return await service.create_case_draft(
                case_id,
                draft,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cases/{case_id}/actions", response_model=ProposedAction, status_code=201)
async def create_proposed_action(
    case_id: str,
    action: ProposedActionCreate,
    user: Annotated[UserContext, Depends(require_writer)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> ProposedAction:
    with trace_span("create_proposed_action_endpoint", attributes={"case_id": case_id}):
        try:
            return await service.create_proposed_action(
                case_id,
                action,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cases/{case_id}/approvals", response_model=CaseApproval, status_code=201)
async def create_case_approval(
    case_id: str,
    approval: CaseApprovalCreate,
    user: Annotated[UserContext, Depends(require_admin)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> CaseApproval:
    with trace_span("create_case_approval_endpoint", attributes={"case_id": case_id}):
        try:
            return await service.create_case_approval(
                case_id,
                approval,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/actions/{action_id}/authorize", response_model=AuthorizedIntent)
async def authorize_proposed_action(
    action_id: str,
    authorization: AuthorizeIntentCreate,
    user: Annotated[UserContext, Depends(require_admin)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> AuthorizedIntent:
    with trace_span("authorize_proposed_action_endpoint", attributes={"action_id": action_id}):
        try:
            return await service.authorize_intent(
                action_id,
                authorization,
                actor=_actor(user),
                auth_source=user.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/audit/decisions", response_model=list[DecisionAuditEvent])
async def list_decision_audit_events(
    _user: Annotated[UserContext, Depends(require_admin)],
    service: Annotated[CasesService, Depends(get_cases_service)],
) -> list[DecisionAuditEvent]:
    with trace_span("list_decision_audit_events_endpoint"):
        return await service.list_decision_audit_events()


def _actor(user: UserContext) -> str:
    assert user.email is not None
    return user.email
