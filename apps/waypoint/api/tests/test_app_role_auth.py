"""Tests for writer role and app-only (managed identity) token authorization.

These cover the headless service-principal path used by the Forge aggregator agent
alongside the existing delegated user and API-key behavior.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.auth import _expand_roles, _parse_api_keys, _roles_from_msal_claims
from app.common.database import reset_waypoint_repository_for_tests
from app.common.msal_bearer import (
    MsalBearerValidationError,
    _validate_authorization,
    is_app_only_token,
)
from app.common.settings import Settings, get_settings
from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()
    reset_waypoint_repository_for_tests()


def override_settings(settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


# --- Token-type detection -------------------------------------------------


def test_is_app_only_token_detects_app_identity():
    assert is_app_only_token({"idtyp": "app", "roles": ["Waypoint.Write"]}) is True
    assert is_app_only_token({"roles": ["Waypoint.Write"]}) is True


def test_is_app_only_token_treats_delegated_tokens_as_user():
    assert is_app_only_token({"scp": "user_impersonation"}) is False
    assert is_app_only_token({"idtyp": "user", "roles": ["Waypoint.Admin"]}) is False


# --- App-only authorization validation ------------------------------------


def test_validate_authorization_allows_app_token_with_writer_role():
    settings = Settings()
    _validate_authorization({"roles": [settings.msal_writer_app_role]}, settings)


def test_validate_authorization_rejects_app_token_without_known_role():
    settings = Settings()
    with pytest.raises(MsalBearerValidationError):
        _validate_authorization({"roles": ["Some.Other.Role"]}, settings)


def test_validate_authorization_enforces_app_id_allow_list():
    settings = Settings(msal_allowed_app_ids="11111111-1111-1111-1111-111111111111")
    with pytest.raises(MsalBearerValidationError):
        _validate_authorization(
            {"roles": [settings.msal_writer_app_role], "azp": "deadbeef"},
            settings,
        )
    _validate_authorization(
        {
            "roles": [settings.msal_writer_app_role],
            "azp": "11111111-1111-1111-1111-111111111111",
        },
        settings,
    )


def test_validate_authorization_still_requires_scope_for_delegated_tokens():
    settings = Settings()
    with pytest.raises(MsalBearerValidationError):
        _validate_authorization({"scp": "Files.Read"}, settings)
    # A delegated token with the required scope passes.
    _validate_authorization({"scp": settings.msal_required_scope}, settings)


# --- Claim -> role mapping ------------------------------------------------


def test_app_roles_map_to_waypoint_roles():
    settings = Settings()
    writer = _roles_from_msal_claims({"roles": [settings.msal_writer_app_role]}, settings)
    reader = _roles_from_msal_claims({"roles": [settings.msal_reader_app_role]}, settings)
    admin = _roles_from_msal_claims({"roles": [settings.msal_admin_app_role]}, settings)

    assert writer == {"writer"}
    assert reader == {"reader"}
    assert "admin" in admin


def test_delegated_token_keeps_default_reader():
    settings = Settings()
    roles = _roles_from_msal_claims({"scp": settings.msal_required_scope}, settings)
    assert "reader" in roles


def test_role_hierarchy_expansion():
    assert _expand_roles({"admin"}) == {"admin", "writer", "reader"}
    assert _expand_roles({"writer"}) == {"writer", "reader"}
    assert _expand_roles({"reader"}) == {"reader"}


# --- API-key writer parsing -----------------------------------------------


def test_api_key_parser_supports_writer_role():
    parsed = _parse_api_keys("agent-key:agent-secret:writer")
    assert len(parsed) == 1
    assert parsed[0].roles == {"writer"}


# --- End-to-end writer enforcement via API key ----------------------------


@pytest.mark.asyncio
async def test_writer_can_create_case_but_not_admin_only_operations(client: AsyncClient):
    override_settings(
        Settings(
            api_key_auth_enabled=True,
            api_keys="agent-key:agent-secret:writer",
            default_seed_enabled=True,
        )
    )

    headers = {"x-api-key": "agent-secret"}

    read_response = await client.get("/api/work", headers=headers)
    run_response = await client.post(
        "/api/runs",
        headers=headers,
        json={"name": "invoice-assurance"},
    )
    case_response = await client.post(
        "/api/cases",
        headers=headers,
        json={
            "invoice_id": "inv-2026-08034",
            "finding_id": "finding-08034-investigation",
            "summary": "Investigate supplier-caused investigation charge.",
        },
    )
    validation_response = await client.post(
        "/api/findings/finding-08034-investigation/validations",
        headers=headers,
        json={
            "run_id": run_response.json()["id"],
            "case_id": case_response.json()["id"],
            "source": "pacioli",
            "outcome": "validated_existing_finding",
            "confidence": "0.92",
        },
    )
    seed_response = await client.post(
        "/api/admin/seed/ledgerfield",
        headers=headers,
        json={"source": "test"},
    )
    audit_response = await client.get("/api/audit/decisions", headers=headers)

    assert read_response.status_code == 200
    assert run_response.status_code == 201
    assert case_response.status_code == 201
    assert validation_response.status_code == 201
    # Admin-only governance surfaces remain closed to writers.
    assert seed_response.status_code == 403
    assert audit_response.status_code == 403


@pytest.mark.asyncio
async def test_writer_cannot_authorize_intent(client: AsyncClient):
    override_settings(
        Settings(
            api_key_auth_enabled=True,
            api_keys="agent-key:agent-secret:writer;admin-key:admin-secret:admin",
            default_seed_enabled=True,
        )
    )

    writer = {"x-api-key": "agent-secret"}
    admin = {"x-api-key": "admin-secret"}

    case = (
        await client.post(
            "/api/cases",
            headers=writer,
            json={"invoice_id": "inv-2026-08034", "finding_id": "finding-08034-investigation"},
        )
    ).json()
    action = (
        await client.post(
            f"/api/cases/{case['id']}/actions",
            headers=writer,
            json={
                "action_type_id": "recommend_recover",
                "title": "Recover supplier-caused investigation charge.",
            },
        )
    ).json()

    writer_authorize = await client.post(
        f"/api/actions/{action['id']}/authorize",
        headers=writer,
        json={"approval_id": "missing"},
    )
    admin_can_reach_authorize = await client.post(
        f"/api/actions/{action['id']}/authorize",
        headers=admin,
        json={"approval_id": "missing"},
    )

    # Writer is forbidden from the admin-only authorize step.
    assert writer_authorize.status_code == 403
    # Admin passes authorization and reaches business validation (not a 403).
    assert admin_can_reach_authorize.status_code != 403


# --- Drill-down config endpoint -------------------------------------------


@pytest.mark.asyncio
async def test_config_endpoint_returns_drilldown_bases(client: AsyncClient):
    override_settings(
        Settings(
            local_auth_enabled=True,
            foundry_endpoint="https://foundry.example.com/project",
            foundry_project_url="https://ai.azure.com/projects/waypoint",
            app_insights_resource_id="/subscriptions/x/providers/microsoft.insights/components/wp",
        )
    )

    response = await client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["foundry_endpoint"] == "https://foundry.example.com/project"
    assert body["foundry_project_url"] == "https://ai.azure.com/projects/waypoint"
    assert body["app_insights_resource_id"].endswith("/components/wp")


@pytest.mark.asyncio
async def test_config_endpoint_requires_authentication(client: AsyncClient):
    override_settings(Settings(local_auth_enabled=False))

    response = await client.get("/api/config")

    assert response.status_code == 401
