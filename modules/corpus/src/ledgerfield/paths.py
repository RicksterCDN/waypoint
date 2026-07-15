"""Repository path and JSON loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    """Return the repository root for an editable checkout or installed package."""

    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    """Read JSON from a UTF-8 file."""

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def data_path(root: Path | None = None) -> Path:
    """Return the data folder path."""

    return (root or repo_root()) / "data"
