# invoice-analyst

`invoice-analyst` is the hosted, read-only human-facing agent for Caldova
Forge. It answers invoice-assurance questions and status requests by combining:

- direct Waypoint status tools for run and case status on both Responses and
  Microsoft 365 activity surfaces.
- `waypoint_iq`: operational facts from the durable `waypoint-iq` toolbox.
- `foundryiq_kb`: contract and policy grounding from the `kb-mcp-connection`
  knowledge-base MCP connection.

`main.py` loads `prompt.md`, wires the MCP tool surfaces from the project
endpoint, adds direct read-only Waypoint status tools, and serves both the
Foundry Responses protocol and the Microsoft 365 activity protocol.

## Response formatting per surface

- **Responses / Foundry chat** (`/responses`): scannable Markdown (bold TL;DR,
  section headings, tables, severity cues). Foundry auto-bridges Responses to
  Activity server-side for the direct-bot chat surface, so that path is Markdown
  too.
- **Microsoft 365 AI Teammate** (`/api/messages`): renders a native **Adaptive
  Card** instead of Markdown. The teammate agent is given an extra instruction
  (`adaptive_cards.CARD_INSTRUCTIONS`) to append a structured JSON payload;
  `activity_protocol.py` extracts it and sends a `1.5` Adaptive Card
  (FactSet + Table sections). If the payload is missing or malformed, it falls
  back to sending the plain-text answer, so replies never break.

Run the card unit tests with `python test_adaptive_cards.py` from the agent
folder (also runnable under `pytest`).

## Local Adaptive Card preview against Foundry

Use the local activity bridge when you want the Copilot **Adaptive Card preview**
canvas to test the deployed Foundry `invoice-analyst` agent instead of a local
agent process:

```bash
cd agents/invoice-analyst
python3 foundry_activity_bridge.py --port 8090
```

The bridge listens at `http://127.0.0.1:8090/api/messages`, calls the hosted
Foundry Responses endpoint for `invoice-analyst`, renders the structured payload
with `adaptive_cards.py`, and posts the Bot Framework reply back to the canvas
connector. The VS Code task **Run invoice-analyst Foundry activity bridge**
uses the same command. Endpoint resolution checks `--project-endpoint`,
`PROJECT_ENDPOINT` / `AZURE_AI_PROJECT_ENDPOINT`, the active `azd` environment,
and finally the active Azure CLI account's `rg-forge` / `ai-project-forge`
resources.
