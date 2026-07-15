#!/usr/bin/env python3
"""Hosted Assurance Orchestrator evidence-lane fan-out smoke.

Runs Assurance Orchestrator in diagnostic fan-out mode so each deployed evidence expert is invoked
once without waypoint-recorder handoff or Waypoint writes. The current expected state is
partial: WebIQ/FoundryIQ/FabricIQ should return completed lane contracts, while
WorkIQ may fail until real Microsoft 365 MCP endpoint/auth is configured.
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


DEFAULT_INVOICE_ID = "INV-2026-08034"
LANE_ALIASES = {
    "workiq": "collaboration",
    "webiq": "market",
    "foundryiq": "contract_policy",
    "fabriciq": "operations_data",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice-id", default=os.getenv("INVOICE_ID", DEFAULT_INVOICE_ID))
    parser.add_argument("--project-endpoint", default=_env("PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"))
    parser.add_argument("--resource-group", default=os.getenv("FORGE_RESOURCE_GROUP", "rg-forge"))
    parser.add_argument("--ai-account-name", default=os.getenv("AZURE_AI_ACCOUNT_NAME"))
    parser.add_argument("--ai-project-name", default=os.getenv("AZURE_AI_PROJECT_NAME", "ai-project-forge"))
    parser.add_argument("--poll-timeout", type=float, default=float(os.getenv("DRIVE_AGENT_POLL_TIMEOUT", "420")))
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("DRIVE_AGENT_POLL_INTERVAL", "10")))
    parser.add_argument("--require-workiq", action="store_true", help="Fail if WorkIQ is not completed.")
    args = parser.parse_args()

    result = drive_hosted_agent(
        "assurance-orchestrator",
        args.project_endpoint or _resolve_project_endpoint(args),
        _prompt(args.invoice_id),
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
        log=lambda message: print(message, file=sys.stderr),
    )
    if result.get("status") != "completed":
        raise SystemExit(f"assurance-orchestrator did not complete: status={result.get('status')}")

    payload = _parse_json_object(_extract_output_text(result))
    _validate_payload(payload, args.invoice_id, require_workiq=args.require_workiq)
    print(
        json.dumps(
            {
                "status": "passed",
                "responseId": result.get("id") or result.get("response_id"),
                "agentVersion": ((result.get("agent") or {}).get("version")),
                "overallStatus": payload["overall_status"],
                "lanes": payload["lanes"],
            },
            indent=2,
        )
    )
    return 0


def _prompt(invoice_id: str) -> str:
    return f"""Diagnostic fan-out smoke only. Do not call handoff_to_waypoint_recorder and do not write to Waypoint.
Invoice id: {invoice_id}.
Call each configured IQ expert consult tool once: consult_collaboration_evidence_expert, consult_market_evidence_expert, consult_contract_policy_expert, and consult_operations_data_expert.
Return ONLY a JSON object with: invoice_id, side_effects_performed=false, lanes[{{lane,status,ok,evidence_count,summary,error}}], and overall_status.
"""


def _validate_payload(payload: dict[str, Any], invoice_id: str, *, require_workiq: bool) -> None:
    if payload.get("invoice_id") != invoice_id:
        raise SystemExit(f"Unexpected invoice_id: {payload.get('invoice_id')}")
    if payload.get("side_effects_performed") is not False:
        raise SystemExit("Assurance Orchestrator fan-out smoke reported side effects.")
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        raise SystemExit("Assurance Orchestrator fan-out payload did not include lanes[].")
    by_lane = {lane.get("lane"): lane for lane in lanes if isinstance(lane, dict)}
    normalized_lanes = set(by_lane) | {
        legacy
        for legacy, renamed in LANE_ALIASES.items()
        if renamed in by_lane
    }
    missing = set(LANE_ALIASES) - normalized_lanes
    if missing:
        raise SystemExit(f"Assurance Orchestrator fan-out missing lanes: {sorted(missing)}")
    for lane in ("webiq", "foundryiq", "fabriciq"):
        lane_payload = by_lane.get(lane) or by_lane[LANE_ALIASES[lane]]
        if lane_payload.get("status") not in {"completed", "ok"} or lane_payload.get("ok") is not True:
            raise SystemExit(f"{lane} did not complete: {lane_payload}")
    workiq_payload = by_lane.get("workiq") or by_lane[LANE_ALIASES["workiq"]]
    if require_workiq and workiq_payload.get("status") != "completed":
        raise SystemExit(f"workiq did not complete: {workiq_payload}")
    if workiq_payload.get("status") != "completed" and payload.get("overall_status") not in {
        "partial",
        "partial_failure",
    }:
        raise SystemExit(f"Unexpected overall_status for WorkIQ failure: {payload.get('overall_status')}")


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
    subscription_id = kbinit._az_json(["account", "show", "--query", "id", "-o", "tsv"]).strip()
    project = kbinit._az_json(
        [
            "resource",
            "show",
            "-g",
            args.resource_group,
            "--ids",
            (
                f"/subscriptions/{subscription_id}/resourceGroups/{args.resource_group}"
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


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise SystemExit("AssuranceOrchestrator output was not a JSON object.")
    return value


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
