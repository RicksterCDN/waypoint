# Copyright (c) Microsoft. All rights reserved.
"""Waypoint Recorder agent — fuses expert evidence, runs the final policy check, and writes
the governed assurance result to Waypoint. This is the pipeline's only write boundary."""

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
from waypoint_write_tools import build_waypoint_write_tools

load_dotenv()

AGENT_ROOT = Path(__file__).resolve().parent
TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
AGENT_NAME = "waypoint-recorder"
TASK_FAMILY = "invoice_assurance_decision"


def _load_instructions() -> str:
    prompt = (AGENT_ROOT / "prompts" / "waypoint-recorder.md").read_text(encoding="utf-8")
    if prompt.startswith("---"):
        _, _, remainder = prompt.partition("\n---")
        return remainder.lstrip()
    return prompt


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

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=credential,
    )

    tools = []
    toolbox = _build_toolbox_tool(credential)
    if toolbox is not None:
        tools.append(toolbox)
    tools.extend(build_waypoint_write_tools())

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
    # so the per-instance MI cannot reach the project toolbox MCP endpoint.
    # Hand the activity protocol a tool-less clone that only uses
    # FoundryChatClient. See scripts/grant_instance_mi_roles.py for the
    # per-hire Foundry User role grant.
    teammate_agent = Agent(
        client=client,
        instructions=instructions,
        default_options={"store": False},
    )
    mount_activity_protocol(host, teammate_agent)
    host.run()


if __name__ == "__main__":
    main()
