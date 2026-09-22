"""Many ways of asking for the same thing, played through the real chat
handler on all three OAs — without a model, so the run shows exactly which
phrasings the deterministic layer understands and which are left to the AI.

Every case carries an expectation:
  help   the guide must come back (never a permission list, never "not sure")
  greet  a greeting, not a model call
  rule   handled before the model, with a non-failing reply
  fault  (customer OA) opens or continues a fault report
  ai     free text the model is meant to parse — going to the AI is fine
  unsure gibberish; saying "ยังไม่แน่ใจ" IS the right answer, not a defect
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

#: Plain: the model road is answered locally, so the run is fast and the
#: same every time — which is what the gate wants. `--real`, with OR_KEY
#: (or OPENROUTER_API_KEY) set, sends the same phrasings to the deployed
#: model instead, which is the ONLY way the "ai" expectations below are
#: actually measured rather than assumed (18 ก.ย. 2569). Until this
#: existed, every `expected ai, got rule` line was read as a fact about
#: the model that no model had been asked about.
REAL = "--real" in sys.argv
_KEY = os.environ.get("OR_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
if REAL and _KEY:
    settings.openrouter_api_key = _KEY
    settings.openrouter_model = os.environ.get("OR_MODEL", "qwen/qwen3.6-35b-a3b")
    print("=== asking the deployed model for real ===")
else:
    if REAL:
        print("!! --real needs OR_KEY (or OPENROUTER_API_KEY) — falling back to offline")
        REAL = False
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
    """Remembers whether the model was asked — and, offline, answers for it.

    The `calls` counter is what tells a rule answer from a model answer in
    the table below, so it has to keep working in both modes. Offline it
    is bumped by the mock; against the real model an event hook counts the
    request on its way out.
    """
    def __init__(self):
        self.calls = 0
        if REAL:
            self.client = httpx.AsyncClient(event_hooks={"request": [self._count]})
        else:
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _count(self, request):
        self.calls += 1

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
    "approval.manage", "view_reports", "invoice.read", "invoice.create", "invoice.update", "invoice.void",
]
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read", "warranty.read"]
CUST_KEYS = ["customer.read", "ticket.create", "ticket.read", "warranty.read", "warranty.create", "invoice.read"]

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
            # A sentence that must REACH the model. This simulator stubs the
            # model as NOT_SURE, so the reply is never assertable here —
            # what is assertable, and what the conversion is about, is that
            # a keyword did not decide it (11 ก.ย. 2569).
            ok = layer == "AI"
        elif expect == "unsure":
            # Gibberish. "ยังไม่แน่ใจว่าต้องการอะไร" is the RIGHT answer to
            # it, and labelling these "any" made them count as answered
            # badly — three of them, every run, in the number I read out
            # to the owner as if it were a list of defects (20 ก.ย. 2569).
            # What matters is that the system says so rather than guessing.
            ok = kind in ("NOT_SURE", "ok")
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
SMALL_TALK = [("ขอบคุณครับ", "any"), ("โอเค", "any"), ("ครับ", "any"), ("ค่ะ", "any"), ("ok", "any"), ("👍", "any"), ("555", "any"), ("asdfgh", "unsure")]


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
        ("รายชื่อลูกค้า", "rule"), ("ลูกค้า", "ai"), ("ขอดูลูกค้า", "ai"), ("ขอรายชื่อลูกค้า", "ai"),
        # Moved to the model road 11 ก.ย. 2569: the deployed model reads both
        # as read/customer, so a keyword no longer decides them. "any" because
        # this simulator stubs the model as NOT_SURE, which is exactly what a
        # converted sentence looks like here.
        ("ลูกค้ามีใครบ้าง", "ai"), ("ลูกค้าทั้งหมดมีกี่คน", "ai"), ("ดูรายชื่อลูกค้าหน่อย", "ai"), ("รายชื่อลูกค้าครับ", "ai"), ("สินค้า", "ai"),
        ("list customers", "ai"), ("ค้นหาลูกค้า สมชาย", "ai"), ("หาลูกค้าชื่อสมชาย", "ai"), ("ค้นหา สมชาย", "ai"),
        ("สมชาย เบอร์อะไร", "ai"), ("เบอร์สมชาย", "ai"), ("ลูกค้าชื่อสมชาย", "ai"), ("ข้อมูลของสมชาย", "ai"),
        ("ขอเบอร์ลูกค้า สมชาย", "ai"), ("ข้อมูลลูกค้า สมชาย", "ai"), ("ข้อมูลลูกค้า C-2026-0001", "ai"),
        ("เพิ่มลูกค้าใหม่ สมหญิง ดีใจ 0898765432", "ai"), ("ลูกค้าใหม่ชื่อ สมหญิง ดีใจ โทร 089-876-5432", "ai"),
        ("สมหญิง ดีใจ 0898765432 สนใจแอร์", "ai"), ("มีลูกค้าสนใจแอร์ ชื่อสมหญิง", "ai"), ("add customer Somying 0898765432", "ai"),
        ("เพิ่มลูกค้า สมหญิง ดีใจ 0898765432", "ai"), ("เพิ่มลูกค้า", "ai"), ("สร้างลูกค้า", "rule"),
        ("แก้เบอร์สมชายเป็น 0899999999", "ai"), ("ลบลูกค้า สมชาย", "ai"), ("ยกเลิก", "any"), ("ลบ lead สมชาย", "ai"), ("ยกเลิก", "any"),
        # deals
        ("ดีล", "ai"), ("ดีลทั้งหมด", "ai"), ("ขอดูดีล", "ai"), ("ดีลที่ยังเปิดอยู่", "ai"), ("ดีลที่ยังไม่ปิด", "ai"),
        ("เปิดดีลให้สมชาย", "ai"), ("สร้างดีลใหม่ให้สมชาย มูลค่า 50000", "ai"), ("สมชายจะซื้อแอร์ 2 ตัว", "ai"),
        ("ปิดดีล D-2026-0001", "any"), ("ปิดสำเร็จ D-2026-0001", "ai"), ("ดีล D-2026-0001 ปิดแล้ว", "ai"),
        ("ปิดการขายสำเร็จ D-2026-0001", "any"), ("ลูกค้าไม่เอา D-2026-0001", "any"), ("ข้อมูลดีล D-2026-0001", "ai"),
        ("ยอดขาย", "ai"), ("ยอดขายเดือนนี้", "ai"), ("ขายได้เท่าไหร่เดือนนี้", "ai"), ("สรุปยอดขาย", "ai"), ("pipeline", "any"),
        # the same numbers as a picture (owner, 8 Sep 2026) — no model call
        ("อยากดู report ยอดขายเป็นกราฟ", "ai"), ("ขอกราฟยอดขาย", "ai"), ("ยอดขาย 6 เดือนเป็นกราฟ", "ai"),
        ("กราฟดีลแต่ละสถานะ", "ai"), ("กราฟยอดขายรายเดือน", "ai"), ("สินค้าขายดี 5 อันดับ เป็นกราฟ", "ai"),
        ("ยอดขายรายคนเป็นกราฟ", "ai"), ("ขอแผนภูมิยอดขายหน่อย", "ai"), ("sales report as a chart", "ai"),
        ("show me a graph of monthly sales", "ai"), ("top products chart", "ai"),
        ("ดีลเกิน 10000", "ai"), ("ดีลของสมชาย", "ai"),
        # quotes
        ("ใบเสนอราคา", "ai"), ("ขอใบเสนอราคา", "any"), ("ทำใบเสนอราคาให้สมชาย", "ai"), ("ออกใบเสนอราคา D-2026-0001", "ai"),
        ("สร้างใบเสนอราคา D-2026-0001", "ai"), ("รายการใบเสนอราคา", "ai"), ("ใบเสนอราคาทั้งหมด", "ai"),
        ("ส่วนลด Q-2026-0001 500 บาท", "ai"), ("ลด 10%", "ai"), ("ส่งใบเสนอราคาให้ลูกค้า", "any"), ("ใบเสนอราคาของสมชาย", "ai"),
        # notes & reminders
        ("บันทึกว่า สมชายขอเลื่อน", "ai"), ("จดไว้ว่า สมชายขอเลื่อน", "any"), ("โน้ต: ลูกค้าขอส่วนลด", "ai"),
        ("เตือนพรุ่งนี้ 10 โมง โทรหาสมชาย", "ai"), ("พรุ่งนี้นัดสมชาย 10 โมง", "ai"), ("นัดสมชายวันศุกร์", "ai"),
        ("วันนี้มีนัดอะไรบ้าง", "any"), ("นัดหมายวันนี้", "any"), ("งานวันนี้", "rule"), ("วันนี้ต้องทำอะไร", "any"),
        ("พรุ่งนี้มีอะไร", "any"), ("อาทิตย์นี้มีนัดไหม", "any"), ("ดูนัดหมาย", "ai"), ("รายการเตือน", "ai"),
        # tickets on the sales OA
        ("รายการงาน", "rule"), ("งานซ่อม", "ai"), ("งานซ่อมมีอะไรบ้าง", "any"), ("มีงานซ่อมค้างไหม", "any"), ("งานซ่อมวันนี้", "any"),
        ("มอบหมาย T-2026-0001 ให้ สมศักดิ์", "ai"), ("ให้สมศักดิ์ไปทำงาน T-2026-0001", "any"), ("จ่ายงาน T-2026-0001 ทีมแอร์", "ai"),
        ("ใครว่างบ้าง", "any"), ("รายชื่อช่าง", "rule"), ("ช่างมีใครบ้าง", "ai"), ("ช่าง", "any"), ("ข้อมูลงาน T-2026-0001", "ai"),
        ("T-2026-0001", "any"), ("รายการรออนุมัติ", "rule"), ("มีอะไรรออนุมัติไหม", "any"),
        # shop, settings, language, permissions
        ("ข้อมูลร้าน", "rule"), ("รหัสร้าน", "ai"), ("รหัสร้านเราคืออะไร", "any"), ("ร้านเราชื่ออะไร", "ai"),
        ("ขอรหัสเชิญช่าง", "rule"), ("เพิ่มช่าง", "rule"), ("เชิญช่างเข้าร้าน", "any"), ("ตั้งค่า", "any"), ("ตั้งค่าร้าน", "any"),
        ("ข้อมูลบริษัท", "rule"), ("เปลี่ยนภาษา", "ai"), ("english", "any"), ("ภาษาอังกฤษ", "any"),
        ("สิทธิ์ของฉัน", "rule"), ("ฉันทำอะไรได้บ้าง", "help"), ("ผมมีสิทธิ์อะไรบ้าง", "any"), ("ทำอะไรกับลูกค้าได้บ้าง", "rule"),
        ("รายการสินค้า", "rule"), ("เพิ่มสินค้า พัดลม ราคา 1500", "ai"),
        # line items, the way the owner typed them (8 Sep 2026) — the deal in context has no lines yet
        ("ขอข้อมูลดีลล่าสุด", "ai"), ("ดีลล่าสุด", "ai"), ("สินค้าในดีล", "ai"), ("ดีลนี้", "ai"),
        ("มีสินค้าอะไรบ้างที่เป็น แอร์", "ai"), ("ค้นหาสินค้า แอร์", "ai"), ("มีแอร์อะไรบ้าง", "ai"),
        ("ลูกค้าสนใจอยากได้แอร์ 1 ตัว", "ai"), ("ไม่ใช่", "any"),
        ("ข้อมูลดีล D-2026-0001", "ai"),  # puts the deal in context for the lines below
        ("เพิ่ม ทีวี 40 นิ้ว ราคา 4000 ไปอีก 2 รายการ", "ai"), ("เพิ่มทีวี 40 นิ้ว อีก 1 ตัว", "ai"), ("เพิ่มอีก 1 ตัว", "ai"),
        ("แก้ทีวี 40 นิ้วเป็น 5 ตัว", "ai"), ("ลดทีวี 40 นิ้ว 2 ตัว", "ai"), ("ปรับราคาเป็น 3500", "ai"),
        ("เพิ่มพัดลม 18 นิ้ว 2 ตัว", "ai"), ("เป็นสินค้ารายการใหม่", "rule"), ("1500", "rule"),
        ("D-2026-0001 ลบสินค้าพัดลม 18 นิ้วออก", "ai"), ("เอาทีวีออก", "ai"),
        # reads phrased the way people phrase them (owner, 8 Sep 2026): each
        # of these was answered "ในแชทยังทำรายการนี้ไม่ได้" while the typed
        # form worked. The ones marked "ai" reach the model, which is fine —
        # what must not happen is the model understanding and the router
        # dropping it, and tests/unit/test_chat_read_intents.py holds that.
        ("ขอดูข้อมูลคุณสมชาย", "ai"), ("ดูข้อมูลลูกค้าสมชาย", "ai"), ("ขอดูข้อมูลของสมชาย", "ai"),
        ("show me Somchai's details", "ai"), ("open deal D-2026-0001", "ai"),
        ("ขอดูดีล D-2026-0001", "ai"), ("ขอดูใบเสนอราคาล่าสุด", "ai"),
        ("ขอดูใบเสนอราคา Q-2026-0001", "ai"), ("อยากเห็นบันทึกของ C-2026-0001", "ai"),
        ("อยากเห็นรายการรออนุมัติ", "ai"), ("อยากเห็นทีมที่ตั้งไว้", "ai"),
        ("อยากเห็นช่างที่อยู่ในร้าน", "ai"), ("อยากเห็นข้อมูลบริษัทที่บันทึกไว้", "ai"),
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
        ("งานพรุ่งนี้", "any"), ("งานวันนี้", "rule"), ("มีงานไหม", "ai"), ("งานที่เปิดรับ", "rule"), ("งานว่าง", "ai"),
        ("รับงาน T-2026-0002", "ai"), ("รับงานนี้", "any"), ("ผมรับงาน T-2026-0002 เอง", "ai"), ("ขอรับงาน", "any"), ("รับ", "any"),
        ("ไม่รับ", "any"), ("ไม่ว่าง ไปไม่ได้", "any"), ("ปฏิเสธงาน T-2026-0001 ป่วย", "ai"),
        ("ถึงหน้างานแล้ว", "ai"), ("ถึงบ้านลูกค้าแล้ว", "any"), ("มาถึงแล้วครับ", "ai"), ("อยู่หน้างานแล้ว", "any"),
        ("เช็คอิน", "rule"), ("check in", "ai"), ("เช็คอินงาน T-2026-0001", "ai"), ("เริ่มทำงานแล้ว", "any"),
        ("ลูกค้าบอกว่าถึงแล้วค่อยโทร", "any"),
        ("เสร็จแล้ว", "any"), ("งานเสร็จแล้ว", "any"), ("ทำเสร็จแล้วครับ", "any"), ("ปิดงาน", "rule"),
        ("ปิดงาน T-2026-0001\nพบ: คอมรั่ว\nแก้: เปลี่ยนคอม", "ai"), ("ซ่อมเสร็จแล้ว เปลี่ยนคอมเพรสเซอร์", "any"),
        ("ส่งรายงาน", "any"), ("รายงานของฉัน", "rule"), ("ข้อมูลงาน T-2026-0001", "ai"), ("T-2026-0001", "any"),
        ("งานนี้ที่อยู่ไหน", "any"), ("ลูกค้าเบอร์อะไร", "any"), ("ที่อยู่ลูกค้า", "any"),
        ("เช็คประกัน SN12345678", "ai"), ("SN12345678 ประกันหมดยัง", "any"), ("เครื่องนี้ยังมีประกันไหม", "any"),
        ("งานของทีม", "rule"), ("ทีมมีงานไหม", "any"),
        ("โปรไฟล์", "any"), ("ข้อมูลของฉัน", "rule"), ("แก้เบอร์เป็น 0899999999", "ai"), ("สิทธิ์ของฉัน", "rule"),
        ("เปลี่ยนภาษา", "ai"), ("เปลี่ยนร้าน", "any"),
        # the technician's own reads, phrased freely
        ("อยากเห็นรายละเอียดของงาน T-2026-0001", "ai"), ("อยากเห็นรายงานที่ผมส่งไปแล้ว", "ai"),
    ]
    await run("technician", c, cases)


async def customer():
    print("\n=== CUSTOMER OA ===")
    c = T.FakeDataClient(permission_keys=CUST_KEYS)
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "customer_chann_uid": "CHN-S-000001", "warranty_end": "2027-01-01"}]
    # The storefront the customer OA actually searches — `_products` is
    # the shop's own catalogue and a customer never sees it. Without these
    # rows "อยากซื้อแอร์" and "ค้นหา พัดลม" could only ever answer
    # "ไม่พบสินค้า": a correct reply to an empty shelf, and a case that
    # tested nothing (18 ก.ย. 2569).
    c._storefront_results = [
        {"id": "p1", "product_id": "AC12", "product_name": "แอร์ 12000 BTU",
         "unit_price": "15900.00", "license_id": "L1", "company_name": "ร้านชาญแอร์"},
        {"id": "p2", "product_id": "FAN01", "product_name": "พัดลมตั้งพื้น 16 นิ้ว",
         "unit_price": "890.00", "license_id": "L1", "company_name": "ร้านชาญแอร์"},
    ]
    cases = [(m, "help") for m in HELP_VARIANTS] + [(m, "greet") for m in GREETINGS] + SMALL_TALK
    faults = [
        "แอร์ไม่เย็น", "แอร์ไม่เย็นเลยค่ะ", "แอร์เสีย", "เครื่องซักผ้าไม่หมุน", "ตู้เย็นไม่เย็น", "ทีวีเปิดไม่ติด",
        "มีน้ำหยดจากแอร์", "แอร์มีเสียงดัง", "อยากให้ช่างมาดู", "ขอช่างมาซ่อมหน่อย", "ขอนัดช่าง", "ต้องการแจ้งซ่อม",
        "แจ้งซ่อมครับ", "ซ่อมแอร์", "ล้างแอร์", "อยากล้างแอร์", "แอร์เป่าลมไม่ออก", "รีโมทกดไม่ติด", "เครื่องทำน้ำอุ่นไม่ร้อน",
        "ประตูเลื่อนไม่ได้",
    ]
    # Each fault on its own client: a job opened by one case would make the
    # next fault ask "same matter?" (the guard added 14 ก.ย. 2569), which is
    # right for a person and wrong for a list of independent sentences.
    for m in faults:
        fresh = T.FakeDataClient(permission_keys=CUST_KEYS)
        fresh._warranties = list(c._warranties)
        await run("customer", fresh, [(m, "ai")])  # a fault is read by the model first since round 12
    # The status questions below need a job of the customer's own — the
    # faults used to leave one behind on this client; now they run apart.
    c._tickets = [{"id": "t-open", "ticket_number": "T-2026-0001", "status": "assigned", "customer_chann_uid": "CHN-S-000001",
                   "customer_name": "สมชาย", "issue_description": "ตู้เย็นไม่เย็น", "scheduled_date": "2026-09-20", "scheduled_time": "10:00"}]
    cases += [
        ("ช่างมาเมื่อไหร่", "any"), ("ช่างมากี่โมง", "any"), ("งานผมถึงไหนแล้ว", "any"), ("สถานะ", "rule"), ("เช็คสถานะงาน", "ai"),
        ("ซ่อมเสร็จยัง", "any"), ("งานของฉัน", "rule"), ("ประกัน", "any"), ("ประกันของฉัน", "rule"), ("เครื่องผมยังมีประกันไหม", "any"),
        ("หมดประกันเมื่อไหร่", "any"), ("ลงทะเบียน", "any"), ("ลงทะเบียนสินค้า SN12345678", "rule"), ("SN12345678", "any"),
        ("ราคาแอร์เท่าไหร่", "any"), ("มีแอร์รุ่นไหนบ้าง", "any"), ("อยากซื้อแอร์", "any"), ("สินค้า", "any"), ("ดูสินค้า", "rule"),
        # Round 20V: the receipt and the bills are answered (the fake holds none, so the
        # reply is "no invoices yet" — a real answer, not a shrug; the model reads them first,
        # the typed words answer when it shrugs — test_chat_phrasings pins the text). BEFORE "คุยกับร้าน":
        # once a conversation is open every later line is forwarded to the shop with an
        # empty reply, which the classifier would read as a pass that tests nothing.
        ("ขอใบเสร็จ", "ai"), ("ใบแจ้งหนี้ของฉัน", "ai"), ("ยอดค้าง", "ai"),
        ("ค้นหา พัดลม", "any"), ("คุยกับร้าน", "any"), ("คุยกับร้าน ราคาล้างแอร์", "any"), ("ขอคุยกับพนักงาน", "any"),  # storefront/live chat live in other modules the fake lacks
        ("ติดต่อร้าน", "rule"), ("เบอร์ร้าน", "rule"), ("ร้านเปิดกี่โมง", "any"), ("เลื่อนนัด", "rule"), ("เลื่อนนัดเป็นวันศุกร์", "rule"),
        ("ขอเลื่อนเป็นพรุ่งนี้", "rule"), ("ยกเลิก", "rule"), ("ยกเลิกงาน", "rule"), ("ไม่ซ่อมแล้ว", "any"),
        ("เปลี่ยนที่อยู่", "any"), ("แก้เบอร์เป็น 0899999999", "ai"), ("ที่อยู่ 99/1 สุขุมวิท", "any"),
        ("จ่ายเงินยังไง", "any"), ("โปรไฟล์", "rule"), ("ข้อมูลของฉัน", "rule"), ("เปลี่ยนภาษา", "rule"), ("ประวัติการซื้อ", "rule"),
    ]
    await run("customer", c, cases)


async def main():
    await sales(); await technician(); await customer()
    fails = [r for r in results if not r[5]]
    longs = [r for r in results if r[6]]
    # Two different things were being added together, and the sum was read
    # out loud as if it were one (18–20 ก.ย. 2569):
    #
    #   BAD ANSWER   the reply itself is a failure — "ยังไม่แน่ใจ", a
    #                permission list, "ไม่พบ", an apology. Something to fix.
    #   OTHER ROAD   the reply is fine (`ok`), it just came from the rule
    #                layer where the case is labelled `ai`, or the other
    #                way round. A label about intent, not a defect.
    #
    # Six cases of the second kind and three of the first read as "9 not
    # as expected", and I reported that number to the owner twice as if
    # every one of them were a problem. The headline line keeps its old
    # shape so the deploy gate's grep still matches; the number that
    # actually needs watching is on the line under it.
    bad = [r for r in fails if r[4] != "ok"]
    other_road = [r for r in fails if r[4] == "ok"]
    print(f"\n=== {len(results)} cases · {len(fails)} not as expected · {len(longs)} long replies ===")
    print(f"    of those: {len(bad)} answered badly · {len(other_road)} answered fine on the other road")
    by = {}
    for oa, msg, expect, layer, kind, ok, long, lines, chars, qr, text in fails:
        by.setdefault((kind != "ok", oa, expect, layer, kind), []).append(msg)
    for (is_bad, oa, expect, layer, kind), msgs in sorted(by.items(), reverse=True):
        mark = "!!" if is_bad else "  "
        print(f"  {mark} [{oa}] expected {expect}, got {layer}/{kind} ({len(msgs)}): "
              + " | ".join(m.replace(chr(10), ' ')[:24] for m in msgs[:12]))
    print("\nLONG replies (lines/chars):")
    # Grouped by the reply, not deduped into silence: the header counted
    # every long case while this list dropped the ones whose reply shared
    # a first line, so "2 long replies" printed one row and the second
    # phrasing was invisible (18 ก.ย. 2569).
    groups: dict[tuple, list] = {}
    for oa, msg, expect, layer, kind, ok, long, lines, chars, qr, text in longs:
        groups.setdefault((oa, text.splitlines()[0][:40], lines, chars), []).append(msg)
    for (oa, head, lines, chars), msgs in groups.items():
        tail = f" (+{len(msgs) - 1} more)" if len(msgs) > 1 else ""
        print(f"  [{oa}] {msgs[0][:24]:26} {lines}L/{chars}c  {head}{tail}")
        for extra in msgs[1:]:
            print(f"  [{oa}] {extra[:24]:26} ↑ same reply")
    if "--dump" in sys.argv:
        with open(sys.argv[sys.argv.index("--dump") + 1], "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps({"oa": r[0], "message": r[1], "expect": r[2], "layer": r[3], "kind": r[4], "ok": r[5], "lines": r[7], "chars": r[8], "quick_replies": r[9], "text": r[10]}, ensure_ascii=False) + "\n")

asyncio.run(main())
