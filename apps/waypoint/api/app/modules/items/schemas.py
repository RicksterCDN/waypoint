"""Pydantic schemas for the Items module."""

from datetime import datetime

from pydantic import BaseModel, Field


class ItemBase(BaseModel):
    """Base schema with common item fields."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the item",
    )
    description: str = Field(
        default="",
        max_length=500,
        description="Optional description of the item",
    )
    is_active: bool = Field(
        default=True,
        description="Whether the item is active",
    )


class ItemCreate(ItemBase):
    """Schema for creating a new item."""

    pass


class ItemUpdate(BaseModel):
    """Schema for updating an existing item. All fields are optional."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Name of the item",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Optional description of the item",
    )
    is_active: bool | None = Field(
        default=None,
        description="Whether the item is active",
    )


class Item(ItemBase):
    """Full item schema with all fields including system-generated ones."""

    id: str = Field(
        ...,
        description="Unique identifier for the item",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when the item was created",
    )
    updated_at: datetime = Field(
        ...,
        description="Timestamp when the item was last updated",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "item-123",
                    "name": "Example Item",
                    "description": "This is an example item",
                    "is_active": True,
                    "created_at": "2025-01-26T12:00:00Z",
                    "updated_at": "2025-01-26T12:00:00Z",
                }
            ]
        }
    }
