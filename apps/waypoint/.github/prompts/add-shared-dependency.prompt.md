---
description: Add a shared dependency for services (Cosmos, Storage, Foundry)
---

# Add Shared Dependency

Create reusable FastAPI dependency functions following the Waypoint pattern.

## Understanding the Dependency Pattern

Waypoint uses a centralized `dependencies.py` file to create reusable service dependencies.
This promotes code reuse, easier testing, and consistent configuration.

## File Location

`api/app/modules/dependencies.py`

## Dependency Factory Pattern

### Cosmos DB Service Dependencies

Use the factory function to create Cosmos dependencies:

```python
from typing import Callable
from fastapi import Depends
from ..common import CosmosService, get_settings, Settings

def _create_cosmos_service_dependency(
    container_name: str, service_type: str
) -> Callable[[Settings], CosmosService]:
    """Factory function to create Cosmos DB service dependencies.

    Args:
        container_name: Name of the Cosmos DB container
        service_type: Type identifier for the service

    Returns:
        A dependency function that returns a CosmosService instance
    """
    def get_cosmos_service(
        settings: Settings = Depends(get_settings),
    ) -> CosmosService:
        return CosmosService(
            connection_string=settings.database_connection,
            database_name=settings.database_name,
            container_name=container_name,
            type=service_type,
        )

    # Set function name for better debugging
    get_cosmos_service.__name__ = f"get_{service_type}_cosmos_service"
    get_cosmos_service.__doc__ = (
        f"Get a CosmosService instance for {container_name} container."
    )
    return get_cosmos_service

# Create dependencies for each container
get_task_cosmos_service = _create_cosmos_service_dependency("tasks", "task")
get_app_cosmos_service = _create_cosmos_service_dependency("apps", "app")
get_user_cosmos_service = _create_cosmos_service_dependency("users", "user")
```

### Storage Service Dependency

```python
from ..common import StorageService

def get_storage_service(
    settings: Settings = Depends(get_settings),
) -> StorageService | None:
    """Get a StorageService instance for blob storage operations."""
    if not settings.storage_connection:
        return None
    return StorageService(
        storage=settings.storage_connection,
        container=settings.storage_container,
    )
```

### Foundry Service Dependency

```python
from ..common import FoundryService

def get_foundry_service(
    settings: Settings = Depends(get_settings),
) -> FoundryService:
    """Get a FoundryService instance for AI Foundry operations."""
    return FoundryService(
        endpoint=settings.foundry_endpoint,
        azure_openai_endpoint=settings.azure_openai_endpoint,
    )
```

## Using Dependencies in Routes

Import and use dependencies in your route files:

```python
from fastapi import APIRouter, Depends
from ..dependencies import get_task_cosmos_service, get_storage_service
from ...common import CosmosService, StorageService

router = APIRouter(prefix="/task", tags=["task"])

def get_task_service(
    cosmos_service: CosmosService = Depends(get_task_cosmos_service),
    storage_service: StorageService | None = Depends(get_storage_service),
) -> TaskService:
    """Get a TaskService instance with injected dependencies."""
    return TaskService(cosmos_service, storage_service)

@router.post("/")
async def create_task(
    task: Task,
    service: TaskService = Depends(get_task_service),
) -> Task:
    return await service.create_task(task)
```

## Composing Services with Multiple Dependencies

When a service needs multiple dependencies:

```python
def get_app_service(
    cosmos_service: CosmosService = Depends(get_app_cosmos_service),
    storage_service: StorageService | None = Depends(get_storage_service),
    task_service: TaskService = Depends(get_task_service),
) -> AppService:
    """Get an AppService with all required dependencies."""
    return AppService(cosmos_service, storage_service, task_service)
```

## Testing with Dependency Overrides

Override dependencies in tests:

```python
from fastapi.testclient import TestClient
from tests.mocks import MockCosmosService

def test_endpoint():
    app.dependency_overrides[get_task_cosmos_service] = lambda: MockCosmosService()

    client = TestClient(app)
    response = client.get("/api/task/")

    app.dependency_overrides.clear()
```

## Benefits

1. **Code Reuse**: Define once, use everywhere
2. **Testability**: Easy to override with mocks
3. **Consistency**: All services use same configuration
4. **Debugging**: Named functions show up in stack traces
5. **Type Safety**: Full type hints for IDE support
