#!/usr/bin/env python3
"""Drift guard: the Fabric IQ mirrored semantic-model schema must match the API projection.

`provision_fabric_iq.MIRRORED_TABLE_SCHEMA` declares the typed columns the Direct Lake model
exposes over the mirrored operational core. Those columns are only real if the API actually
writes them, which it does via `PostgresWaypointRepository._PROJECTED_COLUMNS` (typed projection)
plus the plain join keys / id declared in `_SCHEMA_SQL`. This test fails if the two drift apart,
so a change to the API projection can't silently leave the semantic model pointing at columns that
never replicate (or omit ones that do).

Run: `python3 infra/scripts/test_fabric_iq_schema.py` (exit 0 = pass). No third-party deps.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

# PostgreSQL type (from repository projection) -> expected TMSL dataType in the semantic model.
_KIND_TO_TMSL = {"text": "string", "numeric": "decimal", "date": "dateTime"}


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_projected_columns() -> dict:
    """Import only `_PROJECTED_COLUMNS` from the API repository without importing psycopg etc.

    The repository module imports heavy deps at import time, so parse the literal instead.
    """
    repo = REPO_ROOT / "api" / "app" / "common" / "repository.py"
    src = repo.read_text(encoding="utf-8")
    import ast

    tree = ast.parse(src)
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0], node.value
        if target is not None and target.id == "_PROJECTED_COLUMNS" and value is not None:
            return ast.literal_eval(value)
    raise AssertionError("_PROJECTED_COLUMNS not found in repository.py")


def main() -> int:
    iq = _load_module("provision_fabric_iq", HERE / "provision_fabric_iq.py")
    declared = iq.MIRRORED_TABLE_SCHEMA
    projected = _load_projected_columns()

    errors: list[str] = []

    # Same set of mirrored tables on both sides.
    if set(declared) != set(projected):
        errors.append(
            f"table set mismatch: semantic model {sorted(declared)} vs projection {sorted(projected)}"
        )

    for table, proj_cols in projected.items():
        model_cols = {name: dtype for name, dtype in declared.get(table, [])}
        # id is the primary key (always mirrors) and must be modelled as a string key.
        if model_cols.get("id") != "string":
            errors.append(f"{table}: expected an 'id' string column in the semantic model")
        for col, kind in proj_cols:
            expected = _KIND_TO_TMSL.get(kind, "string")
            if col not in model_cols:
                errors.append(f"{table}.{col}: in API projection but missing from semantic model")
            elif model_cols[col] != expected:
                errors.append(
                    f"{table}.{col}: projection kind '{kind}' -> expected TMSL '{expected}', "
                    f"model has '{model_cols[col]}'"
                )
        # Every non-key/non-metadata model column must be backed by the API projection.
        allowed = {name for name, _ in proj_cols} | {"id", "updated_at"}
        for col in model_cols:
            if col not in allowed:
                errors.append(f"{table}.{col}: modelled but not written by the API projection")

    if errors:
        print("FAIL: Fabric IQ mirrored schema drifted from the API projection:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK: {len(declared)} mirrored tables match the API projection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
