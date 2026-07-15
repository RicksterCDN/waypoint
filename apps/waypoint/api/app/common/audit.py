"""Request audit logging middleware."""

import logging
from time import perf_counter

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .auth import UserContext
from .database import get_waypoint_repository_for_settings
from .settings import get_settings

logger = logging.getLogger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """Capture caller, route, status, and resource identifiers for API requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            await self._record(request, response, int((perf_counter() - start) * 1000))
        return response

    async def _record(self, request: Request, response: Response, duration_ms: int) -> None:
        context = getattr(request.state, "user_context", UserContext())
        resource_ids = {
            key: value
            for key, value in request.path_params.items()
            if key.endswith("_id") or key in {"invoice_id", "scenario_id", "finding_id"}
        }
        logger.info(
            "API audit caller=%s source=%s key_label=%s method=%s route=%s status=%s resources=%s",
            context.email or "anonymous",
            context.source,
            context.key_label or "",
            request.method,
            request.url.path,
            response.status_code,
            resource_ids,
        )
        settings_provider = request.app.dependency_overrides.get(get_settings, get_settings)
        repository = await get_waypoint_repository_for_settings(settings_provider())
        save_audit_event = getattr(repository, "save_audit_event", None)
        if save_audit_event:
            await save_audit_event(
                {
                    "caller": context.email or "anonymous",
                    "auth_source": context.source,
                    "key_label": context.key_label,
                    "method": request.method,
                    "route": request.url.path,
                    "status": response.status_code,
                    "resource_ids": resource_ids,
                    "duration_ms": duration_ms,
                }
            )
