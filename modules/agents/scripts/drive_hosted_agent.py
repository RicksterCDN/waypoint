"""Drive a Foundry hosted agent through the Responses bridge in *background* mode.

A synchronous POST to the hosted-agent Responses bridge
(``POST <project>/agents/<name>/endpoint/protocols/openai/responses``) is capped
at roughly 120s by the server and returns
``{"error":{"code":"Timeout",...}}`` for any orchestration that runs longer —
e.g. the assurance-orchestrator coordinator fanning out to four evidence experts
(some of which do web-search bursts) and then handing off to the waypoint-recorder writer.

Background mode lifts that cap. Posting ``"background": true`` (with
``"store": true`` so the result can be fetched later) returns immediately with a
response id and ``status: in_progress``. The orchestration keeps running
server-side; this helper then polls
``GET <project>/agents/<name>/endpoint/protocols/openai/responses/<id>`` until the
response reaches a terminal status (``completed`` / ``failed`` / ``cancelled`` /
``incomplete``).

Verified live contract (swedencentral, ai-project-forge):
    POST  body   {"input": "...", "store": true, "background": true}
                 -> {"id": "caresp_...", "status": "in_progress", ...}
    GET   .../responses/<id>?api-version=2025-11-15-preview
                 -> {"status": "completed", "output": [...], ...}
Both calls require the ``api-version`` query parameter, an ``Authorization:
Bearer`` token for ``https://ai.azure.com/.default``, and the ``Foundry-Features``
preview header.

Usable as a CLI or imported as ``drive_hosted_agent(...)``.

CLI:
    python scripts/drive_hosted_agent.py assurance-orchestrator \
        --project-endpoint "$PROJECT_ENDPOINT" \
        --input "Run the full invoice-assurance review for invoice INV-..."

Environment fallbacks:
    PROJECT_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT   project endpoint
    DRIVE_AGENT_POLL_TIMEOUT                        max seconds to poll (default 600)
    DRIVE_AGENT_POLL_INTERVAL                       seconds between polls (default 5)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests
from azure.identity import DefaultAzureCredential

API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_FEATURES = "HostedAgents=V1Preview,AgentEndpoints=V1Preview"
RESPONSES_SUFFIX = "/endpoint/protocols/openai/responses"

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled", "incomplete", "expired"}

DEFAULT_POLL_TIMEOUT = 600.0
DEFAULT_POLL_INTERVAL = 5.0


def _responses_base(project_endpoint: str, agent: str) -> str:
    return f"{project_endpoint.rstrip('/')}/agents/{agent}{RESPONSES_SUFFIX}"


def _extract_output_text(body: dict[str, Any]) -> str:
    """Pull assistant text out of an OpenAI-Responses-style payload."""
    if isinstance(body.get("output_text"), str) and body["output_text"]:
        return body["output_text"]
    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks)


def drive_hosted_agent(
    agent: str,
    project_endpoint: str,
    prompt: str,
    *,
    token: str | None = None,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    log=print,
) -> dict[str, Any]:
    """Start a background run and poll until it reaches a terminal status.

    Returns the final GET response body. Raises ``RuntimeError`` on HTTP errors
    or if the run does not reach a terminal status before ``poll_timeout``.
    """
    if token is None:
        token = DefaultAzureCredential().get_token(AI_SCOPE).token
    headers = {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": FOUNDRY_FEATURES,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base = _responses_base(project_endpoint, agent)
    start_url = f"{base}?api-version={API_VERSION}"
    payload = {"input": prompt, "store": True, "background": True}

    log(f"-> POST {start_url} (background)")
    resp = requests.post(start_url, headers=headers, json=payload, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"background POST failed HTTP {resp.status_code}: {resp.text[:500]}")
    started = resp.json()
    response_id = started.get("id") or started.get("response_id")
    if not response_id:
        raise RuntimeError(f"background POST returned no response id: {json.dumps(started)[:500]}")
    log(f"   started id={response_id} status={started.get('status')}")

    poll_url = f"{base}/{response_id}?api-version={API_VERSION}"
    deadline = time.monotonic() + poll_timeout
    last: dict[str, Any] = started
    transient = 0
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            gresp = requests.get(poll_url, headers=headers, timeout=120)
        except requests.exceptions.RequestException as exc:
            # The bridge may long-poll an in-progress response and drop the
            # connection; the background run keeps going server-side. Treat a
            # transient network error as "still running" and re-poll.
            transient += 1
            log(f"   poll transient error ({transient}): {type(exc).__name__}; retrying")
            continue
        if gresp.status_code >= 400:
            raise RuntimeError(f"poll GET failed HTTP {gresp.status_code}: {gresp.text[:500]}")
        last = gresp.json()
        status = last.get("status")
        log(f"   poll status={status}")
        if status in TERMINAL_STATUSES:
            return last
    raise RuntimeError(
        f"run {response_id} did not reach a terminal status within {poll_timeout:.0f}s "
        f"(last status={last.get('status')})"
    )


def _env(*names: str) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", help="Hosted agent name (e.g. assurance-orchestrator)")
    parser.add_argument(
        "--project-endpoint",
        default=_env("PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"),
        help="Foundry project endpoint (or set PROJECT_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT)",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Prompt text to send to the agent")
    src.add_argument("--input-file", help="Path to a file whose contents are the prompt")
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=float(_env("DRIVE_AGENT_POLL_TIMEOUT") or DEFAULT_POLL_TIMEOUT),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(_env("DRIVE_AGENT_POLL_INTERVAL") or DEFAULT_POLL_INTERVAL),
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the full final JSON body to stdout"
    )
    args = parser.parse_args(argv)

    if not args.project_endpoint:
        parser.error("project endpoint required (--project-endpoint or PROJECT_ENDPOINT)")
    prompt = args.input if args.input is not None else open(args.input_file, encoding="utf-8").read()

    try:
        result = drive_hosted_agent(
            args.agent,
            args.project_endpoint,
            prompt,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
            log=lambda m: print(m, file=sys.stderr),
        )
    except Exception as exc:  # noqa: BLE001 - surface as CLI error
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    status = result.get("status")
    print(f"status={status} id={result.get('id') or result.get('response_id')}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(_extract_output_text(result))
    return 0 if status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
