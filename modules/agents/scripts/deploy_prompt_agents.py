"""Create/update Forge prompt-agent experts from their canonical prompt.md files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, MCPToolFilter, PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from prompty import load as load_prompty


REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_EXPERT_FOLDERS: tuple[str, ...] = ()
DEFAULT_SCOPE = "https://ai.azure.com/.default"
GPT5_TEMPERATURE_PREFIXES = ("gpt-5",)


@dataclass(frozen=True)
class PromptAgentSource:
    folder: str
    prompt_path: Path
    agent_name: str
    display_name: str | None
    description: str | None
    model: str
    instructions: str
    temperature: float | None
    smoke_invoice_id: str | None
    evidence_plane: str | None
    forge: dict[str, Any]
    tools: list[Any]


@dataclass
class DeploymentResult:
    agent_name: str
    action: str
    version: str | None
    warnings: list[str]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-endpoint",
        default=_first_env("PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT"),
        help="Foundry project endpoint, e.g. https://<account>.services.ai.azure.com/api/projects/<project>",
    )
    parser.add_argument(
        "--agent",
        action="append",
        help="Prompt expert folder/name to deploy. Repeat to deploy a subset. Defaults to all prompt-enabled experts.",
    )
    parser.add_argument("--model", help="Override model deployment for every prompt agent.")
    parser.add_argument(
        "--include-temperature",
        action="store_true",
        help="Include prompt.md temperature even for models known to reject sampling parameters.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build definitions and print the plan without updating Foundry.")
    parser.add_argument("--skip-smoke", action="store_true", help="Do not invoke agents after create/update.")
    parser.add_argument("--output-json", action="store_true", help="Print machine-readable JSON results.")
    args = parser.parse_args()

    folders = tuple(args.agent or _discover_prompt_agent_folders())
    if not folders:
        print("No prompt-agent experts are enabled; nothing to deploy.")
        return 0

    if not args.project_endpoint:
        print("::error::--project-endpoint or PROJECT_ENDPOINT/AZURE_AI_PROJECT_ENDPOINT/FOUNDRY_PROJECT_ENDPOINT is required", file=sys.stderr)
        return 2

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=args.project_endpoint, credential=credential)
    results: list[dict[str, Any]] = []
    failed = False

    for folder in folders:
        source = _load_source(folder, model_override=args.model)
        warnings: list[str] = []
        definition = _build_definition(
            client,
            source,
            include_temperature=args.include_temperature,
            warnings=warnings,
        )
        existing_kind = _existing_agent_kind(client, source.agent_name)
        if existing_kind and existing_kind != "prompt":
            print(
                f"::error::{source.agent_name} already exists as kind={existing_kind}; "
                "not replacing a non-prompt agent. Delete or rename explicitly after review.",
                file=sys.stderr,
            )
            failed = True
            continue

        if args.dry_run:
            result = DeploymentResult(source.agent_name, "would_update" if existing_kind else "would_create", None, warnings)
        else:
            version = client.agents.create_version(
                source.agent_name,
                definition=definition,
                description=source.description or "",
                metadata=_metadata_for(source),
            )
            version_dict = version.as_dict() if hasattr(version, "as_dict") else {}
            result = DeploymentResult(
                source.agent_name,
                "updated" if existing_kind else "created",
                str(version_dict.get("version") or getattr(version, "version", "") or ""),
                warnings,
            )

        smoke: dict[str, Any] | None = None
        if not args.dry_run and not args.skip_smoke:
            smoke = _smoke_agent(
                args.project_endpoint,
                credential,
                source,
                model=args.model or source.model,
            )
            if not smoke.get("ok"):
                if _smoke_required(source):
                    failed = True
                else:
                    print(
                        f"::warning::{source.agent_name}: post-deploy smoke failed but "
                        "requireSmoke=false — agent deployed; lane not yet provable from CI "
                        "(see prompt.md). Not failing the run.",
                        file=sys.stderr,
                    )

        item = {
            "agentName": result.agent_name,
            "action": result.action,
            "version": result.version,
            "warnings": result.warnings,
            "smoke": smoke,
            "smokeRequired": _smoke_required(source),
        }
        results.append(item)
        if not args.output_json:
            _print_human(item)

    if args.output_json:
        print(json.dumps(results, indent=2))
    return 1 if failed else 0


def _load_source(folder: str, *, model_override: str | None = None) -> PromptAgentSource:
    prompt_path = REPO_ROOT / "agents" / folder / "prompt.md"
    prompt = load_prompty(prompt_path)
    metadata = prompt.metadata if isinstance(prompt.metadata, dict) else {}
    forge = metadata.get("forge", {})
    deployment = forge.get("deployment", {})
    prompt_deploy = deployment.get("prompt", {})
    hosted_deploy = deployment.get("hosted", {})
    agent_name = prompt_deploy.get("agentName")

    if prompt.name != folder:
        raise ValueError(f"{prompt_path}: prompt name {prompt.name!r} must match folder {folder!r}")
    if agent_name != folder:
        raise ValueError(f"{prompt_path}: metadata.forge.deployment.prompt.agentName must be {folder!r}")
    if prompt_deploy.get("enabled") is not True:
        raise ValueError(f"{prompt_path}: prompt deployment must be enabled")
    if hosted_deploy.get("enabled") is not False:
        raise ValueError(f"{prompt_path}: hosted deployment must be disabled for prompt-agent replacement")
    instructions = prompt.instructions or ""
    if not instructions.strip():
        raise ValueError(f"{prompt_path}: prompt body/instructions are required")

    return PromptAgentSource(
        folder=folder,
        prompt_path=prompt_path,
        agent_name=agent_name,
        display_name=prompt.display_name,
        description=prompt.description,
        model=model_override or prompt.model.id,
        instructions=instructions,
        temperature=getattr(prompt.model.options, "temperature", None),
        smoke_invoice_id=forge.get("smoke", {}).get("invoiceId"),
        evidence_plane=metadata.get("evidence_plane"),
        forge=forge,
        tools=list(prompt.tools),
    )


def _discover_prompt_agent_folders() -> tuple[str, ...]:
    folders: list[str] = []
    for prompt_path in sorted((REPO_ROOT / "agents").glob("*/prompt.md")):
        prompt = load_prompty(prompt_path)
        metadata = prompt.metadata if isinstance(prompt.metadata, dict) else {}
        forge = metadata.get("forge", {}) if isinstance(metadata.get("forge"), dict) else {}
        deployment = forge.get("deployment", {}) if isinstance(forge.get("deployment"), dict) else {}
        prompt_deploy = deployment.get("prompt", {}) if isinstance(deployment.get("prompt"), dict) else {}
        if prompt_deploy.get("enabled") is True:
            folders.append(prompt_path.parent.name)
    return tuple(folders)


def _build_definition(
    client: AIProjectClient,
    source: PromptAgentSource,
    *,
    include_temperature: bool,
    warnings: list[str],
) -> PromptAgentDefinition:
    kwargs: dict[str, Any] = {
        "model": source.model,
        "instructions": source.instructions,
    }
    if source.temperature is not None and (include_temperature or not _model_rejects_temperature(source.model)):
        kwargs["temperature"] = source.temperature

    tools = _build_tools(client, source, warnings)
    if tools:
        kwargs["tools"] = tools
    return PromptAgentDefinition(**kwargs)


def _build_tools(client: AIProjectClient, source: PromptAgentSource, warnings: list[str]) -> list[Any]:
    built: list[Any] = []
    bindings = source.forge.get("toolBindings", {})
    for prompt_tool in source.tools:
        kind = getattr(prompt_tool, "kind", None)
        if kind != "mcp":
            warnings.append(
                f"Skipped local {kind or 'unknown'} tool {getattr(prompt_tool, 'name', '<unnamed>')}; "
                "prompt-agent automation only binds remote MCP tools."
            )
            continue

        binding = _find_binding(bindings, prompt_tool)
        connection_name = binding.get("projectConnectionName") or binding.get("project_connection_name")
        endpoint = binding.get("endpoint")
        if connection_name:
            connection = _connection_dict(client, connection_name)
            endpoint = endpoint or connection.get("target")
        if not endpoint:
            raise ValueError(
                f"{source.prompt_path}: MCP tool {getattr(prompt_tool, 'name', '<unnamed>')} "
                "needs endpoint or projectConnectionName with a target"
            )

        allowed_tools = getattr(prompt_tool, "allowed_tools", None) or binding.get("allowedTools")
        server_label = (
            getattr(prompt_tool, "server_name", None)
            or binding.get("serverName")
            or connection_name
            or getattr(prompt_tool, "name", None)
        )
        built.append(
            MCPTool(
                server_label=server_label,
                server_url=endpoint,
                allowed_tools=MCPToolFilter(tool_names=allowed_tools) if allowed_tools else None,
                require_approval="never",
                project_connection_id=connection_name,
            )
        )
    return built


def _find_binding(bindings: dict[str, Any], prompt_tool: Any) -> dict[str, Any]:
    tool_name = getattr(prompt_tool, "name", "")
    server_name = getattr(prompt_tool, "server_name", "")
    for value in bindings.values():
        if isinstance(value, dict) and server_name and value.get("serverName") == server_name:
            return value
    if tool_name == "web_iq" and isinstance(bindings.get("webIq"), dict):
        return bindings["webIq"]
    if tool_name == "knowledge_base" and isinstance(bindings.get("knowledgeBase"), dict):
        return bindings["knowledgeBase"]
    return {}


def _connection_dict(client: AIProjectClient, connection_name: str) -> dict[str, Any]:
    try:
        connection = client.connections.get(connection_name)
    except ResourceNotFoundError as exc:
        raise ValueError(f"Project connection {connection_name!r} was not found") from exc
    return connection.as_dict() if hasattr(connection, "as_dict") else {}


def _existing_agent_kind(client: AIProjectClient, agent_name: str) -> str | None:
    try:
        details = client.agents.get(agent_name)
    except ResourceNotFoundError:
        return None
    data = details.as_dict() if hasattr(details, "as_dict") else {}
    return (
        data.get("versions", {})
        .get("latest", {})
        .get("definition", {})
        .get("kind")
    )


def _metadata_for(source: PromptAgentSource) -> dict[str, str]:
    return {
        "forgeSource": "prompt.md",
        "forgePromptPath": str(source.prompt_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "forgeEvidencePlane": str(source.evidence_plane or ""),
        "forgeDeployment": "prompt-agent",
    }


def _smoke_agent(
    project_endpoint: str,
    credential: DefaultAzureCredential,
    source: PromptAgentSource,
    *,
    model: str,
) -> dict[str, Any]:
    invoice_id = source.smoke_invoice_id or "INV-2026-08034"
    prompt = (
        "Smoke-test this prompt agent against the shared expert-evidence contract.\n"
        f"Invoice id: {invoice_id}\n"
        "Return ONLY a JSON object with output_type, agent, plane, expert_evidence, "
        "summary, and unsupported. Do not write to Waypoint."
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {credential.get_token(DEFAULT_SCOPE).token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": prompt,
        "store": False,
        "agent_reference": {
            "name": source.agent_name,
            "type": "agent_reference",
        },
    }
    with httpx.Client(timeout=180) as client:
        response = client.post(_responses_url(project_endpoint), headers=headers, json=payload)
    if response.is_error:
        return {"ok": False, "statusCode": response.status_code, "error": response.text[:1000]}
    text = _extract_output_text(response.json())
    contract = _parse_contract(text)
    problems = _validate_contract(contract, expected_agent=source.agent_name)
    return {
        "ok": not problems,
        "responseId": response.json().get("id") if isinstance(response.json(), dict) else None,
        "agent": contract.get("agent") if isinstance(contract, dict) else None,
        "plane": contract.get("plane") if isinstance(contract, dict) else None,
        "evidenceCount": len(_contract_evidence(contract)) if isinstance(contract, dict) else 0,
        "summary": contract.get("summary") if isinstance(contract, dict) else None,
        "problems": problems,
    }


def _responses_url(project_endpoint: str) -> str:
    base = project_endpoint.rstrip("/")
    if base.endswith("/openai/v1"):
        return f"{base}/responses"
    if base.endswith("/openai/v1/responses"):
        return base
    return f"{base}/openai/v1/responses"


def _extract_output_text(body: object) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return json.dumps(body, ensure_ascii=False)
    if isinstance(body.get("output_text"), str) and body["output_text"]:
        return body["output_text"]
    chunks: list[str] = []
    for item in _as_list(body.get("output")):
        if not isinstance(item, dict):
            continue
        for content in _as_list(item.get("content")):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks) if chunks else json.dumps(body, ensure_ascii=False)


def _parse_contract(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else {}


def _validate_contract(contract: dict[str, Any], *, expected_agent: str) -> list[str]:
    problems: list[str] = []
    if not contract:
        return ["response did not contain a JSON object"]
    if contract.get("agent") != expected_agent:
        problems.append(f"agent was {contract.get('agent')!r}, expected {expected_agent!r}")
    if contract.get("plane") not in {"workiq", "webiq", "foundryiq", "fabriciq"}:
        problems.append(f"plane was {contract.get('plane')!r}")
    if not isinstance(_contract_evidence(contract), list):
        problems.append("expert_evidence or evidence must be a list")
    if not isinstance(contract.get("summary"), str):
        problems.append("summary must be a string")
    return problems


def _contract_evidence(contract: dict[str, Any]) -> list[Any]:
    evidence = contract.get("expert_evidence")
    if isinstance(evidence, list):
        return evidence
    return _as_list(contract.get("evidence"))


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _model_rejects_temperature(model: str) -> bool:
    normalized = model.lower()
    return any(normalized.startswith(prefix) for prefix in GPT5_TEMPERATURE_PREFIXES)


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _smoke_required(source: PromptAgentSource) -> bool:
    """Whether a failed post-deploy smoke should fail the run for this agent.

    Defaults to True. An agent whose evidence lane cannot yet be proven from
    CI (e.g. the WorkIQ Microsoft 365 MCP tools require a signed-in user, not
    the CI application identity) sets
    metadata.forge.deployment.prompt.requireSmoke: false so the agent still
    deploys without blocking the pipeline.
    """
    prompt_deploy = source.forge.get("deployment", {}).get("prompt", {})
    return prompt_deploy.get("requireSmoke", True) is not False


def _print_human(item: dict[str, Any]) -> None:
    print(f"{item['action']}: {item['agentName']} version={item.get('version') or '-'}")
    for warning in item["warnings"]:
        print(f"  warning: {warning}")
    smoke = item.get("smoke")
    if smoke:
        status = "passed" if smoke.get("ok") else "failed"
        if not smoke.get("ok") and item.get("smokeRequired") is False:
            status = "failed (non-blocking; requireSmoke=false)"
        print(
            f"  smoke: {status}; plane={smoke.get('plane')}; "
            f"evidence={smoke.get('evidenceCount')}; summary={smoke.get('summary')}"
        )
        for problem in smoke.get("problems") or []:
            print(f"    problem: {problem}")
        if smoke.get("error"):
            print(f"    error: {smoke['error']}")


if __name__ == "__main__":
    sys.exit(main())
