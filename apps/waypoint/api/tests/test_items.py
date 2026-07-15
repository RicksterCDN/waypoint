"""Tests for the Items module."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.database import reset_waypoint_repository_for_tests
from app.common.settings import Settings, get_settings
from app.main import app


@pytest.fixture
async def client():
    """Create an async test client."""
    app.dependency_overrides[get_settings] = lambda: Settings(local_auth_enabled=True)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()
    reset_waypoint_repository_for_tests()


@pytest.mark.asyncio
async def test_list_items(client: AsyncClient):
    """Test listing items returns sample data."""
    response = await client.get("/api/items/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2  # Sample data


@pytest.mark.asyncio
async def test_get_item(client: AsyncClient):
    """Test getting a specific item."""
    response = await client.get("/api/items/item-1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "item-1"
    assert data["name"] == "Welcome Item"


@pytest.mark.asyncio
async def test_get_item_not_found(client: AsyncClient):
    """Test getting a non-existent item returns 404."""
    response = await client.get("/api/items/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_item(client: AsyncClient):
    """Test creating a new item."""
    response = await client.post(
        "/api/items/",
        json={"name": "Test Item", "description": "A test item"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Item"
    assert data["description"] == "A test item"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_update_item(client: AsyncClient):
    """Test updating an existing item."""
    response = await client.patch(
        "/api/items/item-1",
        json={"name": "Updated Item"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Item"


@pytest.mark.asyncio
async def test_delete_item(client: AsyncClient):
    """Test deleting an item."""
    # First create an item to delete
    create_response = await client.post(
        "/api/items/",
        json={"name": "To Delete"},
    )
    item_id = create_response.json()["id"]

    # Delete it
    response = await client.delete(f"/api/items/{item_id}")
    assert response.status_code == 204

    # Verify it's gone
    get_response = await client.get(f"/api/items/{item_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test the health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.text == "Healthy"
