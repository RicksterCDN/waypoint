#!/usr/bin/env python3
"""Read-only live smoke for the FoundryIQ contracts knowledge base.

Run from the repo root after logging in with Azure CLI:

    python scripts/test_foundryiq_kb.py

The check verifies the Search knowledge source/base, the Foundry RemoteTool
connection, and a real MCP retrieval against the contracts KB. It does not
create, update, or delete Azure resources.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import initialize_contracts_kb as kbinit


DEFAULT_QUERY = (
    "What contract clauses, pricing schedules, rate cards, or supplier terms "
    "govern invoice INV-2026-08034?"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default=os.getenv("FORGE_RESOURCE_GROUP", "rg-forge"))
    parser.add_argument("--subscription-id", default=os.getenv("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--ai-account-name", default=os.getenv("AZURE_AI_ACCOUNT_NAME"))
    parser.add_argument("--ai-project-name", default=os.getenv("AZURE_AI_PROJECT_NAME", "ai-project-forge"))
    parser.add_argument("--ai-services-endpoint", default=os.getenv("AZURE_AI_SERVICES_ENDPOINT"))
    parser.add_argument("--search-service-name", default=os.getenv("AZURE_AI_SEARCH_SERVICE_NAME"))
    parser.add_argument("--storage-account-name", default=os.getenv("AZURE_STORAGE_ACCOUNT_NAME"))
    parser.add_argument("--storage-container-name", default=os.getenv("AZURE_STORAGE_CONTAINER_NAME", kbinit.DEFAULT_CONTAINER))
    parser.add_argument("--knowledge-base-name", default=os.getenv("AZURE_AI_SEARCH_KNOWLEDGE_BASE_NAME", kbinit.DEFAULT_KB_NAME))
    parser.add_argument("--knowledge-source-name", default=os.getenv("AZURE_AI_SEARCH_KNOWLEDGE_SOURCE_NAME", kbinit.DEFAULT_KNOWLEDGE_SOURCE_NAME))
    parser.add_argument("--mcp-connection-name", default=os.getenv("AZURE_AI_SEARCH_KB_MCP_CONNECTION_NAME", kbinit.DEFAULT_CONNECTION_NAME))
    parser.add_argument("--search-api-version", default=os.getenv("AZURE_SEARCH_KB_API_VERSION", kbinit.DEFAULT_SEARCH_API_VERSION))
    parser.add_argument("--query", default=os.getenv("FOUNDRYIQ_KB_SMOKE_QUERY", DEFAULT_QUERY))
    parser.add_argument("--skip-retrieval", action="store_true", help="Only validate resource wiring; skip MCP tool retrieval.")
    args = parser.parse_args()

    kbinit._hydrate_defaults_from_azure(args)
    _hydrate_live_resource_defaults(args)
    kbinit._require_args(
        args,
        "subscription_id",
        "resource_group",
        "ai_account_name",
        "ai_project_name",
        "search_service_name",
        "knowledge_base_name",
        "knowledge_source_name",
        "mcp_connection_name",
    )

    search_endpoint = f"https://{args.search_service_name}.search.windows.net"
    source_api_version = (
        kbinit.DEFAULT_KNOWLEDGE_SOURCE_API_VERSION
        if args.search_api_version == kbinit.DEFAULT_SEARCH_API_VERSION
        else args.search_api_version
    )
    mcp_endpoint = (
        f"{search_endpoint}/knowledgebases/{args.knowledge_base_name}/mcp"
        f"?api-version={args.search_api_version}"
    )

    search_token = kbinit._az_token("https://search.azure.com")
    management_token = kbinit._az_token("https://management.azure.com")

    knowledge_source = kbinit._json_request(
        "GET",
        f"{search_endpoint}/knowledgesources('{args.knowledge_source_name}')?api-version={source_api_version}",
        search_token,
    )
    knowledge_base = kbinit._json_request(
        "GET",
        f"{search_endpoint}/knowledgebases('{args.knowledge_base_name}')?api-version={args.search_api_version}",
        search_token,
    )
    connection = kbinit._json_request(
        "GET",
        (
            f"https://management.azure.com/subscriptions/{args.subscription_id}"
            f"/resourceGroups/{args.resource_group}/providers/Microsoft.CognitiveServices"
            f"/accounts/{args.ai_account_name}/projects/{args.ai_project_name}"
            f"/connections/{args.mcp_connection_name}?api-version={kbinit.DEFAULT_CONNECTION_API_VERSION}"
        ),
        management_token,
    )

    _validate_resource_wiring(args, knowledge_source, knowledge_base, connection, mcp_endpoint)

    retrieval_preview = ""
    request_id = ""
    if not args.skip_retrieval:
        initialize = _mcp_call(
            mcp_endpoint,
            search_token,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "forge-foundryiq-kb-smoke", "version": "0.1"},
            },
            request_id=1,
        )
        if "tools" not in initialize.get("result", {}).get("capabilities", {}):
            raise SystemExit("KB MCP endpoint did not advertise tools capability.")

        tools = _mcp_call(mcp_endpoint, search_token, "tools/list", {}, request_id=2)
        tool_names = {tool.get("name") for tool in tools.get("result", {}).get("tools", [])}
        if "knowledge_base_retrieve" not in tool_names:
            raise SystemExit(f"KB MCP tools missing knowledge_base_retrieve: {sorted(tool_names)}")

        result = _mcp_call(
            mcp_endpoint,
            search_token,
            "tools/call",
            {
                "name": "knowledge_base_retrieve",
                "arguments": {"queries": [args.query]},
            },
            request_id=3,
        )
        content = result.get("result", {}).get("content", [])
        text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
        retrieval_text = "\n".join(part for part in text_parts if part).strip()
        if not retrieval_text:
            raise SystemExit("KB MCP retrieval returned no text content.")
        if "ref_id:" not in retrieval_text and "[ref_id" not in retrieval_text:
            raise SystemExit("KB MCP retrieval returned text without source refs.")
        retrieval_preview = retrieval_text[:240].replace("\n", " ")
        request_id = str((content[0].get("_meta") or {}).get("x-request-id", "")) if content else ""

    print(
        json.dumps(
            {
                "status": "passed",
                "searchService": args.search_service_name,
                "knowledgeBase": args.knowledge_base_name,
                "knowledgeSource": args.knowledge_source_name,
                "mcpConnection": args.mcp_connection_name,
                "mcpEndpoint": mcp_endpoint,
                "retrievalRequestId": request_id or None,
                "retrievalPreview": retrieval_preview or None,
            },
            indent=2,
        )
    )
    return 0


def _validate_resource_wiring(
    args: argparse.Namespace,
    knowledge_source: dict[str, Any],
    knowledge_base: dict[str, Any],
    connection: dict[str, Any],
    mcp_endpoint: str,
) -> None:
    if knowledge_source.get("name") != args.knowledge_source_name:
        raise SystemExit(f"Unexpected knowledge source: {knowledge_source.get('name')}")
    if knowledge_base.get("name") != args.knowledge_base_name:
        raise SystemExit(f"Unexpected knowledge base: {knowledge_base.get('name')}")

    source_names = {
        source.get("name")
        for source in knowledge_base.get("knowledgeSources", [])
        if isinstance(source, dict)
    }
    if args.knowledge_source_name not in source_names:
        raise SystemExit(
            f"Knowledge base {args.knowledge_base_name} is not wired to {args.knowledge_source_name}: "
            f"{sorted(source_names)}"
        )

    properties = connection.get("properties") or {}
    metadata = properties.get("metadata") or {}
    if properties.get("category") != "RemoteTool":
        raise SystemExit(f"MCP connection category is not RemoteTool: {properties.get('category')}")
    if properties.get("target") != mcp_endpoint:
        raise SystemExit(
            f"MCP connection target mismatch: expected {mcp_endpoint}, got {properties.get('target')}"
        )
    if metadata.get("knowledgeBaseName") != args.knowledge_base_name:
        raise SystemExit(
            f"MCP connection metadata knowledgeBaseName mismatch: {metadata.get('knowledgeBaseName')}"
        )


def _hydrate_live_resource_defaults(args: argparse.Namespace) -> None:
    """Fill read-only smoke defaults from the target resource group.

    This mirrors the initializer's discovery but keeps the smoke resilient when
    an older Azure CLI shape omits resource properties during the first pass.
    """
    if args.ai_account_name and args.search_service_name:
        return
    resources = json.loads(kbinit._az_json(["resource", "list", "-g", args.resource_group, "-o", "json"]))
    for resource in resources:
        resource_type = str(resource.get("type") or "")
        name = str(resource.get("name") or "")
        kind = str(resource.get("kind") or "")
        if not args.ai_account_name and resource_type.lower() == "microsoft.cognitiveservices/accounts" and kind == "AIServices":
            args.ai_account_name = name
        elif not args.search_service_name and resource_type.lower() == "microsoft.search/searchservices":
            args.search_service_name = name


def _mcp_call(
    endpoint: str,
    token: str,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        payload = _parse_sse_json(text)
    else:
        payload = json.loads(text)
    if "error" in payload:
        raise SystemExit(f"MCP {method} failed: {payload['error']}")
    return payload


def _parse_sse_json(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise SystemExit(f"Could not parse MCP event-stream response: {text[:200]}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
