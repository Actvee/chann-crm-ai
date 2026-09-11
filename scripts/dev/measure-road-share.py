#!/usr/bin/env python3
"""How many sentences does a keyword still decide, and how many reach the model?

The conversion to model-first (docs/MODEL_FIRST.md) only goes one way. A
document says so; this fails the build when it stops being true.

    python3 scripts/dev/measure-road-share.py            # measure, compare, exit 1 if worse
    python3 scripts/dev/measure-road-share.py --record   # write the baseline (state why in the commit)
    python3 scripts/dev/measure-road-share.py --diff     # name every sentence whose road changed

Every utterance of tests/unit/chat_corpus.py and scripts/agent-test/corpus/
is played through the real router with the model stubbed, and the number of
model calls is counted. A sentence that reaches the model was READ; one that
did not was decided by a keyword.

Why a baseline file and not a fixed target: some branches are meant to stay
deterministic forever — a button's payload, a typed record code, a dated
one-shot command the handler can finish in one turn. The number that matters
is the direction, not an absolute.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

BASELINE = ROOT / "tests" / "unit" / "road_share_baseline.json"

import httpx  # noqa: E402

from chann_app.config import settings  # noqa: E402

settings.openrouter_api_key = "test"
settings.openrouter_model = "test"

from chann_app.services import chat as C  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

import chat_corpus  # noqa: E402

SUGGEST = json.dumps({"action": "suggest", "entity": "", "fields": {}, "missing": []})
KEYS = {
    "customer": [],
    "sales": sorted(DEFAULT_ROLE_TEMPLATES["admin"]),
    "technician": sorted(DEFAULT_ROLE_TEMPLATES["technician"]),
}
CUSTOMER = {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
            "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}
# deepcopy, not dict(): DEAL carries a nested products list, so a shallow
# copy handed every utterance the SAME list. One case adding or removing a
# line changed the fixture every later case measured against, and the
# number came out 2 too high (210 against a true 208) — the instrument
# that gates every deploy, measuring its own leftovers (11 ก.ย. 2569).
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


class _Counting(httpx.AsyncBaseTransport):
    def __init__(self, inner):
        self.inner = inner
        self.n = 0

    async def handle_async_request(self, request):
        self.n += 1
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


async def _play(oa: str, messages: list[str]) -> int:
    client = FakeDataClient(role=oa, permission_keys=list(KEYS[oa]),
                            customers=[copy.deepcopy(CUSTOMER)], deals=[copy.deepcopy(DEAL)],
                            quotes=[copy.deepcopy(QUOTE)])
    client._tickets = [copy.deepcopy(TICKET)]
    calls = 0
    for message in messages:
        transport = _Counting(_ai(SUGGEST))
        try:
            await C.handle_chat_message(
                client, ctx=_ctx(primary_role=oa, oa=oa), message=message,
                language="th", ai_client=httpx.AsyncClient(transport=transport),
            )
        except Exception:  # noqa: BLE001 — a handler that raises still decided the road
            pass
        calls += transport.n
    return calls


async def _measure() -> dict:
    per_oa: dict[str, dict[str, int]] = {}
    roads: dict[str, bool] = {}
    for oa, label, messages in _cases():
        read = await _play(oa, messages) > 0
        bucket = per_oa.setdefault(oa, {"read": 0, "rule": 0})
        bucket["read" if read else "rule"] += 1
        roads[f"{oa}\t{label}"] = read
    total_read = sum(b["read"] for b in per_oa.values())
    total = sum(b["read"] + b["rule"] for b in per_oa.values())
    return {"per_oa": per_oa, "reached_model": total_read, "cases": total, "roads": roads}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="overwrite the baseline")
    parser.add_argument("--diff", action="store_true", help="name every sentence whose road changed")
    args = parser.parse_args()

    now = asyncio.run(_measure())
    share = 100 * now["reached_model"] / max(now["cases"], 1)
    for oa in sorted(now["per_oa"]):
        b = now["per_oa"][oa]
        total = b["read"] + b["rule"]
        print(f"  {oa:<11} model {b['read']:>4} · rule {b['rule']:>4}  ({100 * b['read'] / max(total, 1):.0f}% read)")
    print(f"\n=== {now['reached_model']}/{now['cases']} reached the model ({share:.0f}%) ===")

    if args.record:
        BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"baseline written to {BASELINE.relative_to(ROOT)}")
        return 0

    if not BASELINE.exists():
        print(f"no baseline yet — run with --record ({BASELINE.relative_to(ROOT)})")
        return 0

    was = json.loads(BASELINE.read_text(encoding="utf-8"))
    if args.diff:
        old_roads, new_roads = was.get("roads", {}), now["roads"]
        moved = [(k, old_roads.get(k), v) for k, v in new_roads.items() if k in old_roads and old_roads[k] != v]
        print(f"\n{len(moved)} sentences changed road:")
        for key, before, after in moved:
            oa, label = key.split("\t", 1)
            arrow = "rule -> MODEL" if after else "MODEL -> rule"
            print(f"   [{oa}] {arrow}  {label[:70]}")

    before = was.get("reached_model", 0)
    if now["reached_model"] < before:
        print(f"\nHALT: the rule road grew — {before} sentences reached the model, now {now['reached_model']}.")
        print("docs/MODEL_FIRST.md: the conversion only goes one way. Run --diff to see which,")
        print("and if a sentence genuinely belongs to a keyword, say why in the commit and --record.")
        return 1
    if now["reached_model"] > before:
        print(f"\n{now['reached_model'] - before} more sentences reach the model than the baseline."
              f" Run --record once the change is reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
