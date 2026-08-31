#!/usr/bin/env python3
"""Provision the Sales OA rich menu (Phase 10).

A rich menu is the persistent grid at the bottom of a LINE chat. It matters
here because this product is chat-first: without one, a salesperson has to
remember command wording, and the commands are the entire interface. With
one, the four things they do all day are one tap away.

Deliberately a script rather than application code. A rich menu is
provisioned once per LINE channel and then persists on LINE's side — it is
not per-request behaviour, and putting it in the request path would mean
re-uploading an image on every deploy. Run it when the menu changes.

The image is generated here rather than committed as a binary, so the
labels and the picture cannot drift apart: change TILES and re-run, and
the image, the tap areas and the commands all move together. A committed
PNG would silently keep showing the old labels over the new tap areas —
a rich menu whose picture disagrees with its behaviour is worse than none.

Usage:
    LINE_SALES_CHANNEL_ACCESS_TOKEN=... python3 scripts/setup-richmenu.py
    LINE_TECHNICIAN_CHANNEL_ACCESS_TOKEN=... python3 scripts/setup-richmenu.py --oa technician
    LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN=... python3 scripts/setup-richmenu.py --oa customer
    LINE_SALES_CHANNEL_ACCESS_TOKEN=... python3 scripts/setup-richmenu.py --delete

Requires: pillow, requests  (pip install pillow requests)
"""
from __future__ import annotations

import argparse
import io
import os
import sys

import requests

LINE_API = "https://api.line.me/v2/bot"
LINE_DATA_API = "https://api-data.line.me/v2/bot"

# LINE's own supported large size. 2500x1686 is the other option; this one
# is half the height and enough for a 2x3 grid, which keeps the image small
# and the tap targets comfortable on a phone.
WIDTH, HEIGHT = 2500, 1686
COLS, ROWS = 3, 2
TILE_W, TILE_H = WIDTH // COLS, HEIGHT // ROWS

# (label shown on the tile, message sent when tapped)
# One tile set per OA. The three audiences do genuinely different work —
# a customer has no deals and a technician has no products — and a shared
# menu would mean most of everyone's buttons did nothing for them.
#
# Every label is also the text the tile sends, so a person can see what
# they could have typed. That is how anyone learns the chat commands: the
# menu is the discoverable half of a chat-first product.
def _liff_url(env_name: str, path: str = "") -> str | None:
    """A LIFF deep link, or None when that app is not configured.

    None rather than a broken link: a rich menu button that opens an error
    page is worse than one that is absent, because the person taps it,
    waits, and concludes the product is broken.
    """
    liff_id = os.environ.get(env_name, "").strip()
    if not liff_id:
        return None
    return f"https://liff.line.me/{liff_id}{path}"


TILE_SETS = {
    "sales": [
        ("รายชื่อลูกค้า", "รายชื่อลูกค้า"),
        ("รายการดีล", "รายการดีล"),
        ("งานซ่อม", "รายการงาน"),
        ("ใบเสนอราคา", "รายการใบเสนอราคา"),
        ("งานวันนี้", "งานวันนี้"),
        # The dashboard's only entry point. Typing cannot open a web view,
        # so without this tile the LIFF pages are unreachable except from
        # a link the bot happened to send earlier.
        ("แดชบอร์ด", "LIFF_SALES_ID:"),
    ],
    "technician": [
        ("งานของฉัน", "งานของฉัน"),
        ("งานที่เปิดรับ", "รายการงาน"),
        ("เช็คอิน", "เช็คอิน "),
        ("ปิดงาน", "ปิดงาน "),
        ("งานวันนี้", "งานวันนี้"),
        ("แดชบอร์ด", "LIFF_TECHNICIAN_ID:/tickets"),
    ],
    # Four tiles, not six: a customer does two things here, and padding
    # the menu with buttons that explain themselves would make the two
    # that matter harder to find.
    "customer": [
        ("แจ้งซ่อม", "แจ้งซ่อม"),
        ("ดูสถานะงาน", "งานของฉัน"),
        ("วิธีใช้", "วิธีใช้"),
        ("แดชบอร์ด", "LIFF_CUSTOMER_ID:/tickets"),
    ],
}

TILES = TILE_SETS["sales"]

BACKGROUND = (17, 24, 39)
TILE_FILL = (31, 41, 55)
BORDER = (55, 65, 81)
TEXT = (243, 244, 246)

# Thai-capable fonts, in the order they are usually available. A rich menu
# rendered with a font that lacks Thai glyphs produces a grid of boxes —
# and unlike a crash, it uploads and looks fine to the API.
# Verified against a real fonts-thai-tlwg install rather than guessed:
# that package ships Garuda/Norasi/Kinnari/Laksaman, not the Sarabun and
# Loma names an earlier version of this list assumed. DejaVu stays last as
# a Latin-only fallback that _can_render will correctly reject for Thai,
# so its presence can never mask a missing Thai font.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansThai-Regular.otf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/truetype/tlwg/Norasi.ttf",
    "/usr/share/fonts/truetype/tlwg/Laksaman.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class NoThaiFont(RuntimeError):
    """No installed font can draw the menu labels.

    Fatal rather than a warning: a rich menu rendered without Thai glyphs
    uploads successfully, passes every API check, and shows the user a grid
    of empty boxes. It is a silent failure that only a human looking at a
    phone would ever catch, so it has to be caught here instead.
    """


# A Private Use Area codepoint no real font assigns, so whatever a font
# draws for it IS that font's .notdef glyph.
_NOTDEF_PROBE = "\ue000"


def _can_render(font, text: str) -> bool:
    """Whether this font has real glyphs for the text.

    Checking that a glyph has a bounding box is NOT enough — .notdef is
    itself a visible rectangle, so an unsupported character reports a
    perfectly good bbox and the check passes while the output is boxes.
    That is exactly the bug this function was first written with. Instead
    the character is compared against the font's own .notdef: if they
    rasterise identically, the character is not in the font.
    """
    # bytes(mask), not mask.tobytes(): PIL's mask is an ImagingCore, which
    # has no tobytes(). An earlier version called it inside a broad
    # try/except, so every font raised AttributeError and was silently
    # rejected — including the Thai fonts that work perfectly. Narrow the
    # except accordingly, so a future API change fails loudly instead of
    # quietly disqualifying everything.
    notdef = bytes(font.getmask(_NOTDEF_PROBE))
    for char in text:
        if not char.strip():
            continue
        if bytes(font.getmask(char)) == notdef:
            return False
    return True


def _load_font(size: int):
    from PIL import ImageFont

    sample = "".join(label for label, _ in TILES)
    tried = []
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
        except OSError:
            continue
        if _can_render(font, sample):
            return font, path
        tried.append(path)

    raise NoThaiFont(
        "no installed font can render the Thai menu labels"
        + (f" (tried: {', '.join(tried)})" if tried else "")
        + ". Install one first, e.g.:\n"
        "    sudo apt-get install -y fonts-thai-tlwg fonts-noto-core"
    )


def build_image() -> bytes:
    from PIL import Image, ImageDraw

    font, font_path = _load_font(84)
    print(f"font: {font_path}")

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)

    for index, (label, _) in enumerate(TILES):
        col, row = index % COLS, index // COLS
        x0, y0 = col * TILE_W, row * TILE_H
        x1, y1 = x0 + TILE_W, y0 + TILE_H
        draw.rectangle([x0 + 12, y0 + 12, x1 - 12, y1 - 12], fill=TILE_FILL, outline=BORDER, width=4)

        box = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (x0 + (TILE_W - (box[2] - box[0])) / 2, y0 + (TILE_H - (box[3] - box[1])) / 2 - box[1]),
            label, font=font, fill=TEXT,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def menu_definition() -> dict:
    areas = []
    for index, (label, message) in enumerate(TILES):
        col, row = index % COLS, index // COLS
        areas.append({
            "bounds": {"x": col * TILE_W, "y": row * TILE_H, "width": TILE_W, "height": TILE_H},
            # A message action, not a postback: what the person taps becomes
            # a visible message in the conversation, so the chat history
            # still reads as a conversation and the same command can be
            # typed by hand next time. A postback would leave an invisible
            # cause for a visible reply.
            # A tile is either a message — so the person can see what they
            # could have typed — or a link into the LIFF dashboard, which
            # has no typed equivalent. No message opens a web view, so the
            # menu is the ONLY way in.
            "action": (
                {"type": "uri", "label": label[:20], "uri": message}
                if str(message).startswith("https://")
                else {"type": "message", "label": label[:20], "text": message}
            ),
        })
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": "Chann CRM Sales",
        "chatBarText": "เมนู",
        "areas": areas,
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def delete_existing(token: str) -> None:
    response = requests.get(f"{LINE_API}/richmenu/list", headers=_headers(token), timeout=20)
    response.raise_for_status()
    for menu in response.json().get("richmenus", []):
        menu_id = menu["richMenuId"]
        requests.delete(f"{LINE_API}/richmenu/{menu_id}", headers=_headers(token), timeout=20)
        print(f"deleted {menu_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true", help="remove all rich menus and stop")
    parser.add_argument(
        "--oa", default="sales",
        help="which OA to configure: sales, technician or customer",
    )
    args = parser.parse_args()

    oa = (args.oa or "sales").lower()
    if oa not in TILE_SETS:
        print(f"unknown OA {oa!r}; expected one of: {', '.join(TILE_SETS)}", file=sys.stderr)
        return 2

    global TILES
    resolved = []
    for label, message in TILE_SETS[oa]:
        if message.startswith("LIFF_"):
            env_name, _, path = message.partition(":")
            url = _liff_url(env_name, path)
            if url is None:
                # Drop the tile rather than ship a dead button.
                print(f"skipping {label!r}: {env_name} is not set", file=sys.stderr)
                continue
            resolved.append((label, url))
        else:
            resolved.append((label, message))
    TILES = resolved

    env_name = f"LINE_{oa.upper()}_CHANNEL_ACCESS_TOKEN"
    token = os.environ.get(env_name, "").strip()
    if not token:
        print(f"{env_name} is required", file=sys.stderr)
        return 2

    # Rendered before anything is deleted or created on LINE's side, so a
    # missing font fails with the existing menu still in place rather than
    # leaving the channel with no menu at all.
    try:
        image = build_image()
    except NoThaiFont as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    # Always cleared first: LINE keeps every rich menu ever created, and
    # re-running this without deleting would silently accumulate orphans
    # that still count against the channel's limit.
    delete_existing(token)
    if args.delete:
        return 0

    created = requests.post(
        f"{LINE_API}/richmenu", headers={**_headers(token), "Content-Type": "application/json"},
        json=menu_definition(), timeout=20,
    )
    created.raise_for_status()
    menu_id = created.json()["richMenuId"]
    print(f"created {menu_id}")

    uploaded = requests.post(
        f"{LINE_DATA_API}/richmenu/{menu_id}/content",
        headers={**_headers(token), "Content-Type": "image/png"},
        data=image, timeout=60,
    )
    uploaded.raise_for_status()
    print("image uploaded")

    linked = requests.post(
        f"{LINE_API}/user/all/richmenu/{menu_id}", headers=_headers(token), timeout=20,
    )
    linked.raise_for_status()
    print(f"set as the default menu for all users: {menu_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
