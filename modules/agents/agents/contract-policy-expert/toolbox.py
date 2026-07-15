"""Toolbox definition consumed by `scripts/create_toolbox.py --agent <name>`.

Each agent owns its own toolbox so capabilities are scoped per agent.
The script imports `TOOLBOX` (required) and `DESCRIPTION` (optional) and
creates/promotes a toolbox version named `<agent>-tools`.

Tier 1 (no setup):
    WebSearchTool, CodeInterpreterTool, ImageGenTool

Tier 2 (needs Foundry connection / index):
    AzureAISearchTool, FileSearchTool, SharepointPreviewTool

Tier 3 (MCP servers):
    MCPTool(server_label=..., server_url=..., require_approval=...)
    Public examples:
      - MS Learn:  https://learn.microsoft.com/api/mcp
      - GitHub:    https://api.githubcopilot.com/mcp  (needs project_connection_id)

Tier 4 (specialized):
    BingGroundingTool, BrowserAutomationPreviewTool, ComputerUsePreviewTool,
    OpenApiTool, AzureFunctionTool, MicrosoftFabricPreviewTool, ...
"""

import os

from azure.ai.projects.models import MCPTool

DESCRIPTION = "Foundry knowledge / index retrieval tools for contract-policy-expert"

# The contracts knowledge base is exposed to Foundry agents through the
# `kb-mcp-connection` RemoteTool project connection. The data-plane initializer
# keeps that connection pointed at the live `contracts-kb` MCP endpoint.
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
