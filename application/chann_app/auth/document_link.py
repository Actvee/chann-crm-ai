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
"""
from __future__ import annotations

import datetime as dt

import jwt

from ..config import settings

# Long enough for a customer to open a link over a weekend; short enough
# that a forwarded one stops working well before the quote itself is stale.
DOCUMENT_LINK_TTL_S = 7 * 24 * 3600

_PURPOSE = "document.download"


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
