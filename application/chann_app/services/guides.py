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


def help_image_url(slot: str, *, absolute: bool = True) -> str:
    """The picture for a guide slot: a full https URL as given, or a path
    under this service (chann_app/static/help) made absolute with
    PUBLIC_BASE_URL. Chat needs an absolute URL (LINE fetches it) — with no
    base configured it gets "" and sends no picture; the guide page asks for
    absolute=False and proxies the path itself."""
    try:
        data = json.loads(_IMAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = str((data.get("images") or {}).get(slot) or "").strip()
    if not value or value.startswith("http"):
        return value
    if not absolute:
        return value
    from ..config import settings

    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}{value}" if base else ""


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
                    "th": "พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้านที่ซื้อ ระบบจะผูกบัญชีให้ ถ้ามีหลายร้านจะมีปุ่มให้เลือก · "
                          "อยู่กับหลายร้านได้ คุยทีละร้าน — ถามว่า \"อยู่กับร้านไหนบ้าง\" เพื่อดูรายชื่อ (มีบอกว่ากำลังคุยกับร้านไหน) "
                          "พิมพ์ \"เปลี่ยนร้าน\" เพื่อสลับ · ถ้าพิมพ์เลขงานหรือ S/N ของอีกร้าน ระบบจะสลับให้เองแล้วบอกว่าสลับไปร้านไหน",
                    "en": "Type the serial number on the sticker, or the shop's name. Several shops → buttons to pick one. "
                          "You can be with several shops and talk to one at a time: ask \"which shops am I with\" for the list "
                          "(it marks the one you are talking to) and say \"change shop\" to switch. Naming a job or serial that "
                          "belongs to another of your shops switches you there and says so.",
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
                "key": "talk", "title": {"th": "คุยกับร้าน", "en": "While a conversation with the shop is open every message reaches them and the other menus stay out of the way — type \"end chat\" when you are done. Talk to the shop"},
                "body": {
                    "th": "ระหว่างคุยกับร้าน ข้อความทุกอย่างจะถึงร้าน เมนูอื่นจะพักไว้ก่อน — พิมพ์ \"จบการสนทนา\" เมื่อคุยเสร็จแล้วค่อยใช้เมนูต่อ · พิมพ์ \"คุยกับร้าน\" (ต่อด้วยคำถามได้เลย เช่น \"คุยกับร้าน ราคาแอร์ 12000 BTU\") เจ้าหน้าที่ของร้านจะตอบกลับในแชทนี้ ระหว่างคุย ข้อความที่พิมพ์จะส่งถึงร้านทั้งหมด (ไม่มีข้อความยืนยันทุกครั้ง ถ้าส่งไม่ได้ระบบจะบอก) ไม่เปิดเป็นงานซ่อม กลับมาคุยใหม่ก็ต่อจากแชทเดิม · พิมพ์ \"จบการสนทนา\" เมื่อเสร็จ ไม่มีข้อความสักพักระบบปิดให้เอง · บนหน้าจอลูกค้ามีช่องแชทเดียวกัน",
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
                    "th": "แก้ข้อมูลของตัวเอง: พิมพ์ \"แก้ไขข้อมูลส่วนตัว\" แล้วพิมพ์สิ่งที่จะแก้ หลายช่องพร้อมกันได้ เช่น \"ชื่อ สมชาย ใจดี ที่อยู่ 99/1 เบอร์โทร 0891234567\" · พิมพ์ \"ลงทะเบียนสินค้า\" แล้วตามด้วย S/N ที่ร้านบันทึกไว้ให้ เครื่องจะผูกกับคุณ ถ้าระบบยังไม่รู้จักหมายเลข ให้ติดต่อร้าน · บอกวันที่ซื้อได้ด้วยถ้าร้านยังไม่ได้ใส่: \"ลงทะเบียนสินค้า SN12345678 ซื้อเมื่อ 1 ก.ย. 2569\"",
                    "en": "Your own details: type \"edit my profile\", then what to change — several at once is fine, e.g. \"name Somchai Jaidee address 99/1 phone 0891234567\". Type \"register product\" then the S/N the shop recorded. Unknown S/N → contact the shop. Add the purchase date if the shop did not: \"register product SN12345678 bought 2026-09-01\".",
                },
                "commands": ["ลงทะเบียนสินค้า", "ประกันของฉัน"],
                "example": "ลงทะเบียนสินค้า SN12345678",
                "image": "customer-register",
                "image_prompt": "หน้าจอแชท: ลูกค้าพิมพ์ 'ลงทะเบียนสินค้า SN12345678' บอทตอบ 'ลงทะเบียน แอร์ (S/N …) เป็นของคุณแล้ว' พร้อมไอคอนโล่สีส้ม",
            },
            {
                "key": "report", "title": {"th": "แจ้งซ่อม", "en": "Report a fault"},
                "body": {
                    "th": "พิมพ์อาการที่เสียมาได้เลย เช่น \"แอร์ไม่เย็น\" ระบบจะเลือกเครื่องให้ (หรือให้กดเลือกถ้ามีหลายเครื่อง) แล้วบอกกลับว่างานนี้ผูกกับเครื่องไหนและยังอยู่ในประกันหรือไม่ จากนั้นถามที่อยู่ (ถ้ามีที่อยู่ในข้อมูลส่วนตัวแล้ว จะถามว่าใช้ที่อยู่นั้นไหม ตอบ \"ใช่\" หรือพิมพ์ที่อยู่ใหม่) และวันเวลานัด · ถ้าไม่มีหมายเลขเครื่อง กด \"ไม่มีหมายเลขเครื่อง\" ได้ ระบบจะแจ้งว่างานนี้ยังไม่ได้ผูกกับเครื่องที่ลงทะเบียน · ส่งรูปอาการมาในแชทได้ ระบบแนบกับงานให้ช่างดู",
                    "en": "Describe what is wrong, e.g. \"air con not cooling\". The machine is picked for you (or you tap which one), the reply names it and says whether it is still under warranty, then the address (if your profile has one it is offered — answer \"yes\" or type another) and the appointment are asked. With no serial, the job is filed and says so.",
                },
                "commands": ["แจ้งซ่อม", "ไม่มีหมายเลขเครื่อง"],
                "example": "แอร์ไม่เย็น มีน้ำหยด",
                "image": "customer-report",
                "image_prompt": "แชท 3 ฟอง: ลูกค้า 'แอร์ไม่เย็น' → บอท 'รับแจ้งแล้ว เลขงาน T-2026-0001 ขอที่อยู่' → ลูกค้าพิมพ์ที่อยู่ → บอทถามวันนัด",
            },
            {
                "key": "status", "title": {"th": "ดูสถานะ / ขอเลื่อนนัด / ยกเลิก", "en": "Status, ask to move, cancel"},
                # 11 ก.ย. 2569: asking to move a visit is a REQUEST now — the
                # shop checks whether a technician is free and confirms back.
                # The guide said "เลื่อนนัด: …" as though it happened on the
                # spot, which is what the road used to do.
                "body": {
                    "th": "พิมพ์ \"งานของฉัน\" หรือ \"สถานะการซ่อม\" · ขอเลื่อนนัด: \"ขอเลื่อนนัดวันศุกร์ บ่าย 2\" หรือบอกแค่เวลา \"เลื่อนนัดเป็น 9 โมงเช้า\" (วันเดิม) — ร้านจะเช็คคิวช่างแล้วยืนยันกลับ นัดเดิมยังอยู่จนกว่าร้านจะยืนยัน · ยกเลิก: \"ยกเลิกงาน\" (ยกเลิกได้เลย)",
                    "en": "\"my jobs\" / \"repair status\" · ask to move it: \"move it to Friday 2pm\", or just a time \"move it to 9am\" (same day) — the shop checks the technicians' schedule and confirms; the existing appointment stands until they do · \"cancel job\" (cancels straight away)",
                },
                "commands": ["งานของฉัน", "สถานะการซ่อม", "เลื่อนนัด", "ยกเลิกงาน"],
                "example": "งานของฉัน",
                "image": "customer-status",
                "image_prompt": "การ์ดสถานะงานซ่อม T-2026-0001 แสดงขั้น รอมอบหมาย → ช่างรับแล้ว → กำลังทำ → เสร็จ พร้อมไอคอนนาฬิกา",
            },
            {
                "key": "after", "title": {"th": "หลังซ่อมเสร็จ", "en": "After the repair"},
                "body": {
                    "th": "พอช่างปิดงาน คุณจะได้ข้อความทันทีว่างานเลขไหนเสร็จแล้วและช่างทำอะไรไปบ้าง (ยังไม่ต้องรอร้านตรวจ) · จากนั้นเมื่อร้านตรวจงานผ่าน คุณจะได้ปุ่มให้คะแนน 1–3 กดได้เลย · ถ้าร้านตรวจแล้วขอให้ช่างกลับไปดูอีกครั้ง ระบบจะแจ้งคุณด้วย · ดูประวัติทั้งหมดได้ที่ \"เปิดหน้าจอลูกค้า\" ในเมนู",
                    "en": "The moment the technician closes the job you are told which job is finished and what was done — you do not wait for the shop's review. When the shop then approves it you get 1–3 rating buttons; if the shop sends the technician back instead, you are told that too. Everything is on the home screen (menu → Open the dashboard).",
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
            {
                "key": "invoices", "title": {"th": "ใบแจ้งหนี้และใบเสร็จ", "en": "Invoices and receipts"},
                "body": {
                    "th": "พิมพ์ \"ใบแจ้งหนี้ของฉัน\" หรือ \"ยอดค้าง\" เพื่อดูใบแจ้งหนี้ของคุณกับร้านนี้ ยอดที่ค้าง และวันครบกำหนด · พิมพ์ \"ขอใบเสร็จ\" — ถ้าชำระครบแล้วจะได้ลิงก์ใบเสร็จ ถ้ายังค้างระบบบอกยอดค้างและทางร้านจะติดต่อเรื่องช่องทางชำระ · เมื่อร้านออกใบเสร็จ ระบบส่งลิงก์มาให้ในแชทนี้เอง · บนหน้าจอลูกค้ามีส่วน \"ใบแจ้งหนี้และใบเสร็จ\" พร้อมปุ่มเปิด PDF",
                    "en": "Type \"my invoices\" or \"outstanding\" to see your invoices with this shop, what is owed and when it is due. \"receipt\": the receipt link once paid in full; otherwise what is outstanding, and the shop will contact you about how to pay. When the shop issues a receipt the link arrives in this chat. The home screen has the same list with PDF buttons.",
                },
                "commands": ["ใบแจ้งหนี้ของฉัน", "ยอดค้าง", "ขอใบเสร็จ"],
                "example": "ขอใบเสร็จ",
                "image": "customer-invoices",
                "image_prompt": "หน้าจอแชท: ลูกค้าพิมพ์ 'ขอใบเสร็จ' บอทตอบ 'ใบเสร็จของ INV-2026-0001 (ยอด 32,100.00 บาท):' พร้อมลิงก์ ไอคอนใบเสร็จสีส้ม",
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
                    "th": "ขอรหัสเชิญช่างจากร้าน แล้วพิมพ์รหัสนั้นในแชทนี้ — LINE ช่างลงทะเบียนแยกจาก LINE ทีมขาย แม้เป็นเจ้าของหรือ CS ของร้านก็ต้องมีรหัสเชิญช่าง (ข้อมูลส่วนตัวใช้ร่วมกัน) ถ้าอยู่หลายร้าน พิมพ์ \"เปลี่ยนร้าน\" เพื่อสลับ",
                    "en": "Get a technician invite code from the shop and type it here — the technician LINE is its own registration; even the shop's owner or CS needs a technician code (personal details are shared). Several shops → \"switch shop\".",
                },
                "commands": ["เปลี่ยนร้าน"],
                "example": "ABCD1234",
                "image": "tech-join",
                "image_prompt": "ช่างถือโทรศัพท์ พิมพ์รหัสเชิญ 8 ตัวในแชท บอทตอบ 'เข้าร่วม ร้านแอร์ดี แล้ว' ธีมน้ำเงิน",
            },
            {
                "key": "take", "title": {"th": "รับงาน", "en": "Take a job"},
                "body": {
                    "th": "พิมพ์ \"งานที่เปิดรับ\" แล้วกดปุ่มงาน หรือพิมพ์ \"รับงาน T-2026-0001\" (เห็นเฉพาะงานที่ CS เปิดรับหรือมอบหมายมา งานที่ลูกค้าเพิ่งแจ้งจะรอ CS ก่อน) · งานที่ CS มอบหมายตรงมาจะมีปุ่ม รับ/ปฏิเสธ · งานของทีม: หัวหน้ากด \"รับงาน\" ให้ทีมก่อน แล้วสมาชิกดู \"งานของทีม\" และรับต่อ · รับไม่ได้: \"ปฏิเสธงาน T-… เหตุผล\"",
                    "en": "\"open jobs\" then tap one, or \"claim T-2026-0001\" (only jobs the shop opened or assigned appear — a fresh customer report waits for CS). A job given to you directly has accept/decline. Can't: \"decline job T-… reason\".",
                },
                "commands": ["งานที่เปิดรับ", "รับงาน", "ปฏิเสธงาน", "งานของฉัน", "งานวันนี้", "งานของทีม"],
                "example": "รับงาน T-2026-0001",
                "image": "tech-take",
                "image_prompt": "การ์ดรายการงานที่เปิดรับ 2 งาน แต่ละงานมีปุ่ม 'รับงาน' สีน้ำเงิน และงานหนึ่งมีป้าย 'มอบหมายให้คุณ'",
            },
            {
                "key": "checkin", "title": {"th": "เช็คอินเมื่อถึงหน้างาน", "en": "Check in on site"},
                "body": {
                    "th": "ถึงบ้านลูกค้าแล้วพิมพ์ \"เช็คอิน\" หรือ **ส่งตำแหน่ง** (LINE > + > ตำแหน่ง) เพื่อเช็คอินพร้อมพิกัด (มีงานเดียวระบบรู้เอง) งานจะเปลี่ยนเป็น กำลังทำ — ปิดงานได้หลังจากนี้เท่านั้น · ส่งรูปหน้างานมาในแชทได้เลย ระบบแนบกับงานและใส่ในรายงาน PDF · บอกสถานการณ์ได้เลย: \"กำลังไป\" \"ถึงช้า 20 นาที\" \"ลูกค้าไม่อยู่บ้าน\" \"ต้องสั่งอะไหล่\" \"วันนี้ทำไม่จบ\" ระบบบันทึกลงงาน แจ้งร้าน/ลูกค้าให้ และเสนอปุ่มเลื่อนนัดหรือปิดงาน",
                    "en": "On arrival type \"check in\" (one job: it knows which). The job becomes in progress — finishing is only possible after this. Send photos of the site here; they go on the job and into the PDF. · just say what is happening: \"on my way\", \"running 20 min late\", \"customer not home\", \"need parts\", \"cannot finish today\" — noted on the job, the shop or customer is told, and you get reschedule / finish buttons",
                },
                "commands": ["เช็คอิน"],
                "example": "เช็คอิน T-2026-0001",
                "image": "tech-checkin",
                "image_prompt": "ช่างยืนหน้าบ้านลูกค้า กดปุ่ม 'เช็คอินเริ่มงาน' บนโทรศัพท์ มีหมุดตำแหน่งสีน้ำเงิน",
            },
            {
                "key": "finish", "title": {"th": "ปิดงาน + รายงาน", "en": "Finish + report"},
                "body": {
                    "th": "พิมพ์ \"ปิดงาน\" แล้วตอบ 3 อย่าง: ปัญหาที่พบ / สิ่งที่แก้ไข / อะไหล่ที่เปลี่ยน (พิมพ์ \"ไม่มี\" ได้) ระหว่างตอบถามเรื่องงานได้ เช่น \"ลูกค้าเบอร์อะไร\" ระบบตอบแล้วรอคำตอบเดิมต่อ · ตอบผิดพิมพ์ \"พิมพ์ผิด ที่พบคือ …\" เพื่อแก้คำตอบก่อนหน้า · ระบบส่งให้ CS ตรวจทันที **และแจ้งลูกค้าทันทีว่างานเสร็จแล้ว พร้อมสรุป \"สิ่งที่แก้ไข\" ที่คุณพิมพ์** จึงควรเขียนให้ลูกค้าอ่านรู้เรื่อง",
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
        # Each step: a one-line lead in `body`, then `how` — one line per
        # thing a person can do, the words to type beside it. Rewritten from
        # twelve-bullet paragraphs on 20 ก.ย. 2569 (owner: "ตัวอักษรเคลื่อน
        # อ่านไม่รู้เรื่อง"): a LINE bubble has no indentation and no
        # columns, so a line must carry one idea and stand on its own.
        "steps": [
            {
                "key": "setup", "title": {"th": "ตั้งร้านให้พร้อม", "en": "Set the shop up"},
                "body": {
                    "th": "รหัสร้าน ทีมช่าง ข้อมูลบริษัท และเวลาตอบ (SLA) — ตั้งครั้งเดียวก่อนเริ่มงาน",
                    "en": "The shop code, technician teams, company details and reply times (SLA) — set once before work starts.",
                },
                "how": [
                    {"th": "ดูรหัสร้าน ให้ลูกค้าใช้ผูก LINE กับร้าน", "en": "Shop code, for customers to link with", "type": "ข้อมูลร้าน"},
                    {"th": "ดูเวลาเตือนที่ตั้งไว้", "en": "See the reply times", "type": "ดู SLA"},
                    {"th": "ตั้งเวลาเตือน — เตือนผู้จ่ายงาน/ช่าง/ผู้อนุมัติเมื่อเกินเวลา และแจ้งเจ้าของเมื่อยังไม่ขยับ (ตั้งที่หน้าข้อมูลบริษัทก็ได้)",
                     "en": "Set the reply times — dispatchers, technicians and approvers are reminded past the limit, the owner when nothing moves (also on the company page)",
                     "type": "ตั้ง SLA งานไม่มีคนรับ 1 ชม. ช่างไม่ตอบ 30 นาที เลยนัด 15 นาที แจ้งเจ้าของหลัง 1 ชม. อนุมัติค้าง 4 ชม."},
                    {"th": "ขอรหัสเชิญให้คนเข้าร่วม — ระบบถามว่าช่างหรือทีมขาย", "en": "An invite code — it asks technician or sales", "type": "ขอรหัสเชิญ"},
                    {"th": "หรือระบุเลย", "en": "Or say which", "type": "ขอรหัสเชิญช่าง / ขอรหัสเชิญ Sales"},
                    {"th": "ลูกค้าไม่ใช้รหัสเชิญ — ได้รหัสร้านให้ลูกค้าพิมพ์ใน LINE ลูกค้า", "en": "Customers do not use invite codes — this gives the shop code they type in the customer LINE", "type": "ขอรหัสเชิญลูกค้า"},
                    {"th": "สร้างทีมช่าง", "en": "Create a technician team", "type": "สร้างทีมช่าง แอร์"},
                    {"th": "เพิ่มคนเข้าทีม", "en": "Add someone to a team", "type": "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า"},
                    {"th": "ข้อมูลบริษัทสำหรับเอกสาร", "en": "Company details for documents", "type": "ข้อมูลบริษัท"},
                    {"th": "ให้ลูกค้าที่ผูกร้านเข้ารายชื่อทันที (ถามได้ \"…ตอนนี้เปิดอยู่ไหม\")", "en": "Linked customers join the list at once (ask \"…is it on?\")", "type": "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด"},
                    {"group": {"th": "เอกสารแบบของร้านเอง", "en": "Your own document layout"}},
                    {"th": "ให้ AI ร่างแบบให้ก่อน — ยังไม่ใช้จริงจนกว่าจะกด \"ใช้เลย\" มีปุ่มดูตัวอย่างและไฟล์ Word ไว้แก้เอง",
                     "en": "Let the AI draft one — nothing is in use until you press use it; preview and a Word file to edit",
                     "type": "ออกแบบใบเสนอราคา / ออกแบบใบรายงานการซ่อม"},
                    {"th": "หรือบนแดชบอร์ด > แบบฟอร์มเอกสาร: อัปไฟล์ Word (.docx มีตัวอย่างให้โหลด) → ดูตัวอย่าง → เผยแพร่ → เลือกใช้แบบนี้ (เอกสารที่ออกไปแล้วไม่เปลี่ยน)",
                     "en": "Or dashboard > document templates: upload a .docx (samples to download) → preview → publish → choose this one (issued documents never change)"},
                    {"th": "ไม่เลือกหรือเลิกใช้ = กลับไปใช้แบบมาตรฐาน · \"เลิกใช้รุ่นนี้\" ระบบบอกก่อนว่าเอกสารใหม่จะใช้รุ่นไหนแทน",
                     "en": "No choice or none active = the built-in layout · retire tells you first what will render instead"},
                    {"th": "ต้องมีสิทธิ์ \"ตั้งค่าร้าน\"", "en": "Needs the shop settings permission"},
                ],
                "commands": ["ข้อมูลร้าน", "ดู SLA", "ตั้ง SLA", "ขอรหัสเชิญ", "ขอรหัสเชิญช่าง", "ขอรหัสเชิญทีมขาย", "สร้างทีมช่าง", "ทีมช่าง", "รายชื่อช่าง", "ข้อมูลบริษัท", "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ", "ตั้งค่าแชท", "ออกแบบใบเสนอราคา", "ออกแบบใบรายงานการซ่อม"],
                "example": "สร้างทีมช่าง แอร์",
                "image": "sales-setup",
                "image_prompt": "แผนผังร้าน: กล่อง 'ร้าน (รหัส ABCD01)' เชื่อมไป 'ทีมช่าง แอร์ (หัวหน้า สมศักดิ์)' และ 'ลูกค้า' ธีมเขียว",
            },
            {
                "key": "units", "title": {"th": "บันทึกเครื่องที่ขาย", "en": "Record sold units"},
                "body": {
                    "th": "ลงทะเบียนเครื่องที่ขายไว้กับลูกค้า — ลูกค้าดูประกันเองได้ และงานซ่อมผูกกับเครื่องให้อัตโนมัติ",
                    "en": "Register the units you sold against the customer — they can check the warranty themselves, and repairs attach to the unit.",
                },
                "how": [
                    {"th": "ลงทะเบียนเครื่องให้ลูกค้า — ผูกกับเรคอร์ดลูกค้าคนนั้นทันที (ระบบตอบ \"ผูกกับลูกค้า สมชาย (C-…) แล้ว\")",
                     "en": "Register a unit for a customer — attached to their record at once",
                     "type": "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย"},
                    {"th": "ไม่มีลูกค้าชื่อนั้น ระบบถามก่อน ไม่ลงทะเบียนเงียบ ๆ · ไม่ระบุลูกค้าก็ลงทะเบียนได้", "en": "An unknown name is asked about, never registered silently · a unit can be registered with no customer"},
                    {"th": "ลูกค้าพิมพ์ S/N นี้ใน LINE ลูกค้าเพื่อดูประกันเอง", "en": "The customer types this S/N in the customer LINE to see the warranty"},
                    {"th": "ลูกค้าที่ลงทะเบียนเครื่องเอง จะเข้ารายชื่อเป็น \"ลูกค้า\" ทันที (ไม่ใช่ลูกค้ามุ่งหวัง) และเครื่องผูกกับเรคอร์ดนั้นให้เอง — ร้านได้แจ้งเตือน",
                     "en": "Someone who registers a unit joins the customer list as a customer (not a lead) and the unit is linked to that record — the shop is told"},
                    {"th": "วันที่ซื้อใส่ทีหลังได้ (ยังไม่กำหนดวันหมดประกันจนกว่าจะรู้)", "en": "The purchase date can come later (no end date until it is known)", "type": "วันที่ซื้อ SN12345678 1 ก.ย. 2569"},
                    {"th": "หรือใส่ตั้งแต่แรก", "en": "Or give it at once", "type": "ลงทะเบียนสินค้า SN12345678 แอร์ ซื้อวันที่ 1 ก.ย. 2569 ประกัน 2 ปี"},
                    {"th": "ระยะประกันเริ่มต้นมาจากสินค้าแต่ละตัว (หรือช่องระยะประกันในหน้าสินค้า)", "en": "The default period comes from the product (or the warranty field on the products page)", "type": "สินค้า FAN01 รับประกัน 2 ปี"},
                    {"th": "ดูเครื่องทั้งหมด — บอกลูกค้าที่ผูกและว่าผูก LINE แล้วหรือยัง", "en": "Every unit, with its customer and whether they are linked on LINE", "type": "รายการประกัน"},
                    {"th": "เปิดงานให้ลูกค้าที่โทรมา — ระบบผูกงานกับเครื่องที่คนนั้นลงทะเบียนไว้ให้เอง มีหลายเครื่องจะมีปุ่มให้เลือก",
                     "en": "A job you open for a caller is linked to their registered unit, with buttons when they have several",
                     "type": "เปิดงานให้ สมชาย แอร์ไม่เย็น"},
                ],
                "commands": ["ลงทะเบียนสินค้า", "รายการประกัน"],
                "example": "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย",
                "image": "sales-units",
                "image_prompt": "หน้าจอ 'ทะเบียนสินค้า' ธีมเขียว: ปุ่ม 'บันทึกเครื่อง' ที่หัวข้อ ช่องค้นหา ปุ่ม 'นำเข้า CSV' และรายการเครื่องที่บอกลูกค้าที่ผูกและว่าผูก LINE แล้วหรือยัง",
            },
            {
                "key": "dispatch", "title": {"th": "งานซ่อม: มอบหมาย", "en": "Repairs: dispatch"},
                "body": {
                    "th": "ลูกค้าแจ้งซ่อมแล้วคุณได้ LINE พร้อมชื่อเครื่องและสถานะประกัน — งานรอร้านก่อน ช่างยังไม่เห็นจนกว่าจะมอบหมายหรือเปิดให้รับ",
                    "en": "You hear when a customer reports, with the machine and its warranty state — the job waits for the shop; technicians do not see it until it is assigned or opened.",
                },
                "how": [
                    {"th": "ดูคิวที่รอมอบหมาย (งานที่ลูกค้าแจ้งจะมีป้าย \"ช่างยังไม่เห็น\")", "en": "The queue waiting to be dispatched", "type": "งานซ่อม"},
                    {"th": "ดูงานทั้งหมด", "en": "Every job", "type": "รายการงาน"},
                    {"th": "มอบหมายให้ทีม — ต้องมีชื่อ เบอร์ ที่อยู่ นัดครบ ระบบบอกถ้าขาด", "en": "Assign to a team — name, phone, address and appointment required; it says what is missing", "type": "มอบหมาย T-2026-0001 ให้ทีม แอร์"},
                    {"th": "ไม่ต้องพิมพ์เลขเต็ม", "en": "The short number works", "type": "มอบหมาย 0001 ให้ช่าง"},
                    {"th": "หรือ reply ข้อความแจ้งซ่อมนั้นแล้วพิมพ์ — ระบบถามว่าช่างคนไหนพร้อมปุ่มเลือก", "en": "Or reply to the report and type — it asks which technician, with buttons", "type": "มอบหมายให้ช่าง"},
                    {"th": "เปิดให้ช่างทุกคนเห็น ใครรับก่อนได้ (ต้องมีที่อยู่และนัดครบเหมือนมอบหมาย)", "en": "Open it to every technician; the first to accept takes it (address and appointment required)", "type": "เปิดให้ช่างรับ T-2026-0001"},
                    {"th": "พอลูกค้ากรอกที่อยู่/นัดครบ คุณได้ LINE อีกครั้งพร้อมอาการ", "en": "When the customer completes the address/appointment you hear again, with the fault"},
                    {"group": {"th": "ลูกค้าขอเลื่อนนัด", "en": "A customer asks to move the visit"}},
                    {"th": "ยืนยันด้วยคำพูดปกติ — ระบบเลื่อนตามเวลาที่ลูกค้าขอและแจ้งลูกค้าให้ มีหลายงานค้างจะถามว่างานไหน", "en": "Confirm in plain words — the visit moves to the time they asked and the customer is told", "type": "เลื่อนได้ / ยืนยันเป็นวันที่ตามนั้นได้"},
                    {"th": "หรือ reply ข้อความแจ้งเตือนนั้นแล้วพิมพ์ \"เลื่อนได้\"", "en": "Or reply to that notice with \"ok to move\""},
                    {"th": "กำหนดเวลาเอง", "en": "Set your own time", "type": "เลื่อนนัด T-2026-0001 พรุ่งนี้ 10 โมง"},
                    {"group": {"th": "จบได้เองไม่ต้องใช้ช่าง", "en": "Settled without a technician"}},
                    {"th": "ปิดได้เลยไม่ต้องเช็คอิน", "en": "Close it, no check-in", "type": "ปิดงาน T-2026-0001"},
                    {"th": "บันทึกสาเหตุและวิธีแก้ด้วยก็ได้", "en": "Record cause and fix too", "type": "ปิดงาน T-2026-0001 สาเหตุ ฟิวส์ขาด แก้ไข เปลี่ยนฟิวส์"},
                    {"th": "หรือทำบนแดชบอร์ด > งานซ่อม", "en": "Or dashboard > tickets"},
                    {"group": {"th": "กฎมอบหมายอัตโนมัติ", "en": "The automatic rule"}},
                    {"th": "ดูกฎที่ใช้อยู่ (หน้าข้อมูลบริษัทก็แสดงและแก้ได้)", "en": "See the active rule (the company page shows and edits it too)", "type": "ดูกฎมอบหมาย"},
                    {"th": "ปิดกฎ — ระบบถามยืนยันก่อน แล้วงานใหม่ต้องมอบหมายเอง", "en": "Switch it off — confirmed first; new work is then assigned by hand", "type": "ปิดกฎมอบหมาย"},
                ],
                "commands": ["รายการงาน", "มอบหมาย", "ดูกฎมอบหมาย", "ปิดกฎมอบหมาย"],
                "example": "มอบหมาย T-2026-0001 ให้ทีม แอร์",
                "image": "sales-dispatch",
                "image_prompt": "หน้าจอ 'งานซ่อม' บนแดชบอร์ด การ์ดงาน T-2026-0001 มีช่อง 'มอบหมายให้…' เลือกทีมแอร์ และปุ่มมอบหมายสีเขียว ด้านล่างเป็นงานอื่นในร้านพร้อมป้ายสถานะ",
            },
            {
                "key": "chats", "title": {"th": "แชทลูกค้า", "en": "Customer chats"},
                "body": {
                    "th": "ลูกค้าที่กด \"คุยกับร้าน\" ใน LINE บริการลูกค้า จะขึ้นที่ หน้าจอ > แชทลูกค้า — ตอบที่หน้านั้นเท่านั้น ตอบใน LINE ร้านไม่ถึงลูกค้า",
                    "en": "A customer who taps \"talk to the shop\" appears under home > Customer chats — answer there only; a reply in the shop's LINE never reaches them.",
                },
                "how": [
                    {"th": "LINE แจ้งทุกคนแค่ตอนเปิดแชทใหม่พร้อมข้อความแรก ที่เหลืออ่านและตอบบนหน้าแชทลูกค้า · คนแรกที่ตอบเป็นเจ้าของการสนทนา",
                     "en": "LINE announces a NEW conversation with the first thing they said; the rest is read and answered on the chats page · the first to answer owns it"},
                    {"th": "ปุ่ม \"ข้อมูลลูกค้า\" บนหัวแชท เปิดดูดีล งานซ่อม และบันทึกของคนนี้ กดไปที่เรคอร์ดได้เลย", "en": "The \"Customer\" button on the thread opens their deals, jobs and notes, each a link to the record"},
                    {"th": "ส่งรูปได้ — กดปุ่มแนบรูปข้างช่องพิมพ์ เลือกรูป พิมพ์คำอธิบายถ้าต้องการ แล้วกดส่งรูป ลูกค้าเห็นเป็นรูปใน LINE · รูปที่ลูกค้าส่งมาระหว่างคุยขึ้นในแชทนี้เช่นกัน",
                     "en": "Pictures go too — tap the attach button beside the box, pick one, add a caption if you like, then send; the customer sees it as a picture in LINE · a picture the customer sends while talking shows here as well"},
                    {"th": "ร้านเริ่มคุยเองก็ได้ — ลูกค้าต้องผูก LINE กับร้านแล้ว", "en": "The shop can open the conversation — the customer must be linked on LINE", "type": "คุยกับลูกค้า สมชาย"},
                    {"th": "หรือจากงาน", "en": "Or from a job", "type": "คุยกับลูกค้า T-2026-0001"},
                    {"th": "ใส่ข้อความแรกต่อท้ายได้", "en": "A first line may follow a colon", "type": "คุยกับลูกค้า สมชาย: พรุ่งนี้ช่างไปได้ไหม"},
                    {"th": "หรือกดปุ่ม คุยกับลูกค้า บนหน้างานซ่อม/รายชื่อลูกค้า", "en": "Or the Chat with customer button on a job or the customer list"},
                    {"th": "ปล่อยเกินเวลาตอบ (ค่าเริ่มต้น 15 นาที) ระบบแจ้งลูกค้าว่าจะติดต่อกลับและพักการสนทนา — ตอบทีหลังได้ ลูกค้าจะได้รับคำเชิญให้เปิดแชทต่อ",
                     "en": "Past the reply time (default 15 min) the customer is told you will get back and the chat is paused — answer later and they are invited to reopen it"},
                    {"th": "ลูกค้าเงียบ 1 ชั่วโมงปิดให้เอง", "en": "An hour of customer silence closes it"},
                    {"th": "ตั้งเวลาเอง (หรือหน้าข้อมูลบริษัท)", "en": "Set both times (or on the company page)", "type": "ตั้งค่าแชท"},
                ],
                "commands": ["ตั้งค่ารับลูกค้าใหม่อัตโนมัติ"],
                "example": "หน้าจอ > แชทลูกค้า",
                "image": "sales-chats",
                "image_prompt": "หน้าจอแดชบอร์ดสีเขียว รายการแชทลูกค้า 2 รายการ รายการแรกมีป้าย 'ลูกค้ารอคำตอบ' ด้านขวาเป็นบทสนทนา มีรูปที่ร้านส่งและรูปที่ลูกค้าส่ง ด้านล่างมีปุ่มแนบรูป ช่องพิมพ์คำตอบ และปุ่ม 'ส่ง'",
            },
            {
                "key": "approve", "title": {"th": "งานซ่อม: ตรวจรายงาน", "en": "Repairs: review reports"},
                "body": {
                    "th": "ช่างปิดงานแล้วคุณได้ LINE — ตรวจรายงาน อนุมัติหรือตีกลับ ผ่านครบแล้วลูกค้าได้แบบประเมินและ PDF อัตโนมัติ",
                    "en": "When a technician closes a job you hear — review the report, approve or reject; once every step passes the customer gets the survey and the PDF.",
                },
                "how": [
                    {"th": "ดูรายงานที่รอ", "en": "Reports waiting", "type": "รายการรออนุมัติ"},
                    {"th": "อนุมัติ", "en": "Approve", "type": "อนุมัติ SR-2026-0001"},
                    {"th": "ตีกลับพร้อมเหตุผล — เหตุผลส่งถึงช่างเท่านั้น ลูกค้าจะรู้ว่าช่างต้องกลับไปดูอีกครั้ง", "en": "Reject with a reason — the reason goes only to the technician; the customer hears the technician is coming back", "type": "ตีกลับ SR-2026-0001 เหตุผล"},
                    {"th": "เจ้าของร้าน (หรือคนที่ตั้งกฎอนุมัติได้) อนุมัติแทนได้ทุกขั้น แม้ขั้นนั้นรอคนอื่นอยู่ — รายงานจึงไม่ค้าง", "en": "The owner (or anyone who may manage the approval rules) can act on every step, even one waiting on someone else — nothing stays stuck"},
                    {"th": "ตั้งขั้นตอนการอนุมัติ", "en": "Change the approval flow", "type": "ตั้งการอนุมัติ"},
                    {"th": "ลูกค้ารู้ตั้งแต่ช่างปิดงานว่างานเสร็จและช่างทำอะไรไป — PDF ส่งให้หลังผ่านครบ", "en": "The customer already heard at check-out that the job was finished and what was done — the PDF follows once every step passes"},
                ],
                "commands": ["รายการรออนุมัติ", "อนุมัติ", "ตีกลับ", "ตั้งการอนุมัติ", "ออกรายงาน"],
                "example": "อนุมัติ SR-2026-0001",
                "image": "sales-approve",
                "image_prompt": "การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' ด้านล่างเป็นคิวที่เหลือพร้อมป้าย รอตรวจ / อนุมัติแล้ว",
            },
            {
                "key": "crm", "title": {"th": "ลูกค้า ดีล ใบเสนอราคา", "en": "Customers, deals, quotes"},
                "body": {
                    "th": "รายชื่อลูกค้า ดีล ใบเสนอราคา นัดหมาย และบันทึก — พิมพ์ตามที่พูด ชื่อซ้ำระบบให้เลือก ไม่เดาให้",
                    "en": "Customers, deals, quotes, appointments and notes — type it as you would say it; a duplicate name gets a choice, never a guess.",
                },
                "how": [
                    {"group": {"th": "ลูกค้า", "en": "Customers"}},
                    {"th": "ดูรายชื่อ", "en": "The list", "type": "รายชื่อลูกค้า"},
                    {"th": "เพิ่มลูกค้า", "en": "Add one", "type": "สร้างลูกค้า สมชาย ใจดี 0812345678"},
                    {"th": "เพิ่มหลายคนในข้อความเดียว — วางรายชื่อทีละบรรทัด \"ชื่อ นามสกุล เบอร์ อีเมล\" คนที่ไม่มีเบอร์ระบบถามทีละคน", "en": "Several at once — one person per line \"first last phone email\"; a row without a phone is asked for one", "type": "เพิ่มลูกค้าหลายคน"},
                    {"th": "บนหน้ารายชื่อลูกค้า: ปุ่ม \"เพิ่มลูกค้าหลายคน\" และ \"นำเข้า CSV\" (มีไฟล์ตัวอย่าง) อยู่ที่หัวรายการ", "en": "On the customers page the \"Add several\" and \"Import CSV\" buttons sit at the head of the list (sample file provided)"},
                    {"th": "ยืนยันหลายคนพร้อมกัน", "en": "Confirm several at once", "type": "เปลี่ยน สมชาย สมหญิง สมศรี เป็นลูกค้ายืนยัน"},
                    {"th": "ลบออกจากรายชื่อ — ระบบถามยืนยันก่อน (เก็บถาวร ไม่ลบทิ้ง) หลายคนก็ได้ \"ลบลูกค้า สมชาย กับ สมหญิง\"", "en": "Remove — asks first (archived, not erased); several works too", "type": "ลบ Lead สมชาย"},
                    {"th": "บนหน้าจอ: ปุ่ม \"เลือกหลายรายการ\" ที่หัวรายการ แล้วติ๊กแถวที่ต้องการ แถบด้านล่างมี ยืนยันเป็นลูกค้า / ลบออกจากรายชื่อ", "en": "On the dashboard: \"Select several\" at the head of the list, tick the rows, and the bar at the bottom offers confirm / remove"},
                    {"th": "เพิ่มซ้ำเบอร์/อีเมลเดิม ระบบบอกว่าเป็นใครและให้เลือก ใช้เดิม / อัปเดต / ยกเลิก", "en": "A duplicate phone/email is named, with use / update / cancel"},
                    {"th": "เบอร์โทรต้องเป็นตัวเลข ระบบไม่บันทึกเบอร์ที่มีตัวอักษรและบอกเหตุผล", "en": "Phone numbers are digits; one with letters is refused and the reason said"},
                    {"th": "ดูสิ่งที่ทำได้ทีละหมวด", "en": "What can be done, by area", "type": "ทำอะไรกับ Lead ได้บ้าง"},
                    {"group": {"th": "ดีล", "en": "Deals"}},
                    {"th": "เปิดดีล", "en": "Open a deal", "type": "สร้างดีลให้ สมชาย"},
                    {"th": "ใส่มูลค่าและวันคาดว่าจะปิดในประโยคเดียว", "en": "Value and expected close in one sentence", "type": "สร้างดีลให้ อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้"},
                    {"th": "หลังเพิ่มลูกค้า บอกว่าสนใจอะไร ระบบเสนอเปิดดีลพร้อมสินค้าให้", "en": "Right after adding a customer, say what they want and a deal with that line is offered", "type": "ลูกค้าสนใจอยากได้พัดลม 1 ตัว"},
                    {"th": "ดีลแพ้ — ระบบถามเหตุผล พิมพ์สั้น ๆ หรือ \"ข้าม\" (เหตุผลแสดงใน \"ดีลที่แพ้\")", "en": "A lost deal — the reason is asked; type it or \"skip\"", "type": "ดีลนี้แพ้"},
                    {"th": "เปลี่ยนสถานะหลายดีลพร้อมกัน", "en": "Move several deals at once", "type": "ย้ายดีล D-2026-0001 กับ D-2026-0002 ไปชนะ"},
                    {"th": "เก็บถาวรหลายดีล — ถามยืนยันครั้งเดียว", "en": "Archive several — one confirmation", "type": "ลบดีล D-2026-0001 D-2026-0002"},
                    {"th": "ดูดีลที่เพิ่งทำ", "en": "The deal in play", "type": "ดีลล่าสุด / สินค้าในดีล"},
                    {"group": {"th": "สินค้าในดีลและใบเสนอราคา", "en": "Lines on a deal or quote"}},
                    {"th": "เพิ่มสินค้าตามที่พูด — ไม่บอกราคา ระบบดูจากรายการสินค้า ไม่มีก็ถามราคาแล้วจำรายการไว้ ตอบแค่ \"1500\" ได้เลย", "en": "Add a line as you would say it — no price: the catalogue is checked, else the price is asked and \"1500\" is enough", "type": "เพิ่มสินค้า พัดลม 2 ตัว ราคา 1500"},
                    {"th": "หลายอย่างในประโยคเดียว (ใช้ได้ทั้งดีล ใบเสนอราคา และเพิ่มสินค้าเข้าคลัง)", "en": "Several in one sentence (a deal, a quote, or the catalogue)", "type": "เพิ่มพัดลม 2 ตัว และ แอร์ 1 ตัว"},
                    {"th": "บวกเพิ่มจากเดิม", "en": "Add to the quantity", "type": "เพิ่มพัดลมอีก 3 ตัว"},
                    {"th": "ตั้งจำนวนใหม่ / ลดจำนวน / เอาออก / ปรับราคา — ทุกครั้งระบบบอกยอดรวมดีล", "en": "Set / reduce / remove / reprice — every reply states the deal total", "type": "แก้พัดลมเป็น 3 ตัว · ลดพัดลม 1 ตัว · ลบสินค้าพัดลมออก · ปรับราคาพัดลมเป็น 2000"},
                    {"th": "ออกเอกสารใบเสนอราคา", "en": "Issue the quotation", "type": "ออกเอกสาร Q-2026-0001"},
                    {"th": "ค้นรายการสินค้า", "en": "Search the catalogue", "type": "ค้นหาสินค้า พัดลม"},
                    {"group": {"th": "ใบแจ้งหนี้และใบเสร็จ", "en": "Invoices and receipts"}},
                    {"th": "ออกใบแจ้งหนี้จากใบเสนอราคาที่ส่งแล้ว (หรือจากดีล) — ได้เลขที่ INV ยอดรวม กำหนดชำระ 30 วัน และลิงก์ PDF ในคำตอบเดียว", "en": "Invoice a sent quote (or a deal) — the INV number, total, 30-day due date and PDF link come back in one reply", "type": "ออกใบแจ้งหนี้ Q-2026-0001 · สร้างใบแจ้งหนี้ให้ดีล D-2026-0001"},
                    {"th": "บอกแค่ชื่อลูกค้าก็ได้ — ระบบออกจากดีลของเขา (หลายดีลมีปุ่มให้เลือก ไม่มีดีลบอกให้สร้างก่อน เพราะใบแจ้งหนี้ผูกกับดีลเสมอ) · บนจอ: \"สร้างใบแจ้งหนี้\" ในหน้าใบแจ้งหนี้ (ลูกค้า → ดีล → ใบเสนอราคา) หรือ \"ออกใบแจ้งหนี้\" ในหน้าดีล/ลูกค้า", "en": "The customer's name is enough — the bill comes from their deal (several: buttons to choose; none: create one first, an invoice always belongs to a deal) · on screen: \"สร้างใบแจ้งหนี้\" on the invoices page (customer → deal → quote) or \"ออกใบแจ้งหนี้\" on the deal/customer page", "type": "ออกใบแจ้งหนี้ให้ สมชาย"},
                    {"th": "ดูใบแจ้งหนี้ / รายการ / ยอดค้างชำระ", "en": "One invoice, the list, what is owed", "type": "ใบแจ้งหนี้ INV-2026-0001 · รายการใบแจ้งหนี้ · ยอดค้างชำระ"},
                    {"th": "บันทึกรับชำระ: จำนวนเงินกับช่องทาง (เงินสด/โอน/พร้อมเพย์/บัตร) มัดจำก็ได้ ไม่บอกจำนวนระบบถาม ตอบ \"5000\" หรือ \"ครบ\" ได้เลย", "en": "Record a payment: amount and method (cash/transfer/PromptPay/card), a deposit is fine; with no amount it asks — answer \"5000\" or \"ครบ\"", "type": "รับชำระ INV-2026-0001 5000 โอน · มัดจำ INV-2026-0001 2000 เงินสด · รับชำระ INV-2026-0001 ครบ"},
                    {"th": "ออกใบเสร็จเมื่อชำระครบ — ระบบส่งลิงก์ให้ลูกค้าทาง LINE ให้ด้วย", "en": "Issue the receipt once paid in full — the customer gets the link on LINE", "type": "ออกใบเสร็จ INV-2026-0001"},
                    {"th": "ยกเลิกใบแจ้งหนี้ (ถามยืนยันก่อน ยกเลิกได้เฉพาะใบที่ยังไม่มีการรับชำระ)", "en": "Void an invoice (asks first; only one with no payments)", "type": "ยกเลิกใบแจ้งหนี้ INV-2026-0001"},
                    {"group": {"th": "นัดหมายและบันทึก", "en": "Appointments and notes"}},
                    {"th": "ดูสิ่งที่ต้องทำวันนี้", "en": "Today's list", "type": "งานวันนี้"},
                    {"th": "ตั้งเตือน", "en": "Set a reminder", "type": "เตือน D-2026-0001 พรุ่งนี้"},
                    {"th": "แก้นัดเดิม (ยังเป็นนัดเดิม ไม่ได้ตั้งใหม่)", "en": "Move the appointment you already have", "type": "เลื่อนนัด C-2026-0001 เป็นศุกร์ บ่าย 2"},
                    {"th": "ยกเลิกแต่เก็บประวัติไว้", "en": "Cancel, history kept", "type": "ยกเลิกนัด สมชาย"},
                    {"th": "บนหน้าลูกค้า/ดีล/ใบเสนอราคา มีปุ่ม แก้ไขนัด และ ลบนัด (ลบมีกล่องยืนยัน)", "en": "The customer, deal and quote pages have Edit appointment and Delete appointment (delete asks first)"},
                    {"th": "กำลังเพิ่มลูกค้าอยู่แล้วพิมพ์คำสั่งอื่น ระบบถามก่อนว่าจะเปลี่ยนไปทำสิ่งนั้นหรือทำต่อ (ถ้าเปลี่ยน ระบบยังจำลูกค้าที่ค้างไว้ให้เปิดดีลได้)", "en": "Typing another command while a customer is half-added asks whether to switch or continue (the half-added customer is still offered for the deal)"},
                    {"group": {"th": "โอนให้พนักงานคนอื่น", "en": "Handing over to a colleague"}},
                    {"th": "โอนลูกค้าหรือดีลให้เพื่อนร่วมงานดูแล — คนที่รับจะได้รับแจ้งใน LINE (ต้องมีสิทธิ์โอนงาน · ชื่อซ้ำจะมีปุ่มให้เลือก)", "en": "Hand a customer or a deal to a colleague; they are told on LINE (needs the reassign permission; a duplicate name gets buttons)", "type": "โอนลูกค้า สมชาย ให้ สมหญิง · โอนดีล D-2026-0001 ให้ สมหญิง"},
                    {"th": "ทั้งหมดของคนหนึ่งไปอีกคน — ระบบแจงรายชื่อแล้วถามยืนยัน · บนหน้าจอ: ช่อง \"เจ้าของ\" ในหน้าลูกค้า/ดีล หรือเลือกหลายรายการแล้ว \"เปลี่ยนเจ้าของ\"", "en": "Everyone one person holds, to another — listed, then confirmed · on screen: the \"Owner\" field on a customer/deal, or select rows and \"Change owner\"", "type": "โอนลูกค้าทั้งหมดของ สมชาย ให้ สมหญิง"},
                ],
                "commands": ["รายชื่อลูกค้า", "สร้างดีลให้", "ออกเอกสาร", "งานวันนี้", "เลื่อนนัด", "ยกเลิกนัด", "ลบ Lead", "ทำอะไรกับ Lead ได้บ้าง", "เพิ่มลูกค้าหลายคน", "เพิ่มสินค้า", "ลบสินค้า", "ค้นหาสินค้า", "โอนลูกค้า", "โอนดีล", "ออกใบแจ้งหนี้", "รับชำระ", "ออกใบเสร็จ", "ยอดค้างชำระ"],
                "example": "งานวันนี้",
                "image": "sales-crm",
                "image_prompt": "แดชบอร์ดขายธีมเขียว: เมนูด้านซ้ายจัดกลุ่มเป็น งานขาย (แชทลูกค้า ลูกค้า ดีล ใบเสนอราคา ใบแจ้งหนี้ นัดหมาย สินค้า) / งานบริการ / เอกสารและรายงาน / จัดการร้าน ด้านขวาเป็นมูลค่าไปป์ไลน์ ทางลัด 'เริ่มงานต่อ' และดีลที่ต้องตามวันนี้",
            },
            {
                "key": "ai-reports", "title": {"th": "ถามรายงานด้วย AI", "en": "Ask for a report"},
                "body": {
                    "th": "พิมพ์สิ่งที่อยากรู้เป็นภาษาคน ระบบสรุปเป็นตัวเลขทันที อยากเห็นเป็นรูปก็ขอได้",
                    "en": "Type what you want to know in plain words and get the numbers at once; ask for a picture when you want one.",
                },
                "how": [
                    {"th": "ถามเป็นประโยค", "en": "Ask in a sentence", "type": "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / สรุปงานค้างแยกตามช่าง"},
                    {"th": "ระบบถามกลับเมื่อคำถามกว้าง (เช่น \"เทียบตามเจ้าของ หรือ แยกตามช่วงเวลา\") ตอบสั้น ๆ ได้เลย ระบบจำคำถามเดิมไว้", "en": "A broad question is asked back (\"by owner, or by period?\") — answer in a word; the original request is remembered"},
                    {"group": {"th": "กราฟ", "en": "Charts"}},
                    {"th": "ห้ากราฟสำเร็จรูป ใช้ได้ไม่จำกัด (หรือกดปุ่ม \"ดูเป็นกราฟ\" ใต้คำตอบยอดขาย)", "en": "Five ready-made charts, unlimited (or the \"View as chart\" button under a sales summary)", "type": "ขอกราฟยอดขาย · กราฟยอดขายรายเดือน · กราฟสินค้าขายดี · กราฟยอดขายรายคน · กราฟดีลแต่ละสถานะ"},
                    {"th": "กราฟแบบสั่งเอง — ระบบส่งรูปมาในแชทพร้อมสรุปสั้น ๆ", "en": "A made-to-order chart — the picture comes back in the chat with a one-line summary", "type": "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน"},
                    {"th": "กราฟสั่งเองมีโควตาต่อเดือนของแต่ละร้าน (ค่าเริ่มต้น 30 ครั้ง ผู้ดูแล Chann ปรับให้ได้) นับเฉพาะครั้งที่ได้รูปจริง ทั้งในแชทและบนหน้าจอ", "en": "Made-to-order charts have a monthly allowance per shop (30 by default, adjustable by Chann) — counted only when a picture is made, in chat and on the dashboard alike"},
                    {"th": "รายงานที่ออกมาเป็นตัวเลขเดียวก็ได้รูป เป็นการ์ดตัวเลขใหญ่", "en": "A single-number report gets a picture too, as a big-number card"},
                    {"th": "ใช้ครบแล้ว ตัวเลข ตาราง และไฟล์ยังโหลดได้ตามปกติ หายแค่รูป", "en": "Past the allowance the numbers, table and files still come; only the picture does not"},
                    {"group": {"th": "บนหน้าจอ", "en": "On the dashboard"}},
                    {"th": "เมนู \"รายงาน AI\" มีตารางพร้อมกราฟแท่งและปุ่มดาวน์โหลด CSV/PDF", "en": "\"AI reports\" adds a table with bars and CSV/PDF downloads"},
                    {"th": "ใต้ตารางมีปุ่ม \"ปรับรายงานนี้\" เปลี่ยนช่วงเวลา/การแยกกลุ่ม/ตัวเลข แล้วกด \"ดูใหม่ตามที่ปรับ\" ไม่ต้องพิมพ์ใหม่", "en": "\"Adjust this report\" under the table changes the period/grouping/measure without retyping"},
                    {"th": "ต้องมีสิทธิ์ \"ดูรายงาน\" (Sale มีตั้งแต่ต้น CS ต้องให้เจ้าของเปิด) กราฟยอดขายตามสถานะดีลใช้สิทธิ์ \"ดูดีล\"", "en": "Needs the \"View reports\" permission (Sales has it; the owner grants it to CS); the pipeline chart uses \"View deals\""},
                    {"th": "AI ไม่แตะฐานข้อมูลเอง — มันแปลคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลของร้านคุณเท่านั้น", "en": "The AI never touches the database — it turns the question into an allowed query, counted over your shop's data only"},
                    {"th": "คะแนนที่ลูกค้าให้หลังงานซ่อม — เฉลี่ย จำนวนที่ตอบ แยกตามช่าง (เมนู \"ความพึงพอใจ\" บนหน้าจอ)", "en": "What customers scored after a repair — average, answered, per technician (the \"Satisfaction\" page on screen)", "type": "คะแนนความพึงพอใจ · ความพึงพอใจของช่าง สมศักดิ์"},
                ],
                "commands": ["รายงานยอดขายเดือนนี้", "สรุปงานค้างแยกตามช่าง", "ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด", "ขอกราฟยอดขาย", "กราฟยอดขายรายเดือน", "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน", "คะแนนความพึงพอใจ"],
                "example": "สรุปงานค้างแยกตามช่าง",
                "image": "sales-ai-report",
                "image_prompt": "แชท LINE ข้อความ 'สรุปงานค้างแยกตามช่าง' ตอบกลับเป็นรายการชื่อช่างกับตัวเลขและลิงก์ไฟล์ ถัดมาเป็นกราฟแท่งสีเขียวของจำนวนงานต่อช่าง",
            },
            {
                "key": "help", "title": {"th": "ติดขัด", "en": "Stuck"},
                "body": {
                    "th": "ไม่รู้จะพิมพ์อะไร หรือคุยกันจนงง — มีทางออกสามอย่าง",
                    "en": "Not sure what to type, or talking in circles — three ways out.",
                },
                "how": [
                    {"th": "ดูตัวอย่างที่คุณมีสิทธิ์", "en": "Examples you may use", "type": "วิธีใช้"},
                    {"th": "ดูสิทธิ์ทั้งหมด", "en": "Everything you may do", "type": "ทำอะไรได้บ้าง"},
                    {"th": "ล้างเฉพาะบทสนทนา ข้อมูลไม่หาย", "en": "Clears the conversation only, never your data", "type": "เริ่มใหม่"},
                    {"th": "สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > จัดการสิทธิ์และบทบาท)", "en": "Ask the owner for permissions (dashboard > roles)"},
                    {"th": "สลับภาษา", "en": "Switch language", "type": "เปลี่ยนภาษาเป็นอังกฤษ"},
                ],
                "commands": ["วิธีใช้", "ทำอะไรได้บ้าง", "เปลี่ยนภาษาเป็นอังกฤษ", "เริ่มใหม่"],
                "example": "วิธีใช้",
                "image": "sales-help",
                "image_prompt": "หน้าจอ 'จัดการสิทธิ์และบทบาท' ธีมเขียว: รายละเอียดบทบาท ขาย เป็นหมวดสิทธิ์ที่พับไว้พร้อมตัวเลข 5/6 4/4 หมวดหนึ่งกางอยู่เห็นรายการสิทธิ์ ด้านล่างเป็นการ์ด 'ติดขัด?'",
            },
            {
                "key": "members", "title": {"th": "สมาชิกในร้าน", "en": "Members"},
                "body": {
                    "th": "หน้าจอ > สมาชิกในร้าน (ข้างบทบาท): ใครผูกกับบริษัทใน LINE ไหน — คนเดียวกันอยู่ได้ทั้ง LINE ทีมขาย/CS และ LINE ช่าง คนละบทบาท",
                    "en": "Home > Members (next to roles): who is linked to the company on which LINE — one person can be on both LINEs with different roles.",
                },
                "how": [
                    {"group": {"th": "บนหน้าจอ", "en": "On the dashboard"}},
                    {"th": "เปลี่ยนบทบาท · นำออก / กลับมาใช้งาน (นำช่างออก = ถอดจากทีมและคืนงานที่ค้างเข้าคิว)", "en": "Change a role · remove / reactivate (removing a technician takes them off teams and returns their open jobs to the queue)"},
                    {"th": "หน้าจอ > บทบาท: แต่ละบทบาทเป็นแถวสั้น ๆ บอกจำนวนสิทธิ์และกลุ่ม แตะ \"รายละเอียด\" ถึงเห็นรายการสิทธิ์ · สร้าง/แก้ในแผงเดียว กางทีละกลุ่ม เลือกทั้งกลุ่มได้", "en": "Home > Roles: each role is one short row with its permission count and groups; \"Details\" shows the list · create/edit in one sheet, one collapsed group at a time, whole-group select"},
                    {"th": "รีเซ็ตการลงทะเบียน เมื่อแชทของคนนั้นค้าง", "en": "Reset onboarding when someone's chat is stuck"},
                    {"th": "เจ้าของร้านนำออกไม่ได้ ต้องโอนความเป็นเจ้าของก่อน", "en": "The owner cannot be removed; transfer ownership first"},
                    {"group": {"th": "เพิ่มคน", "en": "Adding people"}},
                    {"th": "ขอรหัสเชิญแล้วเลือกว่าช่างหรือทีมขาย — รหัสช่างพิมพ์ใน LINE ช่าง รหัสทีมขาย/CS พิมพ์ใน LINE ฝ่ายขาย", "en": "An invite code, technician or sales — typed in the matching LINE", "type": "ขอรหัสเชิญ"},
                    {"th": "ดูรหัสที่ออกไปแล้ว", "en": "Codes still valid", "type": "ดูรหัสเชิญ"},
                    {"th": "รหัสหลุด ยกเลิกได้ (หรือปุ่มบนหน้าสมาชิก)", "en": "Cancel one that leaked (or the button on the members page)", "type": "ยกเลิกรหัสเชิญ <รหัส>"},
                    {"th": "แต่ละ LINE ลงทะเบียนแยกกัน ใช้ร่วมกันเฉพาะข้อมูลส่วนตัว", "en": "Each LINE is a separate registration; only personal details are shared"},
                    {"group": {"th": "จากแชท", "en": "From chat"}},
                    {"th": "เปลี่ยนบทบาท", "en": "Change a role", "type": "เปลี่ยนบทบาทสมชายเป็นแอดมิน"},
                    {"th": "เอาคนออก / รับกลับ", "en": "Remove / bring back", "type": "เอาสมศักดิ์ออกจากร้าน · ให้สมศักดิ์กลับมาใช้งานได้"},
                    {"th": "ดูบทบาททั้งหมด", "en": "Every role", "type": "มีบทบาทอะไรบ้าง"},
                    {"th": "เพิ่มสิทธิ์ให้บทบาท (เพิ่ม ไม่ทับของเดิม)", "en": "Add a permission to a role (added, never substituted)", "type": "ให้บทบาท cs ดูใบเสนอราคาได้ด้วย"},
                    {"group": {"th": "ใครทำอะไรไปบ้าง", "en": "Who did what"}},
                    {"th": "ดูประวัติ หรือถามตรง ๆ \"ใครลบลูกค้ารายนี้\" — ทั้งหมดอยู่ที่ หน้าจอ > ประวัติการใช้งาน (ต้องมีสิทธิ์ \"ดูประวัติการใช้งาน\")", "en": "The log, or ask directly (\"who deleted this customer\") — all of it under Home > Activity, behind the \"view audit log\" permission", "type": "ประวัติการใช้งาน"},
                ],
                "commands": ["ขอรหัสเชิญ", "ขอรหัสเชิญช่าง", "ดูรหัสเชิญ", "ประวัติการใช้งาน",
                             "มีบทบาทอะไรบ้าง", "เปลี่ยนบทบาทสมชายเป็นแอดมิน"],
                "example": "หน้าจอ > สมาชิกในร้าน",
                "image": "sales-members",
                "image_prompt": "หน้าจอแดชบอร์ดสีเขียว 'สมาชิกในร้าน' ตารางชื่อ / LINE (ทีมขาย·ช่าง) / บทบาท / สถานะ ปุ่ม เปลี่ยนบทบาท นำออก รีเซ็ต แถวเจ้าของมีป้าย 'เจ้าของ'",
            },
            {
                "key": "api", "title": {"th": "เชื่อมต่อระบบภายนอก (API)", "en": "Connect an outside system (API)"},
                "body": {
                    "th": "ให้โปรแกรมบัญชี ERP หรือระบบอื่นอ่านและเขียนข้อมูลร้านได้ผ่าน API — เจ้าของร้านสร้าง key แล้วส่งให้ผู้พัฒนาระบบนั้น key ทำงานในนามร้าน",
                    "en": "Let an accounting program, an ERP or another system read and write the shop's data through the API — the owner makes a key and hands it to that system's developer; the key acts as the shop.",
                },
                "how": [
                    {"th": "แดชบอร์ด > จัดการร้าน > API > \"สร้าง key\" ตั้งชื่อตามระบบที่จะใช้ — key แสดงครั้งเดียว คัดลอกเก็บทันที", "en": "Dashboard > Shop > API > \"Create key\", named after the system — shown once, copy it at once"},
                    {"th": "ส่ง key และลิงก์เอกสาร API (ในหน้าเดียวกัน) ให้ผู้พัฒนาระบบภายนอก", "en": "Give the key and the API docs link (same page) to the outside developer"},
                    {"th": "ดูว่ามี key อะไรบ้างและใช้ล่าสุดเมื่อไหร่", "en": "See which keys exist and when each was last used", "type": "รายการ API key"},
                    {"th": "เพิกถอนเมื่อเลิกใช้หรือ key หลุด — ระบบถามยืนยันก่อน ระบบภายนอกจะเรียกไม่ได้ทันที", "en": "Revoke when no longer used or leaked — confirmed first; the outside system stops at once", "type": "เพิกถอน API key ระบบบัญชี"},
                    {"th": "สร้าง key ทำได้บนหน้าจอเท่านั้น (ในแชทจะได้ปุ่มเปิดหน้า) — key ไม่ควรอยู่ในแชท", "en": "Keys are made on the screen only (chat hands you the button) — a key should never sit in a chat", "type": "สร้าง API key"},
                    {"th": "เฉพาะเจ้าของร้าน · จำกัด 600 คำขอ/นาที/key · ร้านที่ถูกระงับ key อ่านได้แต่เขียนไม่ได้", "en": "Owner only · 600 requests/min/key · a suspended shop's key reads but cannot write"},
                ],
                "commands": ["รายการ API key", "เพิกถอน API key"],
                "example": "รายการ API key",
                "image": "sales-api",
                "image_prompt": "หน้าจอ 'API สำหรับระบบภายนอก' ธีมเขียว: รายการ key สองแถว (ชื่อ · chann_live_ab12… · ใช้ล่าสุด) ปุ่ม 'สร้าง key' และแผงที่แสดง key เต็มครั้งเดียวพร้อมปุ่มคัดลอกและประโยค 'จะไม่แสดงอีก'",
            },
        ],
    },
}


def _how_lines(step: dict, lang: str, *, prefix: str = "• ") -> list[str]:
    """One line per thing a person can do, the words to type beside it.

    A LINE bubble has no indentation and no columns: three leading
    spaces render as nothing on one client and as a ragged left edge on
    another, and a paragraph of twelve " · "-joined clauses is a wall.
    Owner, 20 ก.ย. 2569: "ตัวอักษรเคลื่อน อ่านไม่รู้เรื่อง". So: no indent,
    one idea per line, a group heading where a step has several parts,
    and the thing to type at the END of the line where the eye stops.

    Steps without `how` (the customer and technician guides) get the same
    shape from their " · "-separated body."""
    typed = "พิมพ์" if lang == "th" else "type"
    lines: list[str] = []
    how = step.get("how")
    if how:
        for item in how:
            if "group" in item:
                lines.append("")
                lines.append(f"▪ {item['group'][lang]}")
                continue
            line = prefix + item[lang]
            if item.get("type"):
                line += f' → {typed} "{item["type"]}"'
            lines.append(line)
        return lines
    for part in step["body"][lang].split(" · "):
        part = part.strip()
        if part:
            lines.append(prefix + part)
    return lines


def _lead(step: dict, lang: str) -> str:
    """The one-line lead of a step that has `how`; nothing otherwise (the
    body is then the bullets themselves)."""
    return step["body"][lang] if step.get("how") else ""


def render_help_text(oa: str, language: str = "th", *, allowed_steps: set[str] | None = None) -> str:
    """The chat "วิธีใช้ทั้งหมด": every step, numbered, one line per thing
    to do — readable in one LINE bubble."""
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
        lead = _lead(step, lang)
        if lead:
            lines.append(lead)
        lines.extend(_how_lines(step, lang))
        example = step.get("example")
        if example:
            lines.append(f"▸ {'พิมพ์' if lang == 'th' else 'type'}: \"{example}\"")
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


# ---------------------------------------------------------------- layered help
#
# Owner (6 Sep 2026): the 30-line guide is not read in a chat bubble. Help
# now comes in layers — a short menu of numbered topics with a button per
# topic, then one topic at a time with its picture. The full text is still
# one message away ("วิธีใช้ทั้งหมด") and the illustrated page one tap away.

HELP_SHORT_INTRO = {
    "customer": {"th": "พิมพ์คุยได้เลย ไม่ต้องจำคำสั่ง", "en": "Just type — no commands to remember."},
    "sales": {"th": "ทุกอย่างที่ทำบนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน", "en": "Everything on the dashboard can be typed here too."},
    "technician": {"th": "วันทำงานของช่างมี 4 ขั้น ระบบบอกขั้นถัดไปให้ทุกครั้ง", "en": "A technician's day has 4 steps; the system always says what comes next."},
}
HELP_MENU_PROMPT = {
    "th": "แตะหัวข้อด้านล่าง หรือพิมพ์ตัวเลข",
    "en": "tap a topic below, or type its number",
}
HELP_MENU_FOOT = {
    "th": "พิมพ์ \"วิธีใช้ทั้งหมด\" เพื่อดูทุกขั้นในข้อความเดียว",
    "en": "Type \"help all\" for every step in one message.",
}
HELP_STEP_NEXT = {"th": "ขั้นถัดไป: พิมพ์ {n} หรือแตะปุ่ม", "en": "Next: type {n} or tap the button"}
HELP_STEP_LAST = {"th": "ครบทุกขั้นแล้ว พิมพ์ \"วิธีใช้\" เพื่อกลับไปหัวข้อ", "en": "That was the last step. Type \"help\" for the topics."}


def _lang(language: str) -> str:
    return "en" if language == "en" else "th"


def help_step_count(oa: str) -> int:
    return len((GUIDES.get(oa) or GUIDES["customer"])["steps"])


def help_step_short_title(oa: str, n: int, language: str = "th") -> str:
    """A title that fits a quick-reply label: up to the first " (" or " /",
    then at most 18 characters."""
    step = (GUIDES.get(oa) or GUIDES["customer"])["steps"][n - 1]
    title = step["title"][_lang(language)]
    for cut in (" (", " /", " →", ":"):
        if cut in title:
            title = title.split(cut, 1)[0]
    return title.strip()[:18]


def render_help_menu(oa: str, language: str = "th") -> str:
    """The first layer: title, one line of intro, the numbered topics."""
    guide = GUIDES.get(oa) or GUIDES["customer"]
    lang = _lang(language)
    lines = [guide["title"][lang], HELP_SHORT_INTRO.get(oa, HELP_SHORT_INTRO["customer"])[lang] + " · " + HELP_MENU_PROMPT[lang], ""]
    for n, step in enumerate(guide["steps"], 1):
        lines.append(f"{n}. {step['title'][lang]}")
    # No blank line before the foot: round 21B's tenth sales topic put the
    # menu plus its areas line at 16 lines, one past what a LINE bubble is
    # read at (simulate-phrasings LONG_LINES = 15). The list stays a menu.
    lines.append(HELP_MENU_FOOT[lang])
    return "\n".join(lines)


def render_help_step(oa: str, n: int, language: str = "th") -> tuple[str, str] | None:
    """One topic: its lead, one line per thing to do with the words to
    type, the picture (absolute URL, "" when none). None when n is out
    of range."""
    guide = GUIDES.get(oa) or GUIDES["customer"]
    lang = _lang(language)
    if n < 1 or n > len(guide["steps"]):
        return None
    step = guide["steps"][n - 1]
    lines = [f"{n}. {step['title'][lang]}"]
    lead = _lead(step, lang)
    if lead:
        lines.append(lead)
    lines.extend(_how_lines(step, lang))
    example = step.get("example")
    if example and not step.get("how"):
        lines.append(f"▸ {'พิมพ์' if lang == 'th' else 'type'}: \"{example}\"")
    lines.append("")
    if n < len(guide["steps"]):
        lines.append(HELP_STEP_NEXT[lang].format(n=n + 1))
    else:
        lines.append(HELP_STEP_LAST[lang])
    return "\n".join(lines), help_image_url(step["image"])


def help_menu_quick_replies(oa: str, language: str = "th") -> list[tuple[str, str]]:
    """One button per topic (label ≤ 20 chars, the LINE limit), then the
    full text. The illustrated-guide link is added by the caller."""
    buttons = [
        (f"{n} {help_step_short_title(oa, n, language)}"[:20], f"วิธีใช้ {n}")
        for n in range(1, help_step_count(oa) + 1)
    ]
    buttons.append(("ดูทั้งหมด" if _lang(language) == "th" else "All steps", "วิธีใช้ทั้งหมด"))
    return buttons[:12]


def help_step_by_text(oa: str, text: str, language: str = "th") -> int | None:
    """"วิธีใช้ ผูกกับร้าน" → 1: the topic named by (part of) its title."""
    guide = GUIDES.get(oa) or GUIDES["customer"]
    wanted = (text or "").strip().lower()
    for prefix in ("วิธีใช้", "help", "guide", "คู่มือ"):
        if wanted.startswith(prefix):
            wanted = wanted[len(prefix):].strip(" :")
    if len(wanted) < 3:
        return None
    for n, step in enumerate(guide["steps"], 1):
        for lang in ("th", "en"):
            title = step["title"][lang].lower()
            if wanted == title or wanted in title or title.split(" (")[0].split(" /")[0] in wanted:
                return n
    return None


def guide_as_markdown(oa: str) -> str:
    """The owner's handout with image slots, for docs/guides/."""
    guide = GUIDES[oa]
    out = [f"# {guide['title']['th']}", "", guide["intro"]["th"], "",
           "> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` "
           "และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง", ""]
    for i, step in enumerate(guide["steps"], start=1):
        out.append(f"## {i}. {step['title']['th']}")
        out.append("")
        if step.get("how"):
            out.append(step["body"]["th"])
            out.append("")
            for line in _how_lines(step, "th", prefix="- "):
                out.append(line.replace("▪ ", "**") + ("**" if line.startswith("▪ ") else "") if line else "")
            out.append("")
        else:
            out.append(step["body"]["th"])
            out.append("")
        if step.get("example"):
            out.append(f"พิมพ์: `{step['example']}`")
            out.append("")
        out.append(f"[IMAGE: {step['image']} — {step['image_prompt']}]")
        out.append("")
        if step.get("how"):
            out.append(f"_EN: {step['title']['en']} — {step['body']['en']}_")
            out.append("")
            for line in _how_lines(step, "en", prefix="- "):
                out.append(("_" + line + "_") if line and not line.startswith("▪ ") else line.replace("▪ ", "**") + ("**" if line.startswith("▪ ") else ""))
        else:
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
        ".en{color:#6b6f76;font-size:14px}ul{padding-left:20px}li{margin:4px 0}li.group{list-style:none;margin:10px 0 2px -20px}</style></head><body>",
        f"<h1>{_html.escape(guide['title']['th'])}</h1>",
        f"<p>{_html.escape(guide['intro']['th'])}</p>",
    ]
    for i, step in enumerate(guide["steps"], start=1):
        parts.append(f"<h2>{i}. {_html.escape(step['title']['th'])}</h2>")
        parts.append(f"<p>{_html.escape(step['body']['th'])}</p>")
        if step.get("how"):
            parts.append("<ul>")
            for item in step["how"]:
                if "group" in item:
                    parts.append(f"<li class=\"group\"><strong>{_html.escape(item['group']['th'])}</strong></li>")
                    continue
                typed = f" → พิมพ์ <span class=\"type\">{_html.escape(item['type'])}</span>" if item.get("type") else ""
                parts.append(f"<li>{_html.escape(item['th'])}{typed}</li>")
            parts.append("</ul>")
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

