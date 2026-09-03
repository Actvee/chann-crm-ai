"""The user guides — one source for chat's "วิธีใช้", the LIFF guide page,
and the owner's printable handout.

Owner (3 Sep, late): the how-to must be kept current with every change,
guidance that is clearer with a picture gets a picture, and the images
are produced outside — so every step names its image slot and describes
what the picture should show. `help_images.json` next to this file maps
a slot to a URL once the owner has one; until then the slot is empty and
nothing is sent or shown for it.

Keeping this honest is a test (tests/unit/test_guides.py): every command
a step tells the person to type must be a phrase chat actually
recognises, and the rendered docs/guides/*.md must match this data.
"""
from __future__ import annotations

import json
from pathlib import Path

_IMAGES_FILE = Path(__file__).resolve().parent.parent / "help_images.json"


def help_image_url(slot: str) -> str:
    """The owner-supplied image for a guide slot, or "" when there is none."""
    try:
        data = json.loads(_IMAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str((data.get("images") or {}).get(slot) or "").strip()


# Each step: key, title (th/en), body (th/en) — what it does and what to
# type — commands: the exact phrases (or their first word) chat matches,
# image: the slot name, and image_prompt: what the picture should show,
# for whoever generates it.
GUIDES: dict[str, dict] = {
    "customer": {
        "title": {"th": "วิธีใช้ LINE บริการลูกค้า", "en": "Using the customer LINE"},
        "intro": {
            "th": "พิมพ์คุยได้เลย ไม่ต้องจำคำสั่ง ทำตามลำดับนี้ครั้งแรกครั้งเดียว แล้วแจ้งซ่อมได้ทุกเมื่อ",
            "en": "Just type. Do these once, then report a fault any time.",
        },
        "steps": [
            {
                "key": "link", "title": {"th": "ผูกกับร้าน", "en": "Link to your shop"},
                "body": {
                    "th": "พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้านที่ซื้อ ระบบจะผูกบัญชีให้ ถ้ามีหลายร้านจะมีปุ่มให้เลือก",
                    "en": "Type the serial number on the sticker, or the shop's name. Several shops → buttons to pick one.",
                },
                "commands": ["เปลี่ยนร้าน"],
                "example": "SN12345678",
                "image": "customer-link",
                "image_prompt": "โทรศัพท์เปิด LINE แชทกับร้าน มือถือสติกเกอร์ S/N ของเครื่องใช้ไฟฟ้า ลูกศรชี้จากสติกเกอร์ไปช่องพิมพ์ข้อความ",
            },
            {
                "key": "register", "title": {"th": "ลงทะเบียนสินค้า (รับประกัน)", "en": "Register your product (warranty)"},
                "body": {
                    "th": "พิมพ์ \"ลงทะเบียนสินค้า\" แล้วตามด้วย S/N ที่ร้านบันทึกไว้ให้ เครื่องจะผูกกับคุณ ถ้าระบบยังไม่รู้จักหมายเลข ให้ติดต่อร้าน",
                    "en": "Type \"register product\" then the S/N the shop recorded. Unknown S/N → contact the shop.",
                },
                "commands": ["ลงทะเบียนสินค้า", "ประกันของฉัน"],
                "example": "ลงทะเบียนสินค้า SN12345678",
                "image": "customer-register",
                "image_prompt": "หน้าจอแชท: ลูกค้าพิมพ์ 'ลงทะเบียนสินค้า SN12345678' บอทตอบ 'ลงทะเบียน แอร์ (S/N …) เป็นของคุณแล้ว' พร้อมไอคอนโล่สีส้ม",
            },
            {
                "key": "report", "title": {"th": "แจ้งซ่อม", "en": "Report a fault"},
                "body": {
                    "th": "พิมพ์อาการที่เสียมาได้เลย เช่น \"แอร์ไม่เย็น\" ระบบจะเลือกเครื่องให้ (หรือให้กดเลือกถ้ามีหลายเครื่อง) แล้วถามที่อยู่และวันเวลานัด · ส่งรูปอาการมาในแชทได้ ระบบแนบกับงานให้ช่างดู",
                    "en": "Describe what is wrong, e.g. \"air con not cooling\". The machine is picked for you, then address and appointment are asked.",
                },
                "commands": ["แจ้งซ่อม", "ไม่มีหมายเลขเครื่อง"],
                "example": "แอร์ไม่เย็น มีน้ำหยด",
                "image": "customer-report",
                "image_prompt": "แชท 3 ฟอง: ลูกค้า 'แอร์ไม่เย็น' → บอท 'รับแจ้งแล้ว เลขงาน T-2026-0001 ขอที่อยู่' → ลูกค้าพิมพ์ที่อยู่ → บอทถามวันนัด",
            },
            {
                "key": "status", "title": {"th": "ดูสถานะ / เลื่อนนัด / ยกเลิก", "en": "Status, reschedule, cancel"},
                "body": {
                    "th": "พิมพ์ \"งานของฉัน\" หรือ \"สถานะการซ่อม\" · เลื่อนนัด: \"เลื่อนนัดวันศุกร์ บ่าย 2\" · ยกเลิก: \"ยกเลิกงาน\"",
                    "en": "\"my jobs\" / \"repair status\" · reschedule: \"move it to Friday 2pm\" · \"cancel job\"",
                },
                "commands": ["งานของฉัน", "สถานะการซ่อม", "เลื่อนนัด", "ยกเลิกงาน"],
                "example": "งานของฉัน",
                "image": "customer-status",
                "image_prompt": "การ์ดสถานะงานซ่อม T-2026-0001 แสดงขั้น รอมอบหมาย → ช่างรับแล้ว → กำลังทำ → เสร็จ พร้อมไอคอนนาฬิกา",
            },
            {
                "key": "after", "title": {"th": "หลังซ่อมเสร็จ", "en": "After the repair"},
                "body": {
                    "th": "เมื่อร้านตรวจงานผ่าน คุณจะได้ปุ่มให้คะแนน 1–3 กดได้เลย และดูประวัติทั้งหมดได้ที่ \"เปิดหน้าจอลูกค้า\" ในเมนู",
                    "en": "When the shop approves the work you get 1–3 rating buttons. Everything is on the home screen (menu → Open the dashboard).",
                },
                "commands": ["ติดต่อร้าน", "ข้อมูลของฉัน", "เปลี่ยนภาษาเป็นอังกฤษ", "วิธีใช้"],
                "example": "ข้อมูลของฉัน",
                "image": "customer-after",
                "image_prompt": "ข้อความจากร้าน 'งาน T-… เสร็จแล้ว ช่วยให้คะแนน' พร้อมปุ่ม 1 ไม่ดี / 2 พอใช้ / 3 ดีเยี่ยม สีส้ม",
            },
        ],
    },
    "technician": {
        "title": {"th": "วิธีใช้ LINE ช่าง", "en": "Using the technician LINE"},
        "intro": {
            "th": "วันทำงานของช่างมี 4 ขั้น ทำตามลำดับ ระบบจะบอกขั้นถัดไปให้ทุกครั้ง",
            "en": "A technician's day has four steps, in order. The system names the next one each time.",
        },
        "steps": [
            {
                "key": "join", "title": {"th": "เข้าร่วมร้าน (ครั้งแรก)", "en": "Join the shop (once)"},
                "body": {
                    "th": "ขอรหัสเชิญจากร้าน แล้วพิมพ์รหัสนั้นในแชทนี้ ถ้าอยู่หลายร้าน พิมพ์ \"เปลี่ยนร้าน\" เพื่อสลับ",
                    "en": "Get an invite code from the shop and type it here. Several shops → \"switch shop\".",
                },
                "commands": ["เปลี่ยนร้าน"],
                "example": "ABCD1234",
                "image": "tech-join",
                "image_prompt": "ช่างถือโทรศัพท์ พิมพ์รหัสเชิญ 8 ตัวในแชท บอทตอบ 'เข้าร่วม ร้านแอร์ดี แล้ว' ธีมน้ำเงิน",
            },
            {
                "key": "take", "title": {"th": "รับงาน", "en": "Take a job"},
                "body": {
                    "th": "พิมพ์ \"งานที่เปิดรับ\" แล้วกดปุ่มงาน หรือพิมพ์ \"รับงาน T-2026-0001\" · งานที่ CS มอบหมายตรงมาจะมีปุ่ม รับ/ปฏิเสธ · งานของทีม: หัวหน้ากด \"รับงาน\" ให้ทีมก่อน แล้วสมาชิกดู \"งานของทีม\" และรับต่อ · รับไม่ได้: \"ปฏิเสธงาน T-… เหตุผล\"",
                    "en": "\"open jobs\" then tap one, or \"claim T-2026-0001\". A job given to you directly has accept/decline. Can't: \"decline job T-… reason\".",
                },
                "commands": ["งานที่เปิดรับ", "รับงาน", "ปฏิเสธงาน", "งานของฉัน", "งานวันนี้", "งานของทีม"],
                "example": "รับงาน T-2026-0001",
                "image": "tech-take",
                "image_prompt": "การ์ดรายการงานที่เปิดรับ 2 งาน แต่ละงานมีปุ่ม 'รับงาน' สีน้ำเงิน และงานหนึ่งมีป้าย 'มอบหมายให้คุณ'",
            },
            {
                "key": "checkin", "title": {"th": "เช็คอินเมื่อถึงหน้างาน", "en": "Check in on site"},
                "body": {
                    "th": "ถึงบ้านลูกค้าแล้วพิมพ์ \"เช็คอิน\" (มีงานเดียวระบบรู้เอง) งานจะเปลี่ยนเป็น กำลังทำ — ปิดงานได้หลังจากนี้เท่านั้น · ส่งรูปหน้างานมาในแชทได้เลย ระบบแนบกับงานและใส่ในรายงาน PDF",
                    "en": "On arrival type \"check in\" (one job: it knows which). The job becomes in progress — finishing is only possible after this. Send photos of the site here; they go on the job and into the PDF.",
                },
                "commands": ["เช็คอิน"],
                "example": "เช็คอิน T-2026-0001",
                "image": "tech-checkin",
                "image_prompt": "ช่างยืนหน้าบ้านลูกค้า กดปุ่ม 'เช็คอินเริ่มงาน' บนโทรศัพท์ มีหมุดตำแหน่งสีน้ำเงิน",
            },
            {
                "key": "finish", "title": {"th": "ปิดงาน + รายงาน", "en": "Finish + report"},
                "body": {
                    "th": "พิมพ์ \"ปิดงาน\" แล้วตอบ 3 อย่าง: ปัญหาที่พบ / สิ่งที่แก้ไข / อะไหล่ที่เปลี่ยน (พิมพ์ \"ไม่มี\" ได้) ระบบส่งให้ CS ตรวจทันที",
                    "en": "Type \"finish\" and answer three things: what you found / what you did / parts (or \"none\"). CS is asked to review at once.",
                },
                "commands": ["ปิดงาน", "รายงานของฉัน"],
                "example": "ปิดงาน",
                "image": "tech-finish",
                "image_prompt": "แชท: บอทถาม 'พบปัญหาอะไร' ช่างตอบ 'คอมเพรสเซอร์รั่ว' → 'แก้ไขอย่างไร' → 'เปลี่ยนคอมเพรสเซอร์' → 'อะไหล่' → 'ไม่มี' → 'ปิดงานแล้ว รายงาน SR-2026-0001'",
            },
            {
                "key": "approved", "title": {"th": "รอ CS ตรวจ → PDF", "en": "CS review → PDF"},
                "body": {
                    "th": "ผ่านหรือตีกลับจะแจ้งมาที่แชทนี้ ตีกลับให้แก้แล้วส่งใหม่ · ผ่านแล้วจะได้ลิงก์ PDF รายงาน (หรือพิมพ์ \"ออกรายงาน SR-…\") · วาดลายเซ็นไว้ครั้งเดียวที่ หน้าจอ > ข้อมูลของฉัน > ลายเซ็น จะติดบน PDF ที่คุณเกี่ยวข้อง",
                    "en": "Approved or sent back, you hear here. Approved → the report PDF link (or \"issue report SR-…\").",
                },
                "commands": ["ออกรายงาน", "เปลี่ยนภาษาเป็นอังกฤษ", "วิธีใช้"],
                "example": "ออกรายงาน SR-2026-0001",
                "image": "tech-approved",
                "image_prompt": "ข้อความ 'รายงาน SR-2026-0001 ผ่านการอนุมัติแล้ว PDF (7 วัน): ลิงก์' พร้อมไอคอนเอกสาร PDF และเครื่องหมายถูกสีเขียว",
            },
        ],
    },
    "sales": {
        "title": {"th": "วิธีใช้ LINE ทีมขาย / CS", "en": "Using the sales / CS LINE"},
        "intro": {
            "th": "ทุกอย่างที่ทำได้บนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน ตั้งร้านให้พร้อมก่อน แล้วเดินงานซ่อมตามลำดับ",
            "en": "Everything on the dashboard can be typed here. Set the shop up first, then run repairs in order.",
        },
        "steps": [
            {
                "key": "setup", "title": {"th": "ตั้งร้านให้พร้อม", "en": "Set the shop up"},
                "body": {
                    "th": "\"ข้อมูลร้าน\" ดูรหัสร้าน (ให้ลูกค้าใช้ผูก) · \"ขอรหัสเชิญช่าง\" ให้ช่างเข้าร่วม · \"สร้างทีมช่าง แอร์\" แล้ว \"เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า\" · \"ข้อมูลบริษัท\" สำหรับเอกสาร",
                    "en": "\"shop info\" (the code customers link with) · \"invite technician\" · \"create technician team AC\" then \"add Somsak to team AC as lead\" · \"company profile\" for documents",
                },
                "commands": ["ข้อมูลร้าน", "ขอรหัสเชิญช่าง", "สร้างทีมช่าง", "ทีมช่าง", "รายชื่อช่าง", "ข้อมูลบริษัท"],
                "example": "สร้างทีมช่าง แอร์",
                "image": "sales-setup",
                "image_prompt": "แผนผังร้าน: กล่อง 'ร้าน (รหัส ABCD01)' เชื่อมไป 'ทีมช่าง แอร์ (หัวหน้า สมศักดิ์)' และ 'ลูกค้า' ธีมเขียว",
            },
            {
                "key": "units", "title": {"th": "บันทึกเครื่องที่ขาย", "en": "Record sold units"},
                "body": {
                    "th": "\"ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย\" — ลูกค้าจึงพิมพ์ S/N นี้ผูกเครื่องได้ · \"รายการประกัน\" ดูทั้งหมด",
                    "en": "\"register product SN12345678 aircon for Somchai\" — the customer then attaches it by typing the S/N · \"warranties\" lists them",
                },
                "commands": ["ลงทะเบียนสินค้า", "รายการประกัน"],
                "example": "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย",
                "image": "sales-units",
                "image_prompt": "ตารางเครื่องที่ลงทะเบียน: S/N, สินค้า, สถานะ 'ลูกค้าผูกแล้ว' / 'ยังไม่มีลูกค้าผูก' ธีมเขียว",
            },
            {
                "key": "dispatch", "title": {"th": "งานซ่อม: มอบหมาย", "en": "Repairs: dispatch"},
                "body": {
                    "th": "ลูกค้าแจ้งซ่อมแล้วคุณได้ LINE · \"รายการงาน\" ดูคิว · \"มอบหมาย T-2026-0001 ให้ทีม แอร์\" (ต้องมีชื่อ เบอร์ ที่อยู่ นัดครบ ระบบบอกถ้าขาด) · หรือทำบนแดชบอร์ด > งานซ่อม",
                    "en": "You hear when a customer reports · \"tickets\" for the queue · \"assign T-2026-0001 to team AC\" (name, phone, address, appointment required — it tells you what is missing) · or dashboard > tickets",
                },
                "commands": ["รายการงาน", "มอบหมาย"],
                "example": "มอบหมาย T-2026-0001 ให้ทีม แอร์",
                "image": "sales-dispatch",
                "image_prompt": "หน้าจอ 'งานซ่อม' บนแดชบอร์ด การ์ดงาน T-2026-0001 มีช่อง 'มอบหมายให้…' เลือกทีมแอร์ และปุ่มมอบหมายสีเขียว",
            },
            {
                "key": "approve", "title": {"th": "งานซ่อม: ตรวจรายงาน", "en": "Repairs: review reports"},
                "body": {
                    "th": "ช่างปิดงานแล้วคุณได้ LINE · \"รายการรออนุมัติ\" · \"อนุมัติ SR-2026-0001\" หรือ \"ตีกลับ SR-… เหตุผล\" · ผ่านครบ → ลูกค้าได้แบบประเมิน + PDF รายงานออกอัตโนมัติ · ตั้งขั้นตอน: \"ตั้งการอนุมัติ\"",
                    "en": "\"pending approvals\" · \"approve SR-2026-0001\" or \"reject SR-… reason\" · all steps passed → the customer gets the survey and the PDF is produced · \"approval policy\" to change the flow",
                },
                "commands": ["รายการรออนุมัติ", "อนุมัติ", "ตีกลับ", "ตั้งการอนุมัติ", "ออกรายงาน"],
                "example": "อนุมัติ SR-2026-0001",
                "image": "sales-approve",
                "image_prompt": "การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' สีเทา",
            },
            {
                "key": "crm", "title": {"th": "ลูกค้า ดีล ใบเสนอราคา", "en": "Customers, deals, quotes"},
                "body": {
                    "th": "\"รายชื่อลูกค้า\" · \"สร้างลูกค้า สมชาย ใจดี 0812345678\" · \"สร้างดีลให้ สมชาย\" · \"ออกเอกสาร Q-2026-0001\" · \"งานวันนี้\" ดูสิ่งที่ต้องทำ · \"เตือน D-… พรุ่งนี้\"",
                    "en": "\"customers\" · \"create customer …\" · \"create deal for Somchai\" · \"issue quote Q-…\" · \"today\" · \"remind D-… tomorrow\"",
                },
                "commands": ["รายชื่อลูกค้า", "สร้างดีลให้", "ออกเอกสาร", "งานวันนี้"],
                "example": "งานวันนี้",
                "image": "sales-crm",
                "image_prompt": "แดชบอร์ดขายธีมเขียว: tile ลูกค้า / ดีล / ใบเสนอราคา / งานซ่อม / รอการอนุมัติ / ทีมช่าง",
            },
            {
                "key": "help", "title": {"th": "ติดขัด", "en": "Stuck"},
                "body": {
                    "th": "\"วิธีใช้\" ดูตัวอย่างที่คุณมีสิทธิ์ · \"ทำอะไรได้บ้าง\" ดูสิทธิ์ทั้งหมด · สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > บทบาทและทีม) · \"เปลี่ยนภาษาเป็นอังกฤษ\"",
                    "en": "\"help\" · \"what can I do\" · ask the owner for permissions (dashboard > roles) · \"switch to English\"",
                },
                "commands": ["วิธีใช้", "ทำอะไรได้บ้าง", "เปลี่ยนภาษาเป็นอังกฤษ"],
                "example": "วิธีใช้",
                "image": "sales-help",
                "image_prompt": "หน้าจอ 'บทบาทและทีม' แสดงรายชื่อสมาชิกและสิทธิ์เป็น toggle ธีมเขียว",
            },
        ],
    },
}


def render_help_text(oa: str, language: str = "th", *, allowed_steps: set[str] | None = None) -> str:
    """The chat "วิธีใช้": numbered steps, each with what it does and what
    to type — readable in one LINE bubble."""
    guide = GUIDES.get(oa) or GUIDES["customer"]
    lang = "en" if language == "en" else "th"
    lines = [guide["title"][lang], guide["intro"][lang], ""]
    n = 0
    for step in guide["steps"]:
        if allowed_steps is not None and step["key"] not in allowed_steps:
            continue
        n += 1
        title = step["title"][lang]
        lines.append(f"{n}. {title}")
        lines.append(f"   {step['body'][lang]}")
        example = step.get("example")
        if example:
            lines.append(f"   ▸ {'พิมพ์' if lang == 'th' else 'type'}: \"{example}\"")
    lines.append("")
    lines.append(
        "เปิดคู่มือพร้อมรูปได้จากเมนู \"เปิดหน้าจอ…\" > วิธีใช้" if lang == "th"
        else "The illustrated guide is on the app: menu > Open the dashboard > How to"
    )
    return "\n".join(lines)


def guide_images(oa: str) -> list[str]:
    """Owner-supplied images for this OA's guide, in step order — sent
    with chat's help when present."""
    guide = GUIDES.get(oa) or {}
    urls = []
    for step in guide.get("steps", []):
        url = help_image_url(step["image"])
        if url:
            urls.append(url)
    return urls


def guide_as_markdown(oa: str) -> str:
    """The owner's handout with image slots, for docs/guides/."""
    guide = GUIDES[oa]
    out = [f"# {guide['title']['th']}", "", guide["intro"]["th"], "",
           "> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` "
           "และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง", ""]
    for i, step in enumerate(guide["steps"], start=1):
        out.append(f"## {i}. {step['title']['th']}")
        out.append("")
        out.append(step["body"]["th"])
        out.append("")
        if step.get("example"):
            out.append(f"พิมพ์: `{step['example']}`")
            out.append("")
        out.append(f"[IMAGE: {step['image']} — {step['image_prompt']}]")
        out.append("")
        out.append(f"_EN: {step['title']['en']} — {step['body']['en']}_")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
