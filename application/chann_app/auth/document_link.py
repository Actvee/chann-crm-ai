"""Time-limited links to an issued document.

GCS signed URLs would be the obvious mechanism and are deliberately not
used. Signing one requires the signing service account to hold
`iam.serviceAccounts.signBlob` on itself; `roles/editor` does not grant it,
and this project has decided not to add IAM roles. In production the
signing call failed and the salesperson got "could not create a download
link" with no file — the document existed and was unreachable.

Serving the bytes through this application instead needs no new IAM, keeps
the bucket's `public_access_prevention` intact, and reuses the JWT secret
already configured for platform admin sessions.

The token names exactly one document and nothing else. It is not a session
and grants no other access, so a forwarded link exposes that one quote and
expires on its own.

Asset tokens (review E2, 6 Sep 2026) are the same idea for everything else
that used to be handed out as a GCS signed URL and therefore never worked
here: ticket photos, signatures, the PDPA export page, AI report files.
One names one stored object path; `GET /api/v1/assets/{token}` streams it.
"""
from __future__ import annotations

import datetime as dt

import jwt

from ..config import settings

# Long enough for a customer to open a link over a weekend; short enough
# that a forwarded one stops working well before the quote itself is stale.
DOCUMENT_LINK_TTL_S = 7 * 24 * 3600

_PURPOSE = "document.download"
_ASSET_PURPOSE = "asset.download"


class DocumentLinkInvalid(Exception):
    """The token is missing, malformed, expired, or not a document link."""


def issue_document_token(
    license_id: str, document_id: str, ttl_seconds: int = DOCUMENT_LINK_TTL_S,
) -> str:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {
            # Both ids are in the token so the endpoint never has to trust a
            # license_id from the URL: a token issued for one tenant cannot
            # be replayed against another's document.
            "lic": str(license_id),
            "doc": str(document_id),
            "purpose": _PURPOSE,
            "iat": now,
            "exp": now + dt.timedelta(seconds=ttl_seconds),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_document_token(token: str) -> tuple[str, str]:
    """(license_id, document_id), or raise DocumentLinkInvalid."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception as exc:  # noqa: BLE001
        raise DocumentLinkInvalid(str(exc)) from exc

    # Checked explicitly: a platform-admin session token is signed with the
    # same secret, and without this a stolen one would double as a document
    # link for any document whose id the holder could guess.
    if claims.get("purpose") != _PURPOSE:
        raise DocumentLinkInvalid("not a document link token")
    license_id, document_id = claims.get("lic"), claims.get("doc")
    if not license_id or not document_id:
        raise DocumentLinkInvalid("token is missing its document reference")
    return str(license_id), str(document_id)


#: A link goes into a LINE message and has to survive being tapped there.
#: The template-design link was 612 characters and came back "Signature
#: verification failed" — the DEV log shows the token arriving with a
#: 22-character signature where HS256 writes 43, i.e. cut in half on the
#: way (owner's transcript, 16 ก.ย. 2569, and two 404s at 09:42 and 09:44
#: that day). Every character in the token is a character in the URL, so
#: the claims are as short as they can be: one-letter names, the bucket
#: prefix left off (the store knows its own bucket), and the content type
#: as a code rather than seventy characters of MIME.
_CT_CODES = {
    "a": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "p": "application/pdf",
    "j": "image/jpeg",
    "n": "image/png",
    "w": "image/webp",
    "g": "image/gif",
    "h": "text/html; charset=utf-8",
    "x": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "c": "text/csv; charset=utf-8",
}
_CT_BY_TYPE = {v: k for k, v in _CT_CODES.items()}
_GS = "gs://"


def _short_path(path: str) -> str:
    """The object key without the bucket — 45 characters that never
    change and need not travel."""
    from ..config import settings as _s

    prefix = f"{_GS}{_s.gcs_bucket_name}/"
    return path[len(prefix):] if _s.gcs_bucket_name and path.startswith(prefix) else path


def _full_path(short: str) -> str:
    from ..config import settings as _s

    if short.startswith(_GS) or not _s.gcs_bucket_name:
        return short
    return f"{_GS}{_s.gcs_bucket_name}/{short}"


def issue_asset_token(
    path: str, content_type: str, ttl_seconds: int = DOCUMENT_LINK_TTL_S,
    *, filename: str | None = None,
) -> str:
    """A link token for one stored object (a gs:// path, as the store
    recorded it). The content type travels in the token so the serving
    route never has to guess from an extension."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    if not path:
        raise ValueError("an asset token needs a stored path")
    now = dt.datetime.now(dt.timezone.utc)
    ct = str(content_type or "application/octet-stream")
    claims: dict = {
        "p": _short_path(str(path)),
        "u": _ASSET_PURPOSE,
        "iat": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
    }
    code = _CT_BY_TYPE.get(ct)
    if code:
        claims["c"] = code
    else:
        claims["ct"] = ct
    if filename:
        claims["f"] = str(filename)
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_asset_token(token: str) -> tuple[str, str, str | None]:
    """(path, content_type, filename), or raise DocumentLinkInvalid."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is REQUIRED_NOT_CONFIGURED")
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception as exc:  # noqa: BLE001
        raise DocumentLinkInvalid(str(exc)) from exc
    # A document token or an admin session token must not double as an
    # asset link — same reasoning as decode_document_token.
    # Both spellings: links already in people's chats carry the long one.
    if claims.get("u", claims.get("purpose")) != _ASSET_PURPOSE:
        raise DocumentLinkInvalid("not an asset link token")
    path = claims.get("p") or claims.get("path")
    if not path:
        raise DocumentLinkInvalid("token is missing its object path")
    content_type = (
        _CT_CODES.get(str(claims.get("c") or ""))
        or str(claims.get("ct") or "application/octet-stream")
    )
    return _full_path(str(path)), content_type, claims.get("f") or claims.get("fn")
