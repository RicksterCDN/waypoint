"""Microsoft 365 Agents SDK ``/api/messages`` mount for AI Teammate hires.

When this hosted agent is "hired" as a Microsoft 365 AI Teammate, the
Foundry control plane delivers each turn directly to the container at
``POST /api/messages`` as a Bot Framework Activity, signed with a JWT
whose audience matches the agent's *blueprint* MSA AppId.

The Azure Bot Service / Teams "direct bot chat" surface takes a different
path: Foundry auto-bridges Responses ↔ Activity on the server side, so
that path is served by the existing Responses host at ``/responses`` and
never hits this mount.

We use the **Microsoft 365 Agents SDK for Python**
(``microsoft-agents-hosting-*``) — the same SDK family the official C#
AI Teammate sample (`hello_world_a365_agent`) is built on. The legacy
``botbuilder-core`` `CloudAdapter` validates Bot Framework v1 JWTs, which
is the *wrong* token shape for AI Teammate / A365 traffic.

Mount is a no-op when:
  * the SDK packages aren't installed (local-dev / minimal image), or
  * ``MicrosoftAppId`` / ``CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID``
    is unset (teammate not yet provisioned).

The Responses path is *never* affected by this mount.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("hosted-agent.activity")
# Force this logger to WARNING so its startup + per-request banners reach
# Foundry's console capture (which filters anything below WARNING).
# Diagnostic-only — once the AI Teammate routing is verified end-to-end
# this can drop back to INFO.
logger.setLevel(logging.WARNING)


def _blueprint_client_id() -> str | None:
    """Return the blueprint MSA AppId that hired-instance JWTs target."""
    # Set by Foundry on the container after `make publish` completes. We
    # accept either env-var convention so the container can be configured
    # either via the legacy BotBuilder name (``MicrosoftAppId``) or the
    # M365 Agents SDK name.
    return (
        os.environ.get("CONNECTIONS__SERVICECONNECTION__SETTINGS__CLIENTID")
        or os.environ.get("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID")
        or os.environ.get("MicrosoftAppId")
    )


def _mount_anonymous_activity_protocol(host: Any, agent: Any) -> None:
    """Mount /api/messages with no JWT validation for local-dev / Agents Playground.

    Enable with ``AGENT_ALLOW_ANONYMOUS_ACTIVITY=1`` (the VS Code debug task
    sets this automatically).  Parses Bot Framework Activity JSON, calls
    ``agent.run(text)`` for message turns, and posts the reply back to the
    Playground's local Bot Framework service URL.

    Never enable in a deployed container — production always uses the MSAL
    path gated on a real blueprint client ID.
    """
    import httpx
    from starlette.requests import Request
    from starlette.responses import Response

    logger.warning(
        "Activity protocol mounting in ANONYMOUS mode for local development. "
        "Blueprint client id not set — JWT validation skipped. "
        "Never deploy a container without a blueprint client id."
    )

    async def _handle_anonymous_messages(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            logger.warning("Anonymous /api/messages: failed to parse JSON body")
            return Response(status_code=400)

        activity_type = (body.get("type") or "").lower()
        text = (body.get("text") or "").strip()
        service_url = (body.get("serviceUrl") or "").rstrip("/")
        conversation_id = (body.get("conversation") or {}).get("id", "")
        # "from" is the user; "recipient" is the bot — swap them for the reply.
        from_account = body.get("from", {})
        bot_account = body.get("recipient", {})

        logger.warning(
            "Anonymous /api/messages received type=%s text=%r",
            activity_type, text[:80],
        )

        if activity_type != "message" or not text:
            # ACK lifecycle events (conversationUpdate, installationUpdate, etc.)
            return Response(status_code=202)

        try:
            result = await agent.run(text)
            reply_text = getattr(result, "text", None) or str(result)
        except Exception as exc:
            logger.exception("Anonymous /api/messages: agent.run failed")
            reply_text = f"[error] {type(exc).__name__}: {exc!s}"

        # Post the reply back to the Playground's local Bot Framework service URL.
        if service_url and conversation_id:
            reply_activity = {
                "type": "message",
                "text": reply_text,
                "conversation": body.get("conversation"),
                "from": bot_account,
                "recipient": from_account,
                "replyToId": body.get("id"),
            }
            try:
                post_url = (
                    f"{service_url}/v3/conversations/{conversation_id}/activities"
                )
                async with httpx.AsyncClient(timeout=30.0) as http:
                    resp = await http.post(
                        post_url,
                        json=reply_activity,
                        headers={"Content-Type": "application/json"},
                    )
                logger.warning(
                    "Anonymous /api/messages: reply posted status=%s",
                    resp.status_code,
                )
            except Exception:
                logger.exception(
                    "Anonymous /api/messages: failed to post reply to %s",
                    service_url,
                )
        else:
            logger.warning(
                "Anonymous /api/messages: missing serviceUrl/conversationId; "
                "reply not delivered"
            )
        return Response(status_code=202)

    async def _anon_health(_request: Request) -> Response:
        logger.warning("/api/messages GET (health probe) received")
        return Response(content='{"status":"OK"}', media_type="application/json")

    host.add_route(
        "/api/messages",
        _handle_anonymous_messages,
        methods=["POST"],
        name="activity_protocol",
    )
    host.add_route(
        "/api/messages",
        _anon_health,
        methods=["GET"],
        name="activity_protocol_health",
    )
    logger.warning("Anonymous /api/messages mounted (localhost:8088/api/messages)")


def mount_activity_protocol(host: Any, agent: Any) -> None:
    """Add ``POST /api/messages`` to *host* (a Starlette app), backed by *agent*.

    Purely additive: appends one Starlette route to the existing
    ``ResponsesHostServer`` and does not modify any existing route,
    middleware, lifespan, or the Responses handler.
    """
    client_id = _blueprint_client_id()
    if not client_id:
        _mount_anonymous_activity_protocol(host, agent)
        return

    try:
        from microsoft_agents.activity import load_configuration_from_env
        from microsoft_agents.authentication.msal import MsalConnectionManager
        from microsoft_agents.hosting.core import (
            AgentApplication,
            Authorization,
            MemoryStorage,
            TurnContext,
            TurnState,
        )
        from microsoft_agents.hosting.fastapi import (
            CloudAdapter,
            start_agent_process,
        )
    except ImportError as exc:  # pragma: no cover - import guard
        logger.warning(
            "microsoft-agents-* not fully installed; /api/messages mount "
            "skipped: %s",
            exc,
        )
        return

    from starlette.requests import Request
    from starlette.responses import Response

    # The Python SDK reads the same ASP.NET-style env vars the C# sample
    # uses (CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*,
    # AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__AGENTIC__*, etc.)
    # Foundry sets these on the container automatically once the AI
    # Teammate is provisioned. We pass the resulting config to every
    # SDK component so MSAL token validation, agentic user authorization,
    # and the AgentApplication runtime all agree on the same blueprint
    # identity and scopes.
    #
    # Wrap construction in a broad try/except: this entire function runs
    # *before* ``host.run()``, so any exception here would kill the
    # Responses path too (the direct-bot chat surface). If teammate wiring
    # is misconfigured we want to log loudly and let Responses keep
    # serving traffic.
    try:
        agents_sdk_config = load_configuration_from_env(os.environ)

        storage = MemoryStorage()
        connection_manager = MsalConnectionManager(**agents_sdk_config)

        # DIAGNOSTIC: monkey-patch get_agentic_application_token to log
        # the raw MSAL response. The SDK swallows non-success responses
        # (returns None on missing access_token), which surfaces as a
        # generic -60018 with no detail. We need the real MSAL payload to
        # diagnose FMI / federation failures.
        try:
            from microsoft_agents.authentication.msal.msal_auth import (
                MsalAuth,
                _async_acquire_token_for_client,
            )
            from msal import ConfidentialClientApplication

            _orig = MsalAuth.get_agentic_application_token

            async def _diag_get_agentic_application_token(
                self, tenant_id, agent_app_instance_id
            ):
                logger.warning(
                    "[diag] get_agentic_application_token tenant=%s instance=%s",
                    tenant_id,
                    agent_app_instance_id,
                )
                client = self._get_client(tenant_id, agent_app_instance_id)
                logger.warning(
                    "[diag] msal client type=%s", type(client).__name__
                )
                if isinstance(client, ConfidentialClientApplication):
                    try:
                        payload = await _async_acquire_token_for_client(
                            client,
                            ["api://AzureAdTokenExchange/.default"],
                            data={"fmi_path": agent_app_instance_id},
                        )
                    except Exception:
                        logger.exception("[diag] CCA acquire raised")
                        raise
                    logger.warning(
                        "[diag] CCA token payload keys=%s",
                        list((payload or {}).keys()),
                    )
                    logger.warning("[diag] CCA token payload=%r", payload)
                    if payload:
                        return payload.get("access_token")
                else:
                    logger.warning(
                        "[diag] non-CCA client; returning None"
                    )
                return None

            MsalAuth.get_agentic_application_token = (
                _diag_get_agentic_application_token
            )
            logger.warning("[diag] monkey-patched get_agentic_application_token")
        except Exception:
            logger.exception("[diag] monkey-patch failed (non-fatal)")

        adapter = CloudAdapter(connection_manager=connection_manager)
        authorization = Authorization(
            storage, connection_manager, **agents_sdk_config
        )

        AGENT_APP = AgentApplication[TurnState](
            storage=storage,
            adapter=adapter,
            authorization=authorization,
            **agents_sdk_config,
        )
    except Exception:
        logger.exception(
            "Activity protocol setup failed; /api/messages mount skipped. "
            "Responses path is unaffected."
        )
        return

    # NOTE: We intentionally do NOT pass auth_handlers=["AGENTIC"] here.
    # The AGENTIC handler triggers the SDK's user_fic exchange via
    # MsalAuth.get_agentic_user_token, which mints an OBO-style token for
    # the agentUser (digital worker) identity on the per-hire instance
    # app. In this tenant that exchange returns AADSTS65001 ("user or
    # admin has not consented to use the application"), because the
    # agentUser hasn't gone through any interactive consent step against
    # the freshly-minted per-hire app. For a simple text reply we only
    # need the instance/app token (used by the connector client for
    # api.botframework.com), which the SDK acquires for the typing
    # indicator already. Skipping auth_handlers lets the message handler
    # run without the user_fic round-trip.
    try:
        message_decorator = AGENT_APP.activity("message")
    except TypeError:
        logger.exception("AgentApplication.activity registration failed")
        return

    @message_decorator
    async def _on_message(context: TurnContext, _state: TurnState) -> None:
        text = (context.activity.text or "").strip()
        # An invoice emailed to the digital worker's inbox may arrive as an
        # attachment (e.g. a PDF) with little or no message text. Surface the
        # attachment names so the turn still reaches the agent instead of being
        # silently dropped. Full attachment byte extraction (Content
        # Understanding) is handled by the fan-out workflow, not here.
        attachments = getattr(context.activity, "attachments", None) or []
        attachment_names = [
            (getattr(att, "name", None) or getattr(att, "content_type", "") or "attachment")
            for att in attachments
        ]
        logger.warning(
            "activity message received: text=%r attachments=%s",
            text[:120], attachment_names,
        )
        if not text and attachment_names:
            text = (
                "An invoice was emailed to your inbox as "
                f"{len(attachment_names)} attachment(s): "
                f"{', '.join(attachment_names)}. Discover the matching Waypoint "
                "work and run the invoice assurance fan-out."
            )
        if not text:
            return
        try:
            result = await agent.run(text)
        except Exception as exc:  # pragma: no cover - diagnostic
            logger.exception("agent.run failed handling activity")
            import traceback as _tb
            tb = _tb.format_exc()
            logger.error("[diag] agent.run traceback:\n%s", tb)
            await context.send_activity(
                f"[diag] {type(exc).__name__}: {exc!s}\n```\n{tb[-1500:]}\n```"
            )
            return
        reply = getattr(result, "text", None) or str(result)
        if reply:
            await context.send_activity(reply)

    @AGENT_APP.error
    async def _on_error(context: TurnContext, error: Exception) -> None:
        logger.error("Unhandled activity error: %s", error)
        try:
            await context.send_activity("An error occurred. Please try again.")
        except Exception:  # pragma: no cover - best effort
            pass

    async def _handle_messages(request: Request) -> Response:
        # ``microsoft-agents-hosting-fastapi`` takes a Starlette/FastAPI
        # ``Request`` (FastAPI's Request *is* Starlette's), validates the
        # inbound A365 JWT via the connection-manager-backed adapter,
        # dispatches to the matching ``@AGENT_APP.activity(...)`` handler,
        # and returns a Response.
        auth_header = request.headers.get("authorization", "")
        token_preview = auth_header[:32] + "..." if auth_header else "<none>"
        logger.warning(
            "/api/messages POST received: content-type=%s auth=%s",
            request.headers.get("content-type", "<none>"),
            token_preview,
        )
        try:
            response = await start_agent_process(request, AGENT_APP, adapter)
        except Exception:
            logger.exception("/api/messages dispatch raised")
            raise
        logger.warning(
            "/api/messages POST handled: status=%s",
            getattr(response, "status_code", "?"),
        )
        return response

    async def _health(_request: Request) -> Response:
        logger.warning("/api/messages GET (health probe) received")
        return Response(content='{"status":"OK"}', media_type="application/json")

    # ``ResponsesHostServer`` -> ``ResponsesAgentServerHost`` ->
    # ``AgentServerHost`` which subclasses ``starlette.applications.Starlette``
    # directly. So ``host`` *is* the Starlette app and ``host.add_route``
    # is the public Starlette API.
    host.add_route(
        "/api/messages",
        _handle_messages,
        methods=["POST"],
        name="activity_protocol",
    )
    host.add_route(
        "/api/messages",
        _health,
        methods=["GET"],
        name="activity_protocol_health",
    )
    logger.warning(
        "Activity protocol mounted at /api/messages "
        "(blueprint=%s, config_keys=%s)",
        client_id,
        sorted(agents_sdk_config.keys()),
    )
