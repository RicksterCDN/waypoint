"""API routes for the Items module."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ...common.auth import UserContext, require_admin, require_reader
from ...common.tracer import trace_span
from .schemas import Item, ItemCreate, ItemUpdate
from .service import ItemService, get_item_service

router = APIRouter(prefix="/items", tags=["items"])


@router.get(
    "/",
    response_model=list[Item],
    summary="List all items",
    description="Retrieve all items, optionally filtering by active status.",
)
async def list_items(
    active_only: bool = Query(
        default=False,
        description="If true, only return active items",
    ),
    _user: UserContext = Depends(require_reader),
    service: ItemService = Depends(get_item_service),
) -> list[Item]:
    """List all items."""
    with trace_span("list_items_endpoint", attributes={"active_only": active_only}):
        items = await service.list_items(active_only=active_only)
        return items


@router.get(
    "/{item_id}",
    response_model=Item,
    summary="Get an item by ID",
    description="Retrieve a single item by its unique identifier.",
)
async def get_item(
    item_id: str,
    _user: UserContext = Depends(require_reader),
    service: ItemService = Depends(get_item_service),
) -> Item:
    """Get an item by ID."""
    with trace_span("get_item_endpoint", attributes={"item_id": item_id}):
        item = await service.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
        return item


@router.post(
    "/",
    response_model=Item,
    status_code=201,
    summary="Create a new item",
    description="Create a new item with the provided data.",
)
async def create_item(
    item_create: ItemCreate,
    _user: UserContext = Depends(require_admin),
    service: ItemService = Depends(get_item_service),
) -> Item:
    """Create a new item."""
    with trace_span("create_item_endpoint", attributes={"name": item_create.name}):
        created = await service.create_item(item_create)
        return created


@router.patch(
    "/{item_id}",
    response_model=Item,
    summary="Update an item",
    description="Update an existing item. Only provided fields will be updated.",
)
async def update_item(
    item_id: str,
    item_update: ItemUpdate,
    _user: UserContext = Depends(require_admin),
    service: ItemService = Depends(get_item_service),
) -> Item:
    """Update an existing item."""
    with trace_span("update_item_endpoint", attributes={"item_id": item_id}):
        updated = await service.update_item(item_id, item_update)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
        return updated


@router.delete(
    "/{item_id}",
    status_code=204,
    summary="Delete an item",
    description="Delete an item by its unique identifier.",
)
async def delete_item(
    item_id: str,
    _user: UserContext = Depends(require_admin),
    service: ItemService = Depends(get_item_service),
) -> None:
    """Delete an item."""
    with trace_span("delete_item_endpoint", attributes={"item_id": item_id}):
        deleted = await service.delete_item(item_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
