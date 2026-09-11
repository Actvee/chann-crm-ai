#!/usr/bin/env python3
"""When a keyword decides a sentence, WHICH keyword was it?

measure-road-share.py answers "how many sentences never reached the model".
It cannot answer the next question, which is the one the conversion needs:
of the 391 sales sentences a rule decides, how many does each rule decide?
Narrowing them in the wrong order is how the last attempt broke the
technician's own reported flow while moving four utterances.

Every corpus sentence is played through the real router with the model
stubbed. Each chat.py coroutine is wrapped so the call order is recorded,
and the FIRST one entered is the branch the router dispatched to — which
is the thing a narrowing edit would have to change. (The first cut took
the LAST one instead and named formatters and lookup helpers: _record_lines
owned 25 sentences and decides nothing. --deepest keeps that view, which is
useful for finding where a reply is actually built.) Sentences that reached
the model are excluded — they already belong to the model road.

    python3 scripts/dev/map-rule-roads.py                 # counts per branch
    python3 scripts/dev/map-rule-roads.py --oa sales      # one channel
    python3 scripts/dev/map-rule-roads.py --branch _handle_customer_detail
                                                          # the sentences it owns

The counts are a plan, not a verdict: a branch that owns many sentences may
be one that SHOULD stay deterministic (a button payload, a typed record
code). docs/MODEL_FIRST.md's five steps still apply to every one of them,
starting with asking the deployed model what it makes of the sentence.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import httpx  # noqa: E402

from chann_app.config import settings  # noqa: E402

settings.openrouter_api_key = "test"
settings.openrouter_model = "test"

from chann_app.services import chat as C  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

import chat_corpus  # noqa: E402

SUGGEST = json.dumps({"action": "suggest", "entity": "", "fields": {}, "missing": []})
KEYS = {"customer": [], "sales": sorted(DEFAULT_ROLE_TEMPLATES["admin"]),
        "technician": sorted(DEFAULT_ROLE_TEMPLATES["technician"])}
CUSTOMER = {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
            "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}
DEAL = {"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed", "contact_id": "CUST-1",
        "notes": None, "products": [{"id": "L1", "product_name": "พัดลม",
                                     "quoted_unit_price": "1200", "qty": 1}]}
QUOTE = {"id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "sent", "deal_id": "DEAL-1",
         "contact_id": "CUST-1", "items": [], "total": "1000.00"}
TICKET = {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
          "accept_status": "accepted", "assigned_to_ref": "member-1",
          "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย",
          "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
          "scheduled_date": "2026-09-11", "scheduled_time": "10:00"}

#: Entering these says nothing about which branch decided — they are the
#: road every sentence walks, or a helper every branch calls.
PLUMBING = {
    "handle_chat_message", "_route_chat_message", "_execute_intent",
    "_remember_turn", "_recent_turns", "_remember_customer", "_remember_entity",
    "_last_customer_ref", "_last_entity_ref", "_customer_still_there",
    "_guarded_in_context", "_intent_guard_reply", "_member_id_of",
}


class _Trace:
    """Which chat.py coroutine actually ANSWERED.

    Entry order alone names the wrong thing twice over. The last coroutine
    entered is a formatter (_record_lines "owned" 25 sentences and decides
    nothing); the first is whichever `_maybe_*` probe the router happens to
    try first, which returns None for nearly every sentence
    (_maybe_lead_cleanup_setting "owned" 346 of 395). What identifies the
    branch is returning a ChatReply — and among those, the OUTERMOST one,
    since a dispatch branch calls sub-handlers that also return replies.
    """

    def __init__(self) -> None:
        self.depth = 0
        self.answered: list[tuple[int, int, str]] = []   # (depth, order, name)
        self.order = 0
        self.model_calls = 0

    def wrap(self) -> dict:
        original = {}
        for name, value in list(vars(C).items()):
            if not inspect.iscoroutinefunction(value) or name in PLUMBING:
                continue
            original[name] = value
            self._install(name, value)
        return original

    def _install(self, name: str, func) -> None:
        trace = self

        async def recorded(*args, **kwargs):
            trace.depth += 1
            trace.order += 1
            here = trace.depth
            mine = trace.order
            try:
                out = await func(*args, **kwargs)
            finally:
                trace.depth -= 1
            if isinstance(out, C.ChatReply):
                trace.answered.append((here, mine, name))
            return out

        recorded.__name__ = name
        setattr(C, name, recorded)

    @staticmethod
    def restore(original: dict) -> None:
        for name, value in original.items():
            setattr(C, name, value)

    def decider(self, deepest: bool) -> str:
        if not self.answered:
            return "(the router itself)"
        if deepest:
            return max(self.answered)[2]
        # outermost, and the last one at that depth — a branch that tried a
        # sub-handler first and then answered itself is still the branch.
        shallowest = min(d for d, _o, _n in self.answered)
        return max((o, n) for d, o, n in self.answered if d == shallowest)[1]


class _Counting(httpx.AsyncBaseTransport):
    def __init__(self, inner, trace: _Trace):
        self.inner, self.trace = inner, trace

    async def handle_async_request(self, request):
        self.trace.model_calls += 1
        return await self.inner.handle_async_request(request)


def _cases() -> list[tuple[str, str, list[str]]]:
    out: list[tuple[str, str, list[str]]] = []
    for oa, cases in (("customer", chat_corpus.CUSTOMER), ("sales", chat_corpus.SALES),
                      ("technician", chat_corpus.TECH)):
        for case in cases:
            out.append((oa, case["text"], list(case.get("pre") or []) + [case["text"]]))
    for name in ("corpus-sales", "corpus-tech", "corpus-customer"):
        path = ROOT / "scripts" / "agent-test" / "corpus" / f"{name}.json"
        for case in json.loads(path.read_text(encoding="utf-8")):
            raw = case.get("messages") or []
            msgs = [m if isinstance(m, str) else str(m.get("text") or "") for m in raw]
            if msgs:
                out.append((case.get("oa") or "sales", case["id"], msgs))
    return out


async def _play(oa: str, messages: list[str], deepest: bool) -> tuple[str, bool]:
    """(the branch that decided the LAST message, did it reach the model)."""
    client = FakeDataClient(role=oa, permission_keys=list(KEYS[oa]),
                            customers=[copy.deepcopy(CUSTOMER)], deals=[copy.deepcopy(DEAL)],
                            quotes=[copy.deepcopy(QUOTE)])
    client._tickets = [copy.deepcopy(TICKET)]
    branch, reached = "(none)", False
    for message in messages:
        trace = _Trace()
        original = trace.wrap()
        try:
            await C.handle_chat_message(
                client, ctx=_ctx(primary_role=oa, oa=oa), message=message, language="th",
                ai_client=httpx.AsyncClient(transport=_Counting(_ai(SUGGEST), trace)),
            )
        except Exception:  # noqa: BLE001 — a handler that raises still decided
            pass
        finally:
            _Trace.restore(original)
        branch = trace.decider(deepest)
        reached = trace.model_calls > 0
    return branch, reached


async def _measure(only_oa: str | None, deepest: bool) -> dict[str, list[tuple[str, str]]]:
    owned: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for oa, label, messages in _cases():
        if only_oa and oa != only_oa:
            continue
        branch, reached = await _play(oa, messages, deepest)
        if reached:
            continue
        owned[branch].append((oa, label))
    return owned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oa", choices=("sales", "technician", "customer"))
    parser.add_argument("--branch", help="list the sentences this branch decides")
    parser.add_argument("--deepest", action="store_true",
                        help="name the innermost answerer instead of the outermost")
    args = parser.parse_args()

    owned = asyncio.run(_measure(args.oa, args.deepest))
    if args.branch:
        for oa, label in owned.get(args.branch, []):
            print(f"  [{oa}] {label}")
        print(f"\n{len(owned.get(args.branch, []))} sentences decided by {args.branch}")
        return 0

    counts = Counter({branch: len(rows) for branch, rows in owned.items()})
    total = sum(counts.values())
    for branch, n in counts.most_common():
        channels = Counter(oa for oa, _ in owned[branch])
        spread = " ".join(f"{oa}:{c}" for oa, c in channels.most_common())
        print(f"  {n:>4}  {branch:<42} {spread}")
    print(f"\n=== {total} sentences decided without the model, across {len(counts)} branches ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
