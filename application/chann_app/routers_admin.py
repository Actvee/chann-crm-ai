"""Platform Admin login + a LIFF-guarded example route."""
from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from .auth.liff import LiffTokenInvalid, verify_id_token
from .auth.platform_admin import decode_token, issue_token
from .config import settings
from .data_client import DataClient, DataTierError
from .services.identity import OA_TO_ROLE

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


async def get_data_client():
    client = DataClient()
    try:
        yield client
    finally:
        await client.aclose()


@router.post("/platform/login", response_model=TokenOut)
async def platform_login(payload: LoginIn, client: DataClient = Depends(get_data_client)):
    admin = await client.authenticate_platform_admin(payload.username, payload.password)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    session_id = str(uuid.uuid4())
    try:
        await client.create_platform_admin_session(
            session_id, str(admin["admin_id"]), settings.jwt_ttl_s
        )
    except DataTierError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin session service unavailable",
        )
    return TokenOut(
        access_token=issue_token(str(admin["admin_id"]), admin["username"], session_id)
    )


async def require_admin(
    authorization: str = Header(default=""),
    client: DataClient = Depends(get_data_client),
) -> dict:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        claims = decode_token(authorization.split(" ", 1)[1])
        session = await client.get_platform_admin_session(claims.get("jti", ""))
        if session is None or str(session.get("admin_id")) != str(claims.get("sub")):
            raise ValueError("admin session invalid")
        return claims
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


async def require_liff(
    audience: str,
    x_liff_id_token: str = Header(default=""),
) -> dict:
    try:
        return await verify_id_token(x_liff_id_token, audience)
    except LiffTokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


@router.get("/platform/me")
async def platform_me(claims: dict = Depends(require_admin)):
    return {"username": claims.get("username"), "scope": claims.get("scope")}


@router.post("/platform/logout", status_code=status.HTTP_204_NO_CONTENT)
async def platform_logout(
    claims: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    try:
        await client.delete_platform_admin_session(claims["jti"])
    except DataTierError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="logout could not revoke the admin session",
        )


@router.get("/liff/{audience}/me")
async def liff_me(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    memberships = await client.memberships_of(identity["chann_uid"])
    # This returns only the authenticated user's own memberships so they can
    # select a tenant. It is never an endpoint for probing another identity.
    return {
        "sub": claims.get("sub"),
        "audience": audience,
        "chann_uid": identity["chann_uid"],
        "memberships": memberships,
    }


@router.post("/platform/smartbrowz/verify-connection")
async def smartbrowz_verify_connection(claims: dict = Depends(require_admin)):
    """Phase 10 / Master Spec 10.6 — verify the SmartBrowz OAuth auth path
    actually works from the deployed Application environment, before any
    template/rendering pipeline is built on top of it. Converts one
    trivial, fixed HTML snippet to PDF; never returns the PDF bytes
    themselves (this proves connectivity, it is not the render adapter).

    Deliberately behind require_admin, not a public/unauthenticated route
    — every call spends a real SmartBrowz API request against the
    project's own quota.
    """
    from .services.pdf.smartbrowz import (
        SmartBrowzNotConfigured,
        SmartBrowzRenderError,
        verify_connection,
    )

    try:
        result = await verify_connection()
    except SmartBrowzNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except SmartBrowzRenderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return result


async def require_scheduler(x_sweep_secret: str = Header(default="")) -> None:
    """Auth for the reminder sweep only — see config.reminder_sweep_secret
    for why this is a separate, static, machine-to-machine credential
    rather than reusing require_admin's session-backed JWT flow.
    """
    if not settings.reminder_sweep_secret:
        # Refuses rather than allowing through: an unconfigured secret must
        # never mean "no check", since that would make this endpoint
        # unintentionally public the moment someone forgets to set it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="REMINDER_SWEEP_SECRET is REQUIRED_NOT_CONFIGURED",
        )
    if not hmac.compare_digest(x_sweep_secret, settings.reminder_sweep_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid sweep secret")


@router.post("/platform/reminders/sweep")
async def run_reminder_sweep(
    days: int = 0,
    _: None = Depends(require_scheduler),
    client: DataClient = Depends(get_data_client),
):
    """Push today's due follow-ups to their owners (Master Spec 6.7).

    Called by Cloud Scheduler each morning, authenticated by a static
    shared secret rather than require_admin — see require_scheduler above.
    An unauthenticated version would let anyone make the platform send LINE
    messages to every tenant on demand.

    Returns the sweep's own summary so a failing schedule is visible in the
    Scheduler job's history rather than only in logs.
    """
    from .services.reminders import sweep_due_follow_ups

    return await sweep_due_follow_ups(client, days=max(0, min(days, 7)))
