"""WorkIQ toolbox definition for collaboration-evidence-expert.

The toolbox uses existing Foundry project RemoteTool connections that are
configured with UserEntraToken auth. At runtime the hosted agent forwards the
current request context to this toolbox so WorkIQ/Microsoft 365 calls run under
the signed-in user's authority.
"""

from __future__ import annotations

from azure.ai.projects.models import MCPTool

DESCRIPTION = "Microsoft 365 / WorkIQ evidence tools for collaboration-evidence-expert"

# Foundry persists server_label verbatim on each immutable toolbox version.
# scripts/create_toolbox.py diffs the persisted labels of the current default
# version against this TOOLBOX to decide whether a re-deploy would drop a tool,
# so these labels MUST stay stable across runs -- changing the casing/format of
# an already-deployed label reads as "rename" (drop old + add new) and the
# deploy hard-fails ("Refusing to bump ... would drop tools"). We keep
# server_label identical to project_connection_id (the exact Bicep connection
# name from infra/main.bicep: WorkIQCopilot / WorkIQTeams / WorkIQSharePoint)
# so the label is unambiguous and the UserEntraToken binding resolves. A fresh
# environment creates this toolbox first-time from these labels, so every
# subsequent run is a clean superset bump (idempotent).
TOOLBOX = [
    MCPTool(
        server_label="WorkIQCopilot",
        project_connection_id="WorkIQCopilot",
        require_approval="never",
    ),
    MCPTool(
        server_label="WorkIQTeams",
        project_connection_id="WorkIQTeams",
        require_approval="never",
    ),
    MCPTool(
        server_label="WorkIQSharePoint",
        project_connection_id="WorkIQSharePoint",
        require_approval="never",
    ),
]
