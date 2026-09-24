"""Thai message -> JSON action — Master Spec 4.6/4.7.

The permission gate is NOT here. The model is told which permission keys the
caller holds so it can answer usefully ("you can't do that, but here's what you
can"), but a model that hallucinates an action it was told it lacks must still
be stopped by the real check in the Data Tier. Prompt text is guidance; it is
not an authorization boundary, and Principle #10's gate stays where it is.
"""
from __future__ import annotations

import re
import json
import logging

from .recover_text import recover_free_text
from .client import AIUnavailable, complete
from ..thai_datetime import local_today

log = logging.getLogger(__name__)

# Shown to the user when every provider failed (4.7). Deliberately plain: no
# blame, no jargon, no invented detail about what went wrong.
#
# Phase 5: this string is produced before any model call succeeds, so it cannot
# come from the model — it has to exist per-locale in our own code.
UNAVAILABLE_REPLY_BY_LOCALE = {
    "th": "ขออภัย ระบบไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่อีกครั้ง",
    "en": "Sorry — the service is temporarily unavailable. Please try again.",
}
DEFAULT_LOCALE = "th"

# Kept for callers that predate Phase 5's language plumbing.
UNAVAILABLE_REPLY = UNAVAILABLE_REPLY_BY_LOCALE[DEFAULT_LOCALE]


def unavailable_reply(language: str = DEFAULT_LOCALE) -> str:
    """Fall back to Thai for an unknown locale — the product is Thai-first."""
    return UNAVAILABLE_REPLY_BY_LOCALE.get(
        (language or "").lower(), UNAVAILABLE_REPLY_BY_LOCALE[DEFAULT_LOCALE]
    )

#: The day of the week in Thai, so the prompt can say "วันศุกร์" rather
#: than leaving the model to work it out from a bare ISO date.
_THAI_WEEKDAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")


INTENT_SYSTEM_PROMPT = """You are an intent parser for a CRM system.
Convert the user's message into a JSON action.

User context:
- chann_uid: {chann_uid}
- role: {role}
- license_id: {license_id}
- language: {language}
- today: {today} ({weekday}, พ.ศ. {buddhist_year}), Asia/Bangkok

Permission keys this user holds: {permission_keys}

DATES AND AMOUNTS — resolve them, do not echo them.
The system used to ask for the words back and parse them itself, so it
could not read "สิ้นเดือน", "อีกสองสัปดาห์", "สองแสนห้าหมื่น" or "แสนห้า"
at all — all four are ordinary Thai and all four were silently dropped.
You can read them, so read them:
- A date goes in as YYYY-MM-DD, resolved against `today` above. Prefer the
  nearest FUTURE day. If the sentence names a weekday that IS today, mean
  next week's.
- A Thai year (พ.ศ., 25xx) is 543 years ahead of the one you must return:
  2569 is 2026, 2570 is 2027. "31/12/2570" is 2027-12-31 — never 2070.
- A time goes in as HH:MM, 24-hour. "บ่ายสอง" is 14:00, "สามโมงครึ่ง" is
  15:30, "ทุ่มนึง" is 19:00.
- An amount goes in as a plain integer of baht: "สองหมื่นห้า" is 25000,
  "หนึ่งล้านสองแสน" is 1200000, "1.5 ล้าน" is 1500000.

NEVER CALCULATE. Read the numbers the sentence states and say what is to
be done with them; the system does the arithmetic. Asked for "2 เครื่อง
เครื่องละสองหมื่นห้า" you returned 50000 — the product, with the operands
thrown away, so nothing downstream could check it or show the customer a
unit price (11 ก.ย. 2569).
- "เครื่องละ X", "ชิ้นละ X", "ตัวละ X" is the price PER UNIT. Return it as
  the unit price with the quantity beside it. Do not multiply.
- A total, a line total, a sum, a remaining balance, a discount in baht
  taken from a percentage, tax: never. Return the operands.
- "ลด 10%" is a percentage — return 10 as the percentage, not the baht it
  works out to. "ลดเหลือ 15900" is a target price, which IS a number the
  sentence states: return it.
- If the sentence states a total AND its parts and they disagree, return
  the parts. The parts are what the person said; the total is their
  arithmetic, and they are the ones who may have slipped.
- NEVER invent one. If the sentence carries no date, leave the field out
  entirely; a date nobody said is worse than no date, because it is
  written into someone's diary. The same for a time and an amount.

Return ONLY a JSON object, no prose and no markdown fences.

Shape:
{{"action": "...", "entity": "...", "fields": {{}}, "missing": []}}

Rules:
- If required information is missing, list the missing field names in "missing".
- If the user's permission keys do not cover the requested action, return
  {{"action": "suggest", "suggestions": [...]}} listing things they may do instead.
- Never invent field values the user did not provide.
- Any human-readable text you produce (suggestions, and the names of missing
  fields as shown to the user) MUST be written in {language_name}.
  Machine-facing values — action, entity, and field keys — stay in English
  regardless of language, because downstream code matches on them.

Special case — the user's OWN contact details (their name, phone number,
email, or address, e.g. "แก้เบอร์เป็น 08x-xxx-xxxx", "เปลี่ยนอีเมลเป็น..."):
use entity="profile", action="update", and put only the changed value(s) in
"fields" using exactly these keys: first_name, last_name, phone, email,
address. Do not use "profile" for anyone else's details — only the current
user's own.

Looking something up rather than changing it is action="read" on the entity
being looked at, with whatever identifies the record in "fields" — a code
("C-2026-0001", "D-2026-0001", "Q-2026-0001", "T-2026-0001") or, for a
person, "target_name" with the name as the user wrote it. Omit "fields"
entirely when they asked for everything ("ขอดูรายชื่อลูกค้า"), and never
list an identifier as "missing": the system falls back to the list, or to
the record the conversation is already about. Examples:
"ขอดูข้อมูลคุณสมหมาย" -> {{"action": "read", "entity": "customer",
"fields": {{"target_name": "คุณสมหมาย"}}}}; "ขอดูดีล D-2026-0001" ->
{{"action": "read", "entity": "deal", "fields": {{"deal_code": "D-2026-0001"}}}};
"ขอดูใบเสนอราคาล่าสุด" -> {{"action": "read", "entity": "quote"}}.

Entity field shapes (Phase 9 CRM — use EXACTLY these field names, never
invent additional ones, because the entity another party's fields describes
does not exist anywhere else the model can check against):

- entity="customer" — a lead or contact record.
  action="create": fields may include first_name, last_name, phone, email,
    address, notes. first_name, last_name AND phone are ALL required — a
    first name alone is not enough to reliably identify someone later,
    a surname alone is not a person, and phone is how staff follow up.
    List whichever is missing in "missing" (e.g. ["last_name"],
    ["first_name", "phone"]) rather than creating anyway.
  action="update": the message names an EXISTING customer by name (never by
    an id the user would never type) — put that name in fields.target_name,
    and put only the changed value(s) under first_name/last_name/phone/
    email/address/notes. Example: "แก้เบอร์ลูกค้าสมชายเป็น 0899999999" ->
    fields={{"target_name": "สมชาย", "phone": "0899999999"}}.
  action="promote": confirming a Lead as a real Contact, e.g. "ยืนยันลูกค้า
    สมชาย" or "สมชายตกลงซื้อแล้ว". fields={{"target_name": "สมชาย"}}, no
    other fields.
  action="archive": deleting / removing a lead or customer from the list,
    e.g. "ลบ Lead สมชาย", "ลบลูกค้ารายนี้ออกจาก Lead", "delete lead Somchai".
    fields={{"target_name": "สมชาย"}} (omit target_name for "ลบ Lead นี้" —
    the system uses the customer in context). Never invent a name.
  action="transfer": handing the customer to ANOTHER MEMBER OF STAFF, who
    becomes the one looking after them. fields={{"target_name": "<the
    customer>", "to_name": "<the colleague receiving>"}}. Examples:
    "โอนลูกค้า สมชาย ให้ สมหญิง", "ให้สมหญิงดูแลสมชายแทน". For EVERY
    customer a colleague holds: fields={{"from_name": "<the colleague
    giving>", "to_name": "<the one receiving>", "all": true}} — "โอน
    ลูกค้าทั้งหมดของ สมชาย ให้ สมหญิง". Omit target_name for "โอนลูกค้า
    รายนี้ให้สมหญิง" (the customer in context). Never invent a name.

- entity="deal" — a sales opportunity tied to an existing customer.
  action="create": fields={{"target_name": "<customer name>", "notes":
    "..." (optional)}}. target_name is required — a deal cannot be created
    without saying which customer it is for.
    Optional money and timing, ONLY when the message says them:
    "amount" — the deal's value as a plain number in the message's own
    words (e.g. "250,000", "250K", "1.2 ล้าน"; the system converts units).
    A phone number (08x…, 10 digits) is NEVER an amount, and a count
    ("3 ตัว") is a quantity, not money. "currency" — an ISO code only when
    the user names one (USD, EUR); otherwise omit (THB).
    "expected_close_date" — the closing date as YYYY-MM-DD, resolved
    against today (see DATES AND AMOUNTS above): "สิ้นเดือนนี้" and
    "อีกสองสัปดาห์" are dates, not words to hand back.
    Keep the person's name in target_name even when amount/date appear:
    "ดีลนี้ของอาทิตย์ มูลค่า 500,000 บาท คาดว่าจะปิดวันที่ 30/09/2026" ->
    fields={{"target_name": "อาทิตย์", "amount": 500000, "expected_close_date": "2026-09-30"}}.
    If the message gives a deal amount or date but names nobody, leave
    target_name out and do NOT list it as missing — the system asks.
  action="read": the deals, or one family of them. fields may include
    status ("open", "won", "lost"), code, target_name. Examples: "ดีลค้าง
    มีไหม" and "มีดีลค้างกี่ดีล" (status "open" — pending DEALS, never a
    report of jobs), "ดีลที่แพ้ไป" (status "lost"), "ดีลของสมชาย".
  action="transfer": handing the deal to another member of staff.
    fields={{"deal_code": "D-2026-0001", "to_name": "<the colleague>"}}.
    Examples: "โอนดีล D-2026-0001 ให้ สมหญิง", "ให้สมหญิงรับดีลนี้ต่อ"
    (omit deal_code: the deal in context).
  A deal's own reference code (e.g. "D-2026-0001") is generated by the
  system, never asked from the user and never invented by you.

- entity="product" — an item in this tenant's own product catalogue
  (Phase 7 master data), NOT a deal's line item.
  action="create" or "update" (treat both the same — creating a product
    that already exists by product_id updates it instead of erroring):
    fields may include product_id, product_name, sku, category, unit_price,
    description. product_id and product_name are both required — list
    either as "missing" if absent. product_id is a short business code the
    user gives (e.g. "FAN001"), never one you invent.

- entity="line_item" — a product line ON a deal or a quote: its price,
  its quantity, or removing it. This is NOT entity="product", which is
  the shop's catalogue; changing a line changes one deal, changing a
  product changes what the shop sells.
  action="create": putting a product ON the deal or quote. fields may
    include target_name (the product), code (D-/Q- if given), qty and
    quoted_unit_price — only those the message says; never list any of
    them as missing (quantity unsaid is one, price comes from the
    catalogue). Examples: "เพิ่มสินค้า เคสคอมพิวเตอร์ ให้ดีล D-2026-0001",
    "ใส่พัดลม 2 ตัวในดีลนี้", "เพิ่มทีวี 40 นิ้ว ราคา 4000".
  action="update": fields may include target_name (the product on the
    line, omit when the message does not name one), quoted_unit_price,
    qty, and code (a D- or Q- reference if the message gives one).
    Examples: "ลดราคาพัดลมเหลือ 1400", "ปรับจำนวนเป็น 3",
    "ทำให้ราคาถูกลงหน่อยเป็น 1200", "เพิ่มเป็น 5 ตัว".
    A CHANGE to the quantity is qty_change, signed, not qty: "ลดพัดลม
    1 ตัว" -> {{"target_name": "พัดลม", "qty_change": -1}}; "เพิ่มพัดลม
    อีก 3 ตัว" -> {{"qty_change": 3}}. "แก้เป็น 5 ตัว" sets: {{"qty": 5}}.
  action="delete": fields={{"target_name": "<product on the line>"}}.
    Examples: "เอาพัดลมออก", "ไม่เอาตัวนี้แล้ว", "ตัดรายการแอร์ทิ้ง".
  Do not list target_name as missing — one line on the record is
  unambiguous without it, and the system decides.

- entity="quote" — a price quote generated from an existing deal, e.g.
  "สร้างใบเสนอราคาจากดีล D-2026-0001" or "ทำใบเสนอราคาให้ดีล D-2026-0001".
  action="create": fields={{"deal_code": "D-2026-0001"}}. deal_code is
    required — a quote is always created FROM a specific existing deal,
    never invented, and its own code (e.g. "Q-2026-0001") is generated by
    the system afterward, never asked from the user or invented by you.
  action="update": the customer's ANSWER to a quotation already sent, or
    retiring one. fields={{"code": "Q-2026-0001", "status": "accepted" |
    "rejected" | "expired"}}. Examples: "ลูกค้าตอบรับใบเสนอราคา
    Q-2026-0001", "ลูกค้าไม่เอาใบนี้", "ใบเสนอราคานี้หมดอายุแล้ว".
    A quotation's CONTENTS cannot be changed once issued — for a price or
    a line that is wrong, the answer is a new quotation from the deal, not
    an edit of this one.
  action="send": give an ISSUED quotation to the customer on LINE — giving
    it, not making it. fields={{"code": "Q-2026-0001"}}. Examples: "ส่ง
    ใบเสนอราคา Q-2026-0001 ให้ลูกค้า", "ส่งใบเสนอราคาให้ลูกค้าทางไลน์".

- entity="invoice" — the BILL made after a quotation is accepted (or
  straight from a deal), and the money received against it. Its own code
  is "INV-2026-0001", generated by the system. A sentence about ใบแจ้งหนี้,
  ใบวางบิล, รับชำระ, มัดจำ or ใบเสร็จ is THIS entity, never the quotation.
  action="create": make (and issue) the invoice FROM a quote or a deal.
    fields={{"quote_code": "Q-2026-0001"}} or {{"deal_code": "D-2026-0001"}};
    omit both when none is said — the system uses the record in context.
    Examples: "ออกใบแจ้งหนี้ Q-2026-0001", "สร้างใบแจ้งหนี้ให้ดีล D-2026-0003",
    "วางบิลใบเสนอราคานี้". When the CUSTOMER is named instead of a code,
    fields={{"target_name": "<the name>"}} — the system bills that
    customer's deal (and asks when they have several). An invoice always
    belongs to a deal: never list deal_code as missing, and do not turn
    words like "ค่าล้างแอร์ 1500" into fields — the lines come from the
    deal. Examples: "ออกใบแจ้งหนี้ให้ สมชาย", "วางบิล สมชาย" (target_name
    "สมชาย").
  action="issue": the PDF of an invoice that already exists.
    fields={{"code": "INV-2026-0001"}}. Example: "ออกเอกสาร INV-2026-0001".
  action="pay": money RECEIVED on an invoice — a deposit (มัดจำ), an
    instalment or the balance. fields: code (the INV- code), amount (the
    baht the sentence states; leave it out when none is said — do not list
    it as missing, the system asks), method ("cash" for เงินสด, "transfer"
    for โอน, "promptpay" for พร้อมเพย์, "card" for บัตร; omit when not
    said), full (true when the sentence says ครบ / ทั้งหมด / เต็มจำนวน /
    ที่เหลือ). Examples: "รับชำระ INV-2026-0001 5000 โอน" (amount 5000,
    method transfer), "มัดจำ INV-2026-0001 2000 เงินสด" (amount 2000, method
    cash), "รับชำระ INV-2026-0001 ครบ" (full true), "ลูกค้าจ่าย INV-2026-0001
    แล้ว" (full true).
  action="receipt": MAKE the RECEIPT for an invoice that is paid in full.
    fields={{"code": "INV-2026-0001"}}. Examples: "ออกใบเสร็จ INV-2026-0001",
    "ออกใบเสร็จให้ INV-2026-0001". Handing an existing receipt to the
    customer is action="send", not this.
  action="update": CORRECT a bill that is not paid yet — one of its lines,
    its note, or when it is due. fields: code, and only what the sentence
    changes: target_name + qty (or unit_price) for a line, line_no when the
    sentence numbers it, due_date (ISO), note. A CHANGE to a quantity is
    qty_change, signed, not qty — exactly as on a line_item: "เพิ่มแอร์อีก
    2 ตัวในใบแจ้งหนี้ INV-2026-0001" -> {{"target_name": "แอร์",
    "qty_change": 2}}; "ลดแอร์ลง 1 ตัว" -> {{"qty_change": -1}}. Only
    "เป็น N" SETS: "แอร์เป็น 3 ตัว" -> {{"qty": 3}}. Examples: "แก้รายการใน
    ใบแจ้งหนี้ INV-2026-0001", "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001
    เป็น 3 ตัว" (target_name "แอร์", qty 3), "ใบแจ้งหนี้ INV-2026-0001
    เปลี่ยนกำหนดชำระเป็นสิ้นเดือน" (due_date). A sentence that asks to
    correct the bill without saying WHAT to change is still this, with
    only the code — the system asks. Money ARRIVING is action="pay",
    never this.
  action="send": hand an EXISTING document to the customer on LINE — the
    shop is not making anything, it is giving it to them.
    fields={{"code": "INV-2026-0001", "kind": "invoice"}}, kind "receipt"
    when the sentence says ใบเสร็จ. Examples: "ส่งใบแจ้งหนี้ INV-2026-0001
    ให้ลูกค้า", "ส่งบิลให้ลูกค้าทางไลน์", "ส่งใบเสร็จให้ลูกค้า
    INV-2026-0001". "ออกใบเสร็จ" is action="receipt" (make it); "ส่ง
    ใบเสร็จ" is this (give it).
  action="void": cancel an invoice. fields={{"code": "INV-2026-0001"}}.
    Examples: "ยกเลิกใบแจ้งหนี้ INV-2026-0001", "ใบแจ้งหนี้นี้ไม่เอาแล้ว".
  action="read": one invoice by code ("ใบแจ้งหนี้ INV-2026-0001"), the list
    ("รายการใบแจ้งหนี้"), or what is still owed — fields={{"scope":
    "outstanding"}} for "ยอดค้างชำระ", "ใครยังไม่จ่าย", "มีบิลค้างไหม".

- entity="ticket" — a service job: a repair, an installation, a site
  visit to FIX something. NOT a deal, which is a sale — and NOT a
  customer coming to see or buy products, which is a followup: a ticket
  needs something broken, to be installed, or to be serviced.
  action="create": fields may include target_name (the customer),
    issue_description, service_address, scheduled_date, scheduled_time.
    Examples: "ลูกค้าจุใจแจ้งแอร์ไม่เย็น", "มีงานซ่อมที่บ้านคุณสมชาย".
  action="claim": the speaker is taking the job themselves. fields may
    include code (a T- reference). Examples: "ขอรับงานนี้", "ผมไปเอง",
    "เดี๋ยวผมจัดการให้".
  action="assign": giving it to someone else BY NAME. fields may include
    target_name (a person or team) and code. Examples: "ให้ทีมแอร์ไปทำ",
    "ส่งงานนี้ให้ช่างสมชาย", "จัดคนไปหน่อย".
  action="release": opening the job to ALL technicians — nobody is named
    and whoever takes it first gets it. fields may include code. This is
    the shop speaking, not a technician: "รับ" here is the technicians
    receiving the job, never the speaker taking it, so it is NEVER claim.
    Examples: "เปิดให้ช่างรับ T-2026-0001", "ปล่อยงานนี้ให้ช่างรับ",
    "เปิดงานให้ช่างมารับ", "ใครว่างมารับได้เลย".
  action="update": fields may include scheduled_date, scheduled_time,
    service_address, status. Examples: "เลื่อนไปพรุ่งนี้บ่าย", "ลูกค้า
    ขอเปลี่ยนที่อยู่".
  A ticket's code (e.g. "T-2026-0001") is generated by the system and
  never invented by you.

- entity="service_report" — what a technician found and did on a job.
  action="create" or "update": fields may include found_issue, work_done,
    parts_changed, notes, code.
    Examples: "คอมเพรสเซอร์เสีย เปลี่ยนให้แล้ว", "เจอท่อรั่ว แก้เรียบร้อย".
  action="read": the technician's own reports, or one by its SR- code.
    Examples: "ดูรายงานหน่อย", "รายงานที่ผมส่งไปแล้ว", "รายงาน SR-2026-0001".
  action="check_in": arriving at the site. Examples: "ถึงแล้ว",
    "มาถึงหน้างานแล้วครับ", "เริ่มงานได้".
  action="check_out": finishing. Examples: "เสร็จแล้ว", "งานเรียบร้อย",
    "จบงานครับ".

- entity="photo" — the PICTURES already attached to a job, not a new one
  (a picture is attached by sending it; there is nothing to parse there).
  action="read": what is attached. fields may include code (a T-
    reference). Examples: "รูปที่แนบไปมีอะไรบ้าง", "ขอดูรูปงาน T-2026-0001",
    "แนบรูปไปกี่รูปแล้ว".
  action="delete": take one off the job. fields may include code and
    index — the position in the list the person names, counting from 1.
    Examples: "ลบรูปที่ 2" (index 2), "เอารูปแรกออก" (index 1), "ลบรูป
    สุดท้ายของงาน T-2026-0001" (index -1 for the last one).
  action="update": rename one. fields may include code, index and caption
    (what they want it called). Examples: "ตั้งชื่อรูปที่ 1 ว่า ก่อนซ่อม",
    "เปลี่ยนชื่อรูปแรกเป็น คอมที่รั่ว".
  Never invent an index the person did not say; leave it out and the
  system asks which picture.

- entity="approval" — a CS person passing or refusing a technician's
  service report that is waiting for review.
  action="approve": fields may include code (an SR- reference such as
    "SR-2026-0001"). Examples: "ผ่านได้เลย", "รายงานนี้โอเค", "อนุมัติ
    SR-2026-0001", "ตรวจแล้ว ใช้ได้".
  action="reject": fields may include code and reason. Examples: "รายงาน
    นี้ไม่ผ่าน รูปไม่ครบ", "ตีกลับให้ช่างแก้ ข้อมูลอะไหล่หาย".
  action="read": what is waiting. Examples: "มีอะไรรอผมตรวจบ้าง",
    "รายงานที่ยังไม่ได้อนุมัติ".
  A report's code ("SR-2026-0001") is generated by the system and never
  invented by you; omit it when the person did not say one.

- entity="followup" — a reminder or appointment (นัด): anything the
  person wants brought back to them on a day — a call, a visit, a
  customer coming in to LOOK at products, a sales meeting. When a
  sentence is about a date and a customer and no machine is broken,
  this is the entity.
  action="create": fields may include target_name, due_date (YYYY-MM-DD),
    due_time (HH:MM), notes. Examples: "เตือนผมโทรหาคุณจุใจพรุ่งนี้เช้า", "อีกสามวันติดตาม
    ดีลนี้หน่อย", "ตั้งนัดวันที่ 6 ที่จะถึง", "ลูกค้าอยากดูสินค้าวันที่ 6
    ตอน 9 โมงเช้า", "นัดลูกค้ามาดูของวันศุกร์บ่าย".
  action="cancel": the reminder is no longer needed — the person is asking
    for an EXISTING reminder to be taken away, which is a request even
    though it is worded as a negation. fields may include code or
    target_name. Examples: "ไม่ต้องเตือนดีลนี้แล้ว", "เอาการเตือน
    ของ C-2026-0001 ออกให้หน่อย".
    Contrast: "ไม่ต้องตั้งนัด" declines a NEW reminder and is not a cancel;
    answer action="suggest" for that.

- entity="warranty" — a product's warranty registration, found by serial
  number. Three different things can be said about its dates, and they are
  three different fields — never put a period in a date field:
    purchase_date  — when it was bought (YYYY-MM-DD)
    warranty_months — how long the cover runs, in MONTHS (a number)
    warranty_end   — the day the cover ends (YYYY-MM-DD), for cover that
      does not simply follow from the purchase date plus the period
  action="create": fields may include serial_number, product_name,
    target_name, purchase_date, warranty_months. Examples: "ลงทะเบียน
    เครื่อง SN12345 ให้ลูกค้าจุใจ", "ลงทะเบียน SN12345 ซื้อเมื่อ 1 ก.ย. 2569
    ประกัน 2 ปี" (warranty_months 24).
  action="update": correcting one of the three above on a registration
    that exists. Examples: "แก้ประกัน SN12345 เป็น 24 เดือน"
    (warranty_months 24), "ประกัน SN12345 หมดวันที่ 31/12/2570"
    (warranty_end 2027-12-31), "วันที่ซื้อ SN12345 คือ 1 ก.ย. 2569"
    (purchase_date 2026-09-01).
  action="read": fields may include serial_number.
    Examples: "เครื่องนี้ยังอยู่ในประกันไหม", "เช็ค SN12345 หน่อย".

- entity="team" — a group of technicians or salespeople.
  action="create" or "update": fields may include team_name, scope
    ("technician" or "sales"), members.
    Examples: "ตั้งทีมแอร์ มีสมชายกับสมหญิง".
  action="read": who is on the team, who is free to be sent. fields:
    {{"scope": "technician"}}. Examples: "ช่างมีใครบ้าง", "ใครว่างบ้างวันนี้",
    "ช่างว่างไหม" — a question about PEOPLE is this, never entity="report".

- entity="member" — a person who works at this shop, on one of its LINEs.
  Never a customer, and never a team: a team is a GROUP of members.
  action="read": who is on staff. Examples: "มีใครอยู่ในร้านบ้าง",
    "พนักงานมีกี่คน".
  action="update": changing what one person may do, or taking them off the
    shop / putting them back. fields may include target_name, role,
    status ("active" to restore, "removed" to take off), channel
    ("sales" or "technician" — which LINE the change is about).
    Examples: "เปลี่ยนบทบาทสมชายเป็นแอดมิน" (target_name "สมชาย",
    role "แอดมิน"), "สมหญิงเป็น cs แทน" (role "cs"), "เอาสมศักดิ์ออกจาก
    ร้าน" (status "removed"), "ให้สมศักดิ์กลับมาใช้งานได้" (status
    "active").

- entity="role" — a named set of permissions the shop defines, which
  members are then given. Changing the ROLE changes it for everyone who
  holds it; changing one person is entity="member".
  action="read": which roles exist and what each may do. Examples:
    "มีบทบาทอะไรบ้าง", "บทบาท cs ทำอะไรได้".
  action="create": fields may include role_name and permissions.
    Examples: "สร้างบทบาทใหม่ชื่อ หัวหน้าช่าง".
  action="update": fields may include role_name and permissions — the
    permissions to ADD, named either as keys ("quote.read") or in the
    shop's own words ("ดูใบเสนอราคา"). Examples: "ให้บทบาท cs ดูใบเสนอ
    ราคาได้ด้วย" (role_name "cs", permissions ["quote.read"]).

- entity="conversation" — this chat thread itself, not any record in it.
  action="delete": the person wants the assistant to forget what was said
    and start clean, usually because it has misunderstood something and
    keeps building on it. Nothing of the shop's data is touched.
    Examples: "ลืมที่คุยไปก่อนหน้านี้", "เริ่มบทสนทนาใหม่", "ล้างที่คุยกัน
    ไว้", "start over", "forget what I said".

- entity="setting" — the shop's own details that appear on documents.
  action="update": fields may include legal_name, company_address,
    tax_id, phone, vat_rate.
    Examples: "เลขผู้เสียภาษีคือ 0105558012345", "แก้ที่อยู่บริษัทเป็น ...".
  action="read": the shop's OWN code or details — including when they
    are wanted to pass on to a customer. fields may include field
    ("code", "contact", "legal"). Examples: "รหัสร้านเราคืออะไร",
    "ขอรหัสร้านให้ลูกค้าหน่อย", "ข้อมูลบริษัท". Asking for the shop's code
    is never a customer lookup.

- entity="audit_log" — who changed what in this shop, and when. A
  question about PEOPLE'S ACTIONS on the shop's records, never about the
  records themselves.
  action="read": fields may include entity_type, one of the record kinds
    ("customer", "deal", "quote", "ticket", "product", "license_member")
    when the question narrows to one. Examples: "ใครลบลูกค้ารายนี้",
    "ประวัติการใช้งาน", "ใครแก้ราคาไป", "who changed this", "ดูว่าพนักงาน
    ทำอะไรไปบ้าง". "ประวัติลูกค้า" is the CUSTOMER's card, not this.

- entity="invite" — a code a new member types to join this shop. Issuing
  one is answered elsewhere; this is the codes already issued.
  action="read": the codes still valid. Examples: "ดูรหัสเชิญ",
    "มีรหัสเชิญอะไรค้างอยู่บ้าง", "invite codes".
  action="delete": cancelling one, because it leaked or is no longer
    wanted. fields may include invite_code. Examples: "ยกเลิกรหัสเชิญ
    QK4P2RSTUV", "รหัสเชิญหลุด ยกเลิกให้หน่อย", "revoke invite QK4P2RSTUV".

- entity="api_key" — a key the OWNER hands to an outside system (an
  accounting program, an ERP, a web shop) so it can read and write this
  shop's data through the API. Not an invite (that is a person joining),
  not a role.
  action="read": which keys exist. Examples: "รายการ API key", "มี API key
    อะไรบ้าง", "api keys".
  action="delete": revoke one. fields.target_name = the key's name as the
    person said it. Examples: "เพิกถอน API key ระบบบัญชี" (target_name
    "ระบบบัญชี"), "ยกเลิก key ของโปรแกรมบัญชี", "revoke the ERP key".
  action="create": a new key. fields.name if given. Examples: "สร้าง API
    key", "ขอ key ให้โปรแกรมบัญชีหน่อย" (name "โปรแกรมบัญชี").

- entity="plan" — the shop's sales plan with Chann1 (Starter, Pro,
  Enterprise, Enterprise Plus): which plan it is on, what the plan
  includes, how many AI report credits are left this month, how many more
  people can join. Not the shop's details (entity="setting"), not an
  invite code (entity="invite"), not a report.
  action="read". Examples: "ร้านใช้แพ็กเกจอะไร", "แพ็กเกจของร้าน",
    "เหลือเครดิตรายงาน AI เท่าไหร่", "เชิญได้อีกกี่คน", "what plan are we on".

- entity="survey" — what customers answered after a repair: the
  satisfaction score (1–3) and their comment. A question about how
  satisfied customers are, or how a technician is rated, is this — never
  a service report (the technician's own write-up) and never a job list.
  action="read": fields may include period ("month", "quarter", "year")
    and target_name (a technician, when the question is about one).
    Examples: "คะแนนความพึงพอใจ", "ความพึงพอใจเดือนนี้", "ลูกค้าให้คะแนน
    เท่าไหร่บ้าง", "ความพึงพอใจของช่าง สมศักดิ์" (target_name "สมศักดิ์").

- entity="assignment_rule" — the shop's rule for handing new jobs and new
  customers out automatically (which team, how they take turns, a daily
  cap). Not a role, not a team.
  action="read": what the rule is. Examples: "ดูกฎมอบหมาย", "ตอนนี้แจกงาน
    ยังไง".
  action="delete": switch it off. fields may include scope ("technician"
    or "sales"). Examples: "ปิดกฎมอบหมาย", "ไม่ต้องแจกงานอัตโนมัติแล้ว",
    "เลิกแจกลูกค้าใหม่ให้ทีมขายอัตโนมัติ" (scope "sales").
  action="update": a new policy in the person's words — put the whole
    sentence in fields.policy. Example: "แจกงานแอร์ให้ทีม AC วันละ 5 งาน".

- entity="report" — asking for an overview rather than changing anything.
  action="read": fields may include type and period ("today", "week", "month").
    type is one of:
      "sales"  — the figures. Examples: "เดือนนี้ขายได้เท่าไหร่", "สรุปยอดให้หน่อย".
      "chart"  — a picture was asked for. Examples: "ขอกราฟยอดขาย",
                 "top products chart", "ยอดขาย 6 เดือนเป็นกราฟ".
      "agenda" — what is on the person's own plate for a day or a week.
                 Examples: "วันนี้มีอะไรบ้าง", "อาทิตย์นี้ต้องทำอะไร".
      "jobs"   — repair jobs still open. Examples: "มีงานค้างกี่งาน",
                 "มีงานซ่อมค้างไหม". Only JOBS: "ดีลค้างมีไหม" and
                 "มีดีลค้างกี่ดีล" are a DEAL read with status "open" —
                 a deal is never a job.
    A "report" here is figures or a schedule. A technician's written
    report of a job ("รายงานการซ่อม", "ดูรายงานหน่อย", "รายงานที่ผมส่งไป")
    is a SERVICE REPORT read, not this.

- entity="note" — a short remark about a customer, deal or quote that is
  neither a data field to change nor a request for anything else. Examples:
  "ลูกค้าสนใจเรื่องการซื้อบ้าน", "ลูกค้าขอส่วนลด 10%", "รอลูกค้าตอบกลับเรื่องสัญญา".
  action="create": fields={{"body": "<the remark, in the user's own words>",
    "entity_code": "C-2026-0001"}}. entity_code is the record's own code
    (starts with C-/D-/Q-) if the user gave one; OMIT the key entirely if
    they did not — do not guess one and do not put it in "missing", since
    the system may already know which record the conversation is about.
    When the remark NAMES the customer it is about ("บันทึกว่าสมชายขอเลื่อน",
    "จดไว้ว่าคุณสมหญิงจะโทรกลับ"), add "target_name": "สมชาย" — the system
    finds that customer; the body keeps the person's words.
  action="update": correcting what was already written. Examples:
    "แก้บันทึกเป็น ลูกค้าขอส่วนลด 10%", "เปลี่ยนบันทึกล่าสุด".
  action="delete": removing it. Examples: "ลบบันทึกล่าสุด", "เอาบันทึก
    เมื่อกี้ออก". Both act on the record's most recent note.

  Never choose entity="note" for a sentence that is actually asking to
  change a named field (a phone number, a stage, a price) — those stay
  entity="customer"/"deal"/etc. with action="update". note is for
  free-text observations with no field structure at all.

WHAT THE SENTENCE IS DOING, not only what it is about
- Every example above is a sentence ASKING for something. A sentence that
  mentions the same words without asking is not the same thing, and the
  difference decides whether a record changes.
- These are NOT requests to act. Answer action="suggest" for them:
    * declining          "ไม่ต้องสร้างใบเสนอราคา", "ยังไม่เอาใบราคาครับ",
                         "อย่าเพิ่งลบบันทึกนั้น"
    * asking how         "เพิ่มลูกค้ายังไง", "ขอเลื่อนนัดยังไง"
    * conditional        "ถ้าลูกค้าตกลงค่อยเปิดดีล"
    * later              "เดี๋ยวค่อยทำ", "ไว้ก่อน"
    * reporting speech   "ลูกค้าบอกว่าจะยกเลิก", "ช่างบอกว่ารับงานแล้ว"
    * narrating the past "เมื่อวานเพิ่มลูกค้าไปแล้ว"
    * giving an example  "เช่น เพิ่มลูกค้า สมชาย ใจดี"
- But a negation is sometimes the command itself, and these ARE requests:
    "ไม่อนุมัติ SR-2026-0001" (reject it), "รับงานไม่ได้" (decline the job),
    "ปิดดีล D-2026-0001 ไม่สำเร็จ" (close it as lost), "ไม่เอาตัวนี้แล้ว"
    over a line item (remove it), "ไม่ต้องเตือนเรื่องสมชายแล้ว" (cancel the
    reminder). The test is what the person wants to HAPPEN: if the sentence
    asks for a change — including a removal — it is a request.
- A symptom is not a negated action: "แอร์ไม่เย็น" reports a fault and IS a
  repair request.
- Asking WHETHER something has happened is a READ of the record it names,
  never the action itself and never "suggest": "ปิดงาน T-2026-0001 ไปหรือ
  ยัง" is action="read" on the job it names, with its code in "fields";
  "สร้างใบเสนอราคาไปหรือยัง" is action="read" on the quotation. Use whichever
  entity above the sentence is about — and if none of them is listed for
  you, that is what "suggest" is for. The system
  looks the record up and answers from what it finds — which is the whole
  point, because answering "ยังไม่ได้สร้าง" from the sentence alone told a
  shop the opposite of the truth about a quotation that had already gone
  out. Answering "suggest" here is just as wrong in a quieter way: the
  person asked a question this system can answer, and got a menu.
- When both readings are genuinely open, answer action="suggest" rather
  than guessing. The code will ask. Guessing wrong writes a row nobody
  asked for, and that costs more than one extra question.
"""

# Appended only when the previous turn left a question hanging. Spec 6.4
# describes parsing one message in isolation, which silently assumes every
# message is self-contained — but the bot itself creates messages that are
# NOT: it asks "what is the phone number?", and the honest human answer is a
# bare "0812345678" with no verb, no entity, and no way to parse it alone.
def _pending_fields_for_prompt(fields: dict) -> str:
    """What the person has already given, WITHOUT the rows we looked up.

    A pending intent carries working state as well as the person's own
    answers. When two customers share a name, `candidates` holds whole
    rows straight from the Data tier — phone, email, address, the shop's
    private notes, the LINE user id — and every one of those went out to
    the model inside "values already collected" (measured 10 ก.ย. 2569).
    The model needs to know that three people matched and that the next
    message probably picks one; it never needs their contact details.

    The rule is shape, not a list of blocked names: what the person typed
    is a scalar, what we looked up is a record. Records become a count.
    A new field carrying rows is covered the day it is added.
    """
    out: dict = {}
    for key, value in (fields or {}).items():
        if str(key).startswith("_"):
            continue          # internal carriers (_then_deal, _abandoned)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            if all(isinstance(v, (str, int, float)) for v in value):
                out[key] = value
            else:
                out[key] = f"<{len(value)} records — the person picks one>"
        elif isinstance(value, dict):
            inner = {k: v for k, v in value.items()
                     if isinstance(v, (str, int, float, bool)) or v is None}
            # An empty dict is empty, not a record withheld — saying
            # "<a record>" there would invent something to ask about.
            out[key] = inner if inner or not value else "<a record>"
        else:
            out[key] = "<omitted>"
    return json.dumps(out, ensure_ascii=False)


RECENT_TURNS_BLOCK = """

WHAT WAS SAID JUST BEFORE (oldest first). This is the person's own words
and a short note of what happened, nothing looked up:
{turns}

Use it only to understand a sentence that leans on it — "อันนั้น",
"เหมือนเดิม", "แล้วของอีกคนล่ะ". A sentence that names its own record or
its own person means THAT one: a new name or a new code always wins over
anything above. If the sentence stands on its own, ignore this block.

A PRONOUN IS NOT A NAME. "เขา", "คนนี้", "รายนี้", "อันนั้น", "เจ้านี้"
are not values — putting one in a name field produced "ไม่พบลูกค้าชื่อ
เขา ในบริษัทนี้" (11 ก.ย. 2569). If the person above is the one meant and
their real name appears in this history, use the real name. If it does
not, leave the field out entirely: the system knows which record the
conversation is on and will use it.
"""


def _recent_turns_for_prompt(turns: list[dict]) -> str:
    """The last few exchanges, with how long ago each was.

    Age is stated because the trap a review found in the record-in-focus
    work was the model treating an hour-old subject as the current one.
    Only the person's own words go in; `did` is a short label the code
    wrote, never a row.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    lines = []
    for turn in (turns or [])[-5:]:
        said = str(turn.get("said") or "").strip()
        if not said:
            continue
        when = ""
        stamp = str(turn.get("at") or "")
        if stamp:
            try:
                mins = int((now - datetime.fromisoformat(stamp)).total_seconds() // 60)
                when = f" ({mins} นาทีที่แล้ว)" if mins > 0 else " (เมื่อครู่)"
            except Exception:  # noqa: BLE001 — a bad stamp must not cost the turn
                when = ""
        did = str(turn.get("did") or "").strip()
        lines.append(f"- คน{when}: {said[:200]}" + (f"\n  ระบบ: {did[:120]}" if did else ""))
    return "\n".join(lines)


REPLY_TO_BLOCK = """

THIS MESSAGE IS A REPLY. The person tapped the system's own message about
{r_noun} {r_code} and typed a reply to it. A bare verb answers about THAT
record — "อนุมัติ" is action="approve" on it, "ไม่ผ่าน …" is action="reject",
"ออกเอกสาร" is action="issue", "ยกเลิก" is a cancel of it — with {r_code} in
"fields" as its code. It is never a request to LIST or read the record; the
person is looking at it.
"""

PENDING_PROMPT_BLOCK = """

There is an action ALREADY IN PROGRESS from the previous message:
- action: {p_action}
- entity: {p_entity}
- values already collected: {p_fields}
- still waiting for: {p_missing}

The user's new message is most likely their answer to what was being waited
for. If it is, return that SAME action and entity, and put the value they
just gave into "fields" under the field name that was waited for. Return a
different action/entity ONLY if the user has clearly changed the subject.
"""

# Spelled out for the model rather than passing a bare code: "th" is far more
# ambiguous in a prompt than "Thai".
LANGUAGE_NAMES = {"th": "Thai", "en": "English"}


# Where each entity's block starts in INTENT_SYSTEM_PROMPT. Used to send
# an OA only the capabilities it actually has.
_ENTITY_BLOCK_RE = re.compile(r'^- entity="([a-z_]+)"', re.MULTILINE)


def _entity_blocks(prompt: str) -> tuple[str, dict[str, str], str]:
    """(everything before the first entity block, {entity: its block},
    everything after the last one)."""
    marks = list(_ENTITY_BLOCK_RE.finditer(prompt))
    if not marks:
        return prompt, {}, ""
    head = prompt[: marks[0].start()]
    blocks: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(prompt)
        blocks[m.group(1)] = prompt[m.start():end]
    # The tail is whatever follows the last block that is not part of it:
    # the closing guidance. It is kept whole and always sent.
    tail_at = prompt.rfind("\nWHAT THE SENTENCE IS DOING")
    if tail_at > marks[-1].start():
        blocks[marks[-1].group(1)] = prompt[marks[-1].start():tail_at]
        return head, blocks, prompt[tail_at:]
    return head, blocks, ""


def entities_for(oa: str) -> set[str] | None:
    """The entities this OA can actually reach, or None for "everything".

    Derived from the same two tables the permission gate uses, so the
    prompt cannot drift from what the code will allow. This is the
    isolation the owner asked to keep: a technician is not shown the deal,
    quote or customer vocabulary at all, and cannot propose an action the
    gate would refuse a moment later.
    """
    from ..chat import ACTION_PERMISSIONS, OA_ALLOWED_PERMISSION_KEYS

    allowed = OA_ALLOWED_PERMISSION_KEYS.get(oa)
    if allowed is None:
        return None
    reachable = {e for (_a, e), key in ACTION_PERMISSIONS.items() if key in allowed}
    # Always available whatever the OA: they are not permissioned actions.
    return reachable | {"profile", "report"}


#: Sent only on the customer OA. The person is a CUSTOMER of the shop, not
#: staff: every entity above means their own record, and two things the
#: staff vocabulary has no word for — the shop itself, and cancelling — are
#: named here. Owner, 11 ก.ย. 2569: a customer may report a fault, cancel
#: their own visit and register their own product; moving a visit or
#: changing what they reported is a request the shop decides on.
CUSTOMER_PROMPT_BLOCK = """

THE PERSON IS A CUSTOMER of the shop, not a member of staff. Read every
entity as THEIR OWN record:
- entity="ticket" is their own repair.
    action="create": something of theirs is broken or needs a visit —
      including asking whether a technician can come on a day.
      Examples: "แอร์ไม่เย็น", "ตู้เย็นมีเสียงดัง", "อยากให้ช่างมาดู",
      "ช่างว่างมาดูวันเสาร์ไหมครับ" (fields scheduled_date).
    action="read": how their repair is going, when the technician comes,
      who is coming. Examples: "ช่างมาเมื่อไหร่", "งานผมถึงไหนแล้ว",
      "ซ่อมเสร็จยัง", "สถานะ".
    action="update": they ask to MOVE the visit or CHANGE what they
      reported — a request the shop decides on. fields may include
      scheduled_date, scheduled_time, issue_description. Examples:
      "ขอเลื่อนเป็นวันเสาร์", "ไม่ใช่ๆ ผมหมายถึงแอร์ห้องนอน".
    action="cancel": they no longer want the visit. Examples: "ไม่ซ่อมแล้ว",
      "ยกเลิกนัด", "ไม่ต้องมาแล้วครับ".
- entity="warranty": their own product. action="create" registers one
  ("ลงทะเบียนสินค้า SN12345678"); action="read" asks about it ("ยังมี
  ประกันไหม", "หมดประกันเมื่อไหร่").
- entity="profile": their own name, phone, address (read or update).
- entity="shop" — the shop itself.
    action="read": how to reach it, where it is, when it opens.
      Examples: "เบอร์ร้าน", "ร้านเปิดกี่โมง", "ติดต่อร้าน".
    action="chat": they want a person at the shop, are asking whether
      anyone is there, or are complaining. Examples: "ขอคุยกับพนักงาน",
      "แอดมินอยู่ไหม", "มีคนตอบไหม", "บริการแย่มาก".
    action="switch": WHICH shops they are with, or moving to another one.
      One LINE account can be a customer of several shops, and only one of
      them is being talked to at a time. Examples: "ผมอยู่กับร้านไหนบ้าง",
      "ร้านที่ผูกไว้มีอะไรบ้าง", "ขอเปลี่ยนไปร้านอื่น", "เปลี่ยนร้าน".
- entity="product", action="read": prices, what is for sale, a product
  they want to buy. Examples: "ราคาแอร์เท่าไหร่", "มีแอร์รุ่นไหนบ้าง",
  "อยากซื้อแอร์".
- entity="invoice", action="read": their own bills and receipts — what
  they owe, their invoices, or asking for a receipt. Examples:
  "ใบแจ้งหนี้ของฉัน", "ยอดค้าง", "ต้องจ่ายเท่าไหร่", "ขอใบเสร็จ" (fields
  {{"document": "receipt"}}). A customer never creates, pays or voids one
  here; a question about HOW to pay (bank account, channels) is
  action="suggest".
A greeting, thanks, "ok", or a question about how to use this chat is
action="suggest".
"""


def build_prompt(
    *,
    chann_uid: str,
    role: str,
    license_id: str,
    permission_keys: list[str] | frozenset[str],
    language: str = "th",
    pending: dict | None = None,
    oa: str = "",
    recent: list[dict] | None = None,
    reply_to: dict | None = None,
) -> str:
    keys = sorted(permission_keys)
    lang = (language or DEFAULT_LOCALE).lower()
    template = INTENT_SYSTEM_PROMPT
    wanted = entities_for(oa) if oa else None
    if wanted is not None:
        head, blocks, tail = _entity_blocks(template)
        if blocks:
            kept = [b for name, b in blocks.items() if name in wanted]
            if kept:
                template = head + "".join(kept) + tail
    # The shop's own day, not UTC: at 23:00 in Bangkok the UTC date is
    # still yesterday, and "พรุ่งนี้" would land on today.
    today = local_today()
    prompt = template.format(
        chann_uid=chann_uid,
        role=role,
        license_id=license_id,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang, LANGUAGE_NAMES[DEFAULT_LOCALE]),
        permission_keys=", ".join(keys) if keys else "(none)",
        today=today.isoformat(),
        weekday=_THAI_WEEKDAYS[today.weekday()],
        buddhist_year=today.year + 543,
    )
    if oa == "customer":
        prompt += CUSTOMER_PROMPT_BLOCK
    if recent:
        rendered = _recent_turns_for_prompt(recent)
        if rendered:
            prompt += RECENT_TURNS_BLOCK.format(turns=rendered)
    if reply_to and reply_to.get("code"):
        # A quoted reply on LINE (14 ก.ย. 2569): the owner replied "อนุมัติ"
        # to the approval notification and the model, told nothing of
        # what was replied to, read it as "show me the pending list".
        prompt += REPLY_TO_BLOCK.format(
            r_noun=str(reply_to.get("entity_type") or "record").replace("_", " "),
            r_code=str(reply_to.get("code") or ""),
        )
    if pending:
        prompt += PENDING_PROMPT_BLOCK.format(
            p_action=pending.get("action") or "?",
            p_entity=pending.get("entity") or "?",
            p_fields=_pending_fields_for_prompt(pending.get("fields") or {}),
            p_missing=", ".join(pending.get("missing") or []) or "(nothing)",
        )
    return prompt


def parse_intent_json(raw: str) -> dict:
    """Extract the JSON object from a model reply.

    Tolerates markdown fences and leading prose because models emit them even
    when told not to, but does not tolerate a shape the caller can't rely on:
    a reply without a usable "action" raises rather than returning a dict that
    looks fine until something downstream reads .get("action") and gets None.
    """
    text = (raw or "").strip()

    if text.startswith("```"):
        # ```json ... ``` — drop the fence line and everything after the close.
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()

    if not text.startswith("{") and not text.startswith("["):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise AIUnavailable("model reply contained no JSON object")
        text = text[start : end + 1]

    values = _json_values(text)
    if len(values) == 1 and isinstance(values[0], list):
        values = values[0]
    readings = [v for v in values if isinstance(v, dict)]
    if not readings:
        raise AIUnavailable("model reply was not a JSON object")

    parsed = _usable_intent(readings[0])
    if parsed is None:
        raise AIUnavailable("model reply had no usable 'action'")
    and_then = [
        usable for usable in (_usable_intent(r) for r in readings[1:]) if usable is not None
    ]
    if and_then:
        parsed["and_then"] = and_then
    return parsed


def _json_values(text: str) -> list:
    """Every JSON value in the reply, in order.

    Asked for several products in one sentence ("เพิ่มพัดลม 2 ตัว และ แอร์
    1 ตัว"), DEV's model answers with one object per product on separate
    lines. json.loads calls that "Extra data" and the reading was thrown
    away — the sentence then fell to the typed parser, which read "พัดลม
    และ แอร์" as one product name (owner, 16 ก.ย. 2569). Several objects
    are several readings, not a broken one."""
    decoder = json.JSONDecoder()
    values: list = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index] in " \r\n\t,":
            index += 1
        if index >= len(text):
            break
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            if values:
                break
            raise AIUnavailable(f"model reply was not valid JSON: {exc}") from exc
        values.append(value)
    return values


def _usable_intent(parsed: dict) -> dict | None:
    """The reading with its shape guaranteed, or None when it has no action."""
    action = parsed.get("action")
    if not isinstance(action, str) or not action.strip():
        return None
    parsed.setdefault("entity", None)
    parsed.setdefault("fields", {})
    parsed.setdefault("missing", [])
    if not isinstance(parsed["fields"], dict):
        parsed["fields"] = {}
    if not isinstance(parsed["missing"], list):
        parsed["missing"] = []
    return parsed


async def parse_intent(
    *,
    message: str,
    chann_uid: str,
    role: str,
    license_id: str,
    permission_keys: list[str] | frozenset[str],
    language: str = "th",
    client=None,
    pending: dict | None = None,
    oa: str = "",
    recent: list[dict] | None = None,
    timeout_s: float | None = None,
    attempts: int | None = None,
    reply_to: dict | None = None,
) -> dict:
    """Parse one user message. Raises AIUnavailable; never returns a half-result."""
    system_prompt = build_prompt(
        chann_uid=chann_uid,
        role=role,
        license_id=license_id,
        permission_keys=permission_keys,
        language=language,
        pending=pending,
        recent=recent,
        oa=oa,
        reply_to=reply_to,
    )
    raw = await complete(
        system_prompt=system_prompt,
        user_message=message,
        thinking=False,          # 4.3: chat tier runs with thinking OFF
        client=client,
        timeout_s=timeout_s,
        attempts=attempts,
    )
    intent = parse_intent_json(raw)

    # Free text goes back to the person's own characters. A model copying
    # a long Thai phrase into JSON drops vowel and tone marks — "ซื้อ"
    # comes back as "ซี้" — and the result looks right in a review while
    # being wrong in the record a shop keeps about its customer.
    #
    # The model is still the one that decided which span of the message
    # is a note; it is only its transcription that is discarded.
    fields = intent.get("fields")
    if isinstance(fields, dict):
        intent["fields"] = recover_free_text(fields, message)
    for more in intent.get("and_then") or []:
        if isinstance(more.get("fields"), dict):
            more["fields"] = recover_free_text(more["fields"], message)
    return intent
