# Copyright (c) Microsoft. All rights reserved.
"""WebIQ expert — external/web evidence via Foundry's native web search."""

import os
from pathlib import Path

from activity_protocol import mount_activity_protocol
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from prompty import load as load_prompty
from telemetry import agent_startup_attributes, trace_span

load_dotenv()

AGENT_ROOT = Path(__file__).resolve().parent
# This expert is bound to exactly ONE tool: Foundry's native web search, backed
# by Azure "Grounding with Bing Search". The Bing grounding resource + its project
# connection are provisioned in infra/ (bicep) with the key auto-read via
# listKeys(), so there is NO manual key and NOTHING to inject into this container —
# Foundry resolves the project's bing_grounding connection and runs the search
# server-side. Amount of context Foundry pulls per query: low|medium|high.
WEB_SEARCH_CONTEXT_SIZE = os.environ.get("WEBIQ_SEARCH_CONTEXT_SIZE", "high")
AGENT_NAME = "market-evidence-expert"
TASK_FAMILY = "invoice_assurance_evidence"


def _load_instructions() -> str:
    return load_prompty(AGENT_ROOT / "prompt.md").instructions


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

    # Single tool: the Foundry-hosted web search (Grounding with Bing). No URL or
    # key is needed here — the hosted tool uses the project's bing_grounding
    # connection provisioned in infra/.
    web_search_tool = client.get_web_search_tool(
        search_context_size=WEB_SEARCH_CONTEXT_SIZE
    )

    instructions = _load_instructions()

    agent = Agent(
        client=client,
        instructions=instructions,
        tools=[web_search_tool],
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
