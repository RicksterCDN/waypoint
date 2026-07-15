"""Per-run MCP connection lifecycle for the hosted analyst.

Why this exists
---------------
``MCPStreamableHTTPTool`` opens a streamable-HTTP session (an anyio task group
with a background reader, owned by a dedicated "lifecycle owner" task inside the
mcp client). The first time a tool is used in a run, ``ChatAgent`` enters that
tool into the *agent-level* ``_async_exit_stack`` (see
``ChatAgent._prepare_run_context``: ``self._async_exit_stack.enter_async_context(tool)``).
That exit stack is only ever closed by ``ChatAgent.__aexit__`` -- i.e. when the
agent is used as ``async with agent:``.

Our hosted surfaces never do that:

* the Responses host (``ResponsesHostServer``) just calls ``agent.run(...)`` per
  request, and
* the Microsoft 365 activity mount calls ``agent.run(text)`` per turn.

So the MCP session stays registered on the agent's exit stack and is *never*
torn down. The Foundry hosting runtime then deactivates the container between
requests, which cancels the mcp lifecycle-owner task. Its cancellation path
nulls the owner without closing the streamable-HTTP session, so the orphaned
async generator is finalized later by the garbage collector -- on a *different*
task/loop than the one that entered it -- and anyio raises::

    RuntimeError: Attempted to exit cancel scope in a different task than it was
    entered in

That surfaces to the model as "Function failed". It is a known interaction of
the MCP streamable-HTTP client with anyio cancel scopes
(modelcontextprotocol/python-sdk issues #3025, #3046, #2610) and is *not* fixed
by bumping ``mcp`` alone: the connection simply must not outlive the request.

The durable fix
---------------
Close **and reset** the agent's ``_async_exit_stack`` at the end of every run,
on that run's own task. This is exactly what ``ChatAgent.__aexit__`` does (it
exits each entered MCP tool through its real ``__aexit__`` -> ``close()``, which
is dispatched to the tool's own lifecycle-owner task, so the streamable session
is entered and exited on the same task), plus a reset so the next run re-enters
a fresh stack. Because teardown happens inline on the request task *before* the
handler returns, the connection never survives to a container deactivation and
never leaks to the garbage collector.

Closing the tools directly (``tool.close()``) is deliberately avoided: that
resets ``is_connected`` while the agent's exit stack still references the tool,
which both leaks the stack entry and causes the tool to be entered twice on the
next run. Managing the agent's exit stack keeps the framework's own bookkeeping
consistent.

Applied uniformly to both hosted surfaces via a single agent middleware.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from agent_framework import agent_middleware

logger = logging.getLogger("invoice_analyst.mcp_lifecycle")


async def _reset_agent_exit_stack(agent) -> None:
    """Close the agent's MCP exit stack and swap in a fresh one.

    Mirrors ``ChatAgent.__aexit__`` (``await self._async_exit_stack.aclose()``)
    but leaves the agent reusable for the next request. Cancel-scope errors from
    a session whose owner task was already cancelled are swallowed -- the whole
    point is that we are reclaiming a possibly-wedged connection.
    """
    stack = getattr(agent, "_async_exit_stack", None)
    if stack is None:
        return
    try:
        await stack.aclose()
    except RuntimeError as exc:  # pragma: no cover - defensive
        if "cancel scope" not in str(exc).lower():
            raise
        logger.warning(
            "MCP exit stack closed with a cancel-scope error (owner task was "
            "likely already gone); reclaiming anyway: %s",
            exc,
        )
    except Exception:  # pragma: no cover - teardown is best-effort
        logger.warning("MCP exit stack failed to close cleanly.", exc_info=True)
    finally:
        # A closed AsyncExitStack cannot be reused, so hand the agent a fresh
        # one. The next run re-enters its MCP tools into this new stack.
        agent._async_exit_stack = AsyncExitStack()


@agent_middleware
async def close_mcp_tools_after_run(context, call_next) -> None:
    """Tear down the agent's MCP session once each run completes.

    * Non-streaming runs: reset the exit stack in a ``finally`` after
      ``call_next()`` returns (the tool result is already fully materialized).
    * Streaming runs: the session must stay open until the stream is consumed,
      so defer teardown to a post-consumption cleanup hook. A failure before the
      stream is produced is handled by the ``except`` branch so the connection
      is never left dangling.
    """
    agent = context.agent
    mcp_tools = list(getattr(agent, "mcp_tools", None) or [])

    if not mcp_tools:
        await call_next()
        return

    async def _teardown() -> None:
        await _reset_agent_exit_stack(agent)

    if getattr(context, "stream", False):
        context.stream_cleanup_hooks.append(_teardown)
        try:
            await call_next()
        except BaseException:
            # Stream never materialized; the cleanup hook won't fire, so tear
            # the connection down here on this task instead.
            await _teardown()
            raise
        return

    try:
        await call_next()
    finally:
        await _teardown()
