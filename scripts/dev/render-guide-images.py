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

    def rail(self, groups: list[tuple[str | None, list[str]]], active: str,
             x0: int = 90, x1: int = 330, bottom: int = 908):
        """The left navigation, which is how every dashboard page is really
        reached. It replaced the tile grid on 8 ก.ย. 2569 — a picture of
        tiles sends people looking for a screen that no longer exists."""
        d = self.d
        d.rounded_rectangle((x0, self.y, x1, bottom), radius=18, fill=WHITE, outline=LINE, width=2)
        y = self.y + 14
        for label, entries in groups:
            if label:
                d.text((x0 + 22, y + 14), label, fill=FAINT, font=font(18, True), anchor="lm")
                y += 30
            for name in entries:
                on = name == active
                if on:
                    d.rounded_rectangle((x0 + 10, y, x1 - 10, y + 32), radius=10, fill=OA_SOFT[self.oa])
                d.ellipse((x0 + 24, y + 11, x0 + 34, y + 21), fill=self.accent if on else "#cfcac2")
                d.text((x0 + 46, y + 16), name, fill=INK if on else SOFT, font=font(20, on), anchor="lm")
                y += 32
            y += 4

    def tick(self, x: int, y: int, on: bool):
        """A checkbox drawn with lines. The ✓ character is not in Sarabun,
        so writing one printed an empty box (it did, in sales-help)."""
        d = self.d
        d.rounded_rectangle((x, y, x + 26, y + 26), radius=7,
                            fill=self.accent if on else WHITE, outline=self.accent if on else "#cfcac2", width=2)
        if on:
            d.line([(x + 7, y + 13), (x + 11, y + 18), (x + 19, y + 8)], fill=WHITE, width=3)

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


def customer_invoices(c: Canvas):
    # Round 20V — the receipt, asked for in the customer's own words.
    c.phone_frame()
    c.bubble("ขอใบเสร็จ", "user")
    c.bubble("ใบเสร็จของ INV-2026-0001 (ยอด 32,100.00 บาท):\nhttps://…/documents/…", "bot", chips=["ใบแจ้งหนี้ของฉัน", "คุยกับร้าน"])
    c.bubble("ยอดค้าง", "user")
    c.bubble("ใบแจ้งหนี้ของคุณ\nINV-2026-0002 · รอชำระ · ยอด 5,350.00 บาท · ค้าง 5,350.00 · ครบกำหนด 21 ต.ค. 2569\n\nยอดค้างรวม 5,350.00 บาท — ทางร้านจะติดต่อเรื่องช่องทางชำระครับ", "bot", chips=["ขอใบเสร็จ", "คุยกับร้าน"])
    c.caption("ใบเสร็จมาเองในแชทเมื่อร้านออกให้ · ยอดค้างดูได้ทุกเมื่อ")


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
    for i, (x, title, sub) in enumerate(((215, "ทีมช่าง แอร์", "หัวหน้า สมศักดิ์\nช่าง 3 คน"), (500, "ลูกค้า", "ผูกร้านด้วยรหัส\nหรือ S/N"), (785, "ช่าง", "เข้าร่วมด้วยรหัสเชิญ\nABCD1234"))):
        d.line((500, 300, x, 380), fill=LINE, width=4)
        d.rounded_rectangle((x - 128, 380, x + 128, 520), radius=18, fill=OA_SOFT[c.oa], outline=LINE, width=2)
        d.text((x, 415), title, fill=INK, font=font(27, True), anchor="mm")
        for j, l in enumerate(sub.split("\n")):
            d.text((x, 458 + j * 28), l, fill=SOFT, font=font(21), anchor="mm")
    c.y = 560
    c.note("พิมพ์ในแชท: \"ข้อมูลร้าน\" · \"ขอรหัสเชิญช่าง\" · \"สร้างทีมช่าง แอร์\" · \"เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า\" · \"ข้อมูลบริษัท\" · \"ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด\"")
    c.table(["คำสั่ง", "ผล"], [["ข้อมูลร้าน", "รหัสร้าน ชื่อ ที่อยู่"], ["ขอรหัสเชิญช่าง", "รหัส 8 ตัว ใช้ได้ 7 วัน"], ["สร้างทีมช่าง แอร์", "ทีมใหม่ พร้อมมอบหมายงาน"]], [330, 490])


def sales_members(c: Canvas):
    c.dash_frame("สมาชิกในร้าน", "ข้างบทบาท")
    c.table(["ชื่อ", "LINE", "บทบาท", "สถานะ"],
            [["สมชาย ใจดี", "ทีมขาย", "เจ้าของ", "ใช้งาน"],
             ["สมชาย ใจดี", "ช่าง", "technician", "ใช้งาน"],
             ["สมหญิง ดีใจ", "ทีมขาย", "cs", "ใช้งาน"],
             ["สมศักดิ์ ขยัน", "ช่าง", "technician", "นำออกแล้ว"]],
            [270, 160, 200, 190])
    c.card("สมศักดิ์ ขยัน · LINE ช่าง", ["ถอดจากทีมแล้ว งานที่ค้างกลับเข้าคิวรอมอบหมาย"],
           [("กลับมาใช้งาน", True), ("เปลี่ยนบทบาท", False), ("รีเซ็ตการลงทะเบียน", False)], badge="นำออกแล้ว")
    c.note("คนเดียวกันอยู่ได้ทั้ง LINE ทีมขาย และ LINE ช่าง คนละบทบาท · แต่ละ LINE ลงทะเบียนแยกกัน · เพิ่มช่าง: พิมพ์ \"ขอรหัสเชิญช่าง\" ในแชท · เจ้าของร้านนำออกไม่ได้")


def sales_units(c: Canvas):
    """The warranty register as it stands after rounds 20R/20Q/20Z: the form
    hides behind its button, one search covers the list, the import sits in
    the list head, and a customer who registers their own unit is already a
    customer on the row."""
    c.dash_frame("ทะเบียนสินค้า", "รับประกันสินค้า")
    d = c.d
    for i, line in enumerate(wrap(d, "บันทึกเครื่องที่ขายพร้อม S/N — ลูกค้าพิมพ์ S/N ใน LINE บริการลูกค้าแล้วเครื่องจะผูกกับเขาเอง", font(21), 800)):
        d.text((92, c.y + 14 + i * 28), line, fill=SOFT, font=font(21), anchor="lm")
    c.y += 72
    d.text((92, c.y + 22), "บันทึกเครื่อง", fill=INK, font=font(28, True), anchor="lm")
    bw = int(d.textlength("บันทึกเครื่อง", font=font(22, True))) + 44
    d.rounded_rectangle((910 - bw, c.y, 910, c.y + 48), radius=12, fill=c.accent)
    d.text((910 - bw / 2, c.y + 24), "บันทึกเครื่อง", fill=WHITE, font=font(22, True), anchor="mm")
    c.y += 66
    d.rounded_rectangle((90, c.y, 620, c.y + 54), radius=12, fill=WHITE, outline=LINE, width=2)
    d.ellipse((110, c.y + 17, 130, c.y + 37), outline=FAINT, width=3)
    d.line((128, c.y + 35, 138, c.y + 45), fill=FAINT, width=3)
    d.text((152, c.y + 27), "หมายเลขเครื่อง สินค้า หรือชื่อลูกค้า", fill=FAINT, font=font(21), anchor="lm")
    d.rounded_rectangle((636, c.y, 910, c.y + 54), radius=12, fill=WHITE, outline=LINE, width=2)
    d.text((658, c.y + 27), "อยู่ในประกัน", fill=INK, font=font(21), anchor="lm")
    d.polygon([(866, c.y + 22), (890, c.y + 22), (878, c.y + 38)], fill=SOFT)
    c.y += 70
    d.text((92, c.y + 18), "แสดง 3 จาก 24", fill=FAINT, font=font(20, True), anchor="lm")
    iw = int(d.textlength("นำเข้า CSV", font=font(20, True))) + 40
    d.rounded_rectangle((910 - iw, c.y, 910, c.y + 40), radius=10, fill=WHITE, outline=c.accent, width=2)
    d.text((910 - iw / 2, c.y + 20), "นำเข้า CSV", fill=c.accent, font=font(20, True), anchor="mm")
    c.y += 56
    rows = [("SN12345678 · แอร์ 12000 BTU", "ลูกค้า สมชาย ใจดี (C-2026-0012) · ผูก LINE แล้ว", "หมดประกัน 1 ก.ย. 2571", True),
            ("SN22334455 · พัดลมไอเย็น", "ยังไม่มีลูกค้า", "ยังไม่ระบุวันที่ซื้อ (ยังไม่กำหนดวันหมดประกัน)", False),
            ("SN99887766 · แอร์ 18000 BTU", "ลูกค้า สมหญิง ดีใจ · ยังไม่ผูก LINE", "หมดประกัน 12 มี.ค. 2570", False)]
    for title, who, sub, ok in rows:
        d.rounded_rectangle((90, c.y, 910, c.y + 88), radius=16, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 30), title, fill=INK, font=font(24, True), anchor="lm")
        bx = 114 + int(d.textlength(title, font=font(24, True))) + 16
        bwid = int(d.textlength(who, font=font(18))) + 28
        d.rounded_rectangle((bx, c.y + 14, bx + bwid, c.y + 46), radius=16, fill=OA_SOFT[c.oa] if ok else "#f1eee9")
        d.text((bx + bwid / 2, c.y + 30), who, fill=c.accent if ok else SOFT, font=font(18), anchor="mm")
        d.text((114, c.y + 64), sub, fill=SOFT, font=font(20), anchor="lm")
        c.y += 100
    c.note("ลูกค้าที่พิมพ์ S/N ใน LINE ลูกค้า เข้ารายชื่อเป็น \"ลูกค้า\" ทันที (ไม่ใช่ลูกค้ามุ่งหวัง) และเครื่องผูกกับเรคอร์ดนั้นให้เอง · แชท: \"ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย\"")

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
    c.y += 186
    d.text((92, c.y), "งานอื่นในร้าน", fill=FAINT, font=font(20, True), anchor="lm")
    c.y += 22
    for code, what, who, state, live in (("T-2026-0002 · เครื่องทำน้ำอุ่นไม่ร้อน", "สมหญิง ดีใจ", "ช่าง วิชัย", "กำลังซ่อม", True),
                                         ("T-2026-0003 · ล้างแอร์ประจำปี", "อาทิตย์ แสงจันทร์", "ทีมแอร์", "นัดแล้ว 8 ก.ย.", False)):
        d.rounded_rectangle((90, c.y, 910, c.y + 84), radius=14, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 28), code, fill=INK, font=font(23, True), anchor="lm")
        d.text((114, c.y + 60), f"{what} · {who}", fill=SOFT, font=font(20), anchor="lm")
        sw = int(d.textlength(state, font=font(18))) + 28
        d.rounded_rectangle((886 - sw, c.y + 26, 886, c.y + 58), radius=16, fill=OA_SOFT[c.oa] if live else "#f1eee9")
        d.text((886 - sw / 2, c.y + 42), state, fill=c.accent if live else SOFT, font=font(18), anchor="mm")
        c.y += 96
    c.y += 8
    c.note("แชท: \"มอบหมาย T-2026-0001 ให้ทีม แอร์\" · ช่างทุกคนในทีมได้ข้อความ ใครกด \"รับงาน\" ก่อนได้งาน · ปฏิเสธได้พร้อมเหตุผล")


def sales_chats(c: Canvas):
    """The chat desk. On a wide screen the list and the thread sit side by
    side; on a phone the thread takes the whole screen with a back link
    (round 20T). The box below it sends pictures as well as words."""
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
    for y, who, text in ((236, "them", "ราคาแอร์ 12000 BTU เท่าไหร่ครับ"), (304, "me", "รุ่น Inverter 15,900 รวมติดตั้งครับ"), (372, "them", "ติดตั้งได้เร็วสุดเมื่อไหร่")):
        fnt = font(21)
        w = int(d.textlength(text, font=fnt)) + 36
        if who == "me":
            d.rounded_rectangle((890 - w, y, 890, y + 56), radius=18, fill=c.accent)
            d.text((890 - w + 18, y + 28), text, fill=WHITE, font=fnt, anchor="lm")
        else:
            d.rounded_rectangle((440, y, 440 + w, y + 56), radius=18, fill="#f1f2f5")
            d.text((458, y + 28), text, fill=INK, font=fnt, anchor="lm")
    # A picture the shop sent, and one the customer sent back: both arrive
    # as pictures, in the same columns the words use.
    d.rounded_rectangle((700, 430, 890, 584), radius=18, fill=c.accent)
    d.rounded_rectangle((712, 442, 878, 542), radius=12, fill="#dbe7f2")
    d.polygon([(728, 532), (772, 470), (816, 532)], fill="#9db8cf")
    d.polygon([(796, 532), (830, 490), (864, 532)], fill="#b9cddf")
    d.ellipse((826, 458, 852, 484), fill="#f3d9a8")
    d.text((722, 562), "รุ่นที่แนะนำครับ", fill=WHITE, font=font(19), anchor="lm")
    d.rounded_rectangle((440, 600, 630, 754), radius=18, fill="#f1f2f5")
    d.rounded_rectangle((452, 612, 618, 712), radius=12, fill="#e6e1d8")
    d.rounded_rectangle((472, 640, 546, 700), radius=6, fill="#cfc7b8")
    d.line((490, 660, 528, 660), fill="#a89e8c", width=4)
    d.line((490, 676, 528, 676), fill="#a89e8c", width=4)
    d.ellipse((560, 632, 596, 668), fill="#bfb6a6")
    d.text((462, 732), "รูปเครื่องที่ลูกค้าส่งมา", fill=INK, font=font(19), anchor="lm")
    # Composer: attach, then the box, then send (round 20T).
    d.rounded_rectangle((440, 820, 496, 876), radius=16, fill=WHITE, outline=c.accent, width=2)
    d.rounded_rectangle((454, 836, 482, 860), radius=5, fill=WHITE, outline=c.accent, width=2)
    d.polygon([(458, 857), (466, 845), (474, 857)], fill=c.accent)
    d.ellipse((470, 840, 478, 848), fill=c.accent)
    d.rounded_rectangle((508, 820, 770, 876), radius=16, fill=WHITE, outline=LINE, width=2)
    d.text((528, 848), "พิมพ์คำตอบถึงลูกค้า", fill=FAINT, font=font(21), anchor="lm")
    d.rounded_rectangle((790, 820, 890, 876), radius=16, fill=c.accent)
    d.text((840, 848), "ส่ง", fill=WHITE, font=font(23, True), anchor="mm")
    d.text((440, 790), "แนบรูป → เลือกรูป ใส่คำอธิบายได้ แล้วกดส่ง", fill=SOFT, font=font(19), anchor="lm")
    c.y = 910

def sales_approve(c: Canvas):
    c.dash_frame("รอการอนุมัติ", "รอตรวจ 2")
    c.card("SR-2026-0001 · T-2026-0001 แอร์ไม่เย็น", ["ช่าง สมศักดิ์ · ปิดงาน 4 ก.ย. 11:40", "ปัญหาที่พบ: คอมเพรสเซอร์รั่ว น้ำยาหมด", "สิ่งที่แก้ไข: เปลี่ยนคอมเพรสเซอร์ เติมน้ำยา ทดสอบแล้วเย็นปกติ", "รูปหลังซ่อม 2 รูป · เช็คอิน 10:02 (มีตำแหน่ง)"], [("อนุมัติ", True), ("ตีกลับ", False)], badge="รอตรวจ")
    d = c.d
    c.y += 4
    d.text((92, c.y), "คิวที่เหลือ", fill=FAINT, font=font(20, True), anchor="lm")
    c.y += 22
    for code, who, state, waiting in (("SR-2026-0002 · T-2026-0004 ล้างแอร์", "ช่าง วิชัย · ปิดงาน 5 ก.ย. 16:20", "รอตรวจ", True),
                                      ("SR-2026-0000 · T-2025-0198 ย้ายแอร์", "ช่าง สมศักดิ์ · อนุมัติ 2 ก.ย.", "อนุมัติแล้ว", False)):
        d.rounded_rectangle((90, c.y, 910, c.y + 84), radius=14, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 28), code, fill=INK, font=font(23, True), anchor="lm")
        d.text((114, c.y + 60), who, fill=SOFT, font=font(20), anchor="lm")
        sw = int(d.textlength(state, font=font(18))) + 28
        d.rounded_rectangle((886 - sw, c.y + 26, 886, c.y + 58), radius=16, fill="#fdeee2" if waiting else OA_SOFT[c.oa])
        d.text((886 - sw / 2, c.y + 42), state, fill="#e8731a" if waiting else c.accent, font=font(18), anchor="mm")
        c.y += 96
    c.y += 8
    c.note("แชท: \"อนุมัติ SR-2026-0001\" หรือ \"ตีกลับ SR-2026-0001 รูปไม่ชัด\" · อนุมัติแล้ว ลูกค้าได้ปุ่มให้คะแนน ช่างได้ PDF")


def sales_crm(c: Canvas):
    """The Sales dashboard. The tile grid in the old picture was deleted on
    8 ก.ย. 2569 — every page is reached from the left rail now, and the home
    screen carries the pipeline and the four places the day starts."""
    c.dash_frame("แดชบอร์ดทีมขาย", "ร้านเย็นสบาย")
    d = c.d
    top = c.y
    c.rail([(None, ["ภาพรวม"]),
            ("งานขาย", ["แชทลูกค้า", "ลูกค้า", "ดีล", "ใบเสนอราคา", "ใบแจ้งหนี้", "นัดหมายทั้งหมด", "สินค้า"]),
            ("งานบริการ", ["งานซ่อม", "ทะเบียนสินค้า", "ทีมช่าง"]),
            ("เอกสารและรายงาน", ["รายงานการซ่อม", "รายงาน AI", "ความพึงพอใจ", "รอการอนุมัติ"]),
            ("จัดการร้าน", ["ข้อมูลบริษัท", "สมาชิกในร้าน", "จัดการสิทธิ์และบทบาท"])],
           active="ภาพรวม")
    x0 = 350
    d.rounded_rectangle((x0, top, 910, top + 150), radius=18, fill=OA_SOFT[c.oa], outline=LINE, width=2)
    d.text((x0 + 24, top + 34), "มูลค่าในไปป์ไลน์", fill=SOFT, font=font(22), anchor="lm")
    d.text((x0 + 24, top + 82), "1,250,000 บาท", fill=INK, font=font(38, True), anchor="lm")
    d.text((x0 + 24, top + 122), "7 ดีล · ปิดเดือนนี้ 2 · ใบเสนอราคารอตอบ 3", fill=SOFT, font=font(20), anchor="lm")
    y = top + 172
    d.text((x0 + 4, y), "เริ่มงานต่อ", fill=FAINT, font=font(20, True), anchor="lm")
    y += 22
    for i, (name, sub) in enumerate((("แชทลูกค้า", "รอตอบ 1"), ("ลูกค้า", "24 ราย"), ("ดีล", "7 ดีล"), ("ใบเสนอราคา", "ค้างตอบ 3"))):
        r, col = divmod(i, 2)
        bx, by = x0 + col * 290, y + r * 100
        d.rounded_rectangle((bx, by, bx + 270, by + 84), radius=14, fill=WHITE, outline=LINE, width=2)
        d.ellipse((bx + 18, by + 30, bx + 42, by + 54), fill=OA_SOFT[c.oa])
        d.text((bx + 56, by + 34), name, fill=INK, font=font(23, True), anchor="lm")
        d.text((bx + 56, by + 62), sub, fill=SOFT, font=font(19), anchor="lm")
    y = top + 402
    d.text((x0 + 4, y), "ดีลที่ต้องตามวันนี้", fill=FAINT, font=font(20, True), anchor="lm")
    y += 22
    for code, who, money, state, tone in (("D-2026-0007", "อาทิตย์ แสงจันทร์", "500,000 บาท · ปิด 30 ก.ย. 2569", "ส่งใบเสนอราคาแล้ว", True),
                                          ("D-2026-0006", "สมชาย ใจดี", "250,000 บาท · ปิดสิ้นเดือนนี้", "ออกใบแจ้งหนี้แล้ว", True)):
        d.rounded_rectangle((x0, y, 910, y + 92), radius=14, fill=WHITE, outline=LINE, width=2)
        d.text((x0 + 22, y + 28), f"{code} · {who}", fill=INK, font=font(23, True), anchor="lm")
        d.text((x0 + 22, y + 62), money, fill=SOFT, font=font(20), anchor="lm")
        sw = int(d.textlength(state, font=font(18))) + 28
        d.rounded_rectangle((890 - sw, y + 48, 890, y + 78), radius=15, fill=OA_SOFT[c.oa] if tone else "#f1eee9")
        d.text((890 - sw / 2, y + 63), state, fill=c.accent, font=font(18), anchor="mm")
        y += 104
    d.rounded_rectangle((x0, 800, 910, 900), radius=14, fill=OA_SOFT[c.oa])
    for i, line in enumerate(wrap(d, "แชททำได้เหมือนกัน: \"สร้างดีลให้ อาทิตย์ มูลค่า 500,000 ปิดสิ้นเดือนนี้\" · \"ออกใบแจ้งหนี้จากใบเสนอราคา QT-2026-0007\" · \"ยอดค้างชำระ\"", font(19), 520)[:4]):
        d.text((x0 + 20, 822 + i * 26), line, fill=INK, font=font(19), anchor="lm")
    c.y = 910

def sales_ai_report(c: Canvas):
    c.phone_frame()
    c.bubble("สรุปงานค้างแยกตามช่าง", "user")
    c.bubble("จำนวนงานซ่อม · status=open · แยกตามช่าง\n• สมศักดิ์: 5\n• วิชัย: 2\n• ยังไม่มอบหมาย: 3\nรวม 10\nไฟล์ (ใช้ได้ 7 วัน): CSV · หน้าเว็บ", "bot", bold_first=True)
    c.y += 6
    d = c.d
    d.rounded_rectangle((160, c.y, 840, c.y + 152), radius=18, fill=WHITE, outline=LINE, width=2)
    for i, (name, v) in enumerate((("สมศักดิ์", 5), ("วิชัย", 2), ("ยังไม่มอบหมาย", 3))):
        y = c.y + 24 + i * 42
        d.text((184, y + 10), name, fill=INK, font=font(21), anchor="lm")
        d.rounded_rectangle((380, y, 380 + int(370 * v / 5), y + 20), radius=10, fill=c.accent)
        d.text((800, y + 10), str(v), fill=INK, font=font(21, True), anchor="rm")
    c.y += 166
    c.bubble("ถามแบบเดียวกันได้ที่เมนู \"รายงาน AI\" บนหน้าจอ", "bot")
    c.caption("AI แปลงคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลร้านคุณเท่านั้น")


def sales_help(c: Canvas):
    """Where to go when the chat says you have no permission. Round 20Y made
    this page hide the permissions until they are asked for: a role opens to
    groups, and a group opens to its keys."""
    c.dash_frame("จัดการสิทธิ์และบทบาท", "รายละเอียดบทบาท ขาย")
    d = c.d
    d.text((92, c.y + 16), "ขาย · 18 สิทธิ์ · แตะชื่อหมวดเพื่อกางรายการ", fill=SOFT, font=font(22), anchor="lm")
    c.y += 48
    def group_head(name: str, on: int, total: int, open_: bool) -> None:
        d.rounded_rectangle((90, c.y, 910, c.y + 56), radius=14, fill=WHITE, outline=LINE, width=2)
        cx = 118
        if open_:
            d.polygon([(cx - 10, c.y + 24), (cx + 10, c.y + 24), (cx, c.y + 38)], fill=SOFT)
        else:
            d.polygon([(cx - 5, c.y + 18), (cx + 9, c.y + 28), (cx - 5, c.y + 38)], fill=SOFT)
        d.text((150, c.y + 28), name, fill=INK, font=font(24, True), anchor="lm")
        chip = f"{on}/{total}"
        cw = int(d.textlength(chip, font=font(19, True))) + 30
        d.rounded_rectangle((886 - cw, c.y + 13, 886, c.y + 43), radius=15, fill=OA_SOFT[c.oa] if on else "#f1eee9")
        d.text((886 - cw / 2, c.y + 28), chip, fill=c.accent if on else SOFT, font=font(19, True), anchor="mm")
        c.y += 56
    group_head("ลูกค้า", 5, 6, False)
    c.y += 10
    group_head("ดีล", 4, 4, False)
    c.y += 10
    group_head("ใบแจ้งหนี้", 2, 4, True)
    d.rounded_rectangle((90, c.y, 910, c.y + 106), radius=14, fill="#fbfaf8", outline=LINE, width=2)
    for i, (label, key, on) in enumerate((("ออกใบแจ้งหนี้", "invoice.create", True), ("รับชำระและออกใบเสร็จ", "invoice.payment", False))):
        yy = c.y + 14 + i * 46
        c.tick(150, yy, on)
        d.text((192, yy + 13), label, fill=INK if on else SOFT, font=font(22), anchor="lm")
        d.text((560, yy + 14), key, fill=FAINT, font=font(19), anchor="lm")
    c.y += 116
    group_head("รายงาน", 1, 2, False)
    c.y += 14
    d.rounded_rectangle((90, c.y, 910, c.y + 56), radius=14, fill=OA_SOFT[c.oa])
    d.text((114, c.y + 28), "เลือกไว้ 18 สิทธิ์", fill=INK, font=font(21), anchor="lm")
    for label, primary, right in (("บันทึก", True, 890), ("ยกเลิก", False, 760)):
        bw = int(d.textlength(label, font=font(21, True))) + 44
        d.rounded_rectangle((right - bw, c.y + 10, right, c.y + 46), radius=10, fill=c.accent if primary else WHITE, outline=c.accent, width=2)
        d.text((right - bw / 2, c.y + 28), label, fill=WHITE if primary else c.accent, font=font(21, True), anchor="mm")
    c.y += 72
    c.card("ติดขัด?", ["\"วิธีใช้\" — คู่มือทั้งหมดในแชท", "\"ทำอะไรได้บ้าง\" — สิทธิ์ของคุณตามหมวด", "\"เริ่มใหม่\" — ล้างเฉพาะบทสนทนา ข้อมูลไม่หาย"], [("วิธีใช้", True)])

def permissions_overview(c: Canvas):
    """The owner\'s roles page after round 20Y: a row per role that says only
    how many permissions it holds and which areas they fall in. The keys
    themselves appear in รายละเอียด or แก้ไข, never on the list."""
    c.dash_frame("จัดการสิทธิ์และบทบาท", "เจ้าของร้านตั้งสิทธิ์")
    d = c.d
    d.text((92, c.y + 16), "สิทธิ์ถูกซ่อนไว้ก่อน — แถวบอกแค่จำนวนและหมวด", fill=SOFT, font=font(22), anchor="lm")
    c.y += 50
    d.text((92, c.y + 20), "4 บทบาท", fill=FAINT, font=font(21, True), anchor="lm")
    bw = int(d.textlength("สร้างบทบาทใหม่", font=font(21, True))) + 44
    d.rounded_rectangle((910 - bw, c.y, 910, c.y + 44), radius=11, fill=c.accent)
    d.text((910 - bw / 2, c.y + 22), "สร้างบทบาทใหม่", fill=WHITE, font=font(21, True), anchor="mm")
    c.y += 60
    rows = [("เจ้าของร้าน", "บทบาทเจ้าของ — แก้ไขไม่ได้", "มีทุกสิทธิ์ในร้าน", [], 0, False),
            ("ขาย", None, "18 สิทธิ์", ["ลูกค้า", "ดีล", "ใบเสนอราคา"], 2, True),
            ("CS", None, "11 สิทธิ์", ["แชทลูกค้า", "งานซ่อม"], 1, True),
            ("ช่าง", None, "6 สิทธิ์", ["งานซ่อม", "รายงานการซ่อม"], 0, True)]
    for name, badge, count, chips, more, editable in rows:
        d.rounded_rectangle((90, c.y, 910, c.y + 96), radius=16, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 32), name, fill=INK, font=font(26, True), anchor="lm")
        if badge:
            bx = 114 + int(d.textlength(name, font=font(26, True))) + 16
            w = int(d.textlength(badge, font=font(18))) + 28
            d.rounded_rectangle((bx, c.y + 16, bx + w, c.y + 48), radius=16, fill=OA_SOFT[c.oa])
            d.text((bx + w / 2, c.y + 32), badge, fill=c.accent, font=font(18), anchor="mm")
        d.text((114, c.y + 70), count, fill=SOFT, font=font(20), anchor="lm")
        x = 114 + int(d.textlength(count, font=font(20))) + 16
        for chip in chips:
            w = int(d.textlength(chip, font=font(18))) + 26
            d.rounded_rectangle((x, c.y + 56, x + w, c.y + 84), radius=14, fill="#f1eee9")
            d.text((x + w / 2, c.y + 70), chip, fill=SOFT, font=font(18), anchor="mm")
            x += w + 10
        if more:
            d.text((x + 2, c.y + 70), f"+{more} กลุ่ม", fill=FAINT, font=font(18), anchor="lm")
        right = 890
        for label in (("แก้ไข",) if editable else ()) + ("รายละเอียด",):
            w = int(d.textlength(label, font=font(20, True))) + 36
            d.rounded_rectangle((right - w, c.y + 30, right, c.y + 72), radius=10, fill=WHITE, outline=c.accent, width=2)
            d.text((right - w / 2, c.y + 51), label, fill=c.accent, font=font(20, True), anchor="mm")
            right -= w + 12
        c.y += 108
    c.note("แชทตอบว่า \"ยังไม่มีสิทธิ์ «...»\" ก็มาเปิดที่นี่: แก้ไขบทบาทนั้น กางเฉพาะหมวดที่ต้องใช้ แล้วบันทึก · สิทธิ์ที่เปิดมีผลทันทีทั้งในแชทและบนหน้าจอ")
    d.rounded_rectangle((90, c.y, 910, c.y + 112), radius=16, fill=WHITE, outline=LINE, width=2)
    d.text((114, c.y + 30), "กด \"รายละเอียด\" แล้วสิทธิ์จึงกางออก — ทีละหมวด", fill=SOFT, font=font(21), anchor="lm")
    x = 114
    for name, on, total in (("ลูกค้า", 5, 6), ("ดีล", 4, 4), ("ใบเสนอราคา", 3, 3), ("ใบแจ้งหนี้", 2, 4)):
        label = f"{name} {on}/{total}"
        w = int(d.textlength(label, font=font(20))) + 32
        d.rounded_rectangle((x, c.y + 56, x + w, c.y + 94), radius=12, fill=OA_SOFT[c.oa])
        d.text((x + w / 2, c.y + 75), label, fill=c.accent, font=font(20), anchor="mm")
        x += w + 12
    c.y += 128


def sales_api(c: Canvas):
    """Round 21B — the owner's API page: the list, and the key shown once."""
    c.dash_frame("API สำหรับระบบภายนอก", "เจ้าของร้านเท่านั้น")
    d = c.d
    for i, line in enumerate(wrap(d, "สร้าง key ให้ระบบบัญชี/ERP เรียกข้อมูลร้านนี้ได้ — key ทำงานในนามร้าน เจ้าของสร้างและเพิกถอนเท่านั้น", font(21), 800)):
        d.text((92, c.y + 14 + i * 28), line, fill=SOFT, font=font(21), anchor="lm")
    c.y += 72
    d.text((92, c.y + 18), "2 key", fill=FAINT, font=font(20, True), anchor="lm")
    bw = int(d.textlength("สร้าง key", font=font(21, True))) + 44
    d.rounded_rectangle((910 - bw, c.y, 910, c.y + 44), radius=11, fill=c.accent)
    d.text((910 - bw / 2, c.y + 22), "สร้าง key", fill=WHITE, font=font(21, True), anchor="mm")
    c.y += 62
    for name, prefix, last in (("ระบบบัญชี Express", "chann_live_ab12…", "ใช้ล่าสุด 22 ก.ย. 2569"),
                               ("ร้านค้าออนไลน์", "chann_live_cd34…", "ยังไม่เคยใช้")):
        d.rounded_rectangle((90, c.y, 910, c.y + 96), radius=16, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 32), name, fill=INK, font=font(25, True), anchor="lm")
        px = 114 + int(d.textlength(name, font=font(25, True))) + 16
        d.text((px, c.y + 33), prefix, fill=SOFT, font=font(20), anchor="lm")
        d.text((114, c.y + 68), f"สร้างเมื่อ 20 ก.ย. 2569 · {last}", fill=SOFT, font=font(20), anchor="lm")
        rw = int(d.textlength("เพิกถอน", font=font(20, True))) + 36
        d.rounded_rectangle((890 - rw, c.y + 28, 890, c.y + 70), radius=10, fill=WHITE, outline="#c2410c", width=2)
        d.text((890 - rw / 2, c.y + 49), "เพิกถอน", fill="#c2410c", font=font(20, True), anchor="mm")
        c.y += 108
    c.y += 6
    # The sheet after "สร้าง key": the whole key, once.
    d.rounded_rectangle((90, c.y, 910, c.y + 236), radius=18, fill=WHITE, outline=c.accent, width=3)
    d.text((114, c.y + 30), "สร้าง key แล้ว — ระบบบัญชี Express", fill=INK, font=font(24, True), anchor="lm")
    d.rounded_rectangle((114, c.y + 56, 886, c.y + 112), radius=12, fill="#f6f4ef", outline=LINE, width=2)
    d.text((134, c.y + 84), "chann_live_ab12Kq9ZtP3mWx7LcR2nVb8Yd", fill=INK, font=font(23), anchor="lm")
    d.text((114, c.y + 138), "คัดลอกเก็บไว้ตอนนี้ — จะไม่แสดงอีก ถ้าหาย ให้สร้างใหม่แล้วเพิกถอนอันเดิม", fill=SOFT, font=font(20), anchor="lm")
    for label, primary, right in (("ปิด", False, 886), ("คัดลอก", True, 760)):
        w = int(d.textlength(label, font=font(21, True))) + 44
        d.rounded_rectangle((right - w, c.y + 166, right, c.y + 212), radius=10, fill=c.accent if primary else WHITE, outline=c.accent, width=2)
        d.text((right - w / 2, c.y + 189), label, fill=WHITE if primary else c.accent, font=font(21, True), anchor="mm")
    c.y += 252

SCENES = {
    "customer": {"customer-link": customer_link, "customer-shop": customer_shop, "customer-chat": customer_chat,
                 "customer-register": customer_register, "customer-report": customer_report, "customer-status": customer_status,
                 "customer-after": customer_after, "customer-pdpa": customer_pdpa, "customer-invoices": customer_invoices},
    "technician": {"tech-join": tech_join, "tech-take": tech_take, "tech-checkin": tech_checkin, "tech-finish": tech_finish,
                   "tech-approved": tech_approved},
    "sales": {"sales-setup": sales_setup, "sales-members": sales_members, "sales-api": sales_api, "sales-units": sales_units, "sales-dispatch": sales_dispatch, "sales-chats": sales_chats,
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
