"""Many ways of asking for the same thing, played through the real chat
handler on all three OAs — without a model, so the run shows exactly which
phrasings the deterministic layer understands and which are left to the AI.

Every case carries an expectation:
  help   the guide must come back (never a permission list, never "not sure")
  greet  a greeting, not a model call
  rule   handled before the model, with a non-failing reply
  fault  (customer OA) opens or continues a fault report
  ai     free text the model is meant to parse — going to the AI is fine
  any    just show what happens

Replies longer than LONG_LINES lines or LONG_CHARS characters are listed
separately: a LINE bubble that long is not read.
"""
import asyncio, json, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "application")); sys.path.insert(0, os.path.join(ROOT, "tests", "unit"))
import httpx
import test_phase6_chat as T
from chann_app.config import settings
settings.openrouter_api_key = "k"; settings.openrouter_model = "m"

LONG_LINES, LONG_CHARS = 15, 700

BAD = {
    "GENERIC_ERROR": ("ขออภัย",),
    "NOT_SURE": ("ยังไม่แน่ใจว่าต้องการอะไร",),
    "PERMISSION": ("คุณยังไม่มีสิทธิ์", "ยังไม่มีสิทธิ์ใช้งาน"),
    "NOT_A_FEATURE": ("ระบบยังไม่มีฟังก์ชันนี้",),
    "AI_DOWN": ("ระบบไม่พร้อมใช้งาน",),
    "NOT_FOUND": ("ไม่พบ",),
}


def classify(text):
    for label, needles in BAD.items():
        if any(n in text for n in needles):
            return label
    return "ok"


class AiProbe:
    """Answers every model call with "suggest" and remembers it was asked."""
    def __init__(self):
        self.calls = 0
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        self.calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps({"action": "suggest", "entity": None, "fields": {}, "missing": []})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x",
        })


SALES_KEYS = [
    "customer.create", "customer.read", "customer.update", "customer.archive", "deal.create", "deal.read",
    "deal.update", "quote.create", "quote.read", "quote.update", "product.manage", "followup.create",
    "followup.read", "followup.update", "note.create", "note.read", "ticket.read", "ticket.create",
    "ticket.update", "ticket.assign", "service_report.read", "warranty.read", "warranty.create",
    "team.manage", "member.manage", "setting.manage", "approval.view", "approval.approve", "approval.reject",
    "approval.manage", "view_reports",
]
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read", "warranty.read"]
CUST_KEYS = ["customer.read", "ticket.create", "ticket.read", "warranty.read", "warranty.create"]

results = []


async def run(oa, client, cases, role=None):
    for message, expect in cases:
        probe = AiProbe()
        ctx = T._ctx(oa=oa, primary_role=role or ("technician" if oa == "technician" else "sales"))
        try:
            r = await T.handle_chat_message(client, message=message, ctx=ctx, ai_client=probe.client)
            text = r.text or ""
            qr = len(r.quick_replies or [])
        except Exception as exc:  # noqa: BLE001
            text = f"EXCEPTION {type(exc).__name__}: {exc}"
            qr = 0
        layer = "AI" if probe.calls else "rule"
        kind = classify(text)
        lines = text.count("\n") + 1
        long = lines > LONG_LINES or len(text) > LONG_CHARS
        if expect == "help":
            ok = ("วิธีใช้" in text.splitlines()[0]) if text else False
        elif expect == "greet":
            ok = layer == "rule" and "สวัสดี" in text
        elif expect in ("rule", "fault"):
            ok = layer == "rule" and kind == "ok"
        elif expect == "ai":
            ok = True
        else:
            ok = kind == "ok"
        results.append((oa, message, expect, layer, kind, ok, long, lines, len(text), qr, text))
        flag = "  " if ok else "!!"
        print(f"{flag} [{oa:10}] {message[:34]:36} {expect:5} {layer:4} {kind:13} {lines:>2}L {len(text):>4}c {text.splitlines()[0][:52] if text else ''}")


HELP_VARIANTS = [
    "วิธีใช้", "ใช้ยังไง", "ใช้งานยังไง", "ใช้ยังไงครับ", "ใช้ยังไงคะ", "ใช้ไง", "ใช้งานอย่างไร", "มันใช้ยังไง",
    "ระบบนี้ใช้ยังไง", "ต้องทำยังไง", "ทำยังไง", "ทำไงต่อ", "เริ่มยังไง", "เริ่มต้นยังไง", "เริ่มใช้งานยังไง",
    "สอนใช้หน่อย", "สอนหน่อย", "ช่วยด้วย", "ช่วยหน่อย", "ขอความช่วยเหลือ", "งง", "ไม่เข้าใจ", "ไม่รู้จะทำยังไง",
    "มีอะไรบ้าง", "มีฟังก์ชันอะไรบ้าง", "มีเมนูอะไรบ้าง", "ทำอะไรได้บ้างครับ", "ทำอะไรได้บ้างคะ", "คุณทำอะไรได้บ้าง",
    "บอทนี้ทำอะไรได้", "ช่วยอะไรได้บ้าง", "help me", "how do i use this", "what can you do", "??", "???",
    "คู่มือการใช้งาน", "ขอคู่มือ", "ดูวิธีใช้", "วิธีใช้งานระบบ", "แนะนำการใช้งาน", "แนะนำหน่อย", "ขอคำแนะนำ",
    "อยากรู้วิธีใช้", "ใช้งานไม่เป็น", "ใช้ไม่เป็น", "ตัวอย่างคำสั่ง", "มีคำสั่งอะไรบ้าง", "พิมพ์อะไรได้บ้าง",
    "ต้องพิมพ์ยังไง", "พิมพ์ยังไง", "เมนู", "menu", "help", "ช่วยเหลือ",
]
GREETINGS = [
    "สวัสดีครับ", "สวัสดีค่ะ", "หวัดดี", "ดีครับ", "hello", "hi", "สวัสดีครับ ขอสอบถามหน่อย",
]
SMALL_TALK = [("ขอบคุณครับ", "any"), ("โอเค", "any"), ("ครับ", "any"), ("ค่ะ", "any"), ("ok", "any"), ("👍", "any"), ("555", "any"), ("asdfgh", "any")]


async def sales():
    print("\n=== SALES OA ===")
    c = T.FakeDataClient(permission_keys=SALES_KEYS)
    c._products = [{"id": "p1", "product_id": "AC12", "product_name": "แอร์ 12000 BTU", "unit_price": "15900.00"}]
    c._members = [{"id": "member-1", "chann_uid": "CHN-S-000001", "role": "sales", "status": "active"},
                  {"id": "m-tech", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "first_name": "สมศักดิ์"}]
    cust = await c.create_customer("L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
    deal = await c.create_deal("L1", {"contact_id": cust["id"]})
    c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open", "accept_status": "pending",
                   "customer_name": "สมชาย", "customer_phone": "0812345678", "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
                   "scheduled_date": "2026-09-08", "scheduled_time": "10:00"}]
    cases = [(m, "help") for m in HELP_VARIANTS] + [(m, "greet") for m in GREETINGS] + SMALL_TALK
    cases += [
        # customers
        ("รายชื่อลูกค้า", "rule"), ("ลูกค้า", "rule"), ("ขอดูลูกค้า", "rule"), ("ขอรายชื่อลูกค้า", "rule"),
        ("ลูกค้ามีใครบ้าง", "rule"), ("ลูกค้าทั้งหมดมีกี่คน", "rule"), ("ดูรายชื่อลูกค้าหน่อย", "rule"), ("รายชื่อลูกค้าครับ", "rule"), ("สินค้า", "rule"),
        ("list customers", "rule"), ("ค้นหาลูกค้า สมชาย", "rule"), ("หาลูกค้าชื่อสมชาย", "rule"), ("ค้นหา สมชาย", "rule"),
        ("สมชาย เบอร์อะไร", "ai"), ("เบอร์สมชาย", "ai"), ("ลูกค้าชื่อสมชาย", "rule"), ("ข้อมูลของสมชาย", "rule"),
        ("ขอเบอร์ลูกค้า สมชาย", "rule"), ("ข้อมูลลูกค้า สมชาย", "rule"), ("ข้อมูลลูกค้า C-2026-0001", "rule"),
        ("เพิ่มลูกค้าใหม่ สมหญิง ดีใจ 0898765432", "ai"), ("ลูกค้าใหม่ชื่อ สมหญิง ดีใจ โทร 089-876-5432", "ai"),
        ("สมหญิง ดีใจ 0898765432 สนใจแอร์", "ai"), ("มีลูกค้าสนใจแอร์ ชื่อสมหญิง", "ai"), ("add customer Somying 0898765432", "ai"),
        ("เพิ่มลูกค้า สมหญิง ดีใจ 0898765432", "ai"), ("เพิ่มลูกค้า", "rule"), ("สร้างลูกค้า", "rule"),
        ("แก้เบอร์สมชายเป็น 0899999999", "ai"), ("ลบลูกค้า สมชาย", "rule"), ("ลบ lead สมชาย", "rule"),
        # deals
        ("ดีล", "rule"), ("ดีลทั้งหมด", "rule"), ("ขอดูดีล", "rule"), ("ดีลที่ยังเปิดอยู่", "rule"), ("ดีลที่ยังไม่ปิด", "rule"),
        ("เปิดดีลให้สมชาย", "rule"), ("สร้างดีลใหม่ให้สมชาย มูลค่า 50000", "rule"), ("สมชายจะซื้อแอร์ 2 ตัว", "ai"),
        ("ปิดดีล D-2026-0001", "any"), ("ปิดสำเร็จ D-2026-0001", "rule"), ("ดีล D-2026-0001 ปิดแล้ว", "any"),
        ("ปิดการขายสำเร็จ D-2026-0001", "any"), ("ลูกค้าไม่เอา D-2026-0001", "any"), ("ข้อมูลดีล D-2026-0001", "rule"),
        ("ยอดขาย", "rule"), ("ยอดขายเดือนนี้", "rule"), ("ขายได้เท่าไหร่เดือนนี้", "ai"), ("สรุปยอดขาย", "rule"), ("pipeline", "any"),
        ("ดีลเกิน 10000", "rule"), ("ดีลของสมชาย", "rule"),
        # quotes
        ("ใบเสนอราคา", "any"), ("ขอใบเสนอราคา", "any"), ("ทำใบเสนอราคาให้สมชาย", "rule"), ("ออกใบเสนอราคา D-2026-0001", "rule"),
        ("สร้างใบเสนอราคา D-2026-0001", "rule"), ("รายการใบเสนอราคา", "rule"), ("ใบเสนอราคาทั้งหมด", "rule"),
        ("ส่วนลด Q-2026-0001 500 บาท", "rule"), ("ลด 10%", "any"), ("ส่งใบเสนอราคาให้ลูกค้า", "any"), ("ใบเสนอราคาของสมชาย", "any"),
        # notes & reminders
        ("บันทึกว่า สมชายขอเลื่อน", "rule"), ("จดไว้ว่า สมชายขอเลื่อน", "any"), ("โน้ต: ลูกค้าขอส่วนลด", "rule"),
        ("เตือนพรุ่งนี้ 10 โมง โทรหาสมชาย", "rule"), ("พรุ่งนี้นัดสมชาย 10 โมง", "ai"), ("นัดสมชายวันศุกร์", "rule"),
        ("วันนี้มีนัดอะไรบ้าง", "any"), ("นัดหมายวันนี้", "any"), ("งานวันนี้", "rule"), ("วันนี้ต้องทำอะไร", "any"),
        ("พรุ่งนี้มีอะไร", "any"), ("อาทิตย์นี้มีนัดไหม", "any"), ("ดูนัดหมาย", "rule"), ("รายการเตือน", "rule"),
        # tickets on the sales OA
        ("รายการงาน", "rule"), ("งานซ่อม", "rule"), ("งานซ่อมมีอะไรบ้าง", "any"), ("มีงานซ่อมค้างไหม", "any"), ("งานซ่อมวันนี้", "any"),
        ("มอบหมาย T-2026-0001 ให้ สมศักดิ์", "rule"), ("ให้สมศักดิ์ไปทำงาน T-2026-0001", "any"), ("จ่ายงาน T-2026-0001 ทีมแอร์", "rule"),
        ("ใครว่างบ้าง", "any"), ("รายชื่อช่าง", "rule"), ("ช่างมีใครบ้าง", "rule"), ("ช่าง", "any"), ("ข้อมูลงาน T-2026-0001", "rule"),
        ("T-2026-0001", "any"), ("รายการรออนุมัติ", "rule"), ("มีอะไรรออนุมัติไหม", "any"),
        # shop, settings, language, permissions
        ("ข้อมูลร้าน", "rule"), ("รหัสร้าน", "rule"), ("รหัสร้านเราคืออะไร", "any"), ("ร้านเราชื่ออะไร", "any"),
        ("ขอรหัสเชิญช่าง", "rule"), ("เพิ่มช่าง", "rule"), ("เชิญช่างเข้าร้าน", "any"), ("ตั้งค่า", "any"), ("ตั้งค่าร้าน", "any"),
        ("ข้อมูลบริษัท", "rule"), ("เปลี่ยนภาษา", "rule"), ("english", "any"), ("ภาษาอังกฤษ", "any"),
        ("สิทธิ์ของฉัน", "rule"), ("ฉันทำอะไรได้บ้าง", "help"), ("ผมมีสิทธิ์อะไรบ้าง", "any"), ("ทำอะไรกับลูกค้าได้บ้าง", "rule"),
        ("รายการสินค้า", "rule"), ("เพิ่มสินค้า พัดลม ราคา 1500", "ai"),
        # off topic
        ("อากาศวันนี้เป็นไง", "any"), ("ราคาแอร์เท่าไหร่", "any"),
    ]
    await run("sales", c, cases)


async def technician():
    print("\n=== TECHNICIAN OA ===")
    c = T.FakeDataClient(permission_keys=TECH_KEYS, role="technician")
    c._tickets = [
        {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
         "assigned_to_ref": "member-1", "customer_name": "สมชาย", "customer_phone": "0812345678", "service_address": "99/1 สุขุมวิท",
         "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08", "scheduled_time": "10:00"},
        {"id": "t2", "ticket_number": "T-2026-0002", "status": "open", "accept_status": "pending", "visibility": "public",
         "customer_name": "สมหญิง", "service_address": "12 ลาดพร้าว", "issue_description": "ตู้เย็นไม่เย็น",
         "scheduled_date": "2026-09-09", "scheduled_time": "13:00"},
    ]
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active", "warranty_end": "2027-01-01"}]
    cases = [(m, "help") for m in HELP_VARIANTS] + [(m, "greet") for m in GREETINGS] + SMALL_TALK
    cases += [
        ("งาน", "any"), ("งานของฉัน", "rule"), ("งานของผม", "any"), ("งานผมวันนี้", "any"), ("วันนี้มีงานไหม", "any"),
        ("มีงานอะไรบ้าง", "any"), ("งานที่ต้องไป", "any"), ("ตารางงาน", "any"), ("ตารางงานวันนี้", "any"), ("ตารางงานของฉัน", "any"),
        ("งานพรุ่งนี้", "any"), ("งานวันนี้", "rule"), ("มีงานไหม", "rule"), ("งานที่เปิดรับ", "rule"), ("งานว่าง", "rule"),
        ("รับงาน T-2026-0002", "rule"), ("รับงานนี้", "any"), ("ผมรับงาน T-2026-0002 เอง", "rule"), ("ขอรับงาน", "any"), ("รับ", "any"),
        ("ไม่รับ", "any"), ("ไม่ว่าง ไปไม่ได้", "any"), ("ปฏิเสธงาน T-2026-0001 ป่วย", "rule"),
        ("ถึงหน้างานแล้ว", "rule"), ("ถึงบ้านลูกค้าแล้ว", "any"), ("มาถึงแล้วครับ", "rule"), ("อยู่หน้างานแล้ว", "any"),
        ("เช็คอิน", "rule"), ("check in", "rule"), ("เช็คอินงาน T-2026-0001", "rule"), ("เริ่มทำงานแล้ว", "any"),
        ("ลูกค้าบอกว่าถึงแล้วค่อยโทร", "any"),
        ("เสร็จแล้ว", "any"), ("งานเสร็จแล้ว", "any"), ("ทำเสร็จแล้วครับ", "any"), ("ปิดงาน", "rule"),
        ("ปิดงาน T-2026-0001\nพบ: คอมรั่ว\nแก้: เปลี่ยนคอม", "rule"), ("ซ่อมเสร็จแล้ว เปลี่ยนคอมเพรสเซอร์", "any"),
        ("ส่งรายงาน", "any"), ("รายงานของฉัน", "rule"), ("ข้อมูลงาน T-2026-0001", "rule"), ("T-2026-0001", "any"),
        ("งานนี้ที่อยู่ไหน", "any"), ("ลูกค้าเบอร์อะไร", "any"), ("ที่อยู่ลูกค้า", "any"),
        ("เช็คประกัน SN12345678", "rule"), ("SN12345678 ประกันหมดยัง", "any"), ("เครื่องนี้ยังมีประกันไหม", "any"),
        ("งานของทีม", "rule"), ("ทีมมีงานไหม", "any"),
        ("โปรไฟล์", "any"), ("ข้อมูลของฉัน", "rule"), ("แก้เบอร์เป็น 0899999999", "ai"), ("สิทธิ์ของฉัน", "rule"),
        ("เปลี่ยนภาษา", "rule"), ("เปลี่ยนร้าน", "any"),
    ]
    await run("technician", c, cases)


async def customer():
    print("\n=== CUSTOMER OA ===")
    c = T.FakeDataClient(permission_keys=CUST_KEYS)
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "customer_chann_uid": "CHN-S-000001", "warranty_end": "2027-01-01"}]
    cases = [(m, "help") for m in HELP_VARIANTS] + [(m, "greet") for m in GREETINGS] + SMALL_TALK
    faults = [
        "แอร์ไม่เย็น", "แอร์ไม่เย็นเลยค่ะ", "แอร์เสีย", "เครื่องซักผ้าไม่หมุน", "ตู้เย็นไม่เย็น", "ทีวีเปิดไม่ติด",
        "มีน้ำหยดจากแอร์", "แอร์มีเสียงดัง", "อยากให้ช่างมาดู", "ขอช่างมาซ่อมหน่อย", "ขอนัดช่าง", "ต้องการแจ้งซ่อม",
        "แจ้งซ่อมครับ", "ซ่อมแอร์", "ล้างแอร์", "อยากล้างแอร์", "แอร์เป่าลมไม่ออก", "รีโมทกดไม่ติด", "เครื่องทำน้ำอุ่นไม่ร้อน",
        "ประตูเลื่อนไม่ได้",
    ]
    cases += [(m, "fault") for m in faults]
    cases += [
        ("ช่างมาเมื่อไหร่", "any"), ("ช่างมากี่โมง", "any"), ("งานผมถึงไหนแล้ว", "any"), ("สถานะ", "rule"), ("เช็คสถานะงาน", "any"),
        ("ซ่อมเสร็จยัง", "any"), ("งานของฉัน", "rule"), ("ประกัน", "any"), ("ประกันของฉัน", "rule"), ("เครื่องผมยังมีประกันไหม", "any"),
        ("หมดประกันเมื่อไหร่", "any"), ("ลงทะเบียน", "any"), ("ลงทะเบียนสินค้า SN12345678", "rule"), ("SN12345678", "rule"),
        ("ราคาแอร์เท่าไหร่", "any"), ("มีแอร์รุ่นไหนบ้าง", "any"), ("อยากซื้อแอร์", "any"), ("สินค้า", "any"), ("ดูสินค้า", "rule"),
        ("ค้นหา พัดลม", "any"), ("คุยกับร้าน", "any"), ("คุยกับร้าน ราคาล้างแอร์", "any"), ("ขอคุยกับพนักงาน", "any"),  # storefront/live chat live in other modules the fake lacks
        ("ติดต่อร้าน", "rule"), ("เบอร์ร้าน", "rule"), ("ร้านเปิดกี่โมง", "any"), ("เลื่อนนัด", "rule"), ("เลื่อนนัดเป็นวันศุกร์", "rule"),
        ("ขอเลื่อนเป็นพรุ่งนี้", "rule"), ("ยกเลิก", "rule"), ("ยกเลิกงาน", "rule"), ("ไม่ซ่อมแล้ว", "any"),
        ("เปลี่ยนที่อยู่", "any"), ("แก้เบอร์เป็น 0899999999", "ai"), ("ที่อยู่ 99/1 สุขุมวิท", "any"), ("ขอใบเสร็จ", "any"),
        ("จ่ายเงินยังไง", "any"), ("โปรไฟล์", "rule"), ("ข้อมูลของฉัน", "rule"), ("เปลี่ยนภาษา", "rule"), ("ประวัติการซื้อ", "rule"),
    ]
    await run("customer", c, cases)


async def main():
    await sales(); await technician(); await customer()
    fails = [r for r in results if not r[5]]
    longs = [r for r in results if r[6]]
    print(f"\n=== {len(results)} cases · {len(fails)} not as expected · {len(longs)} long replies ===")
    by = {}
    for oa, msg, expect, layer, kind, ok, long, lines, chars, qr, text in fails:
        by.setdefault((oa, expect, layer, kind), []).append(msg)
    for (oa, expect, layer, kind), msgs in sorted(by.items()):
        print(f"  [{oa}] expected {expect}, got {layer}/{kind} ({len(msgs)}): " + " | ".join(m.replace(chr(10), ' ')[:24] for m in msgs[:12]))
    print("\nLONG replies (lines/chars):")
    seen = set()
    for oa, msg, expect, layer, kind, ok, long, lines, chars, qr, text in longs:
        head = text.splitlines()[0][:40]
        if (oa, head) in seen:
            continue
        seen.add((oa, head))
        print(f"  [{oa}] {msg[:24]:26} {lines}L/{chars}c  {head}")
    if "--dump" in sys.argv:
        with open(sys.argv[sys.argv.index("--dump") + 1], "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps({"oa": r[0], "message": r[1], "expect": r[2], "layer": r[3], "kind": r[4], "ok": r[5], "lines": r[7], "chars": r[8], "quick_replies": r[9], "text": r[10]}, ensure_ascii=False) + "\n")

asyncio.run(main())
