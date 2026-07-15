from __future__ import annotations

import os
from typing import Any

from .paths import repo_root


def run_doctor() -> dict[str, Any]:
    root = repo_root()
    gitignore = root / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""

    checks = [
        _check("pyproject", (root / "pyproject.toml").exists(), "pyproject.toml exists"),
        _check("package", (root / "src" / "caliber").is_dir(), "src/caliber package exists"),
        _check("datasets", (root / "datasets").is_dir(), "datasets directory exists"),
        _check("graders", (root / "graders").is_dir(), "graders directory exists"),
        _check("runs_ignored", "runs/" in ignored, "runs/ is ignored"),
        _check("outputs_ignored", "outputs/" in ignored, "outputs/ is ignored"),
        _check("env_example", (root / ".env.example").exists(), ".env.example exists"),
    ]

    env = {
        "FOUNDRY_PROJECT_ENDPOINT": bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT")),
        "AZURE_AI_PROJECT_ENDPOINT": bool(os.environ.get("AZURE_AI_PROJECT_ENDPOINT")),
    }
    status = "pass" if all(item["ok"] for item in checks) else "warning"
    return {"status": status, "checks": checks, "environment": env}


def _check(name: str, ok: bool, message: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message}
