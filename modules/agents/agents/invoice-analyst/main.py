"""Hosted analyst for read-only invoice assurance questions and status."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from activity_protocol import mount_activity_protocol
from adaptive_cards import CARD_INSTRUCTIONS
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from mcp_lifecycle import close_mcp_tools_after_run
from opentelemetry import trace
from prompty import load as load_prompty
from waypoint_status_tools import build_waypoint_status_tools

AGENT_ROOT = Path(__file__).resolve().parent
AGENT_NAME = "invoice-analyst"
AI_SCOPE = "https://ai.azure.com/.default"
TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
TRACER = trace.get_tracer(__name__)


class BearerTokenAuth(httpx.Auth):
    def __init__(self, get_token):
        self._get_token = get_token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def load_instructions() -> str:
    return load_prompty(AGENT_ROOT / "prompt.md").instructions


def toolbox_tool(name: str, endpoint: str | None, credential) -> MCPStreamableHTTPTool | None:
    if not endpoint:
        return None

    token_provider = get_bearer_token_provider(credential, AI_SCOPE)
    http_client = httpx.AsyncClient(
        auth=BearerTokenAuth(token_provider),
        headers={"Foundry-Features": TOOLBOX_FEATURES_HEADER},
        timeout=120.0,
    )
    return MCPStreamableHTTPTool(
        name=name,
        url=endpoint,
        http_client=http_client,
        load_prompts=False,
    )


def foundryiq_kb_endpoint() -> str | None:
    explicit = os.environ.get("FOUNDRYIQ_KB_TOOLBOX_MCP_ENDPOINT")
    if explicit:
        return explicit

    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        return None
    return f"{project_endpoint.rstrip('/')}/toolboxes/invoice-analyst-tools/mcp?api-version=v1"


def build_tools(credential) -> list[object]:
    # A single server-side toolbox bridge: `invoice-analyst-tools` now carries
    # BOTH the knowledge-base MCP tool AND a read-only projection of WaypointIQ
    # (see scripts/ensure_analyst_waypoint_tool.py). Reaching Waypoint through a
    # dedicated client-side MCP bridge used to crash on Foundry hosted-agent
    # deactivation (anyio cancel-scope teardown across tasks) -> "Function
    # failed". Folding the Waypoint reads into this toolbox makes the Foundry
    # ToolServer execute them server-side, so the container keeps just one
    # lightweight MCP bridge.
    tools = []
    tool = toolbox_tool("foundryiq_kb", foundryiq_kb_endpoint(), credential)
    if tool is not None:
        tools.append(tool)
    return tools


def main() -> None:
    load_dotenv(AGENT_ROOT / ".env")

    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    credential = DefaultAzureCredential()

    with TRACER.start_as_current_span("forge.invoice_analyst.configure") as span:
        span.set_attribute("agent.name", AGENT_NAME)
        span.set_attribute("ai.model_deployment", model)
        span.set_attribute("tool.foundryiq_kb.enabled", foundryiq_kb_endpoint() is not None)
        span.set_attribute(
            "tool.waypoint_status.enabled",
            os.environ.get("WAYPOINT_API_BASE_URL") is not None,
        )

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=credential,
    )

    instructions = load_instructions()
    status_tools = build_waypoint_status_tools()

    agent = Agent(
        client=client,
        instructions=instructions,
        tools=[*build_tools(credential), *status_tools],
        middleware=[close_mcp_tools_after_run],
        default_options={"store": False},
    )

    host = ResponsesHostServer(agent)

    # The Microsoft 365 activity surface (hired AI Teammate) reaches the
    # container directly. Give it the consolidated `invoice-analyst-tools`
    # toolbox (knowledge base + read-only WaypointIQ, folded server-side by
    # scripts/ensure_analyst_waypoint_tool.py) so it can answer operational,
    # status, and contract/policy questions in Teams. That toolbox's WaypointIQ
    # tool authenticates as the Foundry *project* managed identity (which holds
    # Waypoint.Read and is in Waypoint's APP_MSAL_ALLOWED_APP_IDS), so calls run
    # server-side in the Foundry ToolServer and succeed regardless of which
    # ephemeral per-hire managed identity the teammate container runs as.
    #
    # The direct Waypoint status tools are intentionally omitted here: they call
    # Waypoint as the per-hire MI, which is not allow-listed and would 401.
    # Run/case status is instead served through the toolbox
    # (waypointiq_get_runs / waypointiq_get_cases).
    #
    # The mcp_lifecycle middleware is kept because the teammate still carries one
    # client-side MCP bridge (foundryiq_kb); without it the streamable-HTTP
    # session outlives the request and the anyio cancel-scope teardown surfaces
    # to the model as "Function failed" (see mcp_lifecycle.py).
    # The teammate surface renders Adaptive Cards (see activity_protocol.py),
    # so it gets an extra instruction to emit a structured card payload. The
    # Responses agent above stays on plain Markdown and is left untouched.
    teammate_agent = Agent(
        client=client,
        instructions=instructions + CARD_INSTRUCTIONS,
        tools=build_tools(credential),
        middleware=[close_mcp_tools_after_run],
        default_options={"store": False},
    )
    mount_activity_protocol(host, teammate_agent)
    host.run()


if __name__ == "__main__":
    main()
