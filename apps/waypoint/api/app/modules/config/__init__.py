"""Runtime drill-down configuration surfaced to the authenticated web UI."""

from .routes import router as config_router

__all__ = ["config_router"]
