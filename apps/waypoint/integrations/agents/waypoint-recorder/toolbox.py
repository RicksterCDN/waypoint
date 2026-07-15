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

from azure.ai.projects.models import CodeInterpreterTool

DESCRIPTION = "Fusion + policy-check support tools for the waypoint_recorder agent"

# The waypoint_recorder's primary capability is its Waypoint write tools (see
# waypoint_write_tools.py), attached directly in main.py. The toolbox keeps a
# code interpreter for deterministic numeric fusion (money-at-risk, confidence).
TOOLBOX = [
    CodeInterpreterTool(name="code_interpreter"),
]
