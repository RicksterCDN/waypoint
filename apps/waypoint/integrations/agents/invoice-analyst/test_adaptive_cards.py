"""Unit tests for adaptive_cards payload extraction and card rendering."""

from __future__ import annotations

import json
import os

from adaptive_cards import (
    ADAPTIVE_CARD_CONTENT_TYPE,
    CARD_END,
    CARD_START,
    build_status_card,
    build_text_fallback_card,
    card_attachment_dict,
    extract_card_payload,
)

SAMPLE_PAYLOAD = {
    "summary": "All 43 runs completed; 18 investigating cases, USD 39,600 at risk.",
    "sections": [
        {
            "heading": "Operational facts",
            "facts": [
                {"title": "Latest run", "value": "run-04e6 · review · USD 1,120"},
                {"title": "Work queue", "value": "18 investigating"},
            ],
        },
        {
            "heading": "Severity mix",
            "table": {
                "columns": ["Severity", "Cases", "At risk"],
                "rows": [
                    ["🔴 High", "6", "USD 34,580"],
                    ["🟠 Medium", "6", "USD 5,020"],
                    ["🟢 Low", "6", "USD 0"],
                ],
            },
        },
        {"heading": "Notes", "bullets": ["reruns excluded", "current work only"]},
    ],
    "nextStep": "drill into the high-severity queue?",
}


def _wrap(payload: dict) -> str:
    return (
        "Here is the status.\n\n"
        f"{CARD_START}\n{json.dumps(payload)}\n{CARD_END}"
    )


def test_extract_valid_payload_strips_block():
    text = _wrap(SAMPLE_PAYLOAD)
    human, payload = extract_card_payload(text)
    assert payload == SAMPLE_PAYLOAD
    assert CARD_START not in human and CARD_END not in human
    assert human.strip() == "Here is the status."


def test_extract_no_block_returns_text():
    human, payload = extract_card_payload("just a normal answer")
    assert payload is None
    assert human == "just a normal answer"


def test_extract_malformed_json_falls_back():
    text = f"body\n{CARD_START}\n{{not json,,}}\n{CARD_END}"
    human, payload = extract_card_payload(text)
    assert payload is None
    assert "body" in human


def test_extract_missing_summary_rejected():
    text = f"body\n{CARD_START}\n{json.dumps({'sections': []})}\n{CARD_END}"
    _human, payload = extract_card_payload(text)
    assert payload is None


def test_build_card_structure():
    card = build_status_card(SAMPLE_PAYLOAD)
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.5"
    assert card["fallbackText"] == SAMPLE_PAYLOAD["summary"]

    types = [b["type"] for b in card["body"]]
    assert types[0] == "TextBlock"  # summary
    assert "FactSet" in types
    assert "Table" in types

    table = next(b for b in card["body"] if b["type"] == "Table")
    assert table["firstRowAsHeaders"] is True
    # header row + 3 data rows
    assert len(table["rows"]) == 4
    assert len(table["columns"]) == 3

    # next step present
    assert any(
        b["type"] == "TextBlock" and b["text"].startswith("Next step:")
        for b in card["body"]
    )


def test_table_row_padding():
    payload = {
        "summary": "s",
        "sections": [
            {
                "heading": "t",
                "table": {"columns": ["a", "b", "c"], "rows": [["x"]]},
            }
        ],
    }
    card = build_status_card(payload)
    table = next(b for b in card["body"] if b["type"] == "Table")
    data_row = table["rows"][1]
    assert len(data_row["cells"]) == 3  # padded to column count


def test_attachment_dict():
    att = card_attachment_dict(build_status_card(SAMPLE_PAYLOAD))
    assert att["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
    assert att["content"]["type"] == "AdaptiveCard"


def test_text_fallback_card_for_plain_reply():
    card = build_text_fallback_card(
        "**Draft disputes USD 111,000 on BluePeak invoice `INV-2026-08034`; "
        "the matched base-service amount should remain separate.**\n\n"
        "## AP paste-ready hold notice\n"
        "> We are placing USD 111,000 on hold pending dispute review."
    )
    assert card["type"] == "AdaptiveCard"
    assert card["fallbackText"].startswith("Draft disputes USD 111,000")
    assert any(
        "AP paste-ready hold notice" in block.get("text", "")
        for block in card["body"]
        if block["type"] == "TextBlock"
    )


DIGEST_PAYLOAD = {
    "summary": "3 invoices need a decision; $31,790 at risk.",
    "eyebrow": "Needs your decision",
    "eyebrowStyle": "warn",
    "headline": "3 invoices need your decision",
    "atRisk": {"amount": "$31,790", "note": "high-severity queue"},
    "items": [
        {
            "title": "Meridian Contract Mfg",
            "detail": "Invoice #08034 · Surge-capacity overcharge",
            "amount": "$25,000",
            "severity": "high",
            "invoiceId": "INV-2026-08034",
        },
        {
            "title": "Delta Compounding Labs",
            "detail": "Invoice #08462 · Possible duplicate",
            "amount": "$4,680",
            "severity": "medium",
            "invoiceId": "INV-2026-08462",
        },
    ],
    "activity": ["42 reconciled this week · $610K cleared", "15 in review"],
    "footer": {"text": "View full queue in app (18 items)"},
    "nextStep": "drill into the high-severity queue?",
}


def _first(card, pred):
    return next(b for b in card["body"] if pred(b))


def _set_base(value):
    """Set/clear the app base URL env used for invoice links."""
    if value is None:
        os.environ.pop("WAYPOINT_APP_BASE_URL", None)
    else:
        os.environ["WAYPOINT_APP_BASE_URL"] = value


def test_digest_eyebrow_and_headline():
    card = build_status_card(DIGEST_PAYLOAD)
    eyebrow = card["body"][0]
    assert eyebrow["type"] == "TextBlock"
    assert eyebrow["text"] == "NEEDS YOUR DECISION"  # uppercased
    assert eyebrow["color"] == "Attention"  # eyebrowStyle == warn
    headline = card["body"][1]
    assert headline["text"] == "3 invoices need your decision"
    assert headline["size"] == "Large"


def test_digest_at_risk_richtext():
    card = build_status_card(DIGEST_PAYLOAD)
    rich = _first(card, lambda b: b["type"] == "RichTextBlock")
    joined = "".join(r["text"] for r in rich["inlines"])
    assert "$31,790" in joined and "at risk" in joined
    amount_run = rich["inlines"][0]
    assert amount_run["color"] == "Attention" and amount_run["weight"] == "Bolder"


def test_digest_items_render_as_containers():
    card = build_status_card(DIGEST_PAYLOAD)
    containers = [b for b in card["body"] if b["type"] == "Container" and b.get("style") == "emphasis"]
    assert len(containers) == 2
    cols = containers[0]["items"][0]
    assert cols["type"] == "ColumnSet"
    # severity high -> amount coloured Attention
    right_col = cols["columns"][1]
    amount_tb = right_col["items"][0]
    assert amount_tb["color"] == "Attention"
    # medium -> Warning
    cols2 = containers[1]["items"][0]
    assert cols2["columns"][1]["items"][0]["color"] == "Warning"


def test_item_links_require_base_url():
    # No base configured and no explicit url -> not tappable.
    _set_base(None)
    card = build_status_card(DIGEST_PAYLOAD)
    container = _first(card, lambda b: b["type"] == "Container" and b.get("style") == "emphasis")
    assert "selectAction" not in container

    # With a base configured -> each item deep-links to /invoices?invoice={id}.
    _set_base("https://waypoint.example.com/app")
    card = build_status_card(DIGEST_PAYLOAD)
    container = _first(card, lambda b: b["type"] == "Container" and b.get("style") == "emphasis")
    assert container["selectAction"]["type"] == "Action.OpenUrl"
    assert container["selectAction"]["url"] == "https://waypoint.example.com/app/invoices?invoice=INV-2026-08034"
    _set_base(None)


def test_explicit_item_url_wins():
    _set_base("https://base.example.com")
    payload = {
        "summary": "s",
        "items": [{"title": "X", "amount": "$1", "url": "https://direct.example.com/x"}],
    }
    card = build_status_card(payload)
    container = _first(card, lambda b: b["type"] == "Container")
    assert container["selectAction"]["url"] == "https://direct.example.com/x"
    _set_base(None)


def test_human_invoice_number_gets_deep_link():
    _set_base("https://waypoint.example.com")
    payload = {
        "summary": "s",
        "items": [
            {
                "title": "Meridian Contract Mfg",
                "detail": "Invoice #08034 · Surge-capacity overcharge",
                "amount": "$25,000",
            }
        ],
    }
    card = build_status_card(payload)
    container = _first(card, lambda b: b["type"] == "Container")
    assert container["selectAction"]["url"] == "https://waypoint.example.com/invoices?invoice=INV-2026-08034"
    _set_base(None)


def test_footer_action_uses_base():
    _set_base("https://waypoint.example.com")
    card = build_status_card(DIGEST_PAYLOAD)
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["url"] == "https://waypoint.example.com/invoices"
    assert card["actions"][0]["title"].startswith("View full queue")
    _set_base(None)

    # Without a base and without an explicit footer.url, no action is emitted.
    card = build_status_card(DIGEST_PAYLOAD)
    assert "actions" not in card


def test_activity_block_present():
    card = build_status_card(DIGEST_PAYLOAD)
    activity = _first(card, lambda b: b["type"] == "Container" and b.get("separator"))
    texts = [i["text"] for i in activity["items"]]
    assert any("reconciled this week" in t for t in texts)


if __name__ == "__main__":
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        os.environ.pop("WAYPOINT_APP_BASE_URL", None)
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        finally:
            os.environ.pop("WAYPOINT_APP_BASE_URL", None)
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
