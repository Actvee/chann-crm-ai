"""Draw the illustrated-guide pictures (application/chann_app/static/help/*.png).

One scene per guide slot, drawn with Pillow from the same wording as the
guide text: a phone-chat mock-up or a dashboard mock-up in the OA's colour
(Sale green, Tech blue, Customer orange). No AI image generation, so the
pictures are reproducible on any clone:

    python scripts/dev/render-guide-images.py            # rewrite every picture
    python scripts/dev/render-guide-images.py --check    # slots, scenes and files agree
    python scripts/dev/render-guide-images.py --only sales-crm customer-link

When a guide step changes, change its scene here and re-run; both
help_images.json files name the slots (tests/unit/test_guide_image_renderer.py
fails when they drift apart).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "application" / "chann_app" / "static" / "help"
IMAGES_FILE = ROOT / "application" / "chann_app" / "help_images.json"
FONT_DIR = Path(__file__).resolve().parent / "guide-fonts"
REG = str(FONT_DIR / "Sarabun-Regular.ttf")
BOLD = str(FONT_DIR / "Sarabun-Bold.ttf")
W, H = 1000, 1000
INK, SOFT, FAINT, LINE, PAPER, WHITE = "#1a2030", "#5a6478", "#8b93a3", "#e5e0d8", "#faf7f2", "#ffffff"
OA = {"customer": "#e8731a", "technician": "#1f6fd6", "sales": "#178a50"}
OA_SOFT = {"customer": "#fdeee2", "technician": "#e6f0fc", "sales": "#e7f6ee"}
OA_NAME = {"customer": "Chann · ลูกค้า", "technician": "Chann · ช่าง", "sales": "Chann · ทีมขาย / CS"}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(BOLD if bold else REG, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    """Greedy wrap by words; Thai has no spaces, so also break long runs."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for word in words:
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=fnt) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            # a single run longer than the line: break by characters
            while draw.textlength(word, font=fnt) > max_w:
                cut = len(word)
                while cut > 1 and draw.textlength(word[:cut], font=fnt) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        lines.append(cur)
    return lines


class Canvas:
    def __init__(self, oa: str):
        self.oa = oa
        self.im = Image.new("RGB", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.im)
        self.accent = OA[oa]

    # ---------------------------------------------------------------- phone chat
    def phone_frame(self, title: str | None = None):
        d = self.d
        d.rounded_rectangle((120, 40, 880, 960), radius=48, fill="#111318")
        d.rounded_rectangle((136, 56, 864, 944), radius=36, fill=WHITE)
        d.rounded_rectangle((136, 56, 864, 150), radius=36, fill=self.accent)
        d.rectangle((136, 110, 864, 150), fill=self.accent)
        d.ellipse((160, 78, 210, 128), fill=WHITE)
        d.text((185, 103), "C", fill=self.accent, font=font(30, True), anchor="mm")
        d.text((228, 103), title or OA_NAME[self.oa], fill=WHITE, font=font(30, True), anchor="lm")
        d.rectangle((136, 860, 864, 944), fill="#f4f2ee")
        d.rounded_rectangle((160, 880, 780, 928), radius=24, fill=WHITE, outline=LINE)
        d.text((180, 904), "พิมพ์ข้อความ…", fill=FAINT, font=font(24), anchor="lm")
        d.ellipse((800, 878, 852, 930), fill=self.accent)
        self.y = 180

    def bubble(self, text: str, who: str = "bot", chips: list[str] | None = None, bold_first: bool = False):
        d = self.d
        fnt = font(25)
        max_w = 440
        lines = wrap(d, text, fnt, max_w)
        text_w = max(d.textlength(l, font=fnt) for l in lines)
        line_h = 34
        h = line_h * len(lines) + 30
        w = int(text_w) + 44
        if who == "user":
            x1, x0 = 840, 840 - w
            fill, color = self.accent, WHITE
        else:
            x0, x1 = 160, 160 + w
            fill, color = "#f1f2f5", INK
        d.rounded_rectangle((x0, self.y, x1, self.y + h), radius=22, fill=fill)
        yy = self.y + 15
        for i, l in enumerate(lines):
            d.text((x0 + 22, yy), l, fill=color, font=font(25, bold_first and i == 0))
            yy += line_h
        self.y += h + 14
        if chips:
            x = 160
            for chip in chips:
                cw = int(d.textlength(chip, font=font(23))) + 36
                d.rounded_rectangle((x, self.y, x + cw, self.y + 44), radius=22, fill=WHITE, outline=self.accent, width=2)
                d.text((x + 18, self.y + 22), chip, fill=self.accent, font=font(23), anchor="lm")
                x += cw + 12
            self.y += 60

    def caption(self, text: str):
        d = self.d
        d.rounded_rectangle((120, 966, 880, 998), radius=8, fill=PAPER)
        d.text((500, 982), text, fill=SOFT, font=font(22), anchor="mm")

    # ---------------------------------------------------------------- dashboard
    def dash_frame(self, title: str, subtitle: str = ""):
        d = self.d
        d.rounded_rectangle((60, 60, 940, 940), radius=28, fill=WHITE, outline=LINE, width=2)
        d.rectangle((60, 60, 940, 140), fill=self.accent)
        d.rounded_rectangle((60, 60, 940, 140), radius=28, fill=self.accent)
        d.rectangle((60, 110, 940, 140), fill=self.accent)
        d.text((92, 100), title, fill=WHITE, font=font(32, True), anchor="lm")
        if subtitle:
            d.text((908, 100), subtitle, fill="#e8f5ee", font=font(22), anchor="rm")
        self.y = 170

    def tiles(self, items: list[tuple[str, str]], cols: int = 3):
        d = self.d
        gap, x0 = 20, 90
        w = (940 - 60 - 60 - gap * (cols - 1)) // cols
        for i, (name, sub) in enumerate(items):
            r, c = divmod(i, cols)
            x = x0 + c * (w + gap)
            y = self.y + r * 150
            d.rounded_rectangle((x, y, x + w, y + 130), radius=18, fill=OA_SOFT[self.oa], outline=LINE)
            d.text((x + 20, y + 40), name, fill=INK, font=font(28, True))
            for j, l in enumerate(wrap(d, sub, font(20), w - 40)[:2]):
                d.text((x + 20, y + 78 + j * 24), l, fill=SOFT, font=font(20))
        self.y += ((len(items) + cols - 1) // cols) * 150 + 10

    def card(self, title: str, lines: list[str], buttons: list[tuple[str, bool]] | None = None, badge: str | None = None):
        d = self.d
        h = 70 + 34 * len(lines) + (70 if buttons else 0)
        d.rounded_rectangle((90, self.y, 910, self.y + h), radius=18, fill=WHITE, outline=LINE, width=2)
        d.text((112, self.y + 34), title, fill=INK, font=font(28, True), anchor="lm")
        if badge:
            bw = int(d.textlength(badge, font=font(20))) + 30
            d.rounded_rectangle((888 - bw, self.y + 18, 888, self.y + 52), radius=17, fill=OA_SOFT[self.oa])
            d.text((888 - bw / 2, self.y + 35), badge, fill=self.accent, font=font(20), anchor="mm")
        yy = self.y + 66
        for l in lines:
            d.text((112, yy), l, fill=SOFT, font=font(23))
            yy += 34
        if buttons:
            x = 112
            for label, primary in buttons:
                bw = int(d.textlength(label, font=font(23, True))) + 44
                d.rounded_rectangle((x, yy + 8, x + bw, yy + 56), radius=12, fill=self.accent if primary else WHITE, outline=self.accent, width=2)
                d.text((x + bw / 2, yy + 32), label, fill=WHITE if primary else self.accent, font=font(23, True), anchor="mm")
                x += bw + 14
        self.y += h + 18

    def table(self, headers: list[str], rows: list[list[str]], widths: list[int]):
        d = self.d
        x0 = 90
        d.rounded_rectangle((90, self.y, 910, self.y + 50 + 46 * len(rows)), radius=14, fill=WHITE, outline=LINE, width=2)
        x = x0
        for hname, w in zip(headers, widths):
            d.text((x + 14, self.y + 25), hname, fill=FAINT, font=font(20, True), anchor="lm")
            x += w
        d.line((90, self.y + 50, 910, self.y + 50), fill=LINE, width=2)
        yy = self.y + 50
        for row in rows:
            x = x0
            for cell, w in zip(row, widths):
                d.text((x + 14, yy + 23), cell, fill=INK, font=font(22), anchor="lm")
                x += w
            yy += 46
            d.line((90, yy, 910, yy), fill="#f0ede8", width=1)
        self.y = yy + 20

    def steps(self, labels: list[str], done_upto: int, x0: int = 130, x1: int = 870):
        d = self.d
        n = len(labels)
        step = (x1 - x0) // (n - 1)
        d.line((x0, self.y + 30, x1, self.y + 30), fill=LINE, width=6)
        for i, label in enumerate(labels):
            x = x0 + i * step
            filled = i <= done_upto
            d.ellipse((x - 22, self.y + 8, x + 22, self.y + 52), fill=self.accent if filled else WHITE, outline=self.accent, width=4)
            if filled and i < done_upto:
                d.line([(x - 10, self.y + 30), (x - 3, self.y + 38), (x + 11, self.y + 22)], fill=WHITE, width=4)
            elif filled:
                d.ellipse((x - 8, self.y + 22, x + 8, self.y + 38), fill=WHITE)
            d.text((x, self.y + 80), label, fill=INK if filled else FAINT, font=font(21, filled), anchor="mm")
        self.y += 120

    def note(self, text: str):
        d = self.d
        lines = wrap(d, text, font(22), 780)
        h = 30 + 30 * len(lines)
        d.rounded_rectangle((90, self.y, 910, self.y + h), radius=14, fill=OA_SOFT[self.oa])
        for i, l in enumerate(lines):
            d.text((112, self.y + 15 + i * 30), l, fill=INK, font=font(22))
        self.y += h + 16

    def save(self, path: Path):
        self.im.save(path, "PNG", optimize=True)


# ============================================================ the scenes

def customer_link(c: Canvas):
    c.phone_frame()
    c.bubble("สวัสดีครับ พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้านที่ซื้อ ระบบจะผูกบัญชีให้", "bot")
    c.bubble("SN12345678", "user")
    c.bubble("ผูกกับ ร้านเย็นสบาย แล้ว\nเครื่อง: แอร์ติดผนัง 12000 BTU\nพิมพ์ \"แจ้งซ่อม\" ได้ทุกเมื่อ", "bot", chips=["แจ้งซ่อม", "งานของฉัน"])
    c.caption("S/N อยู่บนสติกเกอร์ข้างเครื่อง · พิมพ์ชื่อร้านแทนได้")


def customer_shop(c: Canvas):
    c.phone_frame()
    c.bubble("ค้นหา พัดลม", "user")
    c.bubble("สินค้าที่พบ:\n1. พัดลมไอเย็น 20 ลิตร — 3,500 บาท (ร้านเย็นสบาย)\n2. พัดลมตั้งพื้น 16 นิ้ว — 1,500 บาท (ร้านแอร์ดี)\nพิมพ์เลขข้อที่สนใจ ร้านจะติดต่อกลับ", "bot")
    c.bubble("1", "user")
    c.bubble("แจ้งร้านเย็นสบายแล้วว่าคุณสนใจ พัดลมไอเย็น 20 ลิตร ร้านจะติดต่อกลับครับ", "bot", chips=["สินค้าทั้งหมด", "คุยกับร้าน"])
    c.caption("\"สินค้าทั้งหมด\" ดูทุกร้าน · พิมพ์เลขข้อเพื่อบอกว่าสนใจ")


def customer_chat(c: Canvas):
    c.phone_frame()
    c.bubble("คุยกับร้าน ราคาแอร์ 12000 BTU เท่าไหร่", "user")
    c.bubble("เปิดการสนทนากับ ร้านเย็นสบาย แล้ว ข้อความต่อจากนี้ส่งถึงร้านโดยตรง (ร้านตอบภายใน 15 นาที)", "bot")
    c.bubble("ร้านเย็นสบาย: รุ่น Inverter 12000 BTU ราคา 15,900 รวมติดตั้งครับ", "bot", bold_first=True)
    c.bubble("ขอบคุณครับ", "user")
    c.bubble("พิมพ์ \"จบการสนทนา\" เมื่อคุยเสร็จ หรือระบบปิดให้เองเมื่อเงียบ 60 นาที", "bot", chips=["จบการสนทนา"])
    c.caption("ข้อความของร้านขึ้นต้นด้วยชื่อร้าน")


def customer_register(c: Canvas):
    c.phone_frame()
    c.bubble("ลงทะเบียนสินค้า SN12345678", "user")
    c.bubble("ลงทะเบียน แอร์ติดผนัง 12000 BTU (S/N SN12345678) เป็นของคุณแล้ว\nรับประกันถึง 15 ก.ย. 2570\nใบรับประกัน: WR-2026-0001", "bot", chips=["ประวัติการซื้อ", "แจ้งซ่อม"])
    c.caption("ต้องลงทะเบียนก่อนแจ้งซ่อม ระบบจะรู้ว่าเครื่องไหน")


def customer_report(c: Canvas):
    c.phone_frame()
    c.bubble("แอร์ไม่เย็น มีน้ำหยด", "user")
    c.bubble("รับแจ้งแล้ว เลขงาน T-2026-0001\nขอที่อยู่หน้างานครับ (แชร์ตำแหน่งได้)", "bot")
    c.bubble("99/1 ถ.สุขุมวิท บางนา กทม.", "user")
    c.bubble("บันทึกที่อยู่แล้ว ร้านจะมอบหมายช่างและแจ้งวันนัด\nส่งรูปอาการได้เลย ระบบแนบให้กับงาน T-2026-0001", "bot", chips=["งานของฉัน", "เลื่อนนัด"])
    c.caption("รูปที่ส่งหลังแจ้งซ่อม แนบเข้างานอัตโนมัติ")


def customer_status(c: Canvas):
    c.phone_frame()
    c.bubble("งานของฉัน", "user")
    c.y += 4
    c.d.rounded_rectangle((160, c.y, 840, c.y + 300), radius=22, fill=WHITE, outline=LINE, width=2)
    c.d.text((184, c.y + 34), "T-2026-0001 · แอร์ไม่เย็น", fill=INK, font=font(27, True), anchor="lm")
    c.d.text((184, c.y + 70), "ช่าง: สมศักดิ์ (ทีมแอร์) · นัด 6 ก.ย. 10:00", fill=SOFT, font=font(22), anchor="lm")
    save_y = c.y
    c.y += 110
    c.steps(["รอมอบหมาย", "ช่างรับแล้ว", "กำลังทำ", "เสร็จ"], 1, x0=230, x1=770)
    c.y = save_y + 320
    c.bubble("พิมพ์ \"เลื่อนนัด 8 ก.ย.\" หรือ \"ยกเลิกงาน T-2026-0001\" ได้เลย", "bot", chips=["เลื่อนนัด", "ยกเลิกงาน"])
    c.caption("สถานะอัปเดตทุกครั้งที่ช่างรับงาน เช็คอิน และปิดงาน")


def customer_after(c: Canvas):
    c.phone_frame()
    c.bubble("งาน T-2026-0001 เสร็จแล้ว\nช่างสมศักดิ์ เปลี่ยนคอมเพรสเซอร์และล้างเครื่อง\nช่วยให้คะแนนบริการครับ", "bot", chips=["1 ไม่ดี", "2 พอใช้", "3 ดีเยี่ยม"])
    c.bubble("3", "user")
    c.bubble("ขอบคุณครับ ดูประวัติทั้งหมดได้ที่ \"ข้อมูลของฉัน\" หรือหน้าจอลูกค้าในเมนู", "bot", chips=["ข้อมูลของฉัน", "ประวัติการซื้อ"])
    c.caption("คะแนนส่งถึงร้านทันที")


def customer_pdpa(c: Canvas):
    c.phone_frame()
    c.bubble("ขอข้อมูลของฉัน", "user")
    c.bubble("สำเนาข้อมูลของคุณพร้อมแล้ว (ลิงก์ใช้ได้ 24 ชั่วโมง):\nhttps://…/pdpa/CHN-…html\nมีข้อมูลจาก 2 ร้าน", "bot")
    c.bubble("ขอลบข้อมูล", "user")
    c.bubble("การลบข้อมูลจะลบชื่อ เบอร์ ที่อยู่ ข้อความแชท และรูปของคุณจากทุกร้านที่ผูกไว้ (ประวัติงานยังอยู่แต่ไม่มีชื่อคุณ)\nยืนยันพิมพ์ \"ยืนยันลบข้อมูล\"", "bot", chips=["ยืนยันลบข้อมูล"])
    c.caption("ครั้งแรกระบบขอความยินยอม PDPA ก่อนผูกร้าน")


def tech_join(c: Canvas):
    c.phone_frame()
    c.bubble("สวัสดีครับ พิมพ์รหัสเชิญ 8 ตัวที่ได้จากร้านเพื่อเข้าร่วมทีมช่าง", "bot")
    c.bubble("ABCD1234", "user")
    c.bubble("เข้าร่วม ร้านเย็นสบาย แล้ว บทบาท: ช่าง\nวันทำงานมี 4 ขั้น: รับงาน → เช็คอิน → ปิดงาน → รอตรวจ", "bot", chips=["งานที่เปิดรับ", "งานของฉัน"])
    c.caption("ขอรหัสเชิญจากเจ้าของร้าน (\"ขอรหัสเชิญช่าง\" ใน Sale OA)")


def tech_take(c: Canvas):
    c.dash_frame("งานที่เปิดรับ", "ทีมแอร์ · 2 งาน")
    c.card("T-2026-0001 · แอร์ไม่เย็น มีน้ำหยด", ["ลูกค้า: สมชาย ใจดี · 99/1 ถ.สุขุมวิท", "นัด 6 ก.ย. 10:00 · มอบหมายให้ทีมแอร์"], [("รับงาน", True), ("ปฏิเสธงาน", False)], badge="มอบหมายให้คุณ")
    c.card("T-2026-0002 · ล้างแอร์ 2 เครื่อง", ["ลูกค้า: สมหญิง ดีใจ · หมู่บ้านสุขใจ", "นัด 7 ก.ย. 13:00"], [("รับงาน", True)])
    c.note("ในแชทพิมพ์ \"รับงาน T-2026-0001\" ได้เหมือนกัน · งานที่รับแล้วอยู่ใน \"งานของฉัน\"")


def tech_checkin(c: Canvas):
    c.phone_frame()
    c.bubble("เช็คอิน T-2026-0001", "user")
    c.bubble("แชร์ตำแหน่งปัจจุบันเพื่อบันทึกจุดเริ่มงานครับ", "bot", chips=["📍 แชร์ตำแหน่ง"])
    c.bubble("📍 13.6689, 100.6104", "user")
    c.bubble("เช็คอิน T-2026-0001 แล้ว 10:02 (บันทึกตำแหน่ง)\nถ่ายรูปก่อนซ่อมส่งมาได้เลย แนบให้กับงานนี้\nเสร็จแล้วพิมพ์ \"ปิดงาน\"", "bot", chips=["ปิดงาน", "งานของฉัน"])
    c.caption("เช็คอินซ้ำจะบอกว่าเช็คอินแล้วเมื่อกี่โมง ไม่ใช่ error")


def tech_finish(c: Canvas):
    c.phone_frame()
    c.bubble("ปิดงาน", "user")
    c.bubble("พบปัญหาอะไรครับ", "bot")
    c.bubble("คอมเพรสเซอร์รั่ว น้ำยาหมด", "user")
    c.bubble("แก้ไขอย่างไร", "bot")
    c.bubble("เปลี่ยนคอมเพรสเซอร์ เติมน้ำยา ทดสอบแล้วเย็นปกติ", "user")
    c.bubble("ปิดงาน T-2026-0001 แล้ว 11:40 รายงาน SR-2026-0001 ส่งให้ CS ตรวจ\nแนบรูปหลังซ่อมเพิ่มได้", "bot", chips=["รายงานของฉัน"])
    c.caption("ตอบ 2 คำถาม = รายงานบริการเสร็จ")


def tech_approved(c: Canvas):
    c.phone_frame()
    c.bubble("รายงาน SR-2026-0001 ผ่านการอนุมัติแล้ว\nPDF (ใช้ได้ 7 วัน):\nhttps://…/service-report/SR-2026-0001.pdf", "bot")
    c.y += 6
    c.d.rounded_rectangle((160, c.y, 840, c.y + 190), radius=22, fill=WHITE, outline=LINE, width=2)
    c.d.rounded_rectangle((184, c.y + 24, 274, c.y + 166), radius=10, fill="#fdeceb", outline="#f0c4c1", width=2)
    c.d.text((229, c.y + 95), "PDF", fill="#b4342c", font=font(30, True), anchor="mm")
    c.d.text((300, c.y + 50), "รายงานบริการ SR-2026-0001", fill=INK, font=font(27, True), anchor="lm")
    c.d.text((300, c.y + 92), "ลูกค้า สมชาย ใจดี · แอร์ไม่เย็น", fill=SOFT, font=font(22), anchor="lm")
    c.d.text((300, c.y + 128), "ช่าง สมศักดิ์ · อนุมัติโดย CS 4 ก.ย.", fill=SOFT, font=font(22), anchor="lm")
    c.y += 210
    c.bubble("ขอไฟล์ซ้ำได้ด้วย \"ออกรายงาน SR-2026-0001\"", "bot", chips=["ออกรายงาน SR-2026-0001"])
    c.caption("ก่อนอนุมัติ: \"PDF จะออกให้เมื่อ CS อนุมัติแล้ว\"")


def sales_setup(c: Canvas):
    c.dash_frame("ตั้งร้านให้พร้อม", "ข้อมูลร้าน")
    d = c.d
    d.rounded_rectangle((330, 190, 670, 300), radius=18, fill=c.accent)
    d.text((500, 228), "ร้านเย็นสบาย", fill=WHITE, font=font(30, True), anchor="mm")
    d.text((500, 268), "รหัสร้าน ABCD01 · ให้ลูกค้าใช้ผูก", fill="#e7f6ee", font=font(21), anchor="mm")
    for i, (x, title, sub) in enumerate(((150, "ทีมช่าง แอร์", "หัวหน้า สมศักดิ์\nช่าง 3 คน"), (500, "ลูกค้า", "ผูกร้านด้วยรหัส\nหรือ S/N"), (850, "ช่าง", "เข้าร่วมด้วยรหัสเชิญ\nABCD1234"))):
        d.line((500, 300, x, 380), fill=LINE, width=4)
        d.rounded_rectangle((x - 150, 380, x + 150, 520), radius=18, fill=OA_SOFT[c.oa], outline=LINE, width=2)
        d.text((x, 415), title, fill=INK, font=font(27, True), anchor="mm")
        for j, l in enumerate(sub.split("\n")):
            d.text((x, 458 + j * 28), l, fill=SOFT, font=font(21), anchor="mm")
    c.y = 560
    c.note("พิมพ์ในแชท: \"ข้อมูลร้าน\" · \"ขอรหัสเชิญช่าง\" · \"สร้างทีมช่าง แอร์\" · \"เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า\" · \"ข้อมูลบริษัท\" · \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\"")
    c.table(["คำสั่ง", "ผล"], [["ข้อมูลร้าน", "รหัสร้าน ชื่อ ที่อยู่"], ["ขอรหัสเชิญช่าง", "รหัส 8 ตัว ใช้ได้ 7 วัน"], ["สร้างทีมช่าง แอร์", "ทีมใหม่ พร้อมมอบหมายงาน"]], [330, 490])


def sales_units(c: Canvas):
    c.dash_frame("เครื่องที่ลงทะเบียน", "รายการประกัน")
    c.table(["S/N", "สินค้า", "ลูกค้า", "สถานะ"],
            [["SN12345678", "แอร์ 12000 BTU", "สมชาย ใจดี", "ลูกค้าผูกแล้ว"],
             ["SN22334455", "พัดลมไอเย็น", "—", "ยังไม่มีลูกค้าผูก"],
             ["SN99887766", "แอร์ 18000 BTU", "สมหญิง ดีใจ", "ลูกค้าผูกแล้ว"]],
            [220, 230, 200, 170])
    c.note("แชท: \"ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย\" · ลูกค้าพิมพ์ S/N เดียวกันใน LINE ลูกค้าเพื่อผูกเครื่องกับตัวเอง")
    c.card("นำเข้าจากไฟล์", ["ปุ่ม \"นำเข้า CSV\" บนหน้ารายการประกัน มีไฟล์ตัวอย่างให้ดูในหน้าจอ", "คอลัมน์: serial_number, product_id, customer_phone, warranty_start"], [("นำเข้า CSV", True), ("ดูตัวอย่าง", False)])


def sales_dispatch(c: Canvas):
    c.dash_frame("งานซ่อม", "รอมอบหมาย 1")
    c.card("T-2026-0001 · แอร์ไม่เย็น มีน้ำหยด", ["ลูกค้า: สมชาย ใจดี · 99/1 ถ.สุขุมวิท บางนา", "แจ้งเมื่อ 4 ก.ย. 09:12 · ยังไม่มอบหมาย"], badge="รอมอบหมาย")
    d = c.d
    d.rounded_rectangle((90, c.y, 910, c.y + 150), radius=18, fill=WHITE, outline=LINE, width=2)
    d.text((112, c.y + 30), "มอบหมายให้…", fill=FAINT, font=font(21), anchor="lm")
    d.rounded_rectangle((112, c.y + 50, 560, c.y + 100), radius=12, fill=WHITE, outline=c.accent, width=2)
    d.text((134, c.y + 75), "ทีมแอร์ (หัวหน้า สมศักดิ์)", fill=INK, font=font(24), anchor="lm")
    d.polygon([(520, c.y + 68), (544, c.y + 68), (532, c.y + 84)], fill=SOFT)
    d.rounded_rectangle((590, c.y + 50, 780, c.y + 100), radius=12, fill=c.accent)
    d.text((685, c.y + 75), "มอบหมาย", fill=WHITE, font=font(24, True), anchor="mm")
    d.text((112, c.y + 125), "นัดลูกค้า: 6 ก.ย. 10:00", fill=SOFT, font=font(21), anchor="lm")
    c.y += 170
    c.note("แชท: \"มอบหมาย T-2026-0001 ให้ทีม แอร์\" · ช่างทุกคนในทีมได้ข้อความ ใครกด \"รับงาน\" ก่อนได้งาน · ปฏิเสธได้พร้อมเหตุผล")


def sales_chats(c: Canvas):
    c.dash_frame("แชทลูกค้า", "รอคำตอบ 1")
    d = c.d
    d.rounded_rectangle((90, 170, 400, 900), radius=18, fill=WHITE, outline=LINE, width=2)
    for i, (name, preview, waiting) in enumerate((("สมชาย ใจดี", "ราคาแอร์ 12000 BTU เท่าไหร่", True), ("สมหญิง ดีใจ", "ขอบคุณค่ะ", False))):
        y = 190 + i * 110
        d.rounded_rectangle((100, y, 390, y + 96), radius=12, fill=OA_SOFT[c.oa] if i == 0 else WHITE)
        d.text((116, y + 26), name, fill=INK, font=font(24, True), anchor="lm")
        d.text((116, y + 62), preview, fill=SOFT, font=font(20), anchor="lm")
        if waiting:
            d.rounded_rectangle((270, y + 10, 384, y + 40), radius=15, fill="#fdeee2")
            d.text((327, y + 25), "ลูกค้ารอคำตอบ", fill="#e8731a", font=font(17, True), anchor="mm")
    d.rounded_rectangle((420, 170, 910, 900), radius=18, fill=WHITE, outline=LINE, width=2)
    d.text((440, 200), "สมชาย ใจดี · เปิดเมื่อ 10:41 · ตอบภายใน 15 นาที", fill=SOFT, font=font(20), anchor="lm")
    for y, who, text in ((240, "them", "ราคาแอร์ 12000 BTU เท่าไหร่ครับ"), (320, "me", "รุ่น Inverter 15,900 รวมติดตั้งครับ"), (400, "them", "ติดตั้งได้เร็วสุดเมื่อไหร่")):
        fnt = font(21)
        w = int(d.textlength(text, font=fnt)) + 36
        if who == "me":
            d.rounded_rectangle((890 - w, y, 890, y + 56), radius=18, fill=c.accent)
            d.text((890 - w + 18, y + 28), text, fill=WHITE, font=fnt, anchor="lm")
        else:
            d.rounded_rectangle((440, y, 440 + w, y + 56), radius=18, fill="#f1f2f5")
            d.text((458, y + 28), text, fill=INK, font=fnt, anchor="lm")
    d.rounded_rectangle((440, 820, 770, 876), radius=16, fill=WHITE, outline=LINE, width=2)
    d.text((460, 848), "พิมพ์ตอบลูกค้า…", fill=FAINT, font=font(21), anchor="lm")
    d.rounded_rectangle((790, 820, 890, 876), radius=16, fill=c.accent)
    d.text((840, 848), "ส่ง", fill=WHITE, font=font(23, True), anchor="mm")
    c.y = 910


def sales_approve(c: Canvas):
    c.dash_frame("รอการอนุมัติ", "รายงานบริการ 1")
    c.card("SR-2026-0001 · T-2026-0001 แอร์ไม่เย็น", ["ช่าง สมศักดิ์ · ปิดงาน 4 ก.ย. 11:40", "ปัญหาที่พบ: คอมเพรสเซอร์รั่ว น้ำยาหมด", "สิ่งที่แก้ไข: เปลี่ยนคอมเพรสเซอร์ เติมน้ำยา ทดสอบแล้วเย็นปกติ", "รูปหลังซ่อม 2 รูป · เช็คอิน 10:02 (มีตำแหน่ง)"], [("อนุมัติ", True), ("ตีกลับ", False)], badge="รอตรวจ")
    c.note("แชท: \"อนุมัติ SR-2026-0001\" หรือ \"ตีกลับ SR-2026-0001 รูปไม่ชัด\" · อนุมัติแล้ว ลูกค้าได้ปุ่มให้คะแนน ช่างได้ PDF")


def sales_crm(c: Canvas):
    c.dash_frame("แดชบอร์ดทีมขาย", "ร้านเย็นสบาย")
    c.tiles([("ลูกค้า", "รายชื่อ ค้นหา ยืนยัน Lead ลบออกจากรายชื่อ"), ("ดีล", "สถานะ มูลค่า วันคาดว่าจะปิด"), ("ใบเสนอราคา", "ออก PDF ส่งลูกค้า"),
             ("งานซ่อม", "มอบหมายให้ทีมช่าง"), ("รอการอนุมัติ", "รายงานจากช่าง"), ("รายงาน AI", "ถามเป็นภาษาคน")])
    c.table(["ดีล", "ลูกค้า", "มูลค่า", "คาดว่าจะปิด"],
            [["D-2026-0007", "อาทิตย์ แสงจันทร์", "500,000 บาท", "30 ก.ย. 2569"], ["D-2026-0006", "สมชาย ใจดี", "250,000 บาท", "สิ้นเดือนนี้"]],
            [200, 260, 200, 160])
    c.note("แชททำได้เหมือนกัน: \"สร้างดีลให้ อาทิตย์ มูลค่า 500,000 ปิดสิ้นเดือนนี้\" · \"เพิ่มลูกค้า สมชาย ใจดี 0812345678\" · \"ลบ Lead สมชาย\"")


def sales_ai_report(c: Canvas):
    c.phone_frame()
    c.bubble("สรุปงานค้างแยกตามช่าง", "user")
    c.bubble("จำนวนงานซ่อม · status=open · แยกตามช่าง\n• สมศักดิ์: 5\n• วิชัย: 2\n• ยังไม่มอบหมาย: 3\nรวม 10\n\nไฟล์ (ใช้ได้ 7 วัน): CSV · หน้าเว็บ", "bot", bold_first=True)
    c.y += 6
    d = c.d
    d.rounded_rectangle((160, c.y, 840, c.y + 200), radius=18, fill=WHITE, outline=LINE, width=2)
    for i, (name, v) in enumerate((("สมศักดิ์", 5), ("วิชัย", 2), ("ยังไม่มอบหมาย", 3))):
        y = c.y + 28 + i * 56
        d.text((184, y + 12), name, fill=INK, font=font(22), anchor="lm")
        d.rounded_rectangle((380, y, 380 + int(400 * v / 5), y + 24), radius=12, fill=c.accent)
        d.text((800, y + 12), str(v), fill=INK, font=font(22, True), anchor="rm")
    c.y += 220
    c.bubble("ถามได้อีก เช่น \"ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด\" หรือเปิดเมนู \"รายงาน AI\" บนหน้าจอ", "bot", chips=["รายงาน AI"])
    c.caption("AI แปลงคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลร้านคุณเท่านั้น")


def sales_help(c: Canvas):
    c.dash_frame("บทบาทและทีม", "สิทธิ์")
    c.table(["สมาชิก", "บทบาท", "ดูรายงาน", "เก็บถาวรลูกค้า"],
            [["สมชาย (เจ้าของ)", "owner", "✓", "✓"], ["สมหญิง", "sales", "✓", "✓"], ["วิชัย", "cs", "—", "—"]],
            [260, 160, 200, 200])
    c.note("ถ้าแชทตอบว่า \"ยังไม่มีสิทธิ์\" มาที่หน้านี้ เปิด toggle ให้บทบาทนั้น · พิมพ์ \"วิธีใช้\" ได้ทุกเมื่อ · \"ทำอะไรกับ Lead ได้บ้าง\" ดูทีละหมวด")
    c.card("ติดขัด?", ["\"วิธีใช้\" — คู่มือทั้งหมดในแชท", "\"ฉันมีสิทธิ์ทำอะไร\" — สิทธิ์ของคุณตามหมวด", "\"สลับภาษา\" — English"], [("วิธีใช้", True)])


def permissions_overview(c: Canvas):
    """The owner's roles-and-team page: who can do what, per area."""
    c.dash_frame("บทบาทและทีม", "เจ้าของร้านตั้งสิทธิ์")
    c.tiles([("เจ้าของ", "ทุกสิทธิ์"), ("ขาย", "ลูกค้า ดีล เอกสาร"), ("CS", "แชท งานซ่อม"), ("ช่าง", "รับงาน เช็คอิน")], cols=4)
    d = c.d
    rows = [("ลูกค้า", "ดู / สร้าง / แก้ไข / เก็บถาวร", (True, True, True, False)),
            ("ดีล", "ดู / สร้าง / เปลี่ยนสถานะ", (True, True, False, False)),
            ("งานซ่อม", "ดู / มอบหมาย / ปิดงาน", (True, True, True, True)),
            ("รายงาน", "ดู / รายงาน AI", (True, True, False, False))]
    d.text((112, c.y + 14), "หมวดสิทธิ์", fill=FAINT, font=font(20, True))
    for i, role in enumerate(["เจ้าของ", "ขาย", "CS", "ช่าง"]):
        d.text((560 + i * 90, c.y + 14), role, fill=FAINT, font=font(20, True), anchor="mm")
    c.y += 44
    for name, sub, flags in rows:
        d.rounded_rectangle((90, c.y, 910, c.y + 74), radius=14, fill=WHITE, outline=LINE, width=2)
        d.text((112, c.y + 24), name, fill=INK, font=font(25, True), anchor="lm")
        d.text((112, c.y + 52), sub, fill=SOFT, font=font(19), anchor="lm")
        for i, on in enumerate(flags):
            x = 560 + i * 90
            d.rounded_rectangle((x - 28, c.y + 22, x + 28, c.y + 52), radius=15, fill=c.accent if on else "#d9d4cc")
            kx = x + 13 if on else x - 13
            d.ellipse((kx - 11, c.y + 26, kx + 11, c.y + 48), fill=WHITE)
        c.y += 86
    c.y += 10
    c.note("พนักงานพิมพ์คำสั่งที่ไม่มีสิทธิ์ แชทจะบอกชื่อสิทธิ์ที่ต้องขอ — เปิด toggle ให้บทบาทนั้นที่หน้านี้ แล้วใช้ได้ทันที")


SCENES = {
    "customer": {"customer-link": customer_link, "customer-shop": customer_shop, "customer-chat": customer_chat,
                 "customer-register": customer_register, "customer-report": customer_report, "customer-status": customer_status,
                 "customer-after": customer_after, "customer-pdpa": customer_pdpa},
    "technician": {"tech-join": tech_join, "tech-take": tech_take, "tech-checkin": tech_checkin, "tech-finish": tech_finish,
                   "tech-approved": tech_approved},
    "sales": {"sales-setup": sales_setup, "sales-units": sales_units, "sales-dispatch": sales_dispatch, "sales-chats": sales_chats,
              "sales-approve": sales_approve, "sales-crm": sales_crm, "sales-ai-report": sales_ai_report, "sales-help": sales_help,
              "permissions-overview": permissions_overview},
}


def slots_in_use() -> set[str]:
    return set(json.loads(IMAGES_FILE.read_text(encoding="utf-8"))["images"])


def check(out_dir: Path = OUT_DIR) -> list[str]:
    """Slots without a scene, scenes without a slot, and missing files."""
    scenes = {slot for group in SCENES.values() for slot in group}
    slots = slots_in_use()
    problems = [f"no scene for slot {s}" for s in sorted(slots - scenes)]
    problems += [f"scene {s} is not a guide slot" for s in sorted(scenes - slots)]
    problems += [f"missing {out_dir / (s + '.png')}" for s in sorted(scenes & slots) if not (out_dir / f"{s}.png").is_file()]
    return problems


def main(out_dir: Path = OUT_DIR, only: tuple[str, ...] = ()) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for oa, scenes in SCENES.items():
        for slot, scene in scenes.items():
            if only and slot not in only:
                continue
            c = Canvas(oa)
            scene(c)
            c.save(out_dir / f"{slot}.png")
            print("wrote", slot)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for oa, scenes in SCENES.items():
            for slot in scenes:
                print(oa, slot)
    elif args.check:
        problems = check(args.out)
        for p in problems:
            print(p)
        print("guide pictures are current" if not problems else f"{len(problems)} problem(s)")
        sys.exit(1 if problems else 0)
    else:
        main(args.out, tuple(args.only))
