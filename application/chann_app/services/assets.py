"""Links to stored objects that a browser, LINE, or the PDF renderer can
open — served by this tier, never a GCS signed URL.

Signed URLs need `iam.serviceAccounts.signBlob`, which this deployment
does not grant (see auth/document_link.py). Every place that used to
sign one got an exception in production and showed nothing: the ticket's
photo gallery, the approver's signature on the report PDF, the PDPA
export file, the AI report files (review E2, 6 Sep 2026). They all come
through here now: a JWT that names the object, and
`GET /api/v1/assets/{token}` streams the bytes.

A link needs an absolute base. PUBLIC_BASE_URL is the deployment's own
origin; a route that has the request in hand may pass its base instead.
With neither there is no link — the caller says so (the PDPA export
falls back to an inline summary, the report reply omits the files line)
rather than emitting a relative path that the LINE app cannot open.
"""
from __future__ import annotations

import base64
import logging

from ..auth.document_link import DOCUMENT_LINK_TTL_S, issue_asset_token
from ..config import settings
from .storage.base import get_document_store

log = logging.getLogger(__name__)

ASSET_PATH = "/api/v1/assets/"

_BY_EXTENSION = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "gif": "image/gif", "pdf": "application/pdf", "html": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8", "txt": "text/plain; charset=utf-8",
}


def content_type_for(path: str, default: str = "application/octet-stream") -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return _BY_EXTENSION.get(ext, default)


def _base(base_url: str | None) -> str:
    return (base_url or settings.public_base_url or "").rstrip("/")


def asset_link(
    path: str, *, content_type: str | None = None, ttl_seconds: int = DOCUMENT_LINK_TTL_S,
    base_url: str | None = None, filename: str | None = None,
) -> str | None:
    """An absolute https link to the object at `path`, or None when no
    base URL is known. An http(s) path is already a link and is returned
    as it is."""
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if "://" in path and not path.startswith("gs://"):
        # A marker, not an object. `builtin://none` and `upload://html`
        # are how a template version records "this did not come from an
        # uploaded file"; minting a link for one produced a button that
        # 4xx'd with "stored path 'builtin://none' does not belong to
        # bucket …" (owner, 9 Sep 2026). The store only ever hands out
        # gs:// paths, so anything else with a scheme is a sentinel and
        # has no bytes to serve — a missing button beats a broken one.
        log.warning("refusing to link a non-storage path: %s", path)
        return None
    base = _base(base_url)
    if not base:
        log.warning("no PUBLIC_BASE_URL; cannot link to %s", path)
        return None
    token = issue_asset_token(
        path, content_type or content_type_for(path), ttl_seconds, filename=filename,
    )
    return f"{base}{ASSET_PATH}{token}"


async def image_for_render(path: str, *, ttl_seconds: int = DOCUMENT_LINK_TTL_S, store=None) -> str:
    """What an <img src> in HTML handed to the PDF renderer should carry.

    The renderer runs at Zoho, so it needs a URL it can reach — an asset
    link when this deployment has a public base. Without one (dev, tests)
    the bytes go inline as a data: URI so the picture still renders; the
    snapshot then holds the image itself, which is why the link is
    preferred whenever it exists."""
    link = asset_link(path, ttl_seconds=ttl_seconds)
    if link:
        return link
    content = await (store or get_document_store()).get(path=path)
    return f"data:{content_type_for(path, 'image/jpeg')};base64," + base64.b64encode(content).decode("ascii")
