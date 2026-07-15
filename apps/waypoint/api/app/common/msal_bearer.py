"""Validation for browser-sent MSAL bearer tokens."""

from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from pydantic import BaseModel, Field

from .settings import Settings
from .tracer import trace_span

_CLOCK_SKEW_SECONDS = 300


class MsalBearerValidationError(PermissionError):
    """Raised when a browser-sent MSAL bearer token cannot be trusted."""


class MsalBearerClaims(BaseModel):
    """Validated MSAL bearer token details safe to use for request identity."""

    token: str
    claims: dict[str, Any] = Field(default_factory=dict)
    email: str | None = None
    name: str | None = None


def validate_msal_bearer_token(authorization: str, settings: Settings) -> MsalBearerClaims:
    """Validate an Authorization bearer token issued for the Waypoint API."""

    with trace_span(
        "msal_validate_bearer_token",
        attributes={
            "msal.tenant_configured": bool(settings.msal_tenant_id),
            "msal.client_configured": bool(settings.msal_client_id),
            "msal.required_scope": settings.msal_required_scope,
        },
    ) as span:
        raw_token = _raw_bearer_token(authorization)
        span.set_attribute("msal.bearer.present", bool(raw_token))
        if not raw_token:
            raise MsalBearerValidationError("Authorization header must use the Bearer scheme.")
        if not settings.msal_tenant_id or not settings.msal_client_id:
            raise MsalBearerValidationError("MSAL bearer validation is not configured.")

        try:
            signing_key = _get_signing_key(raw_token, settings)
            claims = jwt.decode(
                raw_token,
                signing_key,
                algorithms=["RS256"],
                audience=_expected_audiences(settings),
                leeway=_CLOCK_SKEW_SECONDS,
                options={"require": ["exp"], "verify_iss": False},
            )
        except (InvalidTokenError, PyJWKClientError) as exc:
            span.set_attribute("msal.validation.result", "decode_failed")
            span.set_attribute("msal.validation.error_type", type(exc).__name__)
            raise MsalBearerValidationError("MSAL bearer token validation failed.") from exc

        if not isinstance(claims, dict):
            span.set_attribute("msal.validation.result", "invalid_payload")
            raise MsalBearerValidationError("MSAL bearer token payload is invalid.")

        span.set_attribute("msal.claim.tid", _string_claim(claims, "tid"))
        span.set_attribute("msal.claim.aud", _string_claim(claims, "aud"))
        span.set_attribute("msal.claim.iss", _string_claim(claims, "iss"))
        span.set_attribute("msal.claim.scp", _string_claim(claims, "scp"))
        span.set_attribute("msal.claim.azp", _first_claim(claims, "azp", "appid") or "")

        _validate_tenant_and_issuer(claims, settings)
        _validate_authorization(claims, settings)
        span.set_attribute("msal.identity.app_only", is_app_only_token(claims))
        span.set_attribute("msal.validation.result", "valid")
        return MsalBearerClaims(
            token=raw_token,
            claims=claims,
            email=_first_claim(claims, "preferred_username", "email", "upn", "unique_name"),
            name=_first_claim(claims, "name", "preferred_username", "email", "upn"),
        )


def _raw_bearer_token(authorization: str) -> str:
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _expected_audiences(settings: Settings) -> list[str]:
    return [settings.msal_client_id, f"api://{settings.msal_client_id}"]


def _validate_tenant_and_issuer(claims: dict[str, Any], settings: Settings) -> None:
    tenant_id = _string_claim(claims, "tid")
    if tenant_id != settings.msal_tenant_id:
        raise MsalBearerValidationError("MSAL bearer token tenant does not match.")

    issuer = _string_claim(claims, "iss")
    expected_issuers = {
        f"https://login.microsoftonline.com/{settings.msal_tenant_id}/v2.0",
        f"https://sts.windows.net/{settings.msal_tenant_id}/",
    }
    if issuer not in expected_issuers:
        raise MsalBearerValidationError("MSAL bearer token issuer does not match.")


def _validate_scope(claims: dict[str, Any], settings: Settings) -> None:
    scopes = _string_claim(claims, "scp").split()
    if settings.msal_required_scope not in scopes:
        raise MsalBearerValidationError("MSAL bearer token is missing the required scope.")


def is_app_only_token(claims: dict[str, Any]) -> bool:
    """Return True when the token represents an app (client-credential) identity.

    Delegated user tokens carry an ``scp`` claim; app-only tokens (managed identities and
    service principals using client credentials) instead carry app ``roles`` and either an
    ``idtyp`` of ``app`` or no ``scp`` claim at all.
    """

    idtyp = _string_claim(claims, "idtyp").lower()
    if idtyp == "app":
        return True
    if idtyp == "user":
        return False
    has_scope = bool(_string_claim(claims, "scp"))
    token_roles = claims.get("roles")
    has_app_roles = isinstance(token_roles, list) and bool(token_roles)
    return not has_scope and has_app_roles


def _configured_app_roles(settings: Settings) -> set[str]:
    return {
        role
        for role in (
            settings.msal_reader_app_role,
            settings.msal_writer_app_role,
            settings.msal_admin_app_role,
        )
        if role
    }


def _parse_csv(raw: str) -> set[str]:
    return {value.strip() for value in raw.split(",") if value.strip()}


def _validate_authorization(claims: dict[str, Any], settings: Settings) -> None:
    """Require a delegated scope for user tokens or a known app role for app-only tokens."""

    if not is_app_only_token(claims):
        _validate_scope(claims, settings)
        return

    configured_roles = _configured_app_roles(settings)
    if not configured_roles:
        raise MsalBearerValidationError(
            "App-only tokens are not authorized: no Waypoint app roles are configured."
        )

    token_roles = claims.get("roles")
    role_values = {str(role) for role in token_roles} if isinstance(token_roles, list) else set()
    if role_values.isdisjoint(configured_roles):
        raise MsalBearerValidationError("App-only token is missing a required Waypoint app role.")

    allowed_app_ids = _parse_csv(settings.msal_allowed_app_ids)
    if allowed_app_ids:
        app_id = _first_claim(claims, "azp", "appid") or ""
        if app_id not in allowed_app_ids:
            raise MsalBearerValidationError("App-only token application is not allow-listed.")


def _first_claim(claims: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = _string_claim(claims, name)
        if value:
            return value
    return None


def _string_claim(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    return value if isinstance(value, str) else ""


def _get_signing_key(token: str, settings: Settings) -> Any:
    try:
        return (
            _get_jwk_client(
                settings.msal_tenant_id,
                settings.msal_jwks_cache_ttl_seconds,
            )
            .get_signing_key_from_jwt(token)
            .key
        )
    except PyJWKClientError:
        _get_jwk_client.cache_clear()
        return (
            _get_jwk_client(
                settings.msal_tenant_id,
                settings.msal_jwks_cache_ttl_seconds,
            )
            .get_signing_key_from_jwt(token)
            .key
        )


@lru_cache
def _get_jwk_client(tenant_id: str, ttl_seconds: int) -> PyJWKClient:
    return PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        cache_keys=True,
        lifespan=ttl_seconds,
        timeout=10,
    )
