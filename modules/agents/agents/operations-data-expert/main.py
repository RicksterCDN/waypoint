# Copyright (c) Microsoft. All rights reserved.
"""Template hosted agent. Copy this folder to `agents/<your-name>/` and edit."""

import os
from pathlib import Path

import httpx
from activity_protocol import mount_activity_protocol
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from prompty import load as load_prompty

from evidence_tools import build_headless_evidence_tools, build_interactive_fabric_tools
from telemetry import agent_startup_attributes, trace_span

load_dotenv()

AGENT_ROOT = Path(__file__).resolve().parent
TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
AGENT_NAME = "operations-data-expert"
TASK_FAMILY = "invoice_assurance_evidence"


def _load_instructions() -> str:
    return load_prompty(AGENT_ROOT / "prompt.md").instructions


class _ToolboxAuth(httpx.Auth):
    def __init__(self, get_token):
        self._get_token = get_token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def _build_toolbox_tool(credential):
    endpoint = os.environ.get("TOOLBOX_MCP_ENDPOINT")
    if not endpoint:
        return None
    token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
    http_client = httpx.AsyncClient(
        auth=_ToolboxAuth(token_provider),
        headers={"Foundry-Features": TOOLBOX_FEATURES_HEADER},
        timeout=120.0,
    )
    return MCPStreamableHTTPTool(
        name="toolbox",
        url=endpoint,
        http_client=http_client,
        load_prompts=False,
    )


def main() -> None:
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    with trace_span(
        "forge.agent.configure",
        agent_startup_attributes(AGENT_NAME, TASK_FAMILY, model),
    ):
        pass

    credential = DefaultAzureCredential()

    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )

    # FabricIQ grounding tools (dual path):
    #  1. interactive/OBO — MicrosoftFabricPreviewTool over the WaypointDataAgent
    #     (waypoint-data-agent-connection). This grounds on-behalf-of the
    #     signed-in user, so it is attached ONLY to the Teammate surface below.
    #     Attaching it to the headless Responses agent makes every turn fail with
    #     an internal server error (no user token to satisfy the OBO tool).
    #  2. headless — the deterministic gather_fabric_evidence function tool that
    #     reads the mirrored operational core over SQL with the project MI. Safe
    #     on every surface; attached to both agents.
    # Both are scoped to suppliers/invoices/invoice_lines/reconciliation_findings.
    headless_tools = build_headless_evidence_tools()
    interactive_fabric_tools = build_interactive_fabric_tools(credential, project_endpoint)

    tools = []
    toolbox = _build_toolbox_tool(credential)
    if toolbox is not None:
        tools.append(toolbox)
    tools.extend(headless_tools)

    instructions = _load_instructions()

    agent = Agent(
        client=client,
        instructions=instructions,
        tools=tools,
        default_options={"store": False},
    )

    host = ResponsesHostServer(agent)

    # Mount /api/messages for Microsoft 365 AI Teammate hires. The hired
    # surface invokes the container directly (no Foundry /responses bridge),
    # so the per-instance MI cannot reach the project *toolbox* MCP endpoint —
    # keep the toolbox tool off this clone. The Fabric data agent tool is
    # different: it is a Foundry-native OBO tool that grounds as the signed-in
    # Teammate user, which is exactly the interactive identity-passthrough path
    # it is designed for, so it IS attached here (and ONLY here). See
    # scripts/grant_instance_mi_roles.py for the per-hire Foundry User grant.
    teammate_agent = Agent(
        client=client,
        instructions=instructions,
        tools=[*headless_tools, *interactive_fabric_tools],
        default_options={"store": False},
    )
    mount_activity_protocol(host, teammate_agent)
    host.run()


if __name__ == "__main__":
    main()
