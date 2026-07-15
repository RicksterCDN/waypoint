"""Durable finalize outbox for the Assurance Orchestrator run harness (F3).

The run harness hands each invoice's fused evidence to the waypoint-recorder as the final,
side-effecting step of a run. That recorder call can fail transiently (a cold-start 503, a
gateway blip) even after the client's own bounded retry. Without a durable record, such a
blip leaves the run orphaned at ``running`` — exactly the failure this convergence closes.

This module persists a small per-invoice ``finalize_pending`` marker BEFORE the recorder
call and flips it to ``finalize_done`` on success. A still-``pending`` marker is retried by
``drain`` on the next run start (and by a bounded in-process retry), so a transient failure
retries instead of orphaning.

Storage reuses the run-journal directory (``ASSURANCE_ORCHESTRATOR_RUN_JOURNAL_DIR``) unless
``ASSURANCE_ORCHESTRATOR_OUTBOX_DIR`` overrides it. When neither is set the outbox is
disabled (``from_env`` returns ``None``) and the harness performs the finalize without a
durable record — safe for local scout mode.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("assurance_orchestrator.finalize_outbox")

OUTBOX_DIR_ENV = "ASSURANCE_ORCHESTRATOR_OUTBOX_DIR"
JOURNAL_DIR_ENV = "ASSURANCE_ORCHESTRATOR_RUN_JOURNAL_DIR"
_SUBDIR = "finalize-outbox"
DEFAULT_MAX_ATTEMPTS = 5


def _usable(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _safe(token: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in token)[:64] or "invoice"


class FinalizeOutbox:
    """A tiny file-backed queue of pending recorder finalizes."""

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self.dir = Path(root_dir) / _SUBDIR
        self.dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "FinalizeOutbox | None":
        root = _usable(OUTBOX_DIR_ENV) or _usable(JOURNAL_DIR_ENV)
        if not root:
            return None
        try:
            return cls(root)
        except OSError:
            logger.warning("could not create finalize outbox under %s", root, exc_info=True)
            return None

    def enqueue(self, invoice_id: str, bundle_json: str, *, operation_id: str | None = None) -> str:
        entry_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{_safe(invoice_id)}-{uuid.uuid4().hex[:8]}"
        entry = {
            "entry_id": entry_id,
            "invoice_id": invoice_id,
            "operation_id": operation_id,
            "bundle_json": bundle_json,
            "status": "pending",
            "attempts": 0,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "last_error": None,
        }
        self._write(entry_id, entry)
        return entry_id

    def mark_done(self, entry_id: str) -> None:
        entry = self._read(entry_id)
        if entry is None:
            return
        entry["status"] = "done"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["updated_at"] = datetime.now(UTC).isoformat()
        entry["last_error"] = None
        self._write(entry_id, entry)

    def mark_failed(self, entry_id: str, error: str) -> None:
        entry = self._read(entry_id)
        if entry is None:
            return
        entry["status"] = "pending"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["updated_at"] = datetime.now(UTC).isoformat()
        entry["last_error"] = str(error)[:800]
        self._write(entry_id, entry)

    def pending(self, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(self.dir.glob("*.json")):
            entry = self._read_path(path)
            if entry is None:
                continue
            if entry.get("status") == "pending" and int(entry.get("attempts", 0)) < max_attempts:
                entries.append(entry)
        return entries

    def drain(
        self,
        deliver: Callable[[str], bool],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        """Retry every pending entry. ``deliver`` returns True on a successful recorder write."""
        delivered: list[str] = []
        still_pending: list[str] = []
        for entry in self.pending(max_attempts=max_attempts):
            entry_id = entry["entry_id"]
            try:
                ok = bool(deliver(entry["bundle_json"]))
            except Exception as exc:  # a delivery attempt should never crash the drain
                self.mark_failed(entry_id, f"{type(exc).__name__}: {exc}")
                still_pending.append(entry_id)
                continue
            if ok:
                self.mark_done(entry_id)
                delivered.append(entry_id)
            else:
                self.mark_failed(entry_id, "recorder finalize returned not-ok")
                still_pending.append(entry_id)
        return {"delivered": delivered, "still_pending": still_pending}

    # ── internals ────────────────────────────────────────────────────────────
    def _path(self, entry_id: str) -> Path:
        return self.dir / f"{entry_id}.json"

    def _write(self, entry_id: str, entry: dict[str, Any]) -> None:
        path = self._path(entry_id)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, entry_id: str) -> dict[str, Any] | None:
        return self._read_path(self._path(entry_id))

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
