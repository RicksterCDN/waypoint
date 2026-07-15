# Copyright (c) Microsoft. All rights reserved.
"""Assurance Orchestrator invoice assurance scout agent."""

import os
from pathlib import Path

import httpx
from activity_protocol import mount_activity_protocol
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from telemetry import agent_startup_attributes, trace_span
from waypoint_tools import build_waypoint_tools

TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
AGENT_ROOT = Path(__file__).resolve().parent
AGENT_NAME = "assurance-orchestrator"
TASK_FAMILY = "invoice_assurance"

load_dotenv(AGENT_ROOT / ".env")


class _ToolboxAuth(httpx.Auth):
    def __init__(self, get_token):
        self._get_token = get_token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def _build_toolbox_tool(credential):
    endpoint = os.environ.get("TOOLBOX_ENDPOINT") or os.environ.get("TOOLBOX_MCP_ENDPOINT")
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


def _load_instructions() -> str:
    prompt = (AGENT_ROOT / "prompts" / "assurance-orchestrator.md").read_text(encoding="utf-8")
    if prompt.startswith("---"):
        _, _, remainder = prompt.partition("\n---")
        return remainder.lstrip()
    return prompt


def main() -> None:
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    with trace_span(
        "forge.agent.configure",
        agent_startup_attributes(AGENT_NAME, TASK_FAMILY, model),
    ):
        pass

    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=credential,
    )

    # Fan-out is CODE-OWNED, not model-driven. The single `run_invoice_assurance` tool
    # (in build_waypoint_tools) deterministically opens the run, fans out to the domain
    # experts in parallel, and ALWAYS finalizes via the waypoint-recorder in a `finally`
    # (completed on success, failed on timeout/error) — so a cut-off turn can no longer
    # orphan a run at `running`. The experts are reached internally by the workflow's
    # HostedResponsesExpertClient over their own Responses endpoints (per-hire managed
    # identity granted Foundry User via `make grant-hires`); the old model-selected
    # consult_*/handoff tools are intentionally no longer exposed to the model.
    fanout_tools = []
    fanout_tools.extend(build_waypoint_tools())

    tools = []
    toolbox = _build_toolbox_tool(credential)
    if toolbox is not None:
        tools.append(toolbox)
    tools.extend(fanout_tools)

    instructions = _load_instructions()

    agent = Agent(
        client=client,
        instructions=instructions,
        tools=tools,
        default_options={"store": False},
    )

    host = ResponsesHostServer(agent)

    # Microsoft 365 AI Teammate / digital-worker surface. Give it the same
    # tools as the Responses agent so an emailed invoice can trigger the
    # code-owned `run_invoice_assurance` lifecycle (parallel fan-out + guaranteed
    # finalize), but deliberately EXCLUDE the project toolbox MCP tool: per-hire
    # teammate managed identities are granted Foundry User on the project (which
    # covers the expert Responses endpoints) but not the project toolbox MCP endpoint.
    teammate_agent = Agent(
        client=client,
        instructions=instructions,
        tools=fanout_tools,
        default_options={"store": False},
    )
    mount_activity_protocol(host, teammate_agent)
    host.run()


if __name__ == "__main__":
    main()
