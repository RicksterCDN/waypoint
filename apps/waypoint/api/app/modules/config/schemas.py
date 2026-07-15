"""Pydantic schemas for runtime drill-down configuration."""

from pydantic import BaseModel


class DrilldownConfig(BaseModel):
    """Non-secret deep-link configuration for Foundry and App Insights views.

    Surfaced at runtime so the Runs, Costs, and Optimize screens can build deep links to
    Foundry and Azure Monitor without baking endpoints into the web bundle.
    """

    foundry_endpoint: str = ""
    foundry_project_url: str = ""
    app_insights_resource_id: str = ""
