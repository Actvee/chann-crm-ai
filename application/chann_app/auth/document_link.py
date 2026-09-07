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
    claims = {
        "path": str(path),
        "ct": str(content_type or "application/octet-stream"),
        "purpose": _ASSET_PURPOSE,
        "iat": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
    }
    if filename:
        claims["fn"] = str(filename)
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
    if claims.get("purpose") != _ASSET_PURPOSE:
        raise DocumentLinkInvalid("not an asset link token")
    path = claims.get("path")
    if not path:
        raise DocumentLinkInvalid("token is missing its object path")
    return str(path), str(claims.get("ct") or "application/octet-stream"), claims.get("fn")
