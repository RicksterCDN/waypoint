---
description: Add tests for API modules using mocks
---

# Testing API Modules

Create tests for FastAPI modules using the mock patterns from Waypoint.

## Test Structure

```text
api/tests/
├── conftest.py           # Shared pytest fixtures
├── mocks/
│   ├── __init__.py       # Export all mocks
│   ├── cosmos.py         # MockCosmosService
│   └── storage.py        # MockStorageService
├── unit/
│   └── test_{{name}}_service.py
└── integration/
    └── test_{{name}}_routes.py
```

## Mock Implementations

### MockCosmosService (`tests/mocks/cosmos.py`)

```python
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from azure.cosmos.exceptions import CosmosResourceNotFoundError

class MockCosmosService:
    """In-memory mock for CosmosService."""

    def __init__(
        self,
        connection_string: str = "mock-connection",
        database_name: str = "test-db",
        container_name: str = "test-container",
        type: str = "mock",
        initial_items: list[dict] | None = None,
    ):
        self.connection_string = connection_string
        self.database_name = database_name
        self.container_name = container_name
        self.type = type
        self._items: dict[str, dict] = {}

        if initial_items:
            for item in initial_items:
                self._items[item["id"]] = item

    @contextlib.asynccontextmanager
    async def get_cosmos_container(self):
        """Get mock cosmos container."""
        yield self._create_mock_container()

    def _create_mock_container(self):
        """Create a mock container with CRUD operations."""
        container = MagicMock()

        async def create_item(item: dict) -> dict:
            self._items[item["id"]] = item
            return item

        async def read_item(item: str, partition_key: str) -> dict:
            if item not in self._items:
                raise CosmosResourceNotFoundError(message="Not found")
            return self._items[item]

        async def delete_item(item: str, partition_key: str) -> None:
            if item not in self._items:
                raise CosmosResourceNotFoundError(message="Not found")
            del self._items[item]

        async def upsert_item(item: dict) -> dict:
            self._items[item["id"]] = item
            return item

        container.create_item = AsyncMock(side_effect=create_item)
        container.read_item = AsyncMock(side_effect=read_item)
        container.delete_item = AsyncMock(side_effect=delete_item)
        container.upsert_item = AsyncMock(side_effect=upsert_item)

        return container

    async def create_container_if_not_exists(self, partition_key_path: str = "/id"):
        """Mock container creation - no-op."""
        pass

    def add_item(self, item: dict):
        """Helper to pre-populate data for tests."""
        self._items[item["id"]] = item
```

### MockStorageService (`tests/mocks/storage.py`)

```python
from unittest.mock import AsyncMock, MagicMock

class MockStorageService:
    """In-memory mock for Azure Blob Storage."""

    def __init__(self):
        self._blobs: dict[str, bytes] = {}

    async def upload_blob(self, blob_name: str, data: bytes) -> str:
        """Upload a blob to mock storage."""
        self._blobs[blob_name] = data
        return f"https://mock.blob.core.windows.net/{blob_name}"

    async def download_blob(self, blob_name: str) -> bytes | None:
        """Download a blob from mock storage."""
        return self._blobs.get(blob_name)

    async def delete_blob(self, blob_name: str) -> bool:
        """Delete a blob from mock storage."""
        if blob_name in self._blobs:
            del self._blobs[blob_name]
            return True
        return False

    async def list_blobs(self, prefix: str = "") -> list[str]:
        """List blobs with optional prefix filter."""
        return [name for name in self._blobs.keys() if name.startswith(prefix)]
```

## Shared Fixtures (`tests/conftest.py`)

```python
import pytest
from tests.mocks import MockCosmosService, MockStorageService

@pytest.fixture
def mock_cosmos_service():
    """Create a mock CosmosService."""
    return MockCosmosService()

@pytest.fixture
def mock_storage_service():
    """Create a mock StorageService."""
    return MockStorageService()

@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.database_connection = "mock-connection"
    settings.database_name = "test-db"
    settings.storage_connection = "mock-storage"
    settings.storage_container = "test-container"
    return settings

# Async test marker configuration
pytest_plugins = ["pytest_asyncio"]
```

## Unit Tests for Services

### `tests/unit/test_{{name}}_service.py`

```python
import pytest
from tests.mocks import MockCosmosService
from app.modules.{{name}}.service import {{Name}}Service
from app.modules.{{name}}.schemas import {{Name}}, {{Name}}Create

@pytest.fixture
def mock_cosmos_service():
    return MockCosmosService()

@pytest.fixture
def sample_{{name}}():
    return {{Name}}(
        id="test-123",
        name="Test {{Name}}",
        description="Test description",
    )

class Test{{Name}}ServiceCreate:
    """Unit tests for {{Name}}Service.create()"""

    @pytest.mark.asyncio
    async def test_create_success(self, mock_cosmos_service):
        service = {{Name}}Service(mock_cosmos_service)
        item = {{Name}}Create(name="New Item", description="Description")

        result = await service.create(item)

        assert result.name == "New Item"
        assert result.id is not None

class Test{{Name}}ServiceGet:
    """Unit tests for {{Name}}Service.get()"""

    @pytest.mark.asyncio
    async def test_get_existing(self, mock_cosmos_service, sample_{{name}}):
        mock_cosmos_service.add_item(sample_{{name}}.model_dump())
        service = {{Name}}Service(mock_cosmos_service)

        result = await service.get("test-123")

        assert result is not None
        assert result.id == "test-123"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mock_cosmos_service):
        service = {{Name}}Service(mock_cosmos_service)

        result = await service.get("nonexistent")

        assert result is None

class Test{{Name}}ServiceDelete:
    """Unit tests for {{Name}}Service.delete()"""

    @pytest.mark.asyncio
    async def test_delete_existing(self, mock_cosmos_service, sample_{{name}}):
        mock_cosmos_service.add_item(sample_{{name}}.model_dump())
        service = {{Name}}Service(mock_cosmos_service)

        result = await service.delete("test-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_cosmos_service):
        service = {{Name}}Service(mock_cosmos_service)

        result = await service.delete("nonexistent")

        assert result is False
```

## Integration Tests for Routes

### `tests/integration/test_{{name}}_routes.py`

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_create_{{name}}():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/{{name}}/",
            json={"name": "Test", "description": "Test desc"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test"

@pytest.mark.asyncio
async def test_get_{{name}}_not_found():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        response = await client.get("/api/{{name}}/nonexistent")
        assert response.status_code == 404
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run only unit tests
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/ --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/unit/test_{{name}}_service.py -v
```

## Key Testing Patterns

1. **Fixture Composition**: Build complex fixtures from simple ones
2. **Mock Isolation**: Each test gets fresh mock instances
3. **Async Support**: Use `@pytest.mark.asyncio` for async tests
4. **Class Grouping**: Group related tests in classes for organization
5. **AAA Pattern**: Arrange, Act, Assert in each test
