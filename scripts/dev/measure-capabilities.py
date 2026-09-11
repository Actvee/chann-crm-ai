#!/usr/bin/env python3
"""What can this system actually DO, and does the model know about it?

Owner, 11 ก.ย. 2569: "concept ง่ายๆ แค่ระบบเรามีความสามารถหรือ api ทำอะไร
ได้บ้าง พอผู้ใช้พิมมา AI ก็อ่านแล้วก็เทียบดูว่าควรทำอันไหน" — for the model
to compare a sentence against what the system can do, something has to hold
the list of what the system can do. Three lists exist today and none of them
is that:

  * ACTION_PERMISSIONS  — (action, entity) -> permission key. Says what is
    REGISTERED, not what a handler will carry out.
  * INTENT_SYSTEM_PROMPT — hand-written prose. Says what the model is TOLD,
    and drifts from the first list every time either changes.
  * the handlers themselves — the only thing that says what actually happens.

This script asks the third one. Every registered capability is played through
the real router with the model stubbed to propose exactly that action, and
the reply is read back:

    executes   the handler did the thing (or asked for a field it needs)
    declines   a real refusal — wrong OA, missing permission, guard
    NOT YET    "ยังทำรายการนี้ไม่ได้" — registered, no handler branch
    (raises)   a crash

    python3 scripts/dev/measure-capabilities.py            # the table
    python3 scripts/dev/measure-capabilities.py --gaps     # only what is wrong
    python3 scripts/dev/measure-capabilities.py --json     # for the registry

The intent guard is stubbed OUT deliberately: this measures whether a road
EXISTS, not whether a particular sentence takes it. Guard coverage is
tests/unit/test_unguarded_writes.py's job, and the two must not be confused —
a capability that "executes" here may still, correctly, refuse a real
sentence that only asks about it.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
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

CUSTOMER = {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
            "last_name": "ใจดี", "phone": "0812345678", "stage": "lead", "notes": None}
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

#: The code each entity answers to, so a probe names a record that exists
#: rather than tripping the entity/code mismatch check on its way in.
CODE_FOR = {
    "customer": "C-2026-0001", "deal": "D-2026-0001", "quote": "Q-2026-0001",
    "ticket": "T-2026-0001", "service_report": "SR-2026-0001",
}
OA_FOR = {"technician": "technician", "customer": "customer"}


#: What INTENT_SYSTEM_PROMPT tells the model to send for each entity. Probing
#: with anything else measures the probe, not the system: the first run
#: reported team create/update/delete as having no handler at all, because it
#: sent target_name where the road reads team_name (11 ก.ย. 2569).
PROMPT_FIELDS: dict[str, dict] = {
    "customer": {"target_name": "สมชาย", "first_name": "สมชาย", "last_name": "ใจดี",
                 "phone": "0898887777"},
    "deal": {"target_name": "สมชาย", "amount": 500000, "currency": "THB"},
    "product": {"product_id": "FAN001", "product_name": "พัดลมไอเย็น", "unit_price": 1200},
    "line_item": {"product_name": "พัดลม", "qty": 2, "unit_price": 1200},
    "quote": {"deal_code": "D-2026-0001"},
    "ticket": {"target_name": "สมชาย", "issue_description": "แอร์ไม่เย็น",
               "service_address": "99/1", "scheduled_date": "2026-09-12",
               "scheduled_time": "10:00"},
    "service_report": {"found_issue": "คอมเพรสเซอร์เสีย", "work_done": "เปลี่ยนแล้ว"},
    "approval": {"reason": "รูปไม่ครบ"},
    "followup": {"target_name": "สมชาย", "due_date": "2026-09-12", "due_time": "10:00"},
    "warranty": {"serial_number": "SN12345678", "product_name": "พัดลมไอเย็น",
                 "target_name": "สมชาย"},
    "team": {"team_name": "แอร์", "scope": "technician", "members": "สมศักดิ์"},
    "sales_group": {"team_name": "ทีมขาย", "scope": "sales", "members": "สมศักดิ์"},
    "setting": {"legal_name": "ร้านเย็นสบาย จำกัด", "tax_id": "0105558012345"},
    "report": {"period": "month"},
    "note": {"body": "ลูกค้าขอส่วนลด 10%", "entity_code": "C-2026-0001"},
    "member": {"target_name": "สมชาย"},
    "role": {"role_name": "ช่างอาวุโส"},
    "audit_log": {},
    "profile": {"phone": "0899998888"},
}


def _fields(entity: str) -> dict:
    out = dict(PROMPT_FIELDS.get(entity) or {"target_name": "สมชาย"})
    code = CODE_FOR.get(entity)
    if code:
        out.setdefault("code", code)
        out.setdefault(f"{entity}_code", code)
    return out


async def _probe(action: str, entity: str, oa: str, keys: list[str]) -> str:
    client = FakeDataClient(role=oa, permission_keys=list(keys),
                            customers=[copy.deepcopy(CUSTOMER)], deals=[copy.deepcopy(DEAL)],
                            quotes=[copy.deepcopy(QUOTE)])
    client._tickets = [copy.deepcopy(TICKET)]
    intent = {"action": action, "entity": entity, "fields": _fields(entity), "missing": []}
    transport = _ai(json.dumps(intent, ensure_ascii=False))
    try:
        reply = await C.handle_chat_message(
            client, ctx=_ctx(primary_role=oa, oa=oa), message="ทำรายการนี้ให้หน่อย",
            language="th", ai_client=httpx.AsyncClient(transport=transport),
        )
    except Exception as exc:  # noqa: BLE001
        return f"raises {type(exc).__name__}"
    text = (reply.text or "").strip()
    if text == C._no_handler_reply(intent, "th", oa).text.strip():
        return "NOT YET"
    wrote = [r[0] for r in client.recorded
             if r[0].startswith(("create_", "update_", "delete_", "promote_", "archive_",
                                 "transition_", "set_quote", "check_in", "check_out"))]
    return f"executes ({wrote[0]})" if wrote else "answers"


async def _measure() -> list[dict]:
    guard = C._intent_guard_reply
    C._intent_guard_reply = lambda *a, **k: None  # noqa: ARG005 — see the docstring
    try:
        rows = []
        for (action, entity), key in sorted(C.ACTION_PERMISSIONS.items(), key=lambda p: p[0][::-1]):
            for oa in ("sales", "technician", "customer"):
                if not C._oa_allows(oa, key):
                    continue
                role = OA_FOR.get(oa, "admin")
                keys = sorted(DEFAULT_ROLE_TEMPLATES.get(role, DEFAULT_ROLE_TEMPLATES["admin"]))
                if oa == "customer":
                    keys = []          # a customer holds none, by construction
                elif key not in keys:
                    continue
                rows.append({"entity": entity, "action": action, "key": key, "oa": oa,
                             "outcome": await _probe(action, entity, oa, keys)})
        return rows
    finally:
        C._intent_guard_reply = guard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gaps", action="store_true", help="only capabilities with no road")
    parser.add_argument("--json", action="store_true", help="machine-readable, for the registry")
    args = parser.parse_args()

    rows = asyncio.run(_measure())
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    shown = [r for r in rows if not args.gaps or r["outcome"].startswith(("NOT YET", "raises"))]
    for row in shown:
        print(f"  {row['oa']:<11} {row['entity']:<15} {row['action']:<10} "
              f"{row['key']:<22} {row['outcome']}")
    gaps = sum(1 for r in rows if r["outcome"].startswith("NOT YET"))
    crash = sum(1 for r in rows if r["outcome"].startswith("raises"))
    print(f"\n=== {len(rows)} capability/channel pairs · {gaps} with no handler · {crash} raising ===")
    return 1 if crash else 0


if __name__ == "__main__":
    raise SystemExit(main())
