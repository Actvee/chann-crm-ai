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
            "th": "พิมพ์คุยได้เลย ไม่ต้องจำคำสั่ง ทำตามลำดับนี้ครั้งแรกครั้งเดียว แล้วแจ้งซ่อมได้ทุกเมื่อ · เมนูด้านล่างมี 2 หน้า แตะ \"เพิ่มเติม\" บนหัวเมนูเพื่อดูสินค้า ประวัติการซื้อ ประกัน โปรไฟล์ และสลับภาษา",
            "en": "Just type. Do these once, then report a fault any time. The menu has two pages: tap \"More\" on the menu header for products, history, warranties, profile and language.",
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
                "key": "shop", "title": {"th": "ดูสินค้า / สนใจสินค้า", "en": "Browse products / show interest"},
                "body": {
                    "th": "พิมพ์ \"สินค้าทั้งหมด\" หรือ \"ค้นหา\" ตามด้วยชื่อสินค้า เช่น \"ค้นหา พัดลม\" จะเห็นสินค้าจากทุกร้าน พิมพ์เลขข้อที่สนใจ ร้านนั้นจะได้รับแจ้งและติดต่อกลับ · บนหน้าจอลูกค้ามีช่องค้นหาและปุ่ม \"สนใจ\" เหมือนกัน",
                    "en": "Type \"all products\" or \"search\" and a product name, e.g. \"search fan\". Products from every shop appear; type the number you like and that shop is told and gets back to you. The home screen has the same search and an \"interested\" button.",
                },
                "commands": ["สินค้าทั้งหมด", "ค้นหา", "ประวัติการซื้อ"],
                "example": "ค้นหา พัดลม",
                "image": "customer-shop",
                "image_prompt": "หน้าจอแชท: ลูกค้าพิมพ์ 'ค้นหา พัดลม' บอทตอบรายการ 1. พัดลมไอเย็น (ร้านเย็นสบาย) — 3500 2. … ลูกค้าพิมพ์ '1' บอทตอบ 'ร้านเย็นสบาย จะติดต่อกลับ' พร้อมไอคอนตะกร้าสีส้ม",
            },
            {
                "key": "talk", "title": {"th": "คุยกับร้าน", "en": "Talk to the shop"},
                "body": {
                    "th": "พิมพ์ \"คุยกับร้าน\" (ต่อด้วยคำถามได้เลย เช่น \"คุยกับร้าน ราคาแอร์ 12000 BTU\") เจ้าหน้าที่ของร้านจะตอบกลับในแชทนี้ ระหว่างคุย ข้อความที่พิมพ์จะส่งถึงร้านทั้งหมด (ไม่มีข้อความยืนยันทุกครั้ง ถ้าส่งไม่ได้ระบบจะบอก) ไม่เปิดเป็นงานซ่อม กลับมาคุยใหม่ก็ต่อจากแชทเดิม · พิมพ์ \"จบการสนทนา\" เมื่อเสร็จ ไม่มีข้อความสักพักระบบปิดให้เอง · บนหน้าจอลูกค้ามีช่องแชทเดียวกัน",
                    "en": "Type \"talk to the shop\" (a question may follow, e.g. \"talk to the shop price of a 12000 BTU air con\"). A person at the shop answers here. While talking, what you type goes to the shop (no confirmation each time; a failure is reported) and does not open a repair job. Coming back later continues the same conversation. \"end chat\" when done; it closes itself after a quiet while. The home screen has the same chat box.",
                },
                "commands": ["คุยกับร้าน", "จบการสนทนา"],
                "example": "คุยกับร้าน ราคาแอร์ 12000 BTU เท่าไหร่",
                "image": "customer-chat",
                "image_prompt": "หน้าจอแชท LINE: ลูกค้าพิมพ์ 'คุยกับร้าน ราคาแอร์ 12000 BTU' บอทตอบ 'เปิดการสนทนากับ ร้านเย็นสบาย แล้ว' แล้วมีข้อความจากร้าน '💬 ร้านเย็นสบาย: 15,900 บาทครับ' ไอคอนคนสวมหูฟังสีส้ม",
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
                "commands": ["ประวัติการซื้อ", "ติดต่อร้าน", "ข้อมูลของฉัน", "เปลี่ยนภาษาเป็นอังกฤษ", "รูปแบบวันที่", "วิธีใช้"],
                "example": "ข้อมูลของฉัน",
                "image": "customer-after",
                "image_prompt": "ข้อความจากร้าน 'งาน T-… เสร็จแล้ว ช่วยให้คะแนน' พร้อมปุ่ม 1 ไม่ดี / 2 พอใช้ / 3 ดีเยี่ยม สีส้ม",
            },
            {
                "key": "pdpa", "title": {"th": "ข้อมูลส่วนตัว (PDPA)", "en": "Your personal data (PDPA)"},
                "body": {
                    "th": "ครั้งแรกระบบจะขอความยินยอมก่อนผูกร้าน (ตอบ \"ยอมรับ\") · ขอสำเนาข้อมูลทั้งหมดได้ด้วย \"ขอข้อมูลของฉัน\" จะได้ลิงก์หน้าสรุปใช้ได้ 24 ชั่วโมง · ขอลบด้วย \"ขอลบข้อมูล\" แล้วยืนยัน ชื่อ เบอร์ ที่อยู่ แชท และรูปจะถูกลบจากทุกร้าน (ประวัติงานยังอยู่แต่ไม่มีชื่อคุณ) · บนหน้าจอลูกค้า ส่วน \"โปรไฟล์\" มีปุ่มเดียวกัน",
                    "en": "The first time, consent is asked before linking a shop (answer \"accept\"). \"my data\" gives a 24-hour link to a page with everything; \"delete my data\" plus a confirmation erases your name, phone, address, chat lines and pictures from every shop (job history stays, without your name). The profile section on the home screen has the same buttons.",
                },
                "commands": ["ขอข้อมูลของฉัน", "ขอลบข้อมูล", "ยืนยันลบข้อมูล"],
                "example": "ขอข้อมูลของฉัน",
                "image": "customer-pdpa",
                "image_prompt": "แชท LINE ข้อความ 'ขอข้อมูลของฉัน' ตอบกลับเป็นลิงก์หน้าสรุปข้อมูล และปุ่ม 'ยืนยันลบข้อมูล' สีส้ม ไอคอนโล่ PDPA",
            },
        ],
    },
    "technician": {
        "title": {"th": "วิธีใช้ LINE ช่าง", "en": "Using the technician LINE"},
        "intro": {
            "th": "วันทำงานของช่างมี 4 ขั้น ทำตามลำดับ ระบบจะบอกขั้นถัดไปให้ทุกครั้ง · เมนูมี 2 หน้า แตะ \"เพิ่มเติม\" บนหัวเมนู: รายงานของฉัน งานของทีม ปฏิเสธงาน โปรไฟล์ สิทธิ์ สลับภาษา",
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
                    "th": "ถึงบ้านลูกค้าแล้วพิมพ์ \"เช็คอิน\" หรือ **ส่งตำแหน่ง** (LINE > + > ตำแหน่ง) เพื่อเช็คอินพร้อมพิกัด (มีงานเดียวระบบรู้เอง) งานจะเปลี่ยนเป็น กำลังทำ — ปิดงานได้หลังจากนี้เท่านั้น · ส่งรูปหน้างานมาในแชทได้เลย ระบบแนบกับงานและใส่ในรายงาน PDF",
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
            "th": "ทุกอย่างที่ทำได้บนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน ตั้งร้านให้พร้อมก่อน แล้วเดินงานซ่อมตามลำดับ · เมนูมี 2 หน้า แตะ \"เพิ่มเติม\" บนหัวเมนู: แชทลูกค้า ดีล สินค้า ทีมช่าง ข้อมูลบริษัท สลับภาษา",
            "en": "Everything on the dashboard can be typed here. Set the shop up first, then run repairs in order.",
        },
        "steps": [
            {
                "key": "setup", "title": {"th": "ตั้งร้านให้พร้อม", "en": "Set the shop up"},
                "body": {
                    "th": "\"ข้อมูลร้าน\" ดูรหัสร้าน (ให้ลูกค้าใช้ผูก) · \"ขอรหัสเชิญช่าง\" ให้ช่างเข้าร่วม · \"สร้างทีมช่าง แอร์\" แล้ว \"เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า\" · \"ข้อมูลบริษัท\" สำหรับเอกสาร · \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\" ให้ลูกค้าที่ผูกร้านเข้ารายชื่อทันที",
                    "en": "\"shop info\" (the code customers link with) · \"invite technician\" · \"create technician team AC\" then \"add Somsak to team AC as lead\" · \"company profile\" for documents",
                },
                "commands": ["ข้อมูลร้าน", "ขอรหัสเชิญช่าง", "สร้างทีมช่าง", "ทีมช่าง", "รายชื่อช่าง", "ข้อมูลบริษัท", "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ", "ตั้งค่าแชท"],
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
                "key": "chats", "title": {"th": "แชทลูกค้า", "en": "Customer chats"},
                "body": {
                    "th": "ลูกค้าที่กด \"คุยกับร้าน\" ใน LINE บริการลูกค้า จะขึ้นที่ หน้าจอ > แชทลูกค้า และทุกคนได้ LINE แจ้ง ตอบที่หน้านั้นเท่านั้น (ตอบใน LINE ร้านไม่ถึงลูกค้า) คนแรกที่ตอบเป็นเจ้าของการสนทนา ถ้าปล่อยเกินเวลาตอบ (ค่าเริ่มต้น 15 นาที) ระบบแจ้งลูกค้าว่าจะติดต่อกลับและพักการสนทนา — ตอบทีหลังได้ ลูกค้าจะได้รับคำเชิญให้เปิดแชทต่อ · ลูกค้าเงียบ 1 ชั่วโมงปิดให้เอง · ตั้งเวลาเองได้ด้วย \"ตั้งค่าแชท\" หรือหน้าข้อมูลบริษัท",
                    "en": "A customer who taps \"talk to the shop\" appears under home > Customer chats, and everyone is pushed a LINE. Answer there only (a reply in the shop's LINE never reaches them). The first to answer owns it. Past the reply time (default 15 min) the customer is told you will get back and the chat is paused — answer later and they are invited to reopen it. An hour of customer silence closes it. Set both with \"chat settings\" or on the company page.",
                },
                "commands": ["ตั้งค่ารับลูกค้าใหม่อัตโนมัติ"],
                "example": "หน้าจอ > แชทลูกค้า",
                "image": "sales-chats",
                "image_prompt": "หน้าจอแดชบอร์ดสีเขียว รายการแชทลูกค้า 2 รายการ รายการแรกมีป้าย 'ลูกค้ารอคำตอบ' ด้านล่างเป็นบทสนทนาและช่องพิมพ์คำตอบ ปุ่ม 'ส่ง' และ 'ปิดการสนทนา'",
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
                "key": "ai-reports", "title": {"th": "ถามรายงานด้วย AI", "en": "Ask for a report"},
                "body": {
                    "th": "พิมพ์สิ่งที่อยากรู้เป็นภาษาคนในแชท เช่น \"ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด\" หรือ \"สรุปงานค้างแยกตามช่าง\" ระบบสรุปเป็นตัวเลขทันที · บนหน้าจอ เมนู \"รายงาน AI\" มีตารางพร้อมกราฟแท่งและปุ่มดาวน์โหลด CSV/PDF · ต้องมีสิทธิ์ \"ดูรายงาน\" (Sale มีตั้งแต่ต้น CS ต้องให้เจ้าของเปิด) · AI ไม่แตะฐานข้อมูลเอง มันแค่แปลคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลของร้านคุณเท่านั้น",
                    "en": "Type what you want to know in the chat — \"won deals in the last 3 months\", \"open tickets by technician\" — and get the numbers at once. The dashboard's \"AI reports\" page adds a table with bars and CSV/PDF downloads. Needs the \"View reports\" permission (Sales has it; the owner grants it to CS).",
                },
                "commands": ["รายงานยอดขายเดือนนี้", "สรุปงานค้างแยกตามช่าง", "ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด"],
                "example": "สรุปงานค้างแยกตามช่าง",
                "image": "sales-ai-report",
                "image_prompt": "แชท LINE ข้อความ 'สรุปงานค้างแยกตามช่าง' ตอบกลับเป็นรายการชื่อช่างกับตัวเลข และหน้าจอตารางมีกราฟแท่งสีเขียว",
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


def guide_as_html(oa: str) -> str:
    """The same handout as a self-contained web page — what the owner
    downloads, fills with pictures, and sends on to customers. Each step
    keeps a visible slot with the image prompt until a picture replaces it."""
    import html as _html

    guide = GUIDES[oa]
    accent = {"sales": "#178a50", "technician": "#1f6fd6", "customer": "#e8731a"}.get(oa, "#178a50")
    parts = [
        "<!doctype html><html lang=\"th\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{_html.escape(guide['title']['th'])}</title>",
        "<style>body{font-family:'IBM Plex Sans Thai','Noto Sans Thai',system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px;line-height:1.6;color:#1f2328}"
        f"h1{{color:{accent};font-size:26px}}h2{{font-size:19px;margin:28px 0 8px;border-left:5px solid {accent};padding-left:10px}}"
        ".slot{border:2px dashed #b9b5ab;border-radius:10px;padding:18px;margin:10px 0;color:#6b6f76;font-size:14px;background:#fafaf8}"
        ".slot img{max-width:100%;display:block;border-radius:8px}.type{font-family:ui-monospace,monospace;background:#f0f0ee;padding:2px 6px;border-radius:4px}"
        ".en{color:#6b6f76;font-size:14px}</style></head><body>",
        f"<h1>{_html.escape(guide['title']['th'])}</h1>",
        f"<p>{_html.escape(guide['intro']['th'])}</p>",
    ]
    for i, step in enumerate(guide["steps"], start=1):
        parts.append(f"<h2>{i}. {_html.escape(step['title']['th'])}</h2>")
        parts.append(f"<p>{_html.escape(step['body']['th'])}</p>")
        if step.get("example"):
            parts.append(f"<p>พิมพ์: <span class=\"type\">{_html.escape(step['example'])}</span></p>")
        url = help_image_url(step["image"])
        if url:
            parts.append(f"<div class=\"slot\" data-slot=\"{step['image']}\"><img src=\"{_html.escape(url)}\" alt=\"{_html.escape(step['title']['th'])}\"></div>")
        else:
            parts.append(
                f"<div class=\"slot\" data-slot=\"{step['image']}\">[IMAGE: {step['image']} — "
                f"{_html.escape(step['image_prompt'])}]</div>"
            )
        parts.append(f"<p class=\"en\">{_html.escape(step['title']['en'])} — {_html.escape(step['body']['en'])}</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"

