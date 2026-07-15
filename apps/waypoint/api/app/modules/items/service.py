"""Business logic for the Items module.

This is an in-memory implementation for demonstration purposes.
Replace with database operations (e.g., Cosmos DB) for production use.
"""

import logging
import uuid
from datetime import UTC, datetime

from ...common.tracer import trace
from .schemas import Item, ItemCreate, ItemUpdate

logger = logging.getLogger(__name__)


class ItemService:
    """Service class for item operations.

    This implementation uses in-memory storage for simplicity.
    For production, inject a database service (e.g., CosmosService).

    Example with Cosmos DB:
        def __init__(self, cosmos_service: CosmosService):
            self.cosmos_service = cosmos_service
    """

    def __init__(self) -> None:
        """Initialize with in-memory storage."""
        self._items: dict[str, Item] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Add some sample items for demonstration."""
        now = datetime.now(UTC)
        sample_items = [
            Item(
                id="item-1",
                name="Welcome Item",
                description="This is a sample item to get you started",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            Item(
                id="item-2",
                name="Documentation",
                description="Check out the README for more information",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
        ]
        for item in sample_items:
            self._items[item.id] = item

    @trace
    async def list_items(self, active_only: bool = False) -> list[Item]:
        """List all items, optionally filtering by active status.

        Args:
            active_only: If True, only return active items

        Returns:
            List of items
        """
        logger.info(f"Listing items (active_only={active_only})")
        items = list(self._items.values())
        if active_only:
            items = [item for item in items if item.is_active]
        return sorted(items, key=lambda x: x.created_at, reverse=True)

    @trace
    async def get_item(self, item_id: str) -> Item | None:
        """Get an item by ID.

        Args:
            item_id: The item's unique identifier

        Returns:
            The item if found, None otherwise
        """
        logger.info(f"Getting item: {item_id}")
        return self._items.get(item_id)

    @trace
    async def create_item(self, item_create: ItemCreate) -> Item:
        """Create a new item.

        Args:
            item_create: The item data to create

        Returns:
            The created item with generated ID and timestamps
        """
        now = datetime.now(UTC)
        item = Item(
            id=f"item-{uuid.uuid4().hex[:8]}",
            name=item_create.name,
            description=item_create.description,
            is_active=item_create.is_active,
            created_at=now,
            updated_at=now,
        )
        self._items[item.id] = item
        logger.info(f"Created item: {item.id}")
        return item

    @trace
    async def update_item(self, item_id: str, item_update: ItemUpdate) -> Item | None:
        """Update an existing item.

        Args:
            item_id: The item's unique identifier
            item_update: The fields to update

        Returns:
            The updated item if found, None otherwise
        """
        existing = self._items.get(item_id)
        if not existing:
            logger.warning(f"Item not found for update: {item_id}")
            return None

        # Update only provided fields
        update_data = item_update.model_dump(exclude_unset=True)
        updated_item = existing.model_copy(
            update={
                **update_data,
                "updated_at": datetime.now(UTC),
            }
        )
        self._items[item_id] = updated_item
        logger.info(f"Updated item: {item_id}")
        return updated_item

    @trace
    async def delete_item(self, item_id: str) -> bool:
        """Delete an item by ID.

        Args:
            item_id: The item's unique identifier

        Returns:
            True if the item was deleted, False if not found
        """
        if item_id in self._items:
            del self._items[item_id]
            logger.info(f"Deleted item: {item_id}")
            return True
        logger.warning(f"Item not found for deletion: {item_id}")
        return False


# Singleton instance for the in-memory service
# For production with dependency injection, remove this and use FastAPI's Depends()
_item_service: ItemService | None = None


def get_item_service() -> ItemService:
    """Get the item service instance.

    For production with a database, this would inject the database service:

        def get_item_service(
            settings: Settings = Depends(get_settings),
        ) -> ItemService:
            cosmos_service = CosmosService(
                connection_string=settings.database_connection,
                database_name=settings.database_name,
                container_name="items",
            )
            return ItemService(cosmos_service)
    """
    global _item_service
    if _item_service is None:
        _item_service = ItemService()
    return _item_service
