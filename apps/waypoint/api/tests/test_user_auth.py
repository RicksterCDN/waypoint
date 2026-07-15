"""Tests for current-user authentication behavior."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.database import reset_waypoint_repository_for_tests
from app.common.settings import Settings, get_settings
from app.main import app


@pytest.fixture
async def client():
    """Create an async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()
    reset_waypoint_repository_for_tests()


def override_settings(settings: Settings) -> None:
    """Override API settings for one test."""

    app.dependency_overrides[get_settings] = lambda: settings


@pytest.mark.asyncio
async def test_get_current_user_uses_local_dev_fallback(client: AsyncClient):
    """Local development returns a stable demo identity."""

    override_settings(
        Settings(
            msal_bearer_validation_enabled=False,
            local_auth_enabled=True,
        )
    )

    response = await client.get("/api/user/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": "operator@waypoint.local",
        "email": "operator@waypoint.local",
        "name": "Waypoint Operator",
        "source": "local-dev",
    }


@pytest.mark.asyncio
async def test_get_current_user_requires_auth_when_local_auth_disabled(
    client: AsyncClient,
):
    """Published environments fail closed without local auth or a bearer token."""

    override_settings(
        Settings(
            msal_bearer_validation_enabled=False,
            local_auth_enabled=False,
        )
    )

    response = await client.get("/api/user/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


@pytest.mark.asyncio
async def test_get_current_user_ignores_dev_headers_when_local_auth_disabled(
    client: AsyncClient,
):
    """Dev spoofing headers are only trusted in local development mode."""

    override_settings(
        Settings(
            msal_bearer_validation_enabled=False,
            local_auth_enabled=False,
        )
    )

    response = await client.get(
        "/api/user/me",
        headers={
            "x-dev-user-email": "spoof@example.com",
            "x-dev-user-name": "Spoofed User",
        },
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_requires_bearer_when_msal_enabled(client: AsyncClient):
    """MSAL mode rejects unauthenticated requests before route data is returned."""

    override_settings(
        Settings(
            msal_bearer_validation_enabled=True,
            local_auth_enabled=True,
            msal_tenant_id="tenant-id",
            msal_client_id="client-id",
        )
    )

    response = await client.get("/api/user/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


@pytest.mark.asyncio
async def test_local_dev_fallback_denies_non_loopback_requests():
    """Local fallback only applies to actual loopback callers."""

    override_settings(
        Settings(
            msal_bearer_validation_enabled=False,
            local_auth_enabled=True,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 12345)),
        base_url="http://test",
    ) as remote_client:
        response = await remote_client.get("/api/user/me")

    assert response.status_code == 401
    app.dependency_overrides.clear()
    reset_waypoint_repository_for_tests()
