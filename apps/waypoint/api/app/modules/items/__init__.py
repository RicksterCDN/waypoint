"""Items module - example CRUD module demonstrating the module pattern."""

from .routes import router as items_router
from .schemas import Item, ItemCreate, ItemUpdate
from .service import ItemService

__all__ = [
    "items_router",
    "Item",
    "ItemCreate",
    "ItemUpdate",
    "ItemService",
]
