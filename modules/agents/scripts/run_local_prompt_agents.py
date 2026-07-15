"""Local deterministic IQ expert stub host for the pipeline-control canvas."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


AGENTS = {
    "workiq": ("collaboration-evidence-expert", "workplace correspondence supports supplier escalation history"),
    "webiq": ("market-evidence-expert", "public supplier notices corroborate delayed capacity"),
    "foundryiq": ("contract-policy-expert", "internal policy retrieval flags invoice assurance review"),
    "fabriciq": ("operations-data-expert", "FabricIQ is temporarily stubbed; no Fabric or OneLake source was queried"),
}

SUPPORTS = {
    "workiq": "review",
    "webiq": "unknown",
    "foundryiq": "recover",
    "fabriciq": "unknown",
}


def _response_contract(agent_key: str, prompt: str) -> dict[str, Any]:
    agent_name, summary = AGENTS[agent_key]
    invoice_id = _extract_invoice_id(prompt)
    return {
        "agent": agent_name,
        "plane": agent_key,
        "invoice_id": invoice_id,
        "evidence": [
            {
                "claim": summary,
                "supports": SUPPORTS[agent_key],
                "source_ref": f"local-stub:{agent_key}:{invoice_id}",
                "classification": "standard",
                "confidence": 0.35 if SUPPORTS[agent_key] == "unknown" else 0.7,
            }
        ],
        "summary": summary,
        "correlation": {"waypoint_run_id": None, "waypoint_invoice_id": invoice_id},
    }


def _extract_invoice_id(prompt: str) -> str:
    marker = "Invoice id:"
    if marker in prompt:
        tail = prompt.split(marker, 1)[1].strip()
        return tail.split(".", 1)[0].strip() or "INV-2026-08034"
    return "INV-2026-08034"


class Handler(BaseHTTPRequestHandler):
    server_version = "ForgeLocalPromptAgents/1.0"

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/healthz":
            self._write_json(
                {
                    "ok": True,
                    "mode": self.server.mode,
                    "agents": [name for name, _ in AGENTS.values()],
                }
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        agent_key = self.path.strip("/").split("/", 1)[0]
        if agent_key not in AGENTS or not self.path.rstrip("/").endswith("/responses"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        prompt = str(body.get("input") or "")
        contract = _response_contract(agent_key, prompt)
        self._write_json(
            {
                "id": f"local-{agent_key}",
                "object": "response",
                "output_text": json.dumps(contract, separators=(",", ":")),
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(contract, separators=(",", ":")),
                            }
                        ],
                    }
                ],
            }
        )

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _write_json(self, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--mode", default="stub")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.mode = args.mode
    print(f"Local prompt-agent stubs listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
