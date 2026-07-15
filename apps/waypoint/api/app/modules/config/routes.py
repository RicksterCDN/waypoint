"""API routes for runtime drill-down configuration."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ...common.auth import UserContext, require_reader
from ...common.settings import Settings, get_settings
from ...common.tracer import trace_span
from .schemas import DrilldownConfig

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=DrilldownConfig, summary="Get UI drill-down configuration")
async def get_drilldown_config(
    _user: Annotated[UserContext, Depends(require_reader)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DrilldownConfig:
    """Return non-secret Foundry and App Insights deep-link bases for authenticated views."""

    with trace_span("get_drilldown_config_endpoint"):
        return DrilldownConfig(
            foundry_endpoint=settings.foundry_endpoint,
            foundry_project_url=settings.foundry_project_url,
            app_insights_resource_id=settings.app_insights_resource_id,
        )
