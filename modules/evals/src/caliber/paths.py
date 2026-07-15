from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root()))
    except ValueError:
        return str(path)
