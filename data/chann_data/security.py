"""Internal shared-secret guard for the Data Tier.

Reduced-security posture (CLAUDE.md 5): the Data Tier is not reachable by end
users. Only the Application Tier calls it, authenticated by a shared header
rather than per-service IAM identity. This is an accepted limitation and is
declared in every Release Manifest as PRODUCTION_PROOF_REDUCED_SECURITY.
"""
import hmac

from fastapi import Header, HTTPException, status

from .config import settings


def require_internal_secret(x_internal_secret: str = Header(default="")) -> None:
    if not settings.admin_secret:
        # Refusing to run open is deliberate: an empty secret in a deployed
        # environment is a misconfiguration, not a development convenience.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_SECRET is REQUIRED_NOT_CONFIGURED",
        )
    if not hmac.compare_digest(x_internal_secret, settings.admin_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal secret")
