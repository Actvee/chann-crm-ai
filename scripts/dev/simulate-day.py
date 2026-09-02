"""A shop's day, played through the real chat handler on all three OAs.

Every reply is classified: fine, or one of the shapes that mean the
system failed the person — a generic apology, a permission list for
something they can do, "not a feature", or an ask for something they
just gave. Anything in the second group is a finding.
"""
import asyncio, json, sys
sys.path.insert(0, "application"); sys.path.insert(0, "tests/unit")
import httpx
import test_phase6_chat as T
from chann_app.config import settings
settings.openrouter_api_key = "k"; settings.openrouter_model = "m"

BAD = {
    "GENERIC_ERROR": ("ขออภัย",),
    "PERMISSION_LIST": ("คุณสามารถทำสิ่งเหล่านี้ได้", "คุณยังไม่มีสิทธิ์"),
    "NOT_A_FEATURE": ("ระบบยังไม่มีฟังก์ชันนี้",),
    "AI_DOWN": ("ระบบไม่พร้อมใช้งาน",),
    "NOT_FOUND": ("ไม่พบ",),
}

def classify(text):
    for label, needles in BAD.items():
        if any(n in text for n in needles):
            return label
    return "ok"

def ai(payload):
    return httpx.AsyncClient(transport=T._ai(json.dumps(payload, ensure_ascii=False)))

findings = []

async def say(client, oa, msg, expect_ok=True, ai_client=None, role=None):
    ctx = T._ctx(oa=oa, primary_role=role or ("technician" if oa == "technician" else "sales"))
    r = await T.handle_chat_message(client, message=msg, ctx=ctx, ai_client=ai_client)
    kind = classify(r.text)
    flag = "  " if (kind == "ok") == expect_ok else "!!"
    first = r.text.splitlines()[0][:70]
    print(f"{flag} [{oa:10}] {msg[:38]:40} -> {kind:15} {first}")
    if (kind == "ok") != expect_ok:
        findings.append((oa, msg, kind, first))
    return r

async def sales_day():
    print("\n=== SALES OA: a salesperson's morning ===")
    c = T.FakeDataClient(permission_keys=[
        "customer.create","customer.read","customer.update","deal.create","deal.read",
        "deal.update","quote.create","quote.read","quote.update","product.manage",
        "followup.create","followup.read","ticket.read","ticket.create","ticket.assign",
        "note.create","note.read",
    ])
    c._products = [
        {"id":"p1","product_id":"FAN16","product_name":"พัดลมตั้งพื้น 16 นิ้ว","unit_price":"1500.00"},
        {"id":"p2","product_id":"AC12","product_name":"แอร์ 12000 BTU","unit_price":"15900.00"},
    ]
    await say(c, "sales", "สวัสดี")
    await say(c, "sales", "วิธีใช้")
    await say(c, "sales", "ทำอะไรได้บ้าง")
    await say(c, "sales", "รายชื่อลูกค้า")
    # new customer via AI
    await say(c, "sales", "มีลูกค้าใหม่ สมชาย ใจดี เบอร์ 0812345678 สนใจแอร์ นัดดูวันศุกร์",
              ai_client=ai({"action":"create","entity":"customer","fields":{"first_name":"สมชาย","last_name":"ใจดี","phone":"0812345678","notes":"สนใจแอร์ นัดดูวันศุกร์"},"missing":[]}))
    await say(c, "sales", "ข้อมูลลูกค้า สมชาย")
    await say(c, "sales", "ดูนัดหมาย")
    await say(c, "sales", "ยืนยันลูกค้าเป็น contact",
              ai_client=ai({"action":"promote","entity":"customer","fields":{"target_name":"สมชาย"},"missing":[]}))
    await say(c, "sales", "สร้างดีล แอร์ 1 ตัว")
    await say(c, "sales", "ดีล D-2026-0001 คาดว่าจะปิดวันศุกร์")
    await say(c, "sales", "เพิ่มสินค้า พัดลม 2 ตัว")
    await say(c, "sales", "ข้อมูลดีล D-2026-0001")
    await say(c, "sales", "แก้ราคาพัดลมเหลือ 1400")
    await say(c, "sales", "สร้างใบเสนอราคา")
    await say(c, "sales", "ลดราคา 10%", expect_ok=True)
    await say(c, "sales", "ออกเอกสาร Q-2026-0001")
    await say(c, "sales", "รายการใบเสนอราคา")
    await say(c, "sales", "ดีลเดือนนี้")
    await say(c, "sales", "ดีลเกิน 10000")
    await say(c, "sales", "บันทึกว่า ลูกค้าขอใบเสนอราคาเพิ่ม")
    await say(c, "sales", "ดูบันทึก D-2026-0001")
    await say(c, "sales", "เตือน D-2026-0001 พรุ่งนี้")
    await say(c, "sales", "ปิดสำเร็จ D-2026-0001")
    await say(c, "sales", "ปิดไม่สำเร็จ D-2026-0001 เพราะราคาสูง")  # won->lost is legal
    await say(c, "sales", "ลูกค้าสมชายแจ้งแอร์ไม่เย็น",
              ai_client=ai({"action":"create","entity":"ticket","fields":{"target_name":"สมชาย","issue_description":"แอร์ไม่เย็น"},"missing":[]}))
    await say(c, "sales", "รายการงานซ่อม")
    await say(c, "sales", "มอบหมาย T-2026-0001 ให้อัตโนมัติ")
    await say(c, "sales", "ค้นหาลูกค้า 0812345678")
    await say(c, "sales", "หาลูกค้าที่ยังไม่มีดีล", expect_ok=False)  # not a feature, want to see how it fails
    await say(c, "sales", "ยอดขายเดือนนี้เท่าไหร่",
              ai_client=ai({"action":"read","entity":"report","fields":{"period":"month"},"missing":[]}))

async def tech_day():
    print("\n=== TECHNICIAN OA: a job ===")
    c = T.FakeDataClient(permission_keys=[
        "ticket.read","ticket.update","ticket.close","service_report.create",
        "service_report.read","service_report.update","warranty.read",
    ], role="technician")
    c._tickets = [{
        "id":"t1","ticket_number":"T-2026-0001","status":"assigned","accept_status":"pending",
        "assigned_to_ref":"member-1","customer_name":"สมชาย","service_address":"99/1 สุขุมวิท",
        "issue_description":"แอร์ไม่เย็น","scheduled_date":"2026-09-02","scheduled_time":"10:00",
    }]
    await say(c, "technician", "สวัสดี")
    await say(c, "technician", "งานของฉัน")
    await say(c, "technician", "งานวันนี้")
    await say(c, "technician", "ข้อมูลงาน T-2026-0001")
    await say(c, "technician", "รับงาน")
    await say(c, "technician", "เดี๋ยวผมไปเอง", ai_client=ai({"action":"claim","entity":"ticket","fields":{},"missing":[]}))
    c._tickets[0]["accept_status"] = "accepted"
    await say(c, "technician", "ถึงแล้ว")
    await say(c, "technician", "เช็คอิน")
    c._tickets[0]["status"] = "in_progress"
    await say(c, "technician", "เช็คประกัน SN12345", expect_ok=False)  # no such serial seeded
    await say(c, "technician", "ปิดงาน")
    await say(c, "technician", "คอมเพรสเซอร์รั่ว")
    await say(c, "technician", "เปลี่ยนคอมเพรสเซอร์แล้ว")
    await say(c, "technician", "ไม่มี")
    await say(c, "technician", "รายงานของฉัน", expect_ok=True)

async def customer_day():
    print("\n=== CUSTOMER OA: a customer ===")
    c = T.FakeDataClient(permission_keys=["customer.read","ticket.create","ticket.read","warranty.read","warranty.create"])
    await say(c, "customer", "สวัสดีครับ")
    await say(c, "customer", "วิธีใช้")
    await say(c, "customer", "แจ้งซ่อม")
    await say(c, "customer", "แอร์ไม่เย็นครับ")
    await say(c, "customer", "ลงทะเบียนสินค้า")
    await say(c, "customer", "เช็คประกัน")
    await say(c, "customer", "งานของฉัน")
    await say(c, "customer", "เลื่อนนัดวันศุกร์ บ่าย 2")
    await say(c, "customer", "ยกเลิกงาน")
    await say(c, "customer", "ขอบคุณครับ")

async def main():
    await sales_day(); await tech_day(); await customer_day()
    print(f"\n=== {len(findings)} FINDINGS ===")
    for oa, msg, kind, first in findings:
        print(f"  [{oa}] {msg!r} -> {kind}: {first}")

asyncio.run(main())
