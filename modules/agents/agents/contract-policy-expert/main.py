# Copyright (c) Microsoft. All rights reserved.
"""Template hosted agent. Copy this folder to `agents/<your-name>/` and edit."""

import os
from pathlib import Path

import httpx
from activity_protocol import mount_activity_protocol
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.observability import enable_instrumentation
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.optimization import load_config, load_skills_from_dir
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from prompty import load as load_prompty

from evidence_tools import build_evidence_tools
from telemetry import trace_span

load_dotenv()
enable_instrumentation(enable_sensitive_data=False)

AGENT_ROOT = Path(__file__).resolve().parent
TOOLBOX_FEATURES_HEADER = "Toolboxes=V1Preview"
AGENT_NAME = "contract-policy-expert"
TASK_FAMILY = "invoice_assurance_evidence"


def _baseline_instructions() -> str:
    return load_prompty(AGENT_ROOT / "prompt.md").instructions


def _compose_instructions(config) -> str:
    # load_config() returns None when no optimization config is discoverable
    # (e.g. .agent_configs is missing from the image). Fall back to the canonical
    # prompt.md so the container still serves instead of crashing on startup.
    if config is None:
        return _baseline_instructions()

    if getattr(config, "skills_dir", None) and not getattr(config, "skills", None):
        config.skills = load_skills_from_dir(Path(config.skills_dir))

    instructions = config.compose_instructions()
    skills = getattr(config, "skills", None) or []
    if not skills:
        return instructions

    sections = [instructions, "", "## Procedural Context"]
    for skill in skills:
        body = getattr(skill, "body", "")
        if body:
            sections.extend(["", f"### {skill.name}", body])
    return "\n".join(sections)


class _ToolboxAuth(httpx.Auth):
    def __init__(self, get_token):
        self._get_token = get_token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def _toolbox_mcp_endpoint() -> str | None:
    """Resolve this agent's project toolbox MCP endpoint.

    Prefer the explicit ``TOOLBOX_MCP_ENDPOINT`` (set by the deploy workflow's
    create_toolbox step). Fall back to the deterministic, name-based endpoint
    derived from ``FOUNDRY_PROJECT_ENDPOINT`` so a flaky or skipped toolbox step
    never leaves FoundryIQ's PRIMARY ``knowledge_base`` MCP tool unwired — this
    mirrors how invoice-analyst resolves its own toolbox. The toolbox is created
    before the agent serves traffic, and the ``gather_foundry_evidence`` fallback
    still covers the rare case where the toolbox itself is missing.
    """
    endpoint = os.environ.get("TOOLBOX_MCP_ENDPOINT")
    if endpoint:
        return endpoint
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        return None
    return f"{project_endpoint.rstrip('/')}/toolboxes/{AGENT_NAME}-tools/mcp?api-version=v1"


def _build_toolbox_tool(credential):
    endpoint = _toolbox_mcp_endpoint()
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
    config = load_config()
    # Infra owns the model *deployment* name via AZURE_AI_MODEL_DEPLOYMENT_NAME, so a
    # fresh one-click env stays portable (matches the other hosted agents). Fall back to
    # the optimizer/baseline model only when the env var is absent (e.g. optimizer
    # candidate runs). metadata.yaml's model is a catalog name, not a deployment name.
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or (
        config.model if config else None
    )
    with trace_span(
        "configure_contract_policy_review_agent",
        {
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.operation.name": "hosted_agent_startup",
            "gen_ai.request.model": model,
            "business_process": "contract_policy_invoice_review",
            "review_stage": "agent_configuration",
            "intelligence_type": "llm_orchestration",
            "llm_role": "choose_evidence_tools_and_synthesize_answer",
            "available_business_tools": [
                "knowledge_base_retrieve",
                "gather_contract_policy_evidence",
            ],
        },
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
    tools.extend(build_evidence_tools())

    instructions = _compose_instructions(config)

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
