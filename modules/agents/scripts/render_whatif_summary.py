#!/usr/bin/env python3
"""Render an `azd provision --preview` result as a GitHub Actions job summary.

azd's preview is emitted as a human-readable text block (even with
`--output json`), shaped like:

     Resources:

     Skip : Resource group : rg-forge
     Modify : Azure AI Services : ai-account-xxxx
     - properties.associatedProjects
     Create : Foundry capability host : agents
     ...
    SUCCESS: Generated provisioning preview in 34 seconds.

Each `<ChangeType> : <Kind> : <Name>` line is a resource; the following
`+` / `-` / `~` lines are its property-level changes. We group resources by
change type and render a counts header plus a Markdown table per type, with the
property changes summarized inline and No-change rows collapsed.

A JSON parse is attempted first so a future azd that emits real JSON still works;
otherwise we parse the text. If neither yields anything, the raw output is dumped
in a collapsible block so the summary is never empty.

Usage: render_whatif_summary.py <preview.json> [preview.err]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Normalized change type -> (emoji, display label, sort order)
CHANGE_META = {
    "create": ("🟢", "Create", 0),
    "modify": ("🟡", "Modify", 1),
    "update": ("🟡", "Modify", 1),
    "deploy": ("🟡", "Modify", 1),
    "delete": ("🔴", "Delete", 2),
    "skip": ("⚪", "No change", 5),
    "nochange": ("⚪", "No change", 5),
    "unchanged": ("⚪", "No change", 5),
    "ignore": ("⚫", "Ignore", 4),
    "unsupported": ("🔵", "Unsupported", 3),
}

CHANGE_TYPES = ("Create", "Modify", "Update", "Deploy", "Delete", "Skip", "NoChange", "Ignore", "Unsupported")
_HEADER_RE = re.compile(r"^\s*(" + "|".join(CHANGE_TYPES) + r")\s*:\s*(.+?)\s*:\s*(.+?)\s*$")


def _norm(change_type: str) -> str:
    return "".join(ch for ch in str(change_type).lower() if ch.isalnum())


def _meta(change_type: str):
    return CHANGE_META.get(_norm(change_type), ("🔵", str(change_type) or "Other", 3))


def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _prop_summary(props: list[str], limit: int = 6) -> str:
    """Compact one-line summary of property changes for a table cell."""
    if not props:
        return "—"
    items: list[str] = []
    for p in props:
        p = p.strip()
        if not p:
            continue
        sym, rest = p[0], p[1:].strip()
        # keep just the property path (before any ': value' / ' => ')
        path = re.split(r"[:=]| =>", rest, 1)[0].strip()
        items.append(f"`{sym}` {_cell(path)}")
    shown = items[:limit]
    extra = len(items) - len(shown)
    out = ", ".join(shown)
    if extra > 0:
        out += f" _(+{extra} more)_"
    return out or "—"


def _parse_text(raw: str) -> list[dict]:
    """Parse azd's text preview into a list of {change_type, kind, name, props}."""
    changes: list[dict] = []
    current: dict | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "+-~" and current is not None:
            current["props"].append(stripped)
            continue
        m = _HEADER_RE.match(line)
        if m:
            current = {"change_type": m.group(1), "kind": m.group(2), "name": m.group(3), "props": []}
            changes.append(current)
        else:
            current = None
    return changes


def _parse_json(raw: str) -> list[dict]:
    """Best-effort: pull resource changes out of a real JSON preview."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            lower = {k.lower(): k for k in node}
            if "changetype" in lower:
                ct = node[lower["changetype"]]
                kind = node.get(lower.get("resourcetype", ""), None) or node.get(lower.get("type", ""), "")
                name = node.get(lower.get("resourcename", ""), None) or node.get(lower.get("name", ""), "")
                rid = node.get(lower.get("resourceid", ""), "") or node.get(lower.get("id", ""), "")
                if (not name) and rid:
                    name = str(rid).rstrip("/").split("/")[-1]
                found.append({"change_type": str(ct), "kind": str(kind) or "(unknown)",
                              "name": str(name) or "(unknown)", "props": []})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def render(preview_json: Path, preview_err: Path | None) -> str:
    out: list[str] = ["## 🔭 Infrastructure what-if (`azd provision --preview`)", ""]

    raw = preview_json.read_text(encoding="utf-8", errors="replace") if preview_json.exists() else ""
    if not raw.strip() and preview_err and preview_err.exists():
        raw = preview_err.read_text(encoding="utf-8", errors="replace")

    changes = _parse_json(raw) or _parse_text(raw)

    if not changes:
        if raw.strip():
            out.append("> Could not parse a structured preview. Raw output below.")
        else:
            out.append("_No preview output was captured._")
        out.append("")
        out.extend(_raw_block("azd output", raw))
        return "\n".join(out) + "\n"

    # Optional context line: resource group from a "Resource group" row.
    rg = next((c["name"] for c in changes if c["kind"].lower() == "resource group"), None)
    if rg:
        out.append(f"Resource group: `{rg}`")
        out.append("")

    # Group by display label.
    groups: dict[str, dict] = {}
    for c in changes:
        emoji, label, order = _meta(c["change_type"])
        g = groups.setdefault(label, {"emoji": emoji, "order": order, "rows": []})
        g["rows"].append(c)

    counts = " · ".join(
        f"{g['emoji']} {label}: **{len(g['rows'])}**"
        for label, g in sorted(groups.items(), key=lambda kv: kv[1]["order"])
    )
    out.append(counts)
    out.append("")

    for label, g in sorted(groups.items(), key=lambda kv: kv[1]["order"]):
        collapse = label == "No change"
        if collapse:
            out.append(f"<details><summary>{g['emoji']} No change ({len(g['rows'])})</summary>")
            out.append("")
        out.append(f"### {g['emoji']} {label} ({len(g['rows'])})")
        out.append("")
        out.append("| Resource | Name | Property changes |")
        out.append("| --- | --- | --- |")
        for r in sorted(g["rows"], key=lambda x: (x["kind"], x["name"])):
            out.append(f"| {_cell(r['kind'])} | `{_cell(r['name'])}` | {_prop_summary(r['props'])} |")
        out.append("")
        if collapse:
            out.append("</details>")
            out.append("")

    out.extend(_raw_block("Full azd preview output", raw))
    return "\n".join(out) + "\n"


def _raw_block(title: str, text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [f"<details><summary>{title}</summary>", "", "```", text[:60000], "```", "</details>", ""]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: render_whatif_summary.py <preview.json> [preview.err]", file=sys.stderr)
        return 2
    preview_json = Path(argv[1])
    preview_err = Path(argv[2]) if len(argv) > 2 else None
    sys.stdout.write(render(preview_json, preview_err))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
