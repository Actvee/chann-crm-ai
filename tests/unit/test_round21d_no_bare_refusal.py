"""Round 21D — no handler answers "you lack permission" without asking the
plan first (spec §5.3). Without this a Starter owner typing a service
trigger is told to ask the owner — themselves — for a permission."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = (ROOT / "application/chann_app/services/chat.py").read_text(encoding="utf-8")


def test_no_bare_no_permission_reply_is_left():
    assert "ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD" not in CHAT


def test_the_lead_is_used_only_by_its_definition_and_no_permission():
    uses = [m.start() for m in re.finditer(r"\bSUGGEST_NO_PERMISSION_LEAD\b", CHAT)]
    start = CHAT.index("def _no_permission(")
    end = start + 1 + re.search(r"\n\S", CHAT[start + 1:]).start()   # the first unindented line after it
    inside = CHAT[start:end].count("SUGGEST_NO_PERMISSION_LEAD")
    assert inside == 1
    assert len(uses) == 2          # the definition + the one inside _no_permission
