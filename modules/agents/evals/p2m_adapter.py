"""P2M callable adapter for local or hosted agent evaluation.

P2M needs a plain Python callable. This module provides one callable that can
route to either:
- a locally running hosted-agent HTTP endpoint (for local development), or
- the deployed hosted agent in Azure Foundry via `azd ai agent invoke` (for CI).

Set:
  P2M_TARGET_MODE=local|hosted
  P2M_AGENT_NAME=assurance_orchestrator|waypoint_recorder
  P2M_LOCAL_AGENT_URL=http://127.0.0.1:8088/responses   # local mode only
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Any


def _agent_name() -> str:
    return os.environ.get("P2M_AGENT_NAME", "assurance_orchestrator")


def _normalize_text(text: str) -> str:
    return text.strip().replace("\r\n", "\n")


def _extract_text_from_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("output_text", "text", "response", "message", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(data.get("choices"), list) and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                message = choice.get("message") or {}
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
        if isinstance(data.get("output"), list):
            parts: list[str] = []
            for item in data["output"]:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str) and item["text"].strip():
                        parts.append(item["text"])
                    content = item.get("content")
                    if isinstance(content, list):
                        for sub in content:
                            if isinstance(sub, dict):
                                if isinstance(sub.get("text"), str) and sub["text"].strip():
                                    parts.append(sub["text"])
                                elif isinstance(sub.get("value"), str) and sub["value"].strip():
                                    parts.append(sub["value"])
            if parts:
                return "\n".join(parts)
    return ""


def _call_local(message: str) -> str:
    url = os.environ.get("P2M_LOCAL_AGENT_URL", "http://127.0.0.1:8088/responses")
    payload = json.dumps({"input": message, "stream": False, "store": False}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"local agent request failed: HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"local agent request failed: {exc}") from exc

    if "json" in content_type.lower():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            text = _extract_text_from_json(data)
            if text:
                return _normalize_text(text)

    # Fall back to plain text or a best-effort regex extraction.
    text = _normalize_text(raw)
    if text:
        match = re.search(r"\[.*?\]\s*(.*)$", text, re.MULTILINE)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
        return text

    raise RuntimeError("local agent response did not contain usable text")


def _call_hosted(message: str) -> str:
    agent = _agent_name()
    # All agents in this repo declare both `responses` and `activity_protocol`
    # in agent.yaml (Playground + M365 surfaces). `azd ai agent invoke` refuses
    # to pick one for you and exits with a "use --protocol" hint, so pin to
    # `responses` — the conversational surface the eval adapter expects.
    proc = subprocess.run(
        ["azd", "ai", "agent", "invoke", agent, message, "--no-prompt", "--protocol", "responses"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "hosted agent invocation failed").strip())

    # azd prints metadata lines and then a response line prefixed with [agent].
    lines = [ln.rstrip() for ln in proc.stdout.splitlines() if ln.strip()]
    marker = f"[{agent}]"
    for idx, line in enumerate(lines):
        if line.startswith(marker):
            first = line[len(marker) :].lstrip()
            rest = [ln for ln in lines[idx + 1 :] if not ln.startswith("Agent:") and not ln.startswith("Session:")]
            text = "\n".join([first] + rest).strip()
            if text:
                return _normalize_text(text)
    # Fallback to the last non-metadata line.
    for line in reversed(lines):
        if not line.startswith("Agent:") and not line.startswith("Message:") and not line.startswith("Session:") and not line.startswith("Conversation:") and not line.startswith("Trace ID:"):
            return _normalize_text(line)
    raise RuntimeError("hosted agent response did not contain usable text")


def chat_sync(message: str) -> str:
    """P2M callable entrypoint.

    Local mode: call a locally running Responses server.
    Hosted mode: call the deployed agent via `azd ai agent invoke`.
    """
    mode = os.environ.get("P2M_TARGET_MODE", "local").lower()
    if mode == "hosted":
        return _call_hosted(message)
    return _call_local(message)
