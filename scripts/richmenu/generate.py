"""Generate the three OA rich menus as one design system, three accents.

Owner spec (2 Sep): CS=orange, Sale=green, Tech=blue, "ตกแต่งให้สวยงาม
กว่านี้" — so the design decisions, written down the way a design review
would ask for them:

* **One layout, three skins.** All three menus share the same 3×2 grid,
  the same type scale, the same icon language. A person who is both a
  salesperson and a technician (common in a Thai SMB) should feel they
  are in the same product, told apart by color — not in three products.
* **The color is the theme, not decoration.** Header band and one accent
  tile carry the OA color at full strength; the other five tiles stay
  near-white with the accent only in the icon and a 6px baseline. Six
  saturated tiles would shout; one accented "primary action" tile per
  menu tells the thumb where to start (Tech → งานของฉัน, CS → แจ้งซ่อม,
  Sale → งานวันนี้).
* **Icons are drawn, not fonts.** Simple geometric glyphs (24px stroke at
  this scale) render identically everywhere and keep the file small.
  Emoji render differently per device and age badly.
* **Text is the label, Thai first.** Garuda Bold at 96px for tile labels
  — readable at the ~23% scale LINE actually displays the menu at.
  A small EN sub-label anchors meaning for mixed teams.
* **Tap targets are the full tile.** 833×703px each — far beyond any
  reachability guideline; the 12px gutters exist to stop mis-taps
  between tiles, not to look airy.

Sizes per LINE spec: 2500×1686, six areas. Output: richmenu-<oa>.png +
richmenu-<oa>.json (areas ready for the API) next to this script.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 2500, 1686
HEADER_H = 280
COLS, ROWS = 3, 2
GUTTER = 12
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

THEMES = {
    "sales": {
        "accent": "#178a50", "deep": "#0d5c34", "soft": "#e7f6ee",
        "ink": "#10281b", "title": "Chann CRM — ทีมขาย",
    },
    "technician": {
        "accent": "#1f6fd6", "deep": "#134a92", "soft": "#e8f1fd",
        "ink": "#12233d", "title": "Chann CRM — ทีมช่าง",
    },
    "customer": {
        "accent": "#e8731a", "deep": "#a94f0d", "soft": "#fdefe2",
        "ink": "#3c2410", "title": "Chann — บริการลูกค้า",
    },
}

# (thai, en, icon, action) per tile, reading order; first tile is the
# accent tile — the one thing this audience does most.
# Owner (3 Sep): every OA gets the same two anchors — one tile that
# plainly opens the full-screen app ("เปิดแดชบอร์ด"), one that explains
# what this OA can do ("วิธีใช้") — and only the everyday verbs beside
# them. Anything a person would not reach for weekly is cut; the chat
# and the app still do it. Message tiles send the exact phrase the chat's
# deterministic triggers match, so a tap never falls through to the AI.
TILES = {
    "sales": [
        ("งานวันนี้", "Today's work", "sun", {"type": "message", "text": "งานวันนี้"}),
        ("รายชื่อลูกค้า", "Customers", "people", {"type": "message", "text": "รายชื่อลูกค้า"}),
        ("รายการรออนุมัติ", "Awaiting approval", "check", {"type": "message", "text": "รายการรออนุมัติ"}),
        ("นัดหมาย", "Appointments", "calendar", {"type": "message", "text": "นัดหมายทั้งหมด"}),
        ("เปิดแดชบอร์ด", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_SALES}"}),
        ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
    ],
    "technician": [
        ("งานของฉัน", "My jobs", "wrench", {"type": "message", "text": "งานของฉัน"}),
        ("งานที่เปิดรับ", "Jobs to take", "inbox", {"type": "message", "text": "งานที่เปิดรับ"}),
        ("เช็คอิน", "Check in on site", "pin", {"type": "message", "text": "เช็คอิน"}),
        ("ปิดงาน+รายงาน", "Finish + report", "check", {"type": "message", "text": "ปิดงาน"}),
        ("เปิดหน้าจอช่าง", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_TECHNICIAN}"}),
        ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
    ],
    "customer": [
        ("แจ้งซ่อม", "Report a fault", "wrench", {"type": "message", "text": "แจ้งซ่อม"}),
        ("สถานะการซ่อม", "Repair status", "clock", {"type": "message", "text": "สถานะการซ่อม"}),
        ("ลงทะเบียนสินค้า", "Register a product", "shield", {"type": "message", "text": "ลงทะเบียนสินค้า"}),
        ("เปิดหน้าจอลูกค้า", "Open the dashboard", "grid", {"type": "uri", "uri": "{LIFF_CUSTOMER}"}),
        ("ติดต่อร้าน", "Contact the shop", "chat", {"type": "message", "text": "ติดต่อร้าน"}),
        ("วิธีใช้", "How this works", "help", {"type": "message", "text": "วิธีใช้"}),
    ],
}


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _icon(draw: ImageDraw.ImageDraw, name: str, cx: int, cy: int, color: str):
    """Geometric glyphs, 24px stroke, drawn inside a 150px box."""
    s = 75  # half-size
    w = 24
    if name == "sun":
        import math
        draw.ellipse([cx - 38, cy - 38, cx + 38, cy + 38], outline=color, width=w)
        for k in range(8):
            a = math.radians(k * 45)
            draw.line(
                [cx + 56 * math.cos(a), cy + 56 * math.sin(a),
                 cx + 82 * math.cos(a), cy + 82 * math.sin(a)],
                fill=color, width=w - 2,
            )
    elif name == "people":
        draw.ellipse([cx - 62, cy - 55, cx - 6, cy + 1], outline=color, width=w)
        draw.arc([cx - 78, cy - 5, cx + 10, cy + 75], 180, 360, fill=color, width=w)
        draw.ellipse([cx + 14, cy - 45, cx + 58, cy - 1], outline=color, width=w)
        draw.arc([cx + 2, cy + 3, cx + 72, cy + 63], 180, 360, fill=color, width=w)
    elif name == "calendar":
        draw.rounded_rectangle([cx - s, cy - 55, cx + s, cy + 60], 14, outline=color, width=w)
        draw.line([cx - s, cy - 18, cx + s, cy - 18], fill=color, width=w)
        draw.line([cx - 35, cy - 75, cx - 35, cy - 45], fill=color, width=w)
        draw.line([cx + 35, cy - 75, cx + 35, cy - 45], fill=color, width=w)
    elif name == "plus":
        draw.line([cx - s, cy, cx + s, cy], fill=color, width=w + 8)
        draw.line([cx, cy - s, cx, cy + s], fill=color, width=w + 8)
    elif name == "doc":
        draw.rounded_rectangle([cx - 55, cy - s, cx + 55, cy + s], 12, outline=color, width=w)
        for i, dy in enumerate((-25, 5, 35)):
            draw.line([cx - 28, cy + dy, cx + (28 if i < 2 else 0), cy + dy], fill=color, width=w - 6)
    elif name == "grid":
        for dx in (-1, 1):
            for dy in (-1, 1):
                draw.rounded_rectangle(
                    [cx + dx * 58 - 32, cy + dy * 58 - 32, cx + dx * 58 + 32, cy + dy * 58 + 32],
                    10, outline=color, width=w - 4,
                )
    elif name == "wrench":
        draw.arc([cx - 70, cy - 70, cx + 10, cy + 10], 300, 200, fill=color, width=w)
        draw.line([cx - 8, cy - 8, cx + 62, cy + 62], fill=color, width=w + 6)
    elif name == "inbox":
        draw.rounded_rectangle([cx - s, cy - 45, cx + s, cy + 55], 12, outline=color, width=w)
        draw.line([cx - s, cy + 8, cx - 30, cy + 8], fill=color, width=w)
        draw.line([cx + 30, cy + 8, cx + s, cy + 8], fill=color, width=w)
        draw.arc([cx - 30, cy - 12, cx + 30, cy + 28], 0, 180, fill=color, width=w)
    elif name == "pin":
        draw.ellipse([cx - 45, cy - 65, cx + 45, cy + 25], outline=color, width=w)
        draw.polygon([(cx - 26, cy + 8), (cx + 26, cy + 8), (cx, cy + 70)], fill=color)
        draw.ellipse([cx - 14, cy - 34, cx + 14, cy - 6], fill=color)
    elif name == "check":
        draw.line([cx - 55, cy + 5, cx - 12, cy + 48], fill=color, width=w + 8)
        draw.line([cx - 12, cy + 48, cx + 60, cy - 45], fill=color, width=w + 8)
    elif name == "clock":
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], outline=color, width=w)
        draw.line([cx, cy, cx, cy - 42], fill=color, width=w - 4)
        draw.line([cx, cy, cx + 30, cy + 14], fill=color, width=w - 4)
    elif name == "shield":
        draw.polygon(
            [(cx, cy - 70), (cx + 58, cy - 45), (cx + 58, cy + 10),
             (cx, cy + 70), (cx - 58, cy + 10), (cx - 58, cy - 45)],
            outline=color, width=w,
        )
        draw.line([cx - 24, cy, cx - 4, cy + 22], fill=color, width=w - 4)
        draw.line([cx - 4, cy + 22, cx + 30, cy - 20], fill=color, width=w - 4)
    elif name == "search":
        draw.ellipse([cx - 60, cy - 60, cx + 20, cy + 20], outline=color, width=w)
        draw.line([cx + 16, cy + 16, cx + 62, cy + 62], fill=color, width=w + 6)
    elif name == "chat":
        draw.rounded_rectangle([cx - 65, cy - 55, cx + 65, cy + 30], 26, outline=color, width=w)
        draw.polygon([(cx - 25, cy + 28), (cx + 8, cy + 28), (cx - 20, cy + 62)], fill=color)
    elif name == "help":
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], outline=color, width=w)
        font = ImageFont.truetype(FONT_BOLD, 88)
        draw.text((cx, cy - 6), "?", font=font, fill=color, anchor="mm")


def build(oa: str, out_dir: Path):
    theme = THEMES[oa]
    img = Image.new("RGB", (W, H), theme["soft"])
    draw = ImageDraw.Draw(img)

    # Header band: brand + which OA this is, at a glance in the chat list.
    draw.rectangle([0, 0, W, HEADER_H], fill=theme["accent"])
    draw.rectangle([0, HEADER_H - 14, W, HEADER_H], fill=theme["deep"])
    title_font = ImageFont.truetype(FONT_BOLD, 110)
    draw.text((70, HEADER_H // 2 - 8), theme["title"], font=title_font,
              fill="#ffffff", anchor="lm")
    tag_font = ImageFont.truetype(FONT_REG, 66)
    draw.text((W - 70, HEADER_H // 2 - 4), "LINE Official Account",
              font=tag_font, fill="#ffffff", anchor="rm")

    tile_w = (W - GUTTER * (COLS + 1)) // COLS
    tile_h = (H - HEADER_H - GUTTER * (ROWS + 1)) // ROWS

    # The whole 2500px image is shown about 375px wide on a phone — a
    # 0.15 scale — so a 52px sub-label landed at ~8px on screen, below
    # the 12px floor any body text needs. 84px is ~13px rendered; the
    # Thai label at 96px is ~14-15px bold, which is why it was readable.
    def fitted(text: str, bold: bool = True,
               sizes=(96, 86, 76, 68, 60), floor: int = 54) -> ImageFont.FreeTypeFont:
        """The largest size that keeps the label inside its tile.

        Thai compounds run long ("ลงทะเบียนรับประกัน") and clipped a tile
        edge in review; shrinking beats wrapping here because a rich menu
        label is a button, and two-line buttons read as two buttons.
        """
        face = FONT_BOLD if bold else FONT_REG
        for size in sizes:
            font = ImageFont.truetype(face, size)
            if draw.textlength(text, font=font) <= tile_w - 90:
                return font
        return ImageFont.truetype(face, floor)

    areas = []
    for i, (thai, en, icon, action) in enumerate(TILES[oa]):
        col, row = i % COLS, i // COLS
        x = GUTTER + col * (tile_w + GUTTER)
        y = HEADER_H + GUTTER + row * (tile_h + GUTTER)
        # Six identical tiles (owner, 3 Sep): the earlier design filled the
        # first tile in the deep shade to mark "the main thing", and in
        # use it read as one tile being different in kind, not in
        # importance. The header carries the OA's colour; every tile is
        # white with the accent icon and baseline.
        _rounded(draw, [x, y, x + tile_w, y + tile_h], 34, "#ffffff")
        # The 6px accent baseline: theme presence without shouting.
        draw.rounded_rectangle(
            [x + 34, y + tile_h - 18, x + tile_w - 34, y + tile_h - 12],
            3, fill=theme["accent"],
        )
        _icon(draw, icon, x + tile_w // 2, y + 205, theme["accent"])
        draw.text((x + tile_w // 2, y + 400), thai, font=fitted(thai),
                  fill=theme["ink"], anchor="mm")
        # 60% black over white is ~5.7:1; the 45% it was is ~3.3:1 and
        # fails for text this small.
        draw.text((x + tile_w // 2, y + 505), en,
                  font=fitted(en, bold=False, sizes=(84, 76, 68), floor=60),
                  fill="#00000099", anchor="mm")
        areas.append({
            "bounds": {"x": x, "y": y, "width": tile_w, "height": tile_h},
            "action": action,
        })

    # Header taps do nothing on purpose: a mis-tap at the top should not
    # fire an action the person cannot see they chose.
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / f"richmenu-{oa}.png", optimize=True)
    (out_dir / f"richmenu-{oa}.json").write_text(json.dumps({
        "size": {"width": W, "height": H},
        "selected": True,
        "name": f"chann-{oa}-v1",
        "chatBarText": "เมนู",
        "areas": areas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{oa}: richmenu-{oa}.png + .json")


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    for oa in THEMES:
        build(oa, out)
