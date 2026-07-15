#!/usr/bin/env python3
"""Hosted expert evidence-contract smoke.

Runs a deployed hosted expert through the Foundry Responses bridge, extracts
the assistant's JSON, and validates the shared evidence contract.

Examples:
    python scripts/test_hosted_iq_agent.py market-evidence-expert --plane webiq --require-evidence
    python scripts/test_hosted_iq_agent.py contract-policy-expert --plane foundryiq
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import initialize_contracts_kb as kbinit
from drive_hosted_agent import _extract_output_text, drive_hosted_agent
from test_evidence_contract import validate_contract


DEFAULT_INVOICE_ID = "INV-2026-08034"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", help="Hosted agent name, e.g. market-evidence-expert")
    parser.add_argument("--plane", required=True, help="Expected evidence plane")
    parser.add_argument("--invoice-id", default=os.getenv("INVOICE_ID", DEFAULT_INVOICE_ID))
    parser.add_argument("--project-endpoint", default=_env("PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"))
    parser.add_argument("--resource-group", default=os.getenv("FORGE_RESOURCE_GROUP", "rg-forge"))
    parser.add_argument("--ai-account-name", default=os.getenv("AZURE_AI_ACCOUNT_NAME"))
    parser.add_argument("--ai-project-name", default=os.getenv("AZURE_AI_PROJECT_NAME", "ai-project-forge"))
    parser.add_argument("--prompt", default=None, help="Override the default evidence-contract prompt.")
    parser.add_argument("--require-evidence", action="store_true")
    parser.add_argument("--poll-timeout", type=float, default=float(os.getenv("DRIVE_AGENT_POLL_TIMEOUT", "300")))
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("DRIVE_AGENT_POLL_INTERVAL", "10")))
    args = parser.parse_args()

    project_endpoint = args.project_endpoint or _resolve_project_endpoint(args)
    prompt = args.prompt or _default_prompt(args.agent, args.plane, args.invoice_id)

    result = drive_hosted_agent(
        args.agent,
        project_endpoint,
        prompt,
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
        log=lambda message: print(message, file=sys.stderr),
    )
    if result.get("status") != "completed":
        raise SystemExit(f"{args.agent} did not complete: status={result.get('status')}")

    output_text = _extract_output_text(result)
    payload = _parse_json_object(output_text)
    validate_contract(payload, expected_agent=args.agent, expected_plane=args.plane)
    if payload["invoice_id"] != args.invoice_id:
        raise SystemExit(f"Unexpected invoice_id: {payload['invoice_id']}")
    if args.require_evidence and not payload["evidence"]:
        raise SystemExit(f"{args.agent} returned no evidence")

    print(
        json.dumps(
            {
                "status": "passed",
                "agent": args.agent,
                "plane": args.plane,
                "invoiceId": args.invoice_id,
                "responseId": result.get("id") or result.get("response_id"),
                "agentVersion": ((result.get("agent") or {}).get("version")),
                "evidenceCount": len(payload["evidence"]),
                "summary": payload["summary"],
            },
            indent=2,
        )
    )
    return 0


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolve_project_endpoint(args: argparse.Namespace) -> str:
    if not args.ai_account_name:
        resources = json.loads(kbinit._az_json(["resource", "list", "-g", args.resource_group, "-o", "json"]))
        for resource in resources:
            if (
                str(resource.get("type") or "").lower() == "microsoft.cognitiveservices/accounts"
                and resource.get("kind") == "AIServices"
            ):
                args.ai_account_name = str(resource.get("name"))
                break
    if not args.ai_account_name:
        raise SystemExit("Could not resolve AI Services account; pass --ai-account-name.")
    project = kbinit._az_json(
        [
            "resource",
            "show",
            "-g",
            args.resource_group,
            "--ids",
            (
                f"/subscriptions/{_subscription_id()}/resourceGroups/{args.resource_group}"
                f"/providers/Microsoft.CognitiveServices/accounts/{args.ai_account_name}"
                f"/projects/{args.ai_project_name}"
            ),
            "-o",
            "json",
        ]
    )
    endpoint = (json.loads(project).get("properties") or {}).get("endpoints", {}).get("AI Foundry API")
    if not endpoint:
        raise SystemExit("Could not resolve AI Foundry project endpoint.")
    return str(endpoint)


def _subscription_id() -> str:
    return kbinit._az_json(["account", "show", "--query", "id", "-o", "tsv"]).strip()


def _default_prompt(agent: str, plane: str, invoice_id: str) -> str:
    return (
        f"Return ONLY the shared evidence JSON contract for {agent}. "
        f"Invoice id: {invoice_id}. "
        f"Evidence plane: {plane}. "
        "Use your configured tools when available. If no grounded signal exists, "
        "return an empty evidence list rather than guessing."
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise SystemExit("Hosted agent output was not a JSON object.")
    return value


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
