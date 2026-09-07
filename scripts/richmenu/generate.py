"""Generate Chann Rich Menu v3: clear hierarchy, six exact tap areas.

The first action is intentionally large because it is the highest-frequency
task for each audience. Five secondary actions use two card sizes. This keeps
all existing commands while giving the menu a clear visual starting point.

Two pages per OA (main / more) and two languages (th / en — Phase 20):
  out/richmenu-<oa>[-more][-en].png + .json
The tabs switch pages through rich-menu aliases (chann-<oa>-<page>[-en]);
the application links the -en pair to a person who chose English
(services/richmenu.py), so the picture follows the language of the chat.
Output stays compatible with richmenu-apply.sh.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
# Bundled with the script (scripts/richmenu/fonts/, TLWG Garuda, GPL-2+
# with font exception) so this runs on any machine — Cloud Shell has no
# sudo for apt and resets installed fonts on every restart. A system copy
# is used when present; RICHMENU_FONT_DIR overrides both.
_FONT_DIRS = [
    p for p in (
        __import__("os").environ.get("RICHMENU_FONT_DIR"),
        str(Path(__file__).parent / "fonts"),
        "/usr/share/fonts/truetype/tlwg",
    ) if p
]
_FONT_DIR = next(
    (d for d in _FONT_DIRS if (Path(d) / "Garuda-Bold.ttf").exists()), _FONT_DIRS[-1],
)
FONT_BOLD = str(Path(_FONT_DIR) / "Garuda-Bold.ttf")
FONT_REG = str(Path(_FONT_DIR) / "Garuda.ttf")

_LANG = ("สลับภาษา EN/TH", "Switch language", "globe", {"type": "message", "text": "สลับภาษา"})
TILES = {
    "sales": {
        "main": [
            ("งานวันนี้", "Today's work", "sun", {"type": "message", "text": "งานวันนี้"}),
            ("รายชื่อลูกค้า", "Customers", "people", {"type": "message", "text": "รายชื่อลูกค้า"}),
            ("รายการรออนุมัติ", "Awaiting approval", "check", {"type": "message", "text": "รายการรออนุมัติ"}),
            ("นัดหมาย", "Appointments", "calendar", {"type": "message", "text": "นัดหมายทั้งหมด"}),
            ("เปิดแดชบอร์ด", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_SALES}"}),
            ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
        ],
        "more": [
            ("แชทลูกค้า", "Customer chats", "chat", {"type": "uri", "uri": "{LIFF_SALES}/chats"}),
            ("รายการดีล", "Deals", "briefcase", {"type": "message", "text": "รายการดีล"}),
            ("รายการสินค้า", "Products", "tag", {"type": "message", "text": "รายการสินค้า"}),
            ("ทีมช่าง", "Technician teams", "people", {"type": "message", "text": "ทีมช่าง"}),
            ("ข้อมูลบริษัท", "Company profile", "building", {"type": "message", "text": "ข้อมูลบริษัท"}),
            _LANG,
        ],
    },
    "technician": {
        "main": [
            ("งานของฉัน", "My jobs", "wrench", {"type": "message", "text": "งานของฉัน"}),
            ("งานที่เปิดรับ", "Jobs to take", "inbox", {"type": "message", "text": "งานที่เปิดรับ"}),
            ("เช็คอิน", "Check in on site", "pin", {"type": "message", "text": "เช็คอิน"}),
            ("ปิดงาน+รายงาน", "Finish + report", "check", {"type": "message", "text": "ปิดงาน"}),
            ("เปิดหน้าจอช่าง", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_TECHNICIAN}"}),
            ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
        ],
        "more": [
            ("รายงานของฉัน", "My reports", "doc", {"type": "uri", "uri": "{LIFF_TECHNICIAN_REPORTS}"}),
            ("งานของทีม", "Team jobs", "people", {"type": "message", "text": "งานของทีม"}),
            ("ปฏิเสธงาน", "Decline a job", "cross", {"type": "message", "text": "ปฏิเสธงาน"}),
            ("ข้อมูลของฉัน", "My profile", "user", {"type": "message", "text": "ข้อมูลของฉัน"}),
            ("สิทธิ์ของฉัน", "What I may do", "key", {"type": "message", "text": "สิทธิ์ของฉัน"}),
            _LANG,
        ],
    },
    "customer": {
        "main": [
            ("แจ้งซ่อม", "Report a fault", "wrench", {"type": "message", "text": "แจ้งซ่อม"}),
            ("สถานะการซ่อม", "Repair status", "clock", {"type": "message", "text": "สถานะการซ่อม"}),
            ("คุยกับร้าน", "Talk to the shop", "chat", {"type": "message", "text": "คุยกับร้าน"}),
            ("ลงทะเบียนสินค้า", "Register a product", "shield", {"type": "message", "text": "ลงทะเบียนสินค้า"}),
            ("เปิดหน้าจอลูกค้า", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_CUSTOMER}"}),
            ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
        ],
        "more": [
            ("สินค้าทั้งหมด", "All products", "cart", {"type": "message", "text": "สินค้าทั้งหมด"}),
            ("ประวัติการซื้อ", "Purchase history", "doc", {"type": "message", "text": "ประวัติการซื้อ"}),
            ("ประกันของฉัน", "My warranties", "shield", {"type": "message", "text": "ประกันของฉัน"}),
            ("ติดต่อร้าน", "Contact the shop", "phone", {"type": "message", "text": "ติดต่อร้าน"}),
            ("ข้อมูลของฉัน", "My profile", "user", {"type": "message", "text": "ข้อมูลของฉัน"}),
            _LANG,
        ],
    },
}


W, H = 2500, 1686
HEADER_H = 300
GAP = 24
PAGES = ("main", "more")
LANGS = ("th", "en")
ALIAS = "chann-{oa}-{page}"
OAS = ("customer", "sales", "technician")

# Words on the chrome (not the tiles) per language.
CHROME = {
    "th": {"tabs": {"main": "หน้าหลัก", "more": "เพิ่มเติม"}, "pill": "งานหลัก · PRIMARY",
           "tap": "แตะเพื่อเริ่ม", "bar": "เมนู"},
    "en": {"tabs": {"main": "Home", "more": "More"}, "pill": "PRIMARY",
           "tap": "Tap to start", "bar": "Menu"},
}

ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "chann-logo.png"
OUT_DIR = ROOT / "out"

THEMES = {
    "customer": {
        "accent": "#EA6A12",
        "deep": "#A83D08",
        "soft": "#FFF0E6",
        "surface": "#FFF8F3",
        "role": "บริการลูกค้า",
        "role_en": "CUSTOMER SERVICE",
    },
    "sales": {
        "accent": "#0F8B57",
        "deep": "#075B3A",
        "soft": "#E6F6EF",
        "surface": "#F4FBF8",
        "role": "ทีมขาย",
        "role_en": "SALES WORKSPACE",
    },
    "technician": {
        "accent": "#1769D2",
        "deep": "#0A438F",
        "soft": "#E8F1FE",
        "surface": "#F5F9FF",
        "role": "ทีมช่าง",
        "role_en": "TECHNICIAN WORKSPACE",
    },
}


def _icon(draw: ImageDraw.ImageDraw, name: str, cx: int, cy: int,
          color: str, size: int = 180):
    """Draw one flat, solid, front-facing glyph with a transparent mask.

    The icon language follows approved concept A: one colour, no badge,
    no outline container, no shadow and only essential negative space.
    Rendering through a 4x mask keeps curves crisp in LINE's scaled view.
    """
    import math

    aa = 4
    base = 200
    mask = Image.new("L", (base * aa, base * aa), 0)
    md = ImageDraw.Draw(mask)

    def box(values):
        return tuple(round(v * aa) for v in values)

    def point(values):
        return tuple((round(x * aa), round(y * aa)) for x, y in values)

    def ellipse(values, fill=255):
        md.ellipse(box(values), fill=fill)

    def rect(values, fill=255):
        md.rectangle(box(values), fill=fill)

    def rounded(values, radius, fill=255):
        md.rounded_rectangle(box(values), radius=round(radius * aa), fill=fill)

    def polygon(values, fill=255):
        md.polygon(point(values), fill=fill)

    def line(values, width, fill=255, caps=True):
        pts = point(values)
        scaled_width = round(width * aa)
        md.line(pts, fill=fill, width=scaled_width, joint="curve")
        if caps:
            radius = width / 2
            for x, y in (values[0], values[-1]):
                ellipse((x - radius, y - radius, x + radius, y + radius), fill)

    if name == "sun":
        ellipse((62, 62, 138, 138))
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            p1 = (100 + 55 * math.cos(rad), 100 + 55 * math.sin(rad))
            p2 = (100 + 79 * math.cos(rad), 100 + 79 * math.sin(rad))
            line((p1, p2), 14)

    elif name == "people":
        ellipse((28, 38, 84, 94))
        rounded((15, 98, 98, 170), 38)
        ellipse((111, 48, 159, 96))
        rounded((99, 102, 174, 164), 32)

    elif name == "calendar":
        rounded((28, 36, 172, 176), 18)
        rect((28, 66, 172, 88), 0)
        rounded((48, 94, 152, 155), 10, 0)
        rounded((52, 22, 74, 58), 10)
        rounded((126, 22, 148, 58), 10)

    elif name == "plus":
        rounded((85, 28, 115, 172), 15)
        rounded((28, 85, 172, 115), 15)

    elif name == "doc":
        rounded((43, 18, 157, 182), 15)
        polygon(((119, 18), (157, 56), (119, 56)), 0)
        rounded((65, 84, 135, 98), 7, 0)
        rounded((65, 114, 135, 128), 7, 0)
        rounded((65, 144, 114, 158), 7, 0)

    elif name == "grid":
        for x in (28, 108):
            for y in (28, 108):
                rounded((x, y, x + 64, y + 64), 15)

    elif name == "wrench":
        # Upright construction avoids the usability problem of a steep tilt.
        ellipse((46, 12, 154, 120))
        ellipse((76, 42, 124, 90), 0)
        polygon(((66, 0), (134, 0), (119, 57), (81, 57)), 0)
        line(((100, 91), (100, 166)), 38)
        ellipse((81, 147, 119, 185))
        ellipse((92, 158, 108, 174), 0)

    elif name == "inbox":
        polygon(((26, 55), (174, 55), (160, 166), (40, 166)))
        polygon(((54, 78), (146, 78), (137, 116), (119, 116),
                 (109, 132), (91, 132), (81, 116), (63, 116)), 0)
        rounded((57, 143, 143, 157), 7, 0)

    elif name == "pin":
        ellipse((42, 18, 158, 134))
        polygon(((56, 101), (144, 101), (100, 184)))
        ellipse((78, 54, 122, 98), 0)

    elif name == "check":
        line(((34, 104), (80, 149), (168, 51)), 28)

    elif name == "clock":
        ellipse((20, 20, 180, 180))
        ellipse((47, 47, 153, 153), 0)
        line(((100, 100), (100, 63)), 18)
        line(((100, 100), (132, 118)), 18)

    elif name == "shield":
        polygon(((100, 16), (166, 42), (158, 123), (100, 184), (42, 123), (34, 42)))
        polygon(((67, 99), (88, 120), (137, 70), (147, 82), (88, 143), (57, 112)), 0)

    elif name == "search":
        ellipse((24, 24, 135, 135))
        ellipse((49, 49, 110, 110), 0)
        line(((122, 122), (171, 171)), 28)

    elif name == "chat":
        rounded((22, 34, 178, 143), 31)
        polygon(((50, 132), (94, 132), (55, 178)))

    elif name == "help":
        font = ImageFont.truetype(FONT_BOLD, 166 * aa)
        md.text((100 * aa, 91 * aa), "?", font=font, fill=255, anchor="mm")

    elif name == "briefcase":
        rounded((20, 56, 180, 170), 18)
        rounded((70, 28, 130, 72), 14)
        rounded((85, 44, 115, 65), 6, 0)
        rect((20, 103, 180, 119), 0)
        rounded((88, 96, 112, 126), 7)

    elif name == "tag":
        polygon(((24, 46), (112, 46), (178, 112), (106, 184), (24, 102)))
        ellipse((48, 66, 76, 94), 0)

    elif name == "building":
        rounded((37, 18, 163, 180), 12)
        for x in (61, 111):
            for y in (49, 91):
                rounded((x, y, x + 28, y + 25), 5, 0)
        rounded((83, 137, 117, 180), 5, 0)

    elif name == "globe":
        ellipse((18, 18, 182, 182))
        ellipse((44, 44, 156, 156), 0)
        line(((100, 23), (100, 177)), 16)
        line(((25, 100), (175, 100)), 16)
        md.arc(box((55, 22, 145, 178)), 90, 270, fill=255, width=12 * aa)
        md.arc(box((55, 22, 145, 178)), 270, 90, fill=255, width=12 * aa)

    elif name == "user":
        ellipse((62, 25, 138, 101))
        rounded((28, 107, 172, 181), 48)

    elif name == "key":
        ellipse((20, 54, 103, 137))
        ellipse((46, 80, 77, 111), 0)
        line(((91, 96), (176, 96)), 28)
        rect((140, 95, 161, 132))
        rect((164, 95, 181, 119))

    elif name == "cart":
        line(((23, 45), (43, 45), (61, 126), (155, 126), (174, 69), (53, 69)), 18)
        ellipse((56, 143, 84, 171))
        ellipse((132, 143, 160, 171))

    elif name == "phone":
        rounded((51, 14, 149, 186), 22)
        rounded((69, 38, 131, 151), 9, 0)
        rounded((87, 164, 113, 174), 5, 0)

    elif name == "cross":
        line(((44, 44), (156, 156)), 30)
        line(((156, 44), (44, 156)), 30)

    else:
        ellipse((55, 55, 145, 145))

    rendered = mask.resize((size, size), Image.Resampling.LANCZOS)
    draw.bitmap((cx - size // 2, cy - size // 2), rendered, fill=color)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _mix(a: str, b: str, ratio: float) -> tuple[int, int, int]:
    aa, bb = _rgb(a), _rgb(b)
    return tuple(round(x + (y - x) * ratio) for x, y in zip(aa, bb))


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, sizes, bold=True):
    path = FONT_BOLD if bold else FONT_REG
    for size in sizes:
        candidate = _font(path, size)
        if draw.textlength(text, font=candidate) <= max_width:
            return candidate
    return _font(path, sizes[-1])


def _round(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _shadow_card(canvas: Image.Image, box, radius=34, shadow=12):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    d.rounded_rectangle(
        [x0, y0 + shadow, x1, y1 + shadow], radius=radius,
        fill=(15, 23, 42, 25),
    )
    canvas.alpha_composite(layer)


def _primary_gradient(canvas: Image.Image, box, start: str, end: str, radius=42):
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    for y in range(height):
        c = _mix(start, end, y / max(1, height - 1))
        pd.line((0, y, width, y), fill=(*c, 255))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius, fill=255)
    panel.putalpha(mask)
    canvas.alpha_composite(panel, (x0, y0))


def card_bounds():
    """Six non-overlapping regions in reading/priority order."""
    y0 = HEADER_H + GAP
    y1 = H - GAP
    content_h = y1 - y0

    primary_w = 900
    primary = (GAP, y0, GAP + primary_w, y1)

    rx0 = primary[2] + GAP
    rx1 = W - GAP
    right_w = rx1 - rx0
    row_h = (content_h - GAP) // 2
    top_y1 = y0 + row_h
    bottom_y0 = top_y1 + GAP

    top_w = (right_w - GAP) // 2
    top_1 = (rx0, y0, rx0 + top_w, top_y1)
    top_2 = (rx0 + top_w + GAP, y0, rx1, top_y1)

    base = (right_w - 2 * GAP) // 3
    bottom_1 = (rx0, bottom_y0, rx0 + base, y1)
    bottom_2 = (rx0 + base + GAP, bottom_y0, rx0 + 2 * base + GAP, y1)
    bottom_3 = (rx0 + 2 * (base + GAP), bottom_y0, rx1, y1)
    return [primary, top_1, top_2, bottom_1, bottom_2, bottom_3]


def _alias(oa: str, page: str, lang: str = "th") -> str:
    return ALIAS.format(oa=oa, page=page) + ("" if lang == "th" else f"-{lang}")


def _suffix(page: str, lang: str = "th") -> str:
    return ("" if page == "main" else "-more") + ("" if lang == "th" else f"-{lang}")


def _labels(tile, lang: str) -> tuple[str, str]:
    """(big label, small label): Thai first on the Thai menu, English first
    on the English one — the same tile, the same action, read the other way."""
    thai, english = tile[0], tile[1]
    return (thai, english) if lang == "th" else (english, thai)


def layout(oa: str, page: str, lang: str = "th") -> dict:
    """The JSON LINE needs for one page — areas for six tiles and the two
    header tabs — without drawing anything. Pure, so it is testable where
    Pillow is not."""
    areas = []
    tab_w, tab_h, tab_gap = 320, 112, 16
    tabs_start = W - GAP - (2 * tab_w + tab_gap)
    tab_y = 98
    for index, tab_page in enumerate(PAGES):
        areas.append({
            "bounds": {
                "x": tabs_start + index * (tab_w + tab_gap),
                "y": tab_y,
                "width": tab_w,
                "height": tab_h,
            },
            "action": {
                "type": "richmenuswitch",
                "richMenuAliasId": _alias(oa, tab_page, lang),
                "data": f"page={tab_page}",
            },
        })

    for bounds, tile in zip(card_bounds(), TILES[oa][page]):
        x0, y0, x1, y1 = bounds
        areas.append({
            "bounds": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
            "action": tile[3],
        })

    return {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": f"chann-{oa}-v3-{page}" + ("" if lang == "th" else f"-{lang}"),
        "chatBarText": CHROME[lang]["bar"],
        "areas": areas,
        "_alias": _alias(oa, page, lang),
    }


def draw_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, theme, page: str, lang: str):
    draw.rectangle((0, 0, W, HEADER_H), fill="#FFFFFF")
    draw.rectangle((0, 0, W, 16), fill=theme["accent"])
    draw.line((0, HEADER_H - 1, W, HEADER_H - 1), fill="#DDE4EC", width=2)

    logo = Image.open(LOGO_PATH).convert("RGBA")
    bbox = logo.getchannel("A").getbbox()
    if bbox:
        logo = logo.crop(bbox)
    logo.thumbnail((150, 150), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (48, 74))

    role_big, role_small = (theme["role"], theme["role_en"]) if lang == "th" else (theme["role_en"].title(), theme["role"])
    draw.text((230, 82), "Chann CRM", font=_font(FONT_BOLD, 86), fill="#102A43", anchor="la")
    draw.text((234, 180), role_big, font=_font(FONT_BOLD, 46), fill=theme["accent"], anchor="la")
    draw.text((234, 250), role_small, font=_font(FONT_BOLD, 23), fill="#6B7C93", anchor="la")

    tab_w, tab_h, tab_gap = 320, 112, 16
    tabs_start = W - GAP - (2 * tab_w + tab_gap)
    tab_y = 98
    for index, tab_page in enumerate(PAGES):
        x = tabs_start + index * (tab_w + tab_gap)
        active = tab_page == page
        label = CHROME[lang]["tabs"][tab_page]
        if active:
            _round(draw, (x, tab_y, x + tab_w, tab_y + tab_h), 30, theme["accent"])
            color = "#FFFFFF"
        else:
            _round(
                draw, (x, tab_y, x + tab_w, tab_y + tab_h), 30,
                theme["soft"], outline=_mix(theme["accent"], "#FFFFFF", 0.45), width=3,
            )
            color = theme["deep"]
        draw.text((x + tab_w // 2, tab_y + tab_h // 2 + 2), label,
                  font=_font(FONT_BOLD, 48), fill=color, anchor="mm")


def draw_primary(canvas, draw, box, tile, theme, lang: str):
    x0, y0, x1, y1 = box
    _shadow_card(canvas, box, radius=42, shadow=14)
    _primary_gradient(canvas, box, theme["accent"], theme["deep"], radius=42)
    draw = ImageDraw.Draw(canvas)

    pill_fill = _mix(theme["deep"], theme["accent"], 0.46)
    _round(draw, (x0 + 56, y0 + 56, x0 + 300, y0 + 116), 30, pill_fill)
    draw.text((x0 + 178, y0 + 86), CHROME[lang]["pill"], font=_font(FONT_BOLD, 25),
              fill="#FFFFFF", anchor="mm")

    _icon(draw, tile[2], x0 + 183, y0 + 317, "#FFFFFF", size=240)

    big, small = _labels(tile, lang)
    # Latin capitals sit lower on the line than Thai glyphs, so the English
    # headline is capped smaller and both labels get a little more air.
    big_sizes = (112, 102, 92, 82, 72, 62) if lang == "th" else (96, 88, 80, 72, 64)
    big_font = _fit(draw, big, x1 - x0 - 112, big_sizes, bold=True)
    small_font = _fit(draw, small, x1 - x0 - 112, (52, 46, 42, 38), bold=False)
    draw.text((x0 + 56, y0 + 556), big, font=big_font, fill="#FFFFFF", anchor="la")
    draw.text((x0 + 60, y0 + 706), small, font=small_font, fill=(255, 255, 255, 218), anchor="la")

    draw.line((x0 + 56, y1 - 164, x1 - 56, y1 - 164), fill=_mix(theme["accent"], "#FFFFFF", 0.58), width=2)
    draw.text((x0 + 60, y1 - 92), CHROME[lang]["tap"], font=_font(FONT_BOLD, 38), fill="#FFFFFF", anchor="lm")
    _round(draw, (x1 - 154, y1 - 140, x1 - 56, y1 - 42), 49, "#FFFFFF")
    arrow_y = y1 - 92
    draw.line((x1 - 129, arrow_y, x1 - 84, arrow_y), fill=theme["deep"], width=9)
    draw.line((x1 - 101, arrow_y - 18, x1 - 83, arrow_y), fill=theme["deep"], width=9)
    draw.line((x1 - 101, arrow_y + 18, x1 - 83, arrow_y), fill=theme["deep"], width=9)


def draw_top_card(canvas, draw, box, tile, theme, lang: str):
    x0, y0, x1, y1 = box
    _shadow_card(canvas, box)
    _round(draw, box, 34, "#FFFFFF", outline="#E5EAF0", width=2)

    _icon(draw, tile[2], x0 + 133, y0 + 133, theme["accent"], size=168)

    big, small = _labels(tile, lang)
    big_font = _fit(draw, big, x1 - x0 - 96, (78, 70, 64, 58, 52, 46), bold=True)
    small_font = _fit(draw, small, x1 - x0 - 96, (40, 36, 32, 28), bold=False)
    draw.text((x0 + 48, y0 + 300), big, font=big_font, fill="#102A43", anchor="la")
    draw.text((x0 + 50, y0 + 390), small, font=small_font, fill="#66788A", anchor="la")
    draw.rounded_rectangle((x0 + 48, y1 - 58, x1 - 48, y1 - 50), 4, fill=theme["accent"])


def draw_small_card(canvas, draw, box, tile, theme, lang: str):
    x0, y0, x1, y1 = box
    _shadow_card(canvas, box)
    _round(draw, box, 34, "#FFFFFF", outline="#E5EAF0", width=2)

    _icon(draw, tile[2], x0 + 118, y0 + 134, theme["accent"], size=148)

    max_text = x1 - x0 - 72
    big, small = _labels(tile, lang)
    big_font = _fit(draw, big, max_text, (56, 50, 46, 42, 38, 34), bold=True)
    small_font = _fit(draw, small, max_text, (31, 28, 25, 22), bold=False)
    draw.text((x0 + 38, y0 + 294), big, font=big_font, fill="#102A43", anchor="la")
    draw.text((x0 + 40, y0 + 372), small, font=small_font, fill="#66788A", anchor="la")
    draw.rounded_rectangle((x0 + 38, y1 - 54, x1 - 38, y1 - 46), 4, fill=theme["accent"])


def render(oa: str, page: str, lang: str = "th") -> Image.Image:
    theme = THEMES[oa]
    canvas = Image.new("RGBA", (W, H), theme["surface"])
    draw = ImageDraw.Draw(canvas)
    draw_header(canvas, draw, theme, page, lang)

    tiles = TILES[oa][page]
    bounds = card_bounds()
    draw_primary(canvas, draw, bounds[0], tiles[0], theme, lang)
    draw = ImageDraw.Draw(canvas)
    draw_top_card(canvas, draw, bounds[1], tiles[1], theme, lang)
    draw_top_card(canvas, draw, bounds[2], tiles[2], theme, lang)
    draw_small_card(canvas, draw, bounds[3], tiles[3], theme, lang)
    draw_small_card(canvas, draw, bounds[4], tiles[4], theme, lang)
    draw_small_card(canvas, draw, bounds[5], tiles[5], theme, lang)
    return canvas


def build(oa: str, page: str, lang: str = "th", out: Path | None = None) -> tuple[Path, Path]:
    out = out or OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    image_path = out / f"richmenu-{oa}{_suffix(page, lang)}.png"
    json_path = out / f"richmenu-{oa}{_suffix(page, lang)}.json"
    render(oa, page, lang).convert("RGB").save(image_path, optimize=True)
    json_path.write_text(json.dumps(layout(oa, page, lang), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{oa}/{page}/{lang}: {image_path.name} + {json_path.name}")
    return image_path, json_path


if __name__ == "__main__":
    for oa_name in OAS:
        for lang_name in LANGS:
            for page_name in PAGES:
                build(oa_name, page_name, lang_name)
