#!/usr/bin/env python3
"""Verify every SQLAlchemy model constructor call uses real columns.

Exists because `LicenseMember(display_name=...)` shipped in a patch that had
passed lint, syntax checks, and 148 unit tests. The column simply does not
exist, and nothing caught it until integration tests ran against a real
Postgres — which is not always available.

This closes that gap statically: it walks every `Model(...)` call in the
source tree and checks each keyword against the model's mapped attributes.
Fast enough to run on every change, and needs no database.

Usage:
    python3 scripts/check-model-kwargs.py          # from the repo root
Exit code 1 if any call uses an attribute the model does not have.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")

from chann_data import models as models_module  # noqa: E402
from chann_data.db import Base  # noqa: E402

# name -> set of assignable attribute names
MODELS: dict[str, set[str]] = {}
for name in dir(models_module):
    obj = getattr(models_module, name)
    if not isinstance(obj, type) or not issubclass(obj, Base) or obj is Base:
        continue
    if not hasattr(obj, "__table__"):
        continue
    attrs = {c.name for c in obj.__table__.columns}
    # relationships are assignable in a constructor too
    try:
        from sqlalchemy import inspect as sa_inspect

        attrs |= set(sa_inspect(obj).relationships.keys())
    except Exception:  # noqa: BLE001
        pass
    MODELS[name] = attrs


def check_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        model_name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if model_name not in MODELS:
            continue
        allowed = MODELS[model_name]
        for kw in node.keywords:
            if kw.arg is None:      # **kwargs — cannot check statically
                continue
            if kw.arg not in allowed:
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"{model_name}({kw.arg}=...) — no such column. "
                    f"Has: {', '.join(sorted(allowed))}"
                )
    return problems


def main() -> int:
    targets: list[Path] = []
    for sub in ("data", "application", "database", "tests"):
        targets.extend((ROOT / sub).rglob("*.py"))

    problems: list[str] = []
    for path in sorted(targets):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        problems.extend(check_file(path))

    if problems:
        print(f"FAIL — {len(problems)} bad model constructor argument(s):\n")
        for p in problems:
            print("  " + p)
        return 1

    print(f"OK — checked {len(MODELS)} models across {len(targets)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
