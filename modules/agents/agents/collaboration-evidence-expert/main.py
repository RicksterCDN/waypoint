# Copyright (c) Microsoft. All rights reserved.
"""WorkIQ expert — Microsoft 365 collaboration evidence."""

import os
import logging
from pathlib import Path

import httpx
from activity_protocol import mount_activity_protocol
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import FoundryAgentRequestContext, get_request_context
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from prompty import load as load_prompty
from telemetry import agent_startup_attributes, trace_span

load_dotenv()

AGENT_ROOT = Path(__file__).resolve().parent
AI_SCOPE = "https://ai.azure.com/.default"
TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
AGENT_NAME = "collaboration-evidence-expert"
TASK_FAMILY = "invoice_assurance_evidence"
logger = logging.getLogger("workiq.mcp")


def _load_instructions() -> str:
    return load_prompty(AGENT_ROOT / "prompt.md").instructions


class _ToolboxUserContextAuth(httpx.Auth):
    """Inject Foundry auth and current-request context on toolbox calls.

    WorkIQ toolbox connections use UserEntraToken. The container authenticates
    to the toolbox with its normal Foundry token, while the request context
    headers let Foundry bind downstream WorkIQ calls to the invoking user.
    """

    def __init__(self, get_token):
        self._get_token = get_token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        request.headers["Foundry-Features"] = TOOLBOX_FEATURES_HEADER
        call_id = _current_call_id()
        if call_id:
            request.headers.update(
                FoundryAgentRequestContext(call_id=call_id).platform_headers()
            )
        yield request


def _current_call_id() -> str | None:
    try:
        return get_request_context().call_id
    except Exception:
        return None


def _workiq_toolbox_endpoint() -> str | None:
    endpoint = os.environ.get("WORKIQ_TOOLBOX_MCP_ENDPOINT") or os.environ.get("TOOLBOX_MCP_ENDPOINT")
    if endpoint:
        return endpoint
    toolbox_name = os.environ.get("WORKIQ_TOOLBOX_NAME")
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if toolbox_name and project_endpoint:
        return f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
    return None


def _build_workiq_toolbox_tool(credential):
    endpoint = _workiq_toolbox_endpoint()
    if not endpoint:
        logger.warning("WorkIQ toolbox endpoint is not configured; collaboration evidence tools disabled.")
        return None
    token_provider = get_bearer_token_provider(credential, AI_SCOPE)
    http_client = httpx.AsyncClient(
        auth=_ToolboxUserContextAuth(token_provider),
        timeout=120.0,
    )
    return MCPStreamableHTTPTool(
        name="workiq",
        url=endpoint,
        http_client=http_client,
        load_prompts=False,
    )


def main() -> None:
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    with trace_span(
        "forge.agent.configure",
        agent_startup_attributes(AGENT_NAME, TASK_FAMILY, model),
    ) as span:
        span.set_attribute("tool.workiq_toolbox.enabled", _workiq_toolbox_endpoint() is not None)

    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=credential,
    )

    tools = []
    workiq_toolbox = _build_workiq_toolbox_tool(credential)
    if workiq_toolbox is not None:
        tools.append(workiq_toolbox)

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
    # so hand the activity protocol a tool-less clone that only uses
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
