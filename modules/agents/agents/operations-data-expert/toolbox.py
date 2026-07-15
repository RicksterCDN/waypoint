"""Toolbox definition consumed by `scripts/create_toolbox.py --agent <name>`.

operations-data-expert has NO Foundry toolbox. Its single capability — the live
Waypoint Fabric data agent (WaypointDataAgent) — is a Foundry-native
`MicrosoftFabricPreviewTool` that must be bound DIRECTLY to the agent so its
on-behalf-of (OBO) identity passthrough works (the tool queries Fabric as the
signed-in user; service-principal auth is not supported). A project *toolbox*
MCP endpoint is called with the agent/instance managed identity, which is the
wrong identity for OBO, so the Fabric tool is attached in `main.py` via
`evidence_tools.build_evidence_tools(...)` instead of here.

The connection itself (`waypoint-data-agent-connection`, category
`MicrosoftFabric`) is provisioned out-of-band by
`scripts/initialize_fabric_data_agent.py`.

Because this list is empty, the deploy workflow skips toolbox creation for this
agent (see .github/workflows/deploy.yml) and leaves `TOOLBOX_MCP_ENDPOINT`
unset, so `main.py` attaches no toolbox MCP tool.
"""

DESCRIPTION = "operations-data-expert has no Foundry toolbox; Fabric data agent tool is bound directly in main.py."

TOOLBOX: list = []
