---
description: Create a new FastAPI module with full structure
---

# New API Module: {{name}}

Create a new API module following the established Waypoint pattern.

## Requirements

- Module name: `{{name}}`
- Description: {{description}}

## Module Pattern Overview

Each module follows the three-file pattern:

- **schemas.py** - Pydantic models for data validation
- **service.py** - Business logic with `@trace` decorator
- **routes.py** - FastAPI endpoints with dependency injection

## Create these files

### 1. `api/app/modules/{{name}}/schemas.py`

Define Pydantic models for request/response:

```python
from pydantic import BaseModel, Field
from datetime import datetime

class {{Name}}Base(BaseModel):
    """Base model with common fields."""
    name: str = Field(..., description="Name of the {{name}}")
    description: str | None = Field(None, description="Optional description")

class {{Name}}Create({{Name}}Base):
    """Model for creating a new {{name}}."""
    pass

class {{Name}}({{Name}}Base):
    """Full {{name}} model with ID and timestamps."""
    id: str = Field(..., description="Unique identifier")
    type: str = Field(default="{{name}}", description="Type discriminator")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Paged{{Name}}Response(BaseModel):
    """Paged response for listing {{name}}s."""
    items: list[{{Name}}]
    continuation_token: str | None = None
```

### 2. `api/app/modules/{{name}}/service.py`

Implement business logic with tracing:

```python
import logging
from ...common import CosmosService
from ...common.tracer import trace
from .schemas import {{Name}}, {{Name}}Create

logger = logging.getLogger(__name__)

class {{Name}}Service:
    def __init__(self, cosmos_service: CosmosService):
        self.cosmos_service = cosmos_service

    async def create_container_if_not_exists(self) -> None:
        """Ensure the container exists."""
        await self.cosmos_service.create_container_if_not_exists(
            partition_key_path="/id"
        )

    @trace
    async def create(self, item: {{Name}}Create) -> {{Name}}:
        """Create a new {{name}}."""
        logger.info(f"Creating {{name}}: {item.name}")
        data = {{Name}}(
            id=...,  # Generate ID
            **item.model_dump()
        )
        async with self.cosmos_service.get_cosmos_container() as container:
            response = await container.create_item(data.model_dump())
            logger.info(f"Successfully created {{name}}: {response['id']}")
            return {{Name}}.model_validate(response)

    @trace
    async def get(self, item_id: str) -> {{Name}} | None:
        """Get a {{name}} by ID."""
        logger.debug(f"Getting {{name}}: {item_id}")
        async with self.cosmos_service.get_cosmos_container() as container:
            from azure.cosmos.exceptions import CosmosResourceNotFoundError
            try:
                item = await container.read_item(item=item_id, partition_key=item_id)
                return {{Name}}.model_validate(item)
            except CosmosResourceNotFoundError:
                logger.warning(f"{{Name}} not found: {item_id}")
                return None

    @trace
    async def list(
        self,
        page_size: int | None = None,
        continuation_token: str | None = None,
    ) -> tuple[list[{{Name}}], str | None]:
        """List all {{name}}s with pagination."""
        async with self.cosmos_service.get_cosmos_container() as container:
            # Implement pagination query
            ...

    @trace
    async def delete(self, item_id: str) -> bool:
        """Delete a {{name}}."""
        async with self.cosmos_service.get_cosmos_container() as container:
            from azure.cosmos.exceptions import CosmosResourceNotFoundError
            try:
                await container.delete_item(item=item_id, partition_key=item_id)
                logger.info(f"Deleted {{name}}: {item_id}")
                return True
            except CosmosResourceNotFoundError:
                return False
```

### 3. `api/app/modules/{{name}}/routes.py`

Create FastAPI router with dependency injection:

```python
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from ...common import CosmosService
from ...common.tracer import trace_span
from ..dependencies import _create_cosmos_service_dependency

from .service import {{Name}}Service
from .schemas import {{Name}}, {{Name}}Create, Paged{{Name}}Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{{name}}", tags=["{{name}}"])

# Create Cosmos DB service dependency
get_{{name}}_cosmos_service = _create_cosmos_service_dependency("{{name}}s", "{{name}}")

def get_{{name}}_service(
    cosmos_service: CosmosService = Depends(get_{{name}}_cosmos_service),
) -> {{Name}}Service:
    """Get a {{Name}}Service instance."""
    return {{Name}}Service(cosmos_service)

@router.post(
    "/",
    response_model={{Name}},
    tags=["{{name}}"],
    summary="Create a new {{name}}",
    description="Create a new {{name}} with the provided data.",
)
async def create_{{name}}(
    item: {{Name}}Create,
    service: {{Name}}Service = Depends(get_{{name}}_service),
) -> {{Name}}:
    """Create a new {{name}}."""
    with trace_span("create_{{name}}", attributes=item.model_dump()):
        return await service.create(item)

@router.get(
    "/{item_id}",
    response_model={{Name}},
    tags=["{{name}}"],
    summary="Get a {{name}} by ID",
    responses={404: {"description": "{{Name}} not found"}},
)
async def get_{{name}}(
    item_id: str,
    service: {{Name}}Service = Depends(get_{{name}}_service),
) -> {{Name}}:
    """Get a {{name}} by its ID."""
    with trace_span("get_{{name}}", attributes={"item_id": item_id}):
        result = await service.get(item_id)
        if result is None:
            raise HTTPException(status_code=404, detail="{{Name}} not found")
        return result

@router.get(
    "/",
    response_model=Paged{{Name}}Response,
    tags=["{{name}}"],
    summary="List all {{name}}s",
)
async def list_{{name}}s(
    page_size: int | None = Query(None, ge=1, le=100, description="Items per page"),
    continuation_token: str | None = Query(None, description="Pagination token"),
    service: {{Name}}Service = Depends(get_{{name}}_service),
) -> Paged{{Name}}Response:
    """List all {{name}}s with optional pagination."""
    items, next_token = await service.list(page_size, continuation_token)
    return Paged{{Name}}Response(items=items, continuation_token=next_token)

@router.delete(
    "/{item_id}",
    tags=["{{name}}"],
    summary="Delete a {{name}}",
    responses={404: {"description": "{{Name}} not found"}},
)
async def delete_{{name}}(
    item_id: str,
    service: {{Name}}Service = Depends(get_{{name}}_service),
) -> dict:
    """Delete a {{name}}."""
    with trace_span("delete_{{name}}", attributes={"item_id": item_id}):
        if not await service.delete(item_id):
            raise HTTPException(status_code=404, detail="{{Name}} not found")
        return {"deleted": True}
```

### 4. `api/app/modules/{{name}}/__init__.py`

Export the router and key classes:

```python
from .schemas import {{Name}}, {{Name}}Create, Paged{{Name}}Response
from .service import {{Name}}Service
from .routes import router as {{name}}_router

__all__ = [
    # Schemas
    "{{Name}}",
    "{{Name}}Create",
    "Paged{{Name}}Response",
    # Service
    "{{Name}}Service",
    # Routes
    "{{name}}_router",
]
```

### 5. Update `api/app/main.py`

Register the router:

```python
from .modules.{{name}} import {{name}}_router

app.include_router({{name}}_router, prefix="/api")
```

### 6. Update `api/app/modules/dependencies.py`

Add the dependency (if using shared dependencies):

```python
get_{{name}}_cosmos_service = _create_cosmos_service_dependency("{{name}}s", "{{name}}")
```

### 7. Add tests in `api/tests/unit/test_{{name}}_service.py`

```python
import pytest
from tests.mocks import MockCosmosService
from app.modules.{{name}}.service import {{Name}}Service
from app.modules.{{name}}.schemas import {{Name}}Create

@pytest.fixture
def mock_cosmos_service():
    return MockCosmosService()

@pytest.mark.asyncio
async def test_create_{{name}}(mock_cosmos_service):
    service = {{Name}}Service(mock_cosmos_service)
    item = {{Name}}Create(name="Test", description="Test description")
    result = await service.create(item)
    assert result.name == "Test"
```

## Key Patterns from Waypoint

1. **Dependency Factory**: Use `_create_cosmos_service_dependency()` for reusable Cosmos dependencies
2. **Trace Decorator**: Add `@trace` to all service methods for OpenTelemetry
3. **Trace Span**: Wrap route handlers with `trace_span()` for request tracing
4. **Error Handling**: Return `None` from service, raise `HTTPException` in routes
5. **Pagination**: Use `continuation_token` pattern for Cosmos DB pagination
6. **Type Discriminator**: Include `type` field in schemas for polymorphic queries
