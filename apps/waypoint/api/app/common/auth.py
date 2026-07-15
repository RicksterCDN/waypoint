"""Authentication helpers for MSAL bearer auth, API keys, and local development."""

import hmac
import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .msal_bearer import MsalBearerValidationError, is_app_only_token, validate_msal_bearer_token
from .settings import Settings, get_settings
from .tracer import trace_span

logger = logging.getLogger(__name__)

AuthSource = Literal["msal-bearer", "api-key", "dev-header", "local-dev", "anonymous"]
Role = Literal["reader", "writer", "admin"]
LOCAL_DEMO_EMAIL = "operator@waypoint.local"
LOCAL_DEMO_NAME = "Waypoint Operator"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class UserContext(BaseModel):
    """Authenticated user information passed to API modules."""

    email: str | None = None
    name: str | None = None
    source: AuthSource = "anonymous"
    roles: set[Role] = Field(default_factory=set)
    key_label: str | None = None
    diagnostics: dict[str, bool] = Field(default_factory=dict)


def get_user_context(request: Request, settings: Settings | None = None) -> UserContext:
    """Resolve user identity from API keys, dev headers, or a validated MSAL bearer token."""

    settings = settings or get_settings()
    is_loopback = _is_loopback_request(request)
    with trace_span(
        "auth_resolve_user_context",
        attributes={
            "auth.header.dev_email_present": bool(request.headers.get("x-dev-user-email")),
            "auth.header.api_key_present": bool(request.headers.get("x-api-key")),
            "auth.header.authorization_bearer_present": bool(
                request.headers.get("authorization", "").lower().startswith("bearer ")
            ),
            "auth.msal_bearer_validation.enabled": settings.msal_bearer_validation_enabled,
            "auth.api_key.enabled": settings.api_key_auth_enabled,
            "auth.local.enabled": settings.local_auth_enabled,
            "auth.local.loopback": is_loopback,
        },
    ) as span:
        dev_email = request.headers.get("x-dev-user-email")
        dev_name = request.headers.get("x-dev-user-name")
        local_fallback_allowed = (
            settings.local_auth_enabled
            and is_loopback
            and not settings.msal_bearer_validation_enabled
        )

        if local_fallback_allowed and (dev_email or dev_name):
            span.set_attribute("auth.source", "dev-header")
            logger.info("Resolved user context from dev headers")
            context = UserContext(
                email=dev_email,
                name=dev_name or dev_email,
                source="dev-header",
                roles={"reader", "admin"},
                diagnostics={
                    "dev_email_header_present": bool(dev_email),
                    "dev_name_header_present": bool(dev_name),
                },
            )
            request.state.user_context = context
            return context

        authorization = request.headers.get("authorization") or ""
        has_bearer_authorization = authorization.lower().startswith("bearer ")
        if settings.msal_bearer_validation_enabled:
            if not has_bearer_authorization:
                span.set_attribute("auth.source", "anonymous")
                logger.info("MSAL validation enabled but no bearer token was present")
            else:
                try:
                    msal_token = validate_msal_bearer_token(authorization, settings)
                except MsalBearerValidationError as exc:
                    span.set_attribute("auth.msal_bearer.valid", False)
                    logger.warning("Invalid MSAL bearer token: %s", exc)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Authorization bearer token.",
                    ) from exc

                span.set_attribute("auth.source", "msal-bearer")
                span.set_attribute("auth.msal_bearer.valid", True)
                span.set_attribute("auth.identity.email_present", bool(msal_token.email))
                logger.info("Resolved user context from MSAL bearer token")
                principal = _msal_principal(msal_token.claims, msal_token.email)
                context = UserContext(
                    email=principal,
                    name=msal_token.name or principal,
                    source="msal-bearer",
                    roles=_roles_from_msal_claims(msal_token.claims, settings),
                    diagnostics={
                        "msal_bearer_validation_enabled": True,
                        "msal_bearer_token_valid": True,
                        "msal_bearer_token_present": True,
                        "msal_bearer_app_only": is_app_only_token(msal_token.claims),
                    },
                )
                request.state.user_context = context
                return context

        api_key_context = _resolve_api_key_context(request, settings)
        if api_key_context:
            span.set_attribute("auth.source", "api-key")
            span.set_attribute("auth.api_key.label", api_key_context.key_label or "")
            request.state.user_context = api_key_context
            return api_key_context

        if local_fallback_allowed:
            span.set_attribute("auth.source", "local-dev")
            logger.info("Resolved user context from local dev fallback")
            context = UserContext(
                email=LOCAL_DEMO_EMAIL,
                name=LOCAL_DEMO_NAME,
                source="local-dev",
                roles={"reader", "admin"},
                diagnostics={"local_dev_fallback": True},
            )
            request.state.user_context = context
            return context

        span.set_attribute("auth.source", "anonymous")
        context = UserContext()
        request.state.user_context = context
        return context


def require_reader(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserContext:
    """Require an authenticated caller with reader access."""

    return _require_role("reader", request, settings)


def require_writer(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserContext:
    """Require an authenticated caller with writer access (admin satisfies writer)."""

    return _require_role("writer", request, settings)


def require_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserContext:
    """Require an authenticated caller with admin access."""

    return _require_role("admin", request, settings)


def require_role(role: Role) -> Callable[[Request, Settings], UserContext]:
    """Create a FastAPI dependency requiring a specific role."""

    def dependency(
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> UserContext:
        return _require_role(role, request, settings)

    return dependency


def _require_role(role: Role, request: Request, settings: Settings) -> UserContext:
    context = get_user_context(request, settings)
    if not context.email or context.source == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    if role not in _expand_roles(context.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{role.title()} access required",
        )
    return context


def _expand_roles(roles: set[Role]) -> set[Role]:
    """Apply the reader < writer < admin hierarchy so a higher role implies lower ones."""

    effective: set[Role] = set(roles)
    if "admin" in effective:
        effective.update({"writer", "reader"})
    if "writer" in effective:
        effective.add("reader")
    return effective


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first_forwarded_host = forwarded_for.split(",", maxsplit=1)[0].strip()
        if first_forwarded_host and first_forwarded_host not in LOOPBACK_HOSTS:
            return False
    return bool(client and client.host in LOOPBACK_HOSTS)


def _roles_from_msal_claims(claims: dict[str, object], settings: Settings) -> set[Role]:
    roles: set[Role] = set()
    token_roles = claims.get("roles")
    role_values = {str(role) for role in token_roles} if isinstance(token_roles, list) else set()

    # Explicit Waypoint app roles assigned to a service principal / managed identity.
    if settings.msal_admin_app_role and settings.msal_admin_app_role in role_values:
        roles.add("admin")
    if settings.msal_writer_app_role and settings.msal_writer_app_role in role_values:
        roles.add("writer")
    if settings.msal_reader_app_role and settings.msal_reader_app_role in role_values:
        roles.add("reader")

    # Legacy delegated behavior: any role ending with "admin" or the admin scope grants admin.
    if any(role.lower().endswith("admin") for role in role_values):
        roles.add("admin")
    scopes = claims.get("scp")
    if isinstance(scopes, str) and "Waypoint.Admin" in scopes.split():
        roles.add("admin")

    # Delegated user tokens (which passed scope validation) get reader access by default.
    if not is_app_only_token(claims):
        roles.add("reader")
    return roles


def _msal_principal(claims: dict[str, object], email: str | None) -> str:
    if email:
        return email
    for claim_name in ("azp", "appid", "sub"):
        claim_value = claims.get(claim_name)
        if isinstance(claim_value, str) and claim_value:
            return f"{claim_value}@msal-app.waypoint.local"
    return "unknown-msal-principal@msal-app.waypoint.local"


def _resolve_api_key_context(request: Request, settings: Settings) -> UserContext | None:
    supplied_key = request.headers.get("x-api-key", "")
    if not settings.api_key_auth_enabled or not supplied_key:
        return None

    for configured_key in _parse_api_keys(settings.api_keys):
        if hmac.compare_digest(supplied_key, configured_key.key):
            return UserContext(
                email=f"{configured_key.label}@api-key.waypoint.local",
                name=configured_key.label,
                source="api-key",
                roles=configured_key.roles,
                key_label=configured_key.label,
                diagnostics={"api_key_authenticated": True},
            )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


class _ConfiguredApiKey(BaseModel):
    label: str
    key: str
    roles: set[Role]


def _parse_api_keys(raw_keys: str) -> list[_ConfiguredApiKey]:
    parsed: list[_ConfiguredApiKey] = []
    for entry in raw_keys.split(";"):
        label, separator, remainder = entry.strip().partition(":")
        if not separator:
            continue
        key, separator, raw_roles = remainder.partition(":")
        if not separator or not label or not key:
            continue
        roles = {role.strip().lower() for role in raw_roles.split(",") if role.strip()}
        valid_roles: set[Role] = set()
        for candidate in ("reader", "writer", "admin"):
            if candidate in roles:
                valid_roles.add(candidate)
        if valid_roles:
            parsed.append(_ConfiguredApiKey(label=label, key=key, roles=valid_roles))
    return parsed
