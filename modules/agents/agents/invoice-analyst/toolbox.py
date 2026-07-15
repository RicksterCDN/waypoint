"""Foundry toolbox definition for invoice-analyst.

This file declares the tools that `scripts/create_toolbox.py` builds into the
`invoice-analyst-tools` toolbox on every deploy. It intentionally lists ONLY
the knowledge-base MCP tool here.

A read-only (GET-only) projection of the durable `waypoint-iq` toolbox is folded
into `invoice-analyst-tools` *after* this build, by
`scripts/ensure_analyst_waypoint_tool.py` (wired into deploy.yml right after the
toolbox step; also `make analyst-waypoint-tool`). We do it as a post-step rather
than declaring the OpenAPI tool here because the canonical WaypointIQ spec + auth
live with the externally-provisioned `waypoint-iq` toolbox (see
docs/WAYPOINTIQ.md); the merge script reads that live tool and curates it down to
reads so the analyst never drifts from the canonical contract.

Folding WaypointIQ into this one toolbox means the Foundry ToolServer executes
the Waypoint reads server-side. The container therefore keeps a single
lightweight client-side MCP bridge (`foundryiq_kb` -> `invoice-analyst-tools`)
instead of a dedicated WaypointIQ streamable session, which used to crash on
Foundry hosted-agent deactivation ("Function failed").

The WaypointIQ tool's downstream call to Waypoint authenticates as the Foundry
*project* managed identity (doc-canonical — see docs/WAYPOINTIQ.md and
scripts/ensure_analyst_waypoint_tool.py), which holds `Waypoint.Read` and is
allow-listed in Waypoint's `APP_MSAL_ALLOWED_APP_IDS`. The project MI is stable
across redeploys and per-hire identities. Run `make waypoint-iq-auth`
(scripts/ensure_waypoint_iq_managed_identity.py) to (re)apply that auth to the
source `waypoint-iq` toolbox in a given azd environment.
"""

from __future__ import annotations

import os

from azure.ai.projects.models import MCPTool

DESCRIPTION = "FoundryIQ contract and policy knowledge tools for invoice-analyst"

REQUIRED_ENV = {
    "knowledge_base": ("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME",),
}

TOOLBOX = []

_kb_mcp_connection = os.environ.get("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME")
if _kb_mcp_connection:
    TOOLBOX.append(
        MCPTool(
            server_label="knowledge_base",
            project_connection_id=_kb_mcp_connection,
            require_approval="never",
        )
    )
