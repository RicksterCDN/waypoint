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

from azure.ai.projects.models import (
    CodeInterpreterTool,
    MCPTool,
    WebSearchTool,
)

DESCRIPTION = "Tools for the my-new-agent agent"

TOOLBOX = [
    WebSearchTool(name="web_search"),
    CodeInterpreterTool(name="code_interpreter"),
    MCPTool(
        server_label="mslearn",
        server_url="https://learn.microsoft.com/api/mcp",
        require_approval="never",
    ),
]
