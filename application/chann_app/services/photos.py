"""Phase 13.1 — photos on a job, and 13.5 — a person's signature.

Both are bytes that must end up in object storage and be referred to by
path from a row: `ticket_photos.photo_url` (the Data Tier has had the
table since Phase 13) and `chann_identities.signature_url`. This module
is the one place that stores them, the same store the documents use,
and the one place that turns a stored path into a link a page or a
renderer can fetch for a while.

Nothing here decides WHICH ticket a picture belongs to — chat and the
routes do, with the rules that fit their surface.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from ..data_client import DataClient
from .storage.base import get_document_store

log = logging.getLogger(__name__)

PHOTO_LINK_TTL_SECONDS = 3600
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class PhotoRefused(RuntimeError):
    """Not an image, or too big — said before anything is stored."""


def _ext(content_type: str) -> str:
    return _EXT.get((content_type or "").split(";")[0].strip().lower(), "jpg")


async def store_ticket_photo(
    client: DataClient, *, license_id: str, ticket_id: str, content: bytes,
    content_type: str = "image/jpeg", photo_type: str = "evidence",
    uploaded_by_member_id: str | None = None, gps_lat=None, gps_lng=None,
) -> dict:
    """Store the bytes, then record the row (store first: an orphan
    object is findable, a row pointing at nothing is a lie)."""
    if not content:
        raise PhotoRefused("empty file")
    if len(content) > MAX_PHOTO_BYTES:
        raise PhotoRefused("photo larger than 10 MB")
    if not (content_type or "").lower().startswith("image/"):
        raise PhotoRefused("not an image")
    stamp = datetime.now(timezone.utc)
    key = (
        f"documents/{_SAFE.sub('-', license_id)}/tickets/{_SAFE.sub('-', ticket_id)}/photos/"
        f"{stamp:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.{_ext(content_type)}"
    )
    stored = await get_document_store().put(key=key, content=content, content_type=content_type)
    return await client.add_ticket_photo(
        license_id, ticket_id,
        {
            "photo_url": stored.path, "photo_type": photo_type,
            "gps_lat": gps_lat, "gps_lng": gps_lng,
            "uploaded_by": uploaded_by_member_id,
        },
    )


async def photo_links(client: DataClient, *, license_id: str, ticket_id: str) -> list[dict]:
    """The ticket's photos with a fetchable link each (signed for an
    hour). A photo whose object cannot be signed is listed without a
    link rather than dropped — the row is still the record."""
    try:
        rows = await client.list_ticket_photos(license_id, ticket_id)
    except Exception:
        log.exception("could not list photos for %s", ticket_id)
        return []
    store = get_document_store()
    out = []
    for row in rows:
        path = str(row.get("photo_url") or "")
        if not path:
            # A GPS-only check-in row: coordinates, no picture. Not a
            # blank entry on the report page, not a signing attempt.
            continue
        url = ""
        try:
            url = path if path.startswith("http") else await store.signed_url(
                path=path, expires_seconds=PHOTO_LINK_TTL_SECONDS,
            )
        except Exception:
            log.warning("could not sign %s", path)
        out.append({**row, "url": url})
    return out


async def store_signature(client: DataClient, *, chann_uid: str, content: bytes, content_type: str = "image/png") -> str:
    """The person's signature image (13.5), kept against the identity so
    it follows them across shops. Returns the stored path."""
    if not content or len(content) > 2 * 1024 * 1024:
        raise PhotoRefused("signature must be an image under 2 MB")
    key = f"signatures/{_SAFE.sub('-', chann_uid)}/{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.{_ext(content_type)}"
    stored = await get_document_store().put(key=key, content=content, content_type=content_type)
    await client.set_identity_signature(chann_uid, stored.path)
    return stored.path


async def signature_link(client: DataClient, *, chann_uid: str) -> str | None:
    try:
        path = await client.identity_signature(chann_uid)
    except Exception:
        return None
    if not path:
        return None
    if path.startswith("http"):
        return path
    try:
        return await get_document_store().signed_url(path=path, expires_seconds=PHOTO_LINK_TTL_SECONDS)
    except Exception:
        log.warning("could not sign signature %s", path)
        return None
