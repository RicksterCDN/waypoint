"""P2M callable adapter that runs the hopper agent in-process.

Unlike ``evals/p2m_adapter.py`` (which calls the hosted Responses server over
HTTP), this adapter imports the LangGraph agent factory directly from
``agents/hopper/lovelace_local_foundry_agent.py`` and invokes it in
the current process. That gives the P2M judge full visibility into the
agent's OpenTelemetry spans when Phoenix / OpenInference auto-instrumentation
is installed — the recommended target shape from the adaptive-eval README.

Usage in a P2M eval config::

    pipeline:
      rollout:
        target:
          callable: evals.lovelace_langchain_adapter:chat_sync

Optional: ``phoenix serve`` locally to browse traces.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

# Make the agent package importable. The folder name contains a hyphen so we
# can't rely on a normal dotted import; add it to sys.path explicitly.
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agents" / "hopper"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from openinference.instrumentation.langchain import LangChainInstrumentor  # noqa: E402
from openinference.instrumentation.litellm import LiteLLMInstrumentor  # noqa: E402
from phoenix.otel import register  # noqa: E402
from lovelace_local_foundry_agent import build_agent  # noqa: E402

_agent_lock = threading.Lock()
_agent = None
_loop: asyncio.AbstractEventLoop | None = None
_instrumented = False


def _instrument_once() -> None:
    global _instrumented
    if not _instrumented:
        register(project_name="hopper", auto_instrument=False)
        LangChainInstrumentor().instrument()
        LiteLLMInstrumentor().instrument()
        _instrumented = True


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


def _get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            _instrument_once()
            _agent = _get_loop().run_until_complete(build_agent())
        return _agent


def _extract_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content)


def chat_sync(message: str) -> str:
    """Synchronous P2M entrypoint — one user turn, returns assistant text."""
    agent = _get_agent()
    loop = _get_loop()
    result = loop.run_until_complete(
        agent.ainvoke({"messages": [{"role": "user", "content": message}]})
    )
    messages = result.get("messages", [])
    if not messages:
        return ""
    return _extract_text(messages[-1]).strip()
