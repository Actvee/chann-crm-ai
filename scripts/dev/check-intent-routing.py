"""Every (action, entity) the AI can emit must reach a handler — or say so.

The bug this exists to stop (owner, 8 Sep 2026): "ขอดูข้อมูลคุณสมหมาย" was
parsed correctly, passed the permission gate, and then fell off the end of
_handle_customer_intent into "เข้าใจแล้วครับ ... แต่ในแชทยังทำรายการนี้ไม่ได้"
— for a read the typed "ดูลูกค้า สมชาย" had done since Phase 9. Nothing
failed. The pair was registered, permitted, and unrouted, and only a person
typing that exact sentence could tell.

So the check is dynamic, not a grep: every pair in ACTION_PERMISSIONS is
pushed through the real _execute_intent with a seeded fake client, on each
OA that allows it, and the run is watched for the two ways a request ends
without work being done —

  * _pending_execution_reply — "not available in chat yet";
  * suggest_what_you_can_do  — "here is what you CAN do", which
    _handle_ai_understood_intent falls back to when it recognises the
    entity but not this shape of it.

A pair that hits either on every OA is UNROUTED. Pairs that genuinely have
no handler live in NO_HANDLER below with the reason and the dashboard page
that does the job, so the list stays at zero and a new hole is visible the
moment it appears.

Success prints one line. A hole exits non-zero.
"""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "application"))
sys.path.insert(0, os.path.join(ROOT, "data"))
sys.path.insert(0, os.path.join(ROOT, "tests", "unit"))

import test_phase6_chat as T                                       # noqa: E402
from chann_app.config import settings                              # noqa: E402
from chann_app.services import chat                                # noqa: E402

# The fakes have no document templates and no LINE token, so the handlers
# log about both while doing exactly what they should. Quietened so the
# script keeps its one-line contract.
logging.disable(logging.CRITICAL)

settings.openrouter_api_key = settings.openrouter_api_key or "k"
settings.openrouter_model = settings.openrouter_model or "m"

ME = "CHN-S-000001"

# Deliberately unrouted, with the reason and where the work is done instead.
# "No handler" is an honest answer; a silent fall-through is not.
NO_HANDLER = {
    ("archive", "deal"): "deals close won/lost by stage; archiving one lives on the deals page",
    ("update", "quote"): "a quote changes through its lines and its discount, each with its own command",
    ("update", "warranty"): "editing a registration is a dashboard job (warranties page); chat registers and reads",
    ("update", "approval"): "the approval flow is configured with \"ตั้งค่าการอนุมัติ\", not from a bare intent",
    ("delete", "product"): "the products page owns removal; chat adds and updates the catalogue",
    ("read", "audit_log"): "the audit trail is a dashboard view — a LINE bubble cannot page through it",
    ("read", "role"): "roles and permissions are managed on the roles page",
    ("create", "role"): "roles and permissions are managed on the roles page",
    ("update", "role"): "roles and permissions are managed on the roles page",
    ("update", "member"): "a member's role and status are changed on the members page",
}

# One representative field set per entity, so a handler that needs a code or
# a name finds one instead of stopping at "which record?".
FIELDS = {
    "customer": {"target_name": "สมชาย"},
    "deal": {"target_name": "สมชาย", "deal_code": "D-2026-0001"},
    "quote": {"deal_code": "D-2026-0001", "quote_code": "Q-2026-0001"},
    "line_item": {"target_name": "แอร์ 12000 BTU", "qty": 2},
    "product": {"product_id": "AC12", "product_name": "แอร์ 12000 BTU"},
    "ticket": {"code": "T-2026-0001", "target_name": "สมศักดิ์", "scheduled_date": "พรุ่งนี้"},
    "service_report": {"code": "SR-2026-0001", "found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"},
    "approval": {"code": "SR-2026-0001", "reason": "รูปไม่ครบ"},
    "followup": {"target_name": "สมชาย", "due_date": "พรุ่งนี้", "due_time": "10:00"},
    "note": {"body": "ลูกค้าขอส่วนลด", "entity_code": "C-2026-0001"},
    "warranty": {"serial_number": "SN12345678", "product_name": "แอร์", "target_name": "สมชาย"},
    "team": {"team_name": "ทีมแอร์", "members": ["สมศักดิ์"]},
    "sales_group": {"team_name": "ทีมขาย", "members": ["สมศักดิ์"]},
    "setting": {"legal_name": "ร้านชาญ", "tax_id": "0105558012345"},
    "member": {"target_name": "สมศักดิ์"},
    "role": {"name": "ช่าง"},
    "report": {"period": "month"},
    "audit_log": {},
}
# The sentence a person would have typed, so the handlers that read the
# message (line items, notes, a technician's situation) get the words they
# were built to read. Keyed by pair first, then by entity.
MESSAGES = {
    ("update", "line_item"): "แก้ แอร์ 12000 BTU เป็น 2 ตัว",
    ("create", "line_item"): "เพิ่ม แอร์ 12000 BTU 2 ตัว ราคา 15900",
    ("delete", "line_item"): "เอา แอร์ 12000 BTU ออก",
    ("update", "note"): "แก้บันทึกเป็น ลูกค้าขอส่วนลด 10%",
    ("delete", "note"): "ลบบันทึกล่าสุด",
    ("update", "ticket"): "เลื่อนไปพรุ่งนี้บ่าย",
    "note": "บันทึกว่า ลูกค้าขอส่วนลด",
    "ticket": "งาน T-2026-0001",
}


async def _seeded(oa):
    keys = sorted({key for key in chat.ACTION_PERMISSIONS.values()})
    role = {"technician": "technician"}.get(oa, "sales")
    client = T.FakeDataClient(permission_keys=keys, role=role)
    client._products = [{"id": "p1", "product_id": "AC12", "product_name": "แอร์ 12000 BTU", "unit_price": "15900.00"}]
    client._members = [
        {"id": "member-1", "chann_uid": ME, "role": role, "status": "active", "first_name": "พนักงาน"},
        {"id": "m-tech", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "first_name": "สมศักดิ์"},
    ]
    customer = await client.create_customer(
        "L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
    deal = await client.create_deal("L1", {"contact_id": customer["id"]})
    await client.add_deal_product(
        "L1", deal["id"], {"product_name": "แอร์ 12000 BTU", "qty": 1, "quoted_unit_price": "15900.00"})
    await client.create_quote("L1", {"deal_id": deal["id"]})
    client._tickets = [{
        "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
        "assigned_to_ref": "member-1", "owner_member_id": "member-1", "visibility": "public",
        "customer_name": "สมชาย", "customer_phone": "0812345678", "service_address": "99/1",
        "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08", "scheduled_time": "10:00"}]
    client._reports = [{
        "id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
        "technician_member_id": "member-1", "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"}}]
    await client.open_approval_steps(T.LICENSE_ID, "sr-1")
    client._warranties = [{
        "id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
        "warranty_end": "2027-01-01", "customer_name": "สมชาย"}]
    await client.set_last_customer_ref(ME, oa, license_id=T.LICENSE_ID, customer_id=customer["id"], name="สมชาย ใจดี")
    await client.set_last_entity_ref(ME, oa, license_id=T.LICENSE_ID, entity_type="deal", entity_id=deal["id"], code=deal["deal_id"])
    return client, keys


class Watch:
    """Did this run end without work being done?"""

    def __init__(self):
        self.gave_up = False
        self.originals = {}

    def __enter__(self):
        for name in ("_pending_execution_reply", "suggest_what_you_can_do"):
            original = getattr(chat, name)
            self.originals[name] = original

            def wrapper(*a, _o=original, **k):
                self.gave_up = True
                return _o(*a, **k)
            setattr(chat, name, wrapper)
        return self

    def __exit__(self, *exc):
        for name, original in self.originals.items():
            setattr(chat, name, original)
        return False


async def probe(action, entity, oa):
    client, keys = await _seeded(oa)
    ctx = T._ctx(oa=oa, primary_role="technician" if oa == "technician" else "sales")
    intent = {"action": action, "entity": entity, "fields": dict(FIELDS.get(entity, {})), "missing": []}
    with Watch() as watch:
        try:
            await chat._execute_intent(
                client, intent=intent, ctx=ctx, license_id=T.LICENSE_ID,
                message=MESSAGES.get((action, entity), MESSAGES.get(entity, "")),
            permission_keys=keys, language="th",
            )
        except Exception as exc:  # noqa: BLE001
            return f"EXCEPTION {type(exc).__name__}: {exc}"
    return "gave_up" if watch.gave_up else "routed"


async def main():
    pairs = sorted(chat.ACTION_PERMISSIONS)
    unrouted, crashed, routed = [], [], []
    for action, entity in pairs:
        needed = chat.ACTION_PERMISSIONS[(action, entity)]
        outcomes = {}
        for oa in ("sales", "technician"):
            if not chat._oa_allows(oa, needed):
                continue
            outcomes[oa] = await probe(action, entity, oa)
        if any(o.startswith("EXCEPTION") for o in outcomes.values()):
            crashed.append((action, entity, next(o for o in outcomes.values() if o.startswith("EXCEPTION"))))
        elif "routed" in outcomes.values():
            routed.append((action, entity))
        else:
            unrouted.append((action, entity))

    stale = sorted(set(NO_HANDLER) & set(routed))
    holes = [p for p in unrouted if p not in NO_HANDLER]

    if not holes and not crashed:
        print(
            f"checked {len(pairs)} (action, entity) pairs: {len(routed)} reach a handler, "
            f"{len(unrouted)} are on the documented no-handler list"
        )
        if stale:
            print("note — listed as unrouted but now handled (tidy NO_HANDLER): "
                  + ", ".join(f"{e}.{a}" for a, e in stale))
        return 0

    if crashed:
        print("\nRAISED INSTEAD OF ANSWERING:")
        for action, entity, why in crashed:
            print(f"  {entity}.{action:12} — {why}")
    if holes:
        print("\nREGISTERED, PERMITTED, AND UNROUTED "
              "(the model can ask for these and the router drops them):")
        for action, entity in holes:
            print(f"  {entity}.{action}")
        print("\nRoute each to the handler that already does it, or add it to "
              "NO_HANDLER with the reason and the dashboard page that does.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
