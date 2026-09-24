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


import pytest  # noqa: E402


@pytest.fixture
def line_accepts(monkeypatch):
    """LINE that takes every push, recorded (round 21E).

    Sending a document to the customer no longer calls a failed push a
    send, and the unit suite has no channel token — so a test that proves
    a document WAS handed over must say that LINE took it. Opt-in, not
    autouse: every other notification still meets the real (unconfigured)
    client and keeps its swallow-and-log behaviour.

    Tests that take it assert on what LINE RECEIVED (`line_got`), never on
    notification rows: a row is not a delivery (21E review, Important 4).
    `CHANN_TEST_LINE_REFUSES=1` turns the stand-in into a LINE that refuses
    every push — the proof that each such test would go red if nothing was
    delivered (`CHANN_TEST_LINE_REFUSES=1 pytest … -k send` must fail).
    """
    import os

    from chann_app.line.client import LineReplyError
    from chann_app.services import notify

    pushed: list[tuple] = []
    refuses = os.environ.get("CHANN_TEST_LINE_REFUSES") == "1"

    async def push_text(oa, to_line_user_id, text, client=None, quick_reply=None):
        if refuses:
            raise LineReplyError("LINE push failed: 500 (test stand-in refuses)")
        pushed.append((oa, to_line_user_id, text))
        return [f"msg-{len(pushed)}"]

    async def push_messages(oa, to_line_user_id, messages, client=None):
        if refuses:
            raise LineReplyError("LINE push failed: 500 (test stand-in refuses)")
        pushed.append((oa, to_line_user_id, messages))
        return [f"msg-{len(pushed)}"]

    monkeypatch.setattr(notify, "push_text", push_text)
    monkeypatch.setattr(notify, "push_messages", push_messages)
    return pushed


def line_got(pushed: list[tuple], to: str, *needles: str) -> str:
    """Assert the stand-in LINE received a push to `to` carrying every
    needle; return its text."""
    assert pushed, "LINE received nothing"
    oa, target, body = pushed[-1]
    text = body if isinstance(body, str) else " ".join(str(m) for m in body)
    assert target == to, (target, to)
    for needle in needles:
        assert needle in text, (needle, text)
    return text
