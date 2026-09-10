"""Path bootstrap for the unit suite, so any one file runs on its own.

`docs/` and `CLAUDE.md` tell people to run a single file — e.g.
`python -m pytest tests/unit/test_agent_test_channel.py -q` — and that
command has to work by itself. It did not: every test module put the
tiers on `sys.path` in its own header, so a file whose first import of
`chann_app` happens inside a test function (rather than at module import
time, after its own header ran) only resolved because SOME OTHER file
collected earlier in a full run had already done the inserts. Running
that file alone raised `ModuleNotFoundError: No module named 'chann_app'`
(review v3, T05).

A conftest in this directory is imported by pytest before any test module
under it, in a single-file run exactly as in a full one, so the paths are
there either way. The per-module inserts are left alone: they are
harmless, and removing ~100 of them would be a much larger diff than the
bug deserves.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Same three entries, in the same order, that the test modules insert for
# themselves: the Application tier, the Data tier, and this directory (the
# unit tests import each other's fakes, e.g. `from test_phase6_chat import
# FakeDataClient`).
for path in (ROOT / "application", ROOT / "data", Path(__file__).resolve().parent):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
