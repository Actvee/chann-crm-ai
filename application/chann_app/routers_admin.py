"""Platform Admin login + a LIFF-guarded example route."""
from __future__ import annotations

import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from .auth.liff import LiffTokenInvalid, verify_id_token
from .auth.platform_admin import decode_token, issue_token
from .config import settings

log = logging.getLogger(__name__)
from .data_client import DataClient, DataTierError
from .services.identity import OA_TO_ROLE, apply_active_tenant

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
    # oa-scoped (see services/authorization.py): the customer app lists
    # the shops this person is a customer of, never the companies they
    # work for. Ordered with the stored choice first; the rest follow so
    # the app can offer a switcher.
    memberships = await client.memberships_of(identity["chann_uid"], oa=audience)
    chosen, alternatives = await apply_active_tenant(
        client, identity["chann_uid"], audience, memberships,
    )
    # This returns only the authenticated user's own memberships so they can
    # select a tenant. It is never an endpoint for probing another identity.
    return {
        "sub": claims.get("sub"),
        "audience": audience,
        "chann_uid": identity["chann_uid"],
        "memberships": chosen + alternatives,
        "active_license_id": chosen[0]["license_id"] if len(chosen) == 1 else None,
    }


# Phase 16.3 — how this person wants to be spoken to, on every OA.
_PREF_FIELDS = ("language", "date_format", "timezone")
_PREF_LANGUAGES = ("th", "en")


@router.get("/liff/{audience}/display-preferences")
async def liff_display_preferences(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    prefs = await client.get_display_preferences(identity["chann_uid"])
    return {field: prefs.get(field) for field in _PREF_FIELDS}


@router.put("/liff/{audience}/display-preferences")
async def liff_set_display_preferences(
    audience: str,
    body: dict,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    fields = {f: body[f] for f in _PREF_FIELDS if body.get(f)}
    if "language" in fields and fields["language"] not in _PREF_LANGUAGES:
        raise HTTPException(status_code=422, detail="language must be th or en")
    if "date_format" in fields and fields["date_format"] not in ("dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd"):
        raise HTTPException(status_code=422, detail="date_format must be dd/mm/yyyy, mm/dd/yyyy or yyyy-mm-dd")
    if "timezone" in fields:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(str(fields["timezone"]))
        except Exception:
            raise HTTPException(status_code=422, detail="timezone must be a valid zone name")
    if not fields:
        raise HTTPException(status_code=422, detail="nothing to update")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    prefs = await client.set_display_preferences(identity["chann_uid"], fields)
    return {field: prefs.get(field) for field in _PREF_FIELDS}


@router.get("/liff/{audience}/guide")
async def liff_guide(
    audience: str, lang: str = "th", format: str = "json", claims: dict = Depends(require_liff),
):
    """The illustrated how-to for this OA — the same steps chat's
    "วิธีใช้" prints, with the owner's image per step when one exists.
    `format=md` / `format=html` returns the handout as a file (owner,
    4 Sep: a file per OA to take away, illustrate, and send to customers)."""
    from fastapi.responses import Response

    from .services.guides import GUIDES, guide_as_html, guide_as_markdown, help_image_url

    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    if format in ("md", "html"):
        content = guide_as_markdown(audience) if format == "md" else guide_as_html(audience)
        media = "text/markdown; charset=utf-8" if format == "md" else "text/html; charset=utf-8"
        return Response(
            content=content, media_type=media,
            headers={"Content-Disposition": f'attachment; filename="guide-{audience}.{format}"'},
        )
    language = "en" if lang == "en" else "th"
    guide = GUIDES[audience]
    return {
        "title": guide["title"][language],
        "intro": guide["intro"][language],
        "steps": [
            {
                "key": step["key"],
                "title": step["title"][language],
                "body": step["body"][language],
                "example": step.get("example"),
                "image_slot": step["image"],
                "image_url": help_image_url(step["image"]) or None,
            }
            for step in guide["steps"]
        ],
    }


@router.get("/liff/{audience}/signature")
async def liff_signature(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    """13.5 — a link to this person's signature image, or null."""
    from .services.photos import signature_link

    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    return {"url": await signature_link(client, chann_uid=identity["chann_uid"])}


@router.post("/liff/{audience}/signature")
async def liff_set_signature(
    audience: str,
    body: dict,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    """Save a drawn signature (a data: URL from the canvas)."""
    import base64

    from .services.photos import PhotoRefused, signature_link, store_signature

    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    data_url = str(body.get("image") or "")
    head, _, payload = data_url.partition(",")
    if not head.startswith("data:image/") or not payload:
        raise HTTPException(status_code=422, detail="image must be a data:image/... URL")
    try:
        content = base64.b64decode(payload)
    except Exception:
        raise HTTPException(status_code=422, detail="image is not valid base64")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    try:
        await store_signature(
            client, chann_uid=identity["chann_uid"], content=content,
            content_type=head[5:].split(";")[0] or "image/png",
        )
    except PhotoRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"url": await signature_link(client, chann_uid=identity["chann_uid"])}


@router.put("/liff/{audience}/active-shop")
async def liff_set_active_shop(
    audience: str,
    body: dict,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    """Choose which of several shops this person acts in on this OA — the
    app twin of chat's "ใช้ร้าน X". Only an id among their own memberships
    is accepted."""
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    license_id = str(body.get("license_id") or "")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    memberships = await client.memberships_of(identity["chann_uid"], oa=audience)
    if not any(str(m.get("license_id")) == license_id for m in memberships):
        raise HTTPException(status_code=403, detail="not one of your shops")
    await client.set_active_tenant(identity["chann_uid"], audience, license_id)
    return {"active_license_id": license_id}


# The person's own profile (Phase 8 fields) from the LIFF app — the
# UI twin of "แก้เบอร์เป็น 08x" in chat, so the parity rule holds for the
# customer and technician OAs (owner, 3 Sep). Only the caller's own
# record is reachable here: the chann_uid comes from the verified ID
# token, never from the request.
_PROFILE_FIELDS = ("first_name", "last_name", "phone", "email", "address")


@router.get("/liff/{audience}/profile")
async def liff_profile(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    profile = await client.get_profile(identity["chann_uid"]) or {}
    return {
        "chann_uid": identity["chann_uid"],
        **{field: profile.get(field) for field in _PROFILE_FIELDS},
    }


@router.patch("/liff/{audience}/profile")
async def liff_profile_update(
    audience: str,
    body: dict,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    fields = {
        field: (str(body[field]).strip() or None)
        for field in _PROFILE_FIELDS
        if field in body and body[field] is not None
    }
    if not fields:
        raise HTTPException(status_code=422, detail="nothing to update")
    identity = await client.resolve_identity(
        claims["sub"], OA_TO_ROLE[audience], claims.get("name")
    )
    updated = await client.update_profile(
        identity["chann_uid"], fields, actor_id=identity["chann_uid"],
    )
    return {
        "chann_uid": identity["chann_uid"],
        **{field: updated.get(field) for field in _PROFILE_FIELDS},
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
    from .services import live_chat
    from .services.reminders import sweep_due_follow_ups

    summary = await sweep_due_follow_ups(client, days=max(0, min(days, 7)))
    # Phase 15: the same tick escalates overdue chats and closes dead ones,
    # so one Scheduler job serves both. A dedicated, more frequent job can
    # call /platform/chat/sweep instead.
    try:
        summary["chat"] = await live_chat.sweep(client)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("chat sweep inside the reminder sweep failed")
    return summary


@router.post("/platform/chat/sweep")
async def run_chat_sweep(
    _: None = Depends(require_scheduler),
    client: DataClient = Depends(get_data_client),
):
    """Phase 15 SLA + timeout sweep (Master Spec 15.4), for a Scheduler job
    that runs every few minutes. Same shared-secret auth as the reminder
    sweep. The dashboard's chat list ticks the same clock on every load."""
    from .services import live_chat

    return await live_chat.sweep(client)


@router.post("/platform/quotes/expire-overdue")
async def run_quote_expiry_sweep(
    _: None = Depends(require_scheduler),
    client: DataClient = Depends(get_data_client),
):
    """Expire quotes past their validity date, across every tenant.

    Same authentication and same shape as the reminder sweep: a static
    shared secret, because an unauthenticated version would let anyone
    change the status of every quote on the platform.

    A quote still reading "sent" a month after it expired tells a
    salesperson the offer stands when it does not — and the "expired"
    status has existed since Phase 10 with nothing able to set it.
    """
    summary = {"tenants": 0, "expired": 0, "failed": []}
    try:
        # exclude_status rather than status="active": a trial tenant is a
        # real tenant, and filtering on "active" silently skipped every
        # one of them when the reminder sweep first shipped.
        licenses = await client.list_licenses(exclude_status="suspended")
    except Exception:
        log.exception("quote expiry sweep could not list tenants")
        return {**summary, "error": "could not list tenants"}

    for lic in licenses:
        license_id = str(lic.get("id") or "")
        if not license_id:
            continue
        summary["tenants"] += 1
        try:
            result = await client.expire_overdue_quotes(license_id)
            summary["expired"] += int(result.get("expired") or 0)
        except Exception:
            # One tenant's failure must not stop the rest: a sweep that
            # aborts halfway leaves the remaining tenants silently unswept
            # until someone notices, which is how the first sweep bug hid.
            log.exception("quote expiry failed for %s", license_id)
            summary["failed"].append(license_id)

    return summary
