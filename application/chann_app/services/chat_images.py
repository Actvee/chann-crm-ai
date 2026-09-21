"""Round 20T — a picture in a conversation.

Owner, 21 ก.ย. 2569: "เพิ่มเติมฟีเจอร์ส่งรูปให้ลูกค้าได้ไหม". The shop
answers with a photo the way it answers with words, and a customer's
photo in a running conversation lands on the thread instead of being
turned away with "no job to attach it to".

The bytes go to the same object store the job photos use (photos.py);
what differs is the shape LINE will accept. An image message needs an
https link for the picture and for its preview, the picture at most
10 MB and the preview at most 1 MB — and a phone camera's 4000-pixel
JPEG is routinely 3–6 MB. So every picture is normalised ONCE, here,
before it is stored: turned the right way up (EXIF), shrunk to at most
1600 pixels on the long side, and saved as a JPEG a few hundred
kilobytes big. One stored object then serves as picture and preview
alike, in LINE and on the dashboard.

The link LINE gets must outlive the conversation: a customer scrolls
back to the picture weeks later and the LINE app fetches it again. So a
chat picture's asset link is issued for a year, not the hour a job
photo gallery gets — anyone who has the link already has the picture,
exactly as with any photo sent in LINE.
"""
from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime, timezone

from .assets import asset_link
from .photos import MAX_PHOTO_BYTES, PhotoRefused
from .storage.base import get_document_store

log = logging.getLogger(__name__)

#: LINE re-fetches a picture whenever it is shown; a year covers a
#: conversation someone scrolls back through.
CHAT_IMAGE_LINK_TTL_S = 365 * 24 * 3600
#: The long side after normalising. 1600 px reads well on any phone and
#: keeps a JPEG under LINE's 1 MB preview cap with room to spare.
MAX_EDGE = 1600
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def normalise_image(content: bytes, content_type: str = "") -> tuple[bytes, str]:
    """The bytes as a right-way-up JPEG of at most MAX_EDGE pixels a side.
    Not an image (Pillow cannot open it) → PhotoRefused, before anything
    is stored. Transparency is laid on white rather than on black."""
    if not content:
        raise PhotoRefused("empty file")
    if len(content) > MAX_PHOTO_BYTES:
        raise PhotoRefused("photo larger than 10 MB")
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PhotoRefused("not an image") from exc
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    for quality in (85, 70, 55):
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= 1024 * 1024:
            break
    return out.getvalue(), "image/jpeg"


async def store_chat_image(
    *, license_id: str, session_id: str, content: bytes, content_type: str = "",
) -> str:
    """Normalise, store, and return the stored path (what the message row
    keeps). Store first, as photos.py does: an orphan object is findable,
    a row pointing at nothing is a lie."""
    data, kind = normalise_image(content, content_type)
    stamp = datetime.now(timezone.utc)
    key = (
        f"documents/{_SAFE.sub('-', license_id)}/chats/{_SAFE.sub('-', session_id)}/"
        f"{stamp:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jpg"
    )
    stored = await get_document_store().put(key=key, content=data, content_type=kind)
    return stored.path


def chat_image_link(path: str | None, *, base_url: str | None = None) -> str | None:
    """An https link to a stored picture, good for a year; None when there
    is no picture or no base to build an absolute link on.

    PUBLIC_BASE_URL first, the request's origin only as the fallback —
    the other way round from the job photo gallery, because this link is
    handed to LINE, which refuses anything but https, and behind the Cloud
    Run proxy the request may well say http."""
    if not path:
        return None
    from ..config import settings

    return asset_link(
        path, content_type="image/jpeg", ttl_seconds=CHAT_IMAGE_LINK_TTL_S,
        base_url=settings.public_base_url or base_url,
    )


def with_image_links(rows: list[dict], *, base_url: str | None = None) -> list[dict]:
    """Each message row with `image_url` beside `image_path` — the
    dashboard shows the picture, never the gs:// path."""
    out = []
    for row in rows:
        row = dict(row)
        row["image_url"] = chat_image_link(row.get("image_path"), base_url=base_url)
        out.append(row)
    return out
