"""Local Bot Framework activity bridge for the hosted invoice-analyst agent.

The Adaptive Card preview canvas speaks the Microsoft 365 activity protocol
(`/api/messages`) because that is the surface that carries card attachments in
Teams. A deployed Foundry hosted agent's activity endpoint cannot call back to a
localhost connector URL, so this local bridge accepts the activity, drives the
hosted invoice-analyst through the Foundry Responses endpoint, renders the
structured card payload locally, and posts the reply back to the canvas connector.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import Request, urlopen

from adaptive_cards import (
    CARD_INSTRUCTIONS,
    build_text_fallback_card,
    build_status_card,
    card_attachment_dict,
    extract_card_payload,
)

AGENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = AGENT_ROOT.parent.parent

DEFAULT_AGENT = "invoice-analyst"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090
DEFAULT_RESOURCE_GROUP = "rg-forge"
DEFAULT_PROJECT_NAME = "ai-project-forge"
API_VERSION = "2025-11-15-preview"
AI_SCOPE = "https://ai.azure.com/.default"
AI_RESOURCE = "https://ai.azure.com"
FOUNDRY_FEATURES = "HostedAgents=V1Preview,AgentEndpoints=V1Preview"
RESPONSES_SUFFIX = "/endpoint/protocols/openai/responses"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled", "incomplete", "expired"}

logger = logging.getLogger("invoice_analyst.foundry_activity_bridge")


def _extract_output_text(body: dict[str, Any]) -> str:
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


def _get_token() -> str:
    explicit = os.environ.get("FOUNDRY_ACCESS_TOKEN") or os.environ.get("AZURE_AI_ACCESS_TOKEN")
    if explicit:
        return explicit

    try:
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential().get_token(AI_SCOPE).token
    except Exception as azure_exc:
        logger.info("DefaultAzureCredential unavailable: %s", azure_exc)

    try:
        raw = subprocess.check_output(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                AI_RESOURCE,
                "-o",
                "json",
            ],
            text=True,
            stderr=subprocess.STDOUT,
        )
        token = json.loads(raw).get("accessToken")
        if token:
            return str(token)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as az_exc:
        raise RuntimeError(
            "Could not acquire a Foundry token. Set FOUNDRY_ACCESS_TOKEN, install "
            "azure-identity, or sign in with Azure CLI (`az login`)."
        ) from az_exc

    raise RuntimeError("Azure CLI did not return an access token")


def _command_text(command: list[str]) -> str | None:
    try:
        return subprocess.check_output(
            command,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _is_url(value: str | None) -> bool:
    return bool(value and value.startswith(("https://", "http://")))


def _resolve_project_endpoint(explicit: str | None) -> str | None:
    for candidate in (
        explicit,
        os.environ.get("PROJECT_ENDPOINT"),
        os.environ.get("AZURE_AI_PROJECT_ENDPOINT"),
    ):
        if _is_url(candidate):
            return candidate

    azd_endpoint = _command_text(["azd", "env", "get-value", "AZURE_AI_PROJECT_ENDPOINT"])
    if _is_url(azd_endpoint):
        return azd_endpoint

    resource_group = os.environ.get("FORGE_RESOURCE_GROUP") or DEFAULT_RESOURCE_GROUP
    project_name = os.environ.get("AZURE_AI_PROJECT_NAME") or DEFAULT_PROJECT_NAME
    account_name = os.environ.get("AZURE_AI_ACCOUNT_NAME")
    if not account_name:
        account_name = _command_text(
            [
                "az",
                "resource",
                "list",
                "-g",
                resource_group,
                "--query",
                "[?type=='Microsoft.CognitiveServices/accounts' && kind=='AIServices']|[0].name",
                "-o",
                "tsv",
            ]
        )
    subscription_id = _command_text(["az", "account", "show", "--query", "id", "-o", "tsv"])
    if not account_name or not subscription_id:
        return None

    resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}"
    )
    return _command_text(
        [
            "az",
            "resource",
            "show",
            "-g",
            resource_group,
            "--ids",
            resource_id,
            "--query",
            'properties.endpoints."AI Foundry API"',
            "-o",
            "tsv",
        ]
    )


def _foundry_request(url: str, token: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Foundry-Features": FOUNDRY_FEATURES,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Foundry request failed HTTP {exc.code}: {details[:500]}") from exc


def _responses_base(project_endpoint: str, agent: str) -> str:
    return f"{project_endpoint.rstrip('/')}/agents/{agent}{RESPONSES_SUFFIX}"


def _find_key(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        found = value.get(key)
        if isinstance(found, str) and found.strip():
            return found.strip()
        for child in value.values():
            nested = _find_key(child, key)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_key(child, key)
            if nested:
                return nested
    return None


def _resolve_waypoint_app_base_url(project_endpoint: str, agent: str) -> str | None:
    for candidate in (
        os.environ.get("WAYPOINT_APP_BASE_URL"),
        _command_text(["azd", "env", "get-value", "WAYPOINT_APP_BASE_URL"]),
    ):
        if _is_url(candidate):
            return candidate.rstrip("/")

    try:
        token = _get_token()
        query = urlencode({"api-version": API_VERSION})
        metadata = _foundry_request(
            f"{project_endpoint.rstrip('/')}/agents/{agent}?{query}",
            token,
        )
    except Exception as exc:
        logger.warning("Could not resolve WAYPOINT_APP_BASE_URL from hosted agent metadata: %s", exc)
        return None

    found = _find_key(metadata, "WAYPOINT_APP_BASE_URL")
    if _is_url(found):
        return found.rstrip("/")
    return None


def _drive_hosted_agent(
    agent: str,
    project_endpoint: str,
    prompt: str,
    *,
    poll_timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    token = _get_token()
    base = _responses_base(project_endpoint, agent)
    query = urlencode({"api-version": API_VERSION})
    started = _foundry_request(
        f"{base}?{query}",
        token,
        body={"input": prompt, "store": True, "background": True},
    )
    response_id = started.get("id") or started.get("response_id")
    if not response_id:
        raise RuntimeError(f"Foundry response did not include an id: {json.dumps(started)[:500]}")

    logger.info("Started hosted %s response id=%s status=%s", agent, response_id, started.get("status"))
    poll_url = f"{base}/{response_id}?{query}"
    deadline = time.monotonic() + poll_timeout
    last = started
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        last = _foundry_request(poll_url, token)
        status = last.get("status")
        logger.info("Hosted %s response id=%s status=%s", agent, response_id, status)
        if status in TERMINAL_STATUSES:
            return last

    raise RuntimeError(
        f"Hosted response {response_id} did not complete within {poll_timeout:.0f}s "
        f"(last status={last.get('status')})"
    )


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def _card_reply(reply_text: str) -> tuple[str, list[dict[str, Any]] | None]:
    human_text, payload = extract_card_payload(reply_text)
    if payload is None:
        card = build_text_fallback_card(human_text or reply_text)
        return "", [card_attachment_dict(card)]
    card = build_status_card(payload)
    return "", [card_attachment_dict(card)]


def _prompt_for_card(message: str) -> str:
    return (
        f"{message.strip()}\n\n"
        "You are being invoked by a local Adaptive Card preview bridge. "
        "Follow the structured-card instructions below for this turn so the "
        "bridge can render the same attachment shape used by the AI Teammate "
        "activity surface.\n"
        f"{CARD_INSTRUCTIONS}"
    )


class BridgeServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        agent: str,
        project_endpoint: str,
        poll_timeout: float,
        poll_interval: float,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.agent = agent
        self.project_endpoint = project_endpoint
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval


class ActivityBridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in {"/api/messages", "/healthz"}:
            _json_response(
                self,
                200,
                {
                    "status": "OK",
                    "agent": self.server.agent,
                    "projectEndpoint": self.server.project_endpoint,
                },
            )
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") != "/api/messages":
            _json_response(self, 404, {"error": "not found"})
            return

        try:
            activity = _read_json(self)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Invalid activity JSON: %s", exc)
            _json_response(self, 400, {"error": "invalid JSON body"})
            return

        activity_type = str(activity.get("type") or "").lower()
        message = str(activity.get("text") or "").strip()
        if activity_type != "message" or not message:
            _json_response(self, 202, {"status": "accepted"})
            return

        try:
            result = _drive_hosted_agent(
                self.server.agent,
                self.server.project_endpoint,
                _prompt_for_card(message),
                poll_timeout=self.server.poll_timeout,
                poll_interval=self.server.poll_interval,
            )
            if result.get("status") != "completed":
                raise RuntimeError(f"hosted agent ended with status={result.get('status')}")
            reply_text = _extract_output_text(result)
            text, attachments = _card_reply(reply_text)
            self._post_callback(activity, text, attachments)
            _json_response(self, 202, {"status": "accepted"})
        except Exception as exc:  # noqa: BLE001 - return a useful local-dev error
            logger.exception("Hosted invoice-analyst turn failed")
            _json_response(self, 502, {"error": f"{type(exc).__name__}: {exc}"})

    def _post_callback(
        self,
        inbound: dict[str, Any],
        text: str,
        attachments: list[dict[str, Any]] | None,
    ) -> None:
        service_url = str(inbound.get("serviceUrl") or "").rstrip("/")
        conversation_id = str((inbound.get("conversation") or {}).get("id") or "")
        if not service_url or not conversation_id:
            raise RuntimeError("activity missing serviceUrl or conversation.id")

        reply_activity: dict[str, Any] = {
            "type": "message",
            "text": text,
            "conversation": inbound.get("conversation"),
            "from": inbound.get("recipient") or {"id": self.server.agent, "name": self.server.agent},
            "recipient": inbound.get("from") or {"id": "canvas-user", "name": "Canvas User"},
            "replyToId": inbound.get("id"),
        }
        if attachments:
            reply_activity["attachments"] = attachments

        payload = json.dumps(reply_activity).encode("utf-8")
        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        request = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                if response.status >= 400:
                    raise RuntimeError(f"callback returned HTTP {response.status}")
        except URLError as exc:
            raise RuntimeError(f"failed to post callback to {url}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=DEFAULT_AGENT, help="Hosted Foundry agent name.")
    parser.add_argument("--project-endpoint", default=None, help="Foundry project endpoint override.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-timeout", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    project_endpoint = _resolve_project_endpoint(args.project_endpoint)
    if not project_endpoint:
        parser.error(
            "Could not resolve Foundry project endpoint. Pass --project-endpoint, set "
            "PROJECT_ENDPOINT/AZURE_AI_PROJECT_ENDPOINT, select an azd env, or sign in "
            "with Azure CLI so rg-forge/ai-project-forge can be discovered."
        )
    if not _is_url(project_endpoint):
        parser.error(f"Foundry project endpoint must be a URL, got: {project_endpoint!r}")

    waypoint_app_base_url = _resolve_waypoint_app_base_url(project_endpoint, args.agent)
    if waypoint_app_base_url:
        os.environ["WAYPOINT_APP_BASE_URL"] = waypoint_app_base_url
        logger.info("Using WAYPOINT_APP_BASE_URL=%s", waypoint_app_base_url)
    else:
        logger.warning("WAYPOINT_APP_BASE_URL is unset; invoice rows will not include deep links")

    server = BridgeServer(
        (args.host, args.port),
        ActivityBridgeHandler,
        agent=args.agent,
        project_endpoint=project_endpoint,
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
    )
    logger.info(
        "Foundry activity bridge listening on http://%s:%s/api/messages -> %s/%s",
        args.host,
        args.port,
        project_endpoint.rstrip("/"),
        args.agent,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping Foundry activity bridge")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
