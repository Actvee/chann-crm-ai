"""The brand a person can see must read "Chann1" everywhere, never bare
"Chann" (owner, 24 ก.ย. 2569: richmenu and the Dashboard still said
"Chann" in places). This scans the user-visible sources for a bare
"Chann" and fails the moment one turns up, so the next screen that
copies old wording does not slip back to the old name.

Matched with `\\bChann\\b` and a negative lookahead for a following "1":
the word-boundary already keeps this off identifiers, since none of
them put a boundary right after "Chann" —
  chann_uid        — lower-case c, boundary rule is case-sensitive
  ChannUid         — "Chann" runs straight into "Uid", both word chars,
                      so there is no boundary between them
  ownerChannUid    — same: "r" before "C" and "U" after "n" are both
                      word characters, no boundary either side
  chann-crm-ai     — lower-case c again
  chann-app/chann_app/chann_data — lower-case c
No separate allow-list is needed for those; they simply never match.

Python files get parsed with `ast` and only genuine string-literal
values are checked (this also catches f-string text) — comments are
not literals at all, and a module/class/function's own docstring is
excluded on purpose, because a few services still narrate their own
history in prose ("...the Chann admin screen counted the chat only...")
without ever putting that text on a screen. TS/TSX/JSON/Markdown files
have no such docstring concept, so those are scanned as plain text.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"\bChann\b(?!1)")

PY_GLOBS = [
    "application/chann_app/services/**/*.py",
    "scripts/richmenu/generate.py",
]
TEXT_GLOBS = [
    "presentation/lib/i18n/*.ts",
    "presentation/app/**/*.tsx",
    "application/chann_app/**/*.json",
    "docs/guides/*.md",
]


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """id() of every Constant node that is a module/class/function docstring."""
    ids: set[int] = set()

    def check_body(body: list[ast.stmt]) -> None:
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                ids.add(id(value))

    check_body(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            check_body(node.body)
    return ids


def scan_py(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return [f"{path}: could not parse — skipped"]
    docstrings = _docstring_nodes(tree)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if PATTERN.search(node.value):
                offenders.append(f"{path}:{node.lineno}: {node.value.strip()[:100]}")
    return offenders


def scan_text(path: Path) -> list[str]:
    offenders = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if PATTERN.search(line):
            offenders.append(f"{path}:{i}: {line.strip()[:120]}")
    return offenders


def main() -> int:
    offenders: list[str] = []
    for pattern in PY_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            offenders += scan_py(path)
    for pattern in TEXT_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            offenders += scan_text(path)

    if offenders:
        print(f"{len(offenders)} offender(s) — brand must read Chann1, not Chann:")
        for o in offenders:
            print("  " + o)
        return 1
    print("ok — no bare \"Chann\" in any user-visible source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
