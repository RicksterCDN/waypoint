"""Adaptive Card rendering for the invoice-analyst AI Teammate surface.

The Responses surface (`/responses`) keeps its scannable Markdown, because
Foundry auto-bridges Responses -> Activity server-side and we only ever hand it
text. The **AI Teammate** surface (`/api/messages`) is different: we build and
send the Bot Framework Activity ourselves, so we can attach a real Adaptive
Card and get proper tables/fact sets in Teams instead of raw Markdown pipes.

Design (kept deliberately robust):

* The teammate agent is instructed (see ``CARD_INSTRUCTIONS``) to append a
  delimited JSON block describing the answer as structured sections.
* ``extract_card_payload`` pulls that JSON out and returns the human-readable
  text with the block stripped.
* ``build_status_card`` turns the payload into an Adaptive Card 1.5 document
  (FactSet / Table / bullet sections).
* If the block is missing or malformed, callers fall back to sending the plain
  text exactly as before — nothing breaks.

Only ``/api/messages`` uses any of this. The Responses path never imports it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import quote

logger = logging.getLogger("invoice_analyst.adaptive_cards")

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Base URL for the Waypoint web app. When set, per-invoice rows and the footer
# become tappable "learn more" links (Action.OpenUrl) that deep-link into the
# Waypoint invoices page (…/invoices?invoice=<id>). This is the Waypoint *web*
# Container App URL — a waypoint deploy output (web_fqdn) seeded into the azd
# env by the forge deploy workflow, distinct from WAYPOINT_API_BASE_URL (the
# API). Left unset in most local runs -> rows still render, just without links.
# The model may also supply an explicit ``url`` per item, which always wins.
_APP_BASE_ENV = "WAYPOINT_APP_BASE_URL"
_DEFAULT_INVOICE_YEAR = "2026"
_INVOICE_ID_RE = re.compile(r"\bINV-\d{4}-[A-Za-z0-9-]+\b", re.IGNORECASE)
_HUMAN_INVOICE_RE = re.compile(r"\bInvoice\s*#\s*([A-Za-z0-9-]+)\b", re.IGNORECASE)

# Maps a severity word to an Adaptive Card text colour. Anything unknown falls
# back to the money-at-risk red so amounts never render as plain text.
_SEVERITY_COLOR = {
    "critical": "Attention",
    "high": "Attention",
    "medium": "Warning",
    "moderate": "Warning",
    "low": "Good",
    "cleared": "Good",
    "ok": "Good",
    "matched": "Good",
}

# Delimiters the model wraps its structured payload in. Chosen to be unlikely to
# appear in normal prose or Markdown and easy to strip.
CARD_START = "<<<ANALYST_CARD>>>"
CARD_END = "<<<END_ANALYST_CARD>>>"

_CARD_BLOCK_RE = re.compile(
    re.escape(CARD_START) + r"\s*(.*?)\s*" + re.escape(CARD_END),
    re.DOTALL,
)

# Teammate-only prompt addendum. Appended to the base prompt for the AI Teammate
# agent so it emits a structured card payload in addition to its normal answer.
CARD_INSTRUCTIONS = f"""

## Structured card payload (Teams / AI Teammate surface only)

After your normal Markdown answer, append a machine-readable JSON block that
restates the answer as a **decision-ready digest card** for a Finance approver.
A downstream renderer turns it into an Adaptive Card in Teams. If it is missing,
your Markdown is used as-is, so always include it for status/summary/queue
answers.

Latency rules for Teams:
- Keep Teams turns under 20 seconds. The activity request can be dropped by the
  Teams/Foundry forwarding path if you keep working too long, even while typing
  indicators are visible.
- For demo follow-ups like "why is BluePeak flagged?", "why is this invoice
  blocked?", "do we have enough evidence?", or "what should AP do?", use
  WaypointIQ operational tools only: invoice assurance, invoice context,
  findings, evidence, work, and invoices. Those records already carry the
  invoice reason, at-risk amount, current decision, and evidence summary.
- Do NOT call the knowledge-base / FoundryIQ retrieval tool for those fast Teams
  follow-ups unless the user explicitly asks for a contract clause, policy text,
  rate card, or citation. If contract/policy grounding is not already present in
  the WaypointIQ result, say "I can pull the contract clause next" as the next
  step instead of doing another retrieval in the same Teams turn.
- Prefer one focused card over exhaustive grounding. Lead with the answer,
  amount, and recommended next review step.

Design the payload for a busy approver, not a developer:
- **Money first.** Lead with the dollars at risk and what needs a decision.
- **Relevance over completeness.** ~5 facts, not 15. Keep run IDs, case UUIDs,
  file paths, and policy IDs OUT of visible text — they are link targets only.
- **Plain language.** Use the supplier/vendor name as each item's title when you
  have one; otherwise use the human invoice number (e.g. "Invoice #08034"). Never
  put a raw UUID or hash in a visible title or detail line.
- **Links to learn more.** For every flagged invoice item, include `invoiceId`
  (and `caseId` if known) so the card can link directly to the invoice detail
  view. If you only know a human invoice number like "Invoice #08034", convert
  it to the canonical id shape `INV-2026-08034`.

Rules:
- Wrap the JSON on its own lines between the exact markers `{CARD_START}` and
  `{CARD_END}`. Emit nothing after the closing marker.
- The JSON must be valid and use this shape (include only keys you have data for;
  `summary` is the only required key):
  {{
    "summary": "one-line plain-text TL;DR (no Markdown, no ** or backticks)",
    "eyebrow": "Needs your decision",
    "eyebrowStyle": "warn",
    "headline": "3 invoices need your decision",
    "atRisk": {{ "amount": "$39,600", "note": "high-severity queue" }},
    "items": [
      {{ "title": "Meridian Contract Mfg",
         "detail": "Invoice #08034 · Surge-capacity overcharge",
         "amount": "$25,000", "severity": "high",
         "invoiceId": "INV-2026-08034", "caseId": "case-d878…" }}
    ],
    "activity": [
      "43 runs completed · $610K cleared",
      "18 in review · 3 awaiting evidence"
    ],
    "footer": {{ "text": "View full queue in app (18 items)" }},
    "nextStep": "want me to drill into the high-severity queue?"
  }}
- `eyebrow` is a short uppercase-style label (may start with an emoji). Set
  `eyebrowStyle` to `"warn"` for decision/at-risk cards, otherwise omit it.
- `atRisk.amount` is the headline dollar figure; `atRisk.note` is a short
  qualifier. Keep amounts as plain strings like "$39,600".
- `items` are the few invoices/cases that actually need attention (cap ~5),
  highest exposure first. `severity` is one of high / medium / low and drives the
  amount colour.
- `activity` is 1-2 ambient "still working" lines (period totals, what is in
  review). Optional.
- `footer.text` is the "see everything" link label; the renderer supplies the
  URL. You may pass `footer.url` to override.
- Keep every visible value plain text: no Markdown emphasis, no backticks. A
  leading severity emoji (🔴/🟠/🟢) is fine.
- For non-digest answers you may instead pass a `sections` array of
  `{{ "heading", "facts" | "table" | "bullets" }}` blocks; the renderer supports
  both, but prefer the digest shape above for status/queue answers.
"""


def extract_card_payload(text: str) -> tuple[str, dict[str, Any] | None]:
    """Split *text* into (human_text, card_payload).

    Returns the text with the card block removed, plus the parsed payload dict
    (or ``None`` when there is no valid block). Never raises on bad input.
    """
    if not text:
        return text, None

    match = _CARD_BLOCK_RE.search(text)
    if not match:
        return text.strip(), None

    human_text = (text[: match.start()] + text[match.end() :]).strip()
    raw = match.group(1).strip()

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Card payload block present but not valid JSON; ignoring.")
        return human_text, None

    if not isinstance(payload, dict) or "summary" not in payload:
        logger.warning("Card payload JSON missing required 'summary'; ignoring.")
        return human_text, None

    return human_text, payload


def _text_block(text: str, **kwargs: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": str(text), "wrap": True}
    block.update(kwargs)
    return block


def _fact_set(facts: list[Any]) -> dict[str, Any] | None:
    items = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        title = str(fact.get("title", "")).strip()
        value = str(fact.get("value", "")).strip()
        if not title and not value:
            continue
        items.append({"title": title, "value": value})
    if not items:
        return None
    return {"type": "FactSet", "facts": items}


def _table(table: dict[str, Any]) -> dict[str, Any] | None:
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    if not columns or not rows:
        return None

    def _cell(value: Any, header: bool = False) -> dict[str, Any]:
        return {
            "type": "TableCell",
            "items": [
                _text_block(
                    value,
                    weight="Bolder" if header else "Default",
                )
            ],
        }

    table_rows = [
        {
            "type": "TableRow",
            "cells": [_cell(col, header=True) for col in columns],
        }
    ]
    for row in rows:
        cells = list(row) if isinstance(row, (list, tuple)) else [row]
        # Pad/truncate to the column count so the grid stays aligned.
        cells = (cells + [""] * len(columns))[: len(columns)]
        table_rows.append(
            {"type": "TableRow", "cells": [_cell(c) for c in cells]}
        )

    return {
        "type": "Table",
        "columns": [{"width": 1} for _ in columns],
        "rows": table_rows,
        "firstRowAsHeaders": True,
        "gridStyle": "default",
        "spacing": "Small",
    }


def _bullets(bullets: list[Any]) -> dict[str, Any] | None:
    lines = [f"• {str(b).strip()}" for b in bullets if str(b).strip()]
    if not lines:
        return None
    return _text_block("\n".join(lines), spacing="Small")


# --- Digest-card helpers ---------------------------------------------------


def _app_base_url() -> str | None:
    value = os.environ.get(_APP_BASE_ENV)
    if not value or value.startswith("{{") or value.startswith("${"):
        return None
    return value.rstrip("/")


def _invoice_url(item: dict[str, Any]) -> str | None:
    """Resolve a "learn more" link for one item.

    An explicit ``url`` always wins. Otherwise, when an app base URL is
    configured, deep-link into the Waypoint invoices page with a query param
    the page honors: ``{base}/invoices?invoice={invoiceId}`` (falling back to
    ``?case={caseId}``). Returns ``None`` when no link can be formed, so rows
    stay non-tappable instead of pointing nowhere.
    """
    explicit = str(item.get("url", "")).strip()
    if explicit:
        return explicit
    base = _app_base_url()
    if not base:
        return None
    invoice_id = _item_invoice_id(item)
    if invoice_id:
        return f"{base}/invoices?invoice={quote(invoice_id, safe='')}"
    case_id = _item_case_id(item)
    if case_id:
        return f"{base}/invoices?case={quote(case_id, safe='')}"
    return None


def _item_invoice_id(item: dict[str, Any]) -> str:
    for key in ("invoiceId", "invoice_id", "invoice"):
        value = str(item.get(key, "")).strip()
        if value and not value.startswith(("{{", "${")):
            return value

    searchable = " ".join(
        str(value)
        for value in item.values()
        if isinstance(value, (str, int, float)) and str(value).strip()
    )
    found = _INVOICE_ID_RE.search(searchable)
    if found:
        return found.group(0).upper()

    human = _HUMAN_INVOICE_RE.search(searchable)
    if human:
        return f"INV-{_DEFAULT_INVOICE_YEAR}-{human.group(1)}"
    return ""


def _item_case_id(item: dict[str, Any]) -> str:
    for key in ("caseId", "case_id", "case"):
        value = str(item.get(key, "")).strip()
        if value and not value.startswith(("{{", "${")):
            return value
    return ""


def _open_url_action(title: str, url: str, *, style: str | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"type": "Action.OpenUrl", "title": title, "url": url}
    if style:
        action["style"] = style
    return action


def _at_risk_block(at_risk: dict[str, Any]) -> dict[str, Any] | None:
    """Money-first line: bold red amount + subtle qualifier (one RichTextBlock)."""
    amount = str(at_risk.get("amount", "")).strip()
    if not amount:
        return None
    note = str(at_risk.get("note", "")).strip()
    runs: list[dict[str, Any]] = [
        {"type": "TextRun", "text": amount, "weight": "Bolder", "color": "Attention", "size": "Large"},
        {"type": "TextRun", "text": " at risk", "color": "Attention"},
    ]
    if note:
        runs.append({"type": "TextRun", "text": f"  ·  {note}", "isSubtle": True})
    return {"type": "RichTextBlock", "spacing": "Small", "inlines": runs}


def _digest_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """One tappable invoice row: supplier + detail on the left, amount on the right."""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title", "")).strip()
    detail = str(item.get("detail", "")).strip()
    amount = str(item.get("amount", "")).strip()
    if not title and not detail and not amount:
        return None

    severity = str(item.get("severity", "")).strip().lower()
    amount_color = _SEVERITY_COLOR.get(severity, "Attention")
    url = _invoice_url(item)

    left_items: list[dict[str, Any]] = []
    if title:
        left_items.append(_text_block(title, weight="Bolder", spacing="None"))
    if detail:
        left_items.append(_text_block(detail, isSubtle=True, size="Small", spacing="None"))

    right_items: list[dict[str, Any]] = []
    if amount:
        right_items.append(
            _text_block(
                amount, weight="Bolder", color=amount_color,
                horizontalAlignment="Right", spacing="None",
            )
        )
    if url:
        right_items.append(
            _text_block(
                "View details →", color="Accent", size="Small",
                horizontalAlignment="Right", spacing="None",
            )
        )

    columns: list[dict[str, Any]] = [
        {"type": "Column", "width": "stretch", "items": left_items or [_text_block("")]}
    ]
    if right_items:
        columns.append(
            {"type": "Column", "width": "auto", "verticalContentAlignment": "Center", "items": right_items}
        )

    container: dict[str, Any] = {
        "type": "Container",
        "style": "emphasis",
        "spacing": "Small",
        "items": [{"type": "ColumnSet", "columns": columns}],
    }
    if url:
        container["selectAction"] = {"type": "Action.OpenUrl", "url": url}
    return container


def _activity_block(activity: list[Any]) -> dict[str, Any] | None:
    lines = [str(a).strip() for a in activity if str(a).strip()]
    if not lines:
        return None
    return {
        "type": "Container",
        "spacing": "Medium",
        "separator": True,
        "items": [
            _text_block(line, isSubtle=True, size="Small", spacing="Small")
            for line in lines
        ],
    }


def _legacy_sections(sections: list[Any]) -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading", "")).strip()
        rendered: dict[str, Any] | None = None
        if "facts" in section and isinstance(section["facts"], list):
            rendered = _fact_set(section["facts"])
        elif "table" in section and isinstance(section["table"], dict):
            rendered = _table(section["table"])
        elif "bullets" in section and isinstance(section["bullets"], list):
            rendered = _bullets(section["bullets"])
        if rendered is None:
            continue
        if heading:
            body.append(
                _text_block(heading, weight="Bolder", size="Medium", spacing="Medium")
            )
        body.append(rendered)
    return body


def build_status_card(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a decision-ready Adaptive Card (1.5) from a structured payload.

    Renders the money-first digest layout (eyebrow → headline → at-risk →
    tappable invoice rows → ambient activity) and still supports the legacy
    ``sections`` blocks for non-digest answers.
    """
    summary = str(payload.get("summary", "")).strip()
    body: list[dict[str, Any]] = []

    eyebrow = str(payload.get("eyebrow", "")).strip()
    if eyebrow:
        warn = str(payload.get("eyebrowStyle", "")).strip().lower() == "warn"
        body.append(
            _text_block(
                eyebrow.upper(), weight="Bolder", size="Small", spacing="None",
                isSubtle=not warn, **({"color": "Attention"} if warn else {}),
            )
        )

    headline = str(payload.get("headline", "")).strip() or summary
    if headline:
        body.append(
            _text_block(headline, weight="Bolder", size="Large", spacing="None")
        )

    at_risk = payload.get("atRisk")
    if isinstance(at_risk, dict):
        block = _at_risk_block(at_risk)
        if block:
            body.append(block)

    for item in payload.get("items", []) or []:
        rendered = _digest_item(item)
        if rendered:
            body.append(rendered)

    body.extend(_legacy_sections(payload.get("sections", [])))

    activity = payload.get("activity")
    if isinstance(activity, list):
        block = _activity_block(activity)
        if block:
            body.append(block)

    next_step = str(payload.get("nextStep", "")).strip()
    if next_step:
        body.append(
            _text_block(f"Next step: {next_step}", isSubtle=True, spacing="Medium")
        )

    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "fallbackText": summary or headline or "Assurance status update",
        "body": body,
    }

    footer = payload.get("footer")
    if isinstance(footer, dict):
        text = str(footer.get("text", "")).strip()
        base = _app_base_url()
        url = str(footer.get("url", "")).strip() or (f"{base}/invoices" if base else "")
        if text and url:
            card["actions"] = [_open_url_action(text, url, style="positive")]

    return card


def build_text_fallback_card(text: str) -> dict[str, Any]:
    """Build a simple Adaptive Card when the model omits structured JSON.

    The Teams activity surface should remain card-first even for drafting or
    audit-trail replies that do not naturally fit the digest payload. Preserve
    the model's readable text, but avoid relying on Teams Markdown rendering.
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    cleaned = [_clean_markdown_line(line) for line in lines]
    summary = next((line for line in cleaned if line), "Invoice Analyst response")

    body: list[dict[str, Any]] = [
        _text_block(
            summary,
            weight="Bolder",
            size="Medium",
            spacing="None",
        )
    ]

    for line in cleaned[1:12]:
        if not line:
            continue
        body.append(_text_block(line, spacing="Small"))

    if len(cleaned) > 12:
        body.append(_text_block("Additional details omitted for Teams card length.", isSubtle=True, size="Small"))

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "fallbackText": summary,
        "body": body,
    }


def _clean_markdown_line(line: str) -> str:
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^(?:[>-]|\*)\s+", "", line)
    line = line.replace("**", "").replace("__", "")
    return line.strip()


def card_attachment_dict(card: dict[str, Any]) -> dict[str, Any]:
    """Bot Framework attachment dict for the raw-JSON (anonymous local) path."""
    return {"contentType": ADAPTIVE_CARD_CONTENT_TYPE, "content": card}
