"""API routes for derived operational work views."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...common.auth import UserContext, require_reader
from ...common.database import get_waypoint_repository
from ...common.onelake import OneLakeClient, get_onelake_client
from ...common.repository import WaypointRepository
from ...common.settings import Settings, get_settings
from ...common.tracer import trace_span
from .schemas import InvoiceContextBundle, WorkQueueItem
from .service import WorkService

router = APIRouter(prefix="", tags=["work"])


def get_work_onelake_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OneLakeClient:
    return get_onelake_client(settings)


async def get_work_service(
    repository: Annotated[WaypointRepository, Depends(get_waypoint_repository)],
    onelake: Annotated[OneLakeClient, Depends(get_work_onelake_client)],
) -> WorkService:
    return WorkService(repository, onelake)


@router.get("/work", response_model=list[WorkQueueItem])
async def list_work_queue(
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WorkService, Depends(get_work_service)],
) -> list[WorkQueueItem]:
    with trace_span("list_work_queue_endpoint"):
        return await service.list_work_queue()


@router.get("/invoices/{invoice_id}/context", response_model=InvoiceContextBundle)
async def get_invoice_context(
    invoice_id: str,
    _user: Annotated[UserContext, Depends(require_reader)],
    service: Annotated[WorkService, Depends(get_work_service)],
    include_sensitive: bool = Query(default=False),
) -> InvoiceContextBundle:
    with trace_span("get_invoice_context_endpoint", attributes={"invoice_id": invoice_id}):
        bundle = await service.get_invoice_context(
            invoice_id,
            include_sensitive=include_sensitive,
        )
        if not bundle:
            raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")
        return bundle
