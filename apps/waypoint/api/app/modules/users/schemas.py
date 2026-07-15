"""Schemas for current user profile responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    """Current authenticated user profile."""

    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    name: str
    email: str
    source: Literal["msal-bearer", "api-key", "dev-header", "local-dev"]
