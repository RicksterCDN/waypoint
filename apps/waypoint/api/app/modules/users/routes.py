"""API routes for current user profile."""

from fastapi import APIRouter, Depends, Request

from ...common.auth import UserContext, require_reader
from ...common.tracer import trace_span
from .schemas import User

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=User, summary="Get current user profile")
async def get_current_user(
    request: Request,
    context: UserContext = Depends(require_reader),
) -> User:
    """Resolve the signed-in user and return their profile."""

    with trace_span(
        "get_current_user_profile",
        attributes={
            "http.route": "/api/user/me",
            "http.method": request.method,
            "user_agent_present": bool(request.headers.get("user-agent")),
            "traceparent_present": bool(request.headers.get("traceparent")),
        },
    ) as span:
        span.set_attribute("auth.source", context.source)
        span.set_attribute("auth.identity_present", bool(context.email))

        span.set_attribute("auth.result", "authorized")
        assert context.email is not None
        assert context.source != "anonymous"
        return User(
            id=context.email.lower().strip(),
            email=context.email,
            name=context.name or context.email,
            source=context.source,
        )
