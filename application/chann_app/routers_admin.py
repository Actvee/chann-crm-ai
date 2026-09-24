"""Platform Admin login + a LIFF-guarded example route."""
from __future__ import annotations

import hmac
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from .auth.liff import LiffTokenInvalid, verify_id_token
from .auth.platform_admin import decode_token, issue_token
from .config import settings

log = logging.getLogger(__name__)
from .data_client import DataClient, DataTierError
from .services.identity import OA_TO_ROLE, apply_active_tenant
from .services import entitlements
from .services import pdpa as pdpa_service

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
    try:
        admin = await client.authenticate_platform_admin(payload.username, payload.password)
    except DataTierError as exc:
        if exc.status_code == status.HTTP_423_LOCKED:
            # Five wrong passwords: the Data tier says until when. Passed
            # through so the login page can say so instead of "wrong
            # password" (review D2, 6 Sep 2026).
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=exc.structured or {"error": "locked", "locked_until": None},
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="admin login service unavailable",
        )
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
        # Phase 18 — the dashboards show a read-only notice for a suspended shop.
        "license_status": (chosen[0].get("license_status") or "active") if len(chosen) == 1 else None,
        "license_expires_at": chosen[0].get("license_expires_at") if len(chosen) == 1 else None,
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
                # One line per thing to do (20 ก.ย. 2569); absent on the
                # guides that still carry their bullets in `body`.
                "how": [
                    {"group": item["group"][language]} if "group" in item
                    else {"text": item[language], "type": item.get("type")}
                    for item in step.get("how") or []
                ] or None,
                "example": step.get("example"),
                "image_slot": step["image"],
                "image_url": help_image_url(step["image"], absolute=False) or None,
            }
            for step in guide["steps"]
        ],
    }


@router.get("/guides/{audience}/file")
async def guide_file(audience: str, format: str = "html"):
    """The handout as a plain file, no session needed: it is the user
    manual, nothing in it is tenant data, and the LINE in-app browser can
    only show or save it when it is a plain URL the phone's own browser
    can open (owner, 4 Sep: "กดดาวน์โหลดแล้วไม่มีอะไรเกิดขึ้น")."""
    from fastapi.responses import Response

    from .services.guides import guide_as_html, guide_as_markdown

    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    if format == "md":
        return Response(
            content=guide_as_markdown(audience), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="guide-{audience}.md"'},
        )
    return Response(
        content=guide_as_html(audience), media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="guide-{audience}.html"'},
    )


@router.get("/document-template-samples/{document_type}")
async def document_template_sample(document_type: str, format: str = "docx"):
    """A starter Word file a shop can open, edit and upload back.

    The owner's report, 9 Sep 2026: "ไม่มีตัวอย่างที่เป็นไฟล์ให้ดาวน์โหลด
    ไปดู". Served exactly like `/guides/{audience}/file` above and for
    the same reasons, which is why it sits beside it rather than behind
    the LIFF guard or the asset-token path: there is no tenant data in it
    (it is the manual, in Word), and the LINE in-app browser can only
    save a file when the URL is one the phone's own browser can open with
    no header on it. The asset-token route is the other candidate and is
    the wrong shape — its tokens name one object in the document store,
    and these files are not stored anywhere.

    Generated per request rather than committed as binaries: a .docx in
    Git cannot be reviewed in a diff, and it would drift from the
    placeholder vocabulary the moment a snapshot key changed — which is
    the exact bug that made `{{company.legal_name}}` print blank for a
    year. `scripts/dev/make-template-samples.py` writes the same bytes to
    disk for anyone who wants to look at one locally.
    """
    from fastapi.responses import Response

    from .services.documents.samples import (
        SAMPLE_DOCUMENT_TYPES, build_sample_docx, sample_docx_filename,
    )

    if document_type not in SAMPLE_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"no sample template for {document_type!r}",
        )
    if format != "docx":
        raise HTTPException(status_code=400, detail="only format=docx is available")
    content = build_sample_docx(document_type)
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition":
                f'attachment; filename="{sample_docx_filename(document_type)}"',
            # Deterministic bytes (see samples._package), so a phone that
            # already has it does not download it twice.
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/assets/{token}")
async def download_asset(token: str):
    """Serve one stored object to whoever holds a valid asset link.

    Not behind the LIFF guard, for the same reason as /documents/{token}:
    the link is opened from a LINE chat, an <img src> on a dashboard
    page, or by the PDF renderer at Zoho — none of which can attach an
    ID token. The token names one object for a limited time; see
    auth/document_link.py and services/assets.py (review E2).
    """
    from fastapi.responses import Response

    from .auth.document_link import DocumentLinkInvalid, decode_asset_token
    from .services.storage.base import (
        DocumentStoreError, DocumentStoreNotConfigured, get_document_store,
    )

    try:
        path, content_type, filename = decode_asset_token(token)
    except DocumentLinkInvalid as exc:
        # 404 rather than 401: a forged or expired token must not confirm
        # that anything exists at the path it names.
        raise HTTPException(status_code=404, detail=f"link is not valid: {exc}")
    try:
        content = await get_document_store().get(path=path)
    except DocumentStoreNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except DocumentStoreError as exc:
        if "no stored document" in str(exc):
            raise HTTPException(status_code=404, detail="asset not found")
        raise HTTPException(status_code=502, detail=str(exc))
    name = filename or path.rsplit("/", 1)[-1] or "asset"
    return Response(
        content=content, media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            # The token already bounds the lifetime; a browser may keep
            # the bytes for as long as the link itself is good.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/liff/{audience}/signature")
async def liff_signature(
    audience: str,
    request: Request,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    """13.5 — a link to this person's signature image, or null."""
    from .services.photos import signature_link

    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    return {"url": await signature_link(
        client, chann_uid=identity["chann_uid"], base_url=str(request.base_url),
    )}


@router.post("/liff/{audience}/signature")
async def liff_set_signature(
    audience: str,
    body: dict,
    request: Request,
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
    return {"url": await signature_link(
        client, chann_uid=identity["chann_uid"], base_url=str(request.base_url),
    )}


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
    days: int = 1,
    _: None = Depends(require_scheduler),
    client: DataClient = Depends(get_data_client),
):
    """Push due follow-ups to their owners (Master Spec 6.7).

    days=1 by default: the spec says a follow-up is announced within one
    day BEFORE its due date, so the morning digest carries today's and
    tomorrow's work. days=0 (today only) was the old default and the
    Scheduler job passed nothing, so nothing was ever announced ahead
    (review E11, 6 Sep 2026).

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
    try:
        from .services import lead_cleanup

        summary["lead_cleanup"] = await lead_cleanup.sweep_inactive_leads(client)
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
    from .services import job_sla, live_chat

    summary = await live_chat.sweep(client)
    # The same five-minute tick nudges the shop about jobs nobody is moving
    # (14 ก.ย. 2569) — best-effort, so a job problem never hides a chat one.
    try:
        summary["jobs"] = await job_sla.sweep_jobs(client)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("job sweep inside the chat sweep failed")
    # …and about reports nobody is approving (approval SLA, 15 ก.ย. 2569).
    try:
        from .services import approval_sla

        summary["approvals"] = await approval_sla.sweep_reports(client)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("approval sweep inside the chat sweep failed")
    return summary


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
    summary = {"tenants": 0, "expired": 0, "warranties_expired": 0, "failed": []}
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
        # Same nightly tick, same reasoning: cover that ran out yesterday
        # must not read "active" today (review E5). The read path already
        # derives the status from the end date; this keeps the column true.
        try:
            result = await client.expire_overdue_warranties(license_id)
            summary["warranties_expired"] += int(result.get("expired") or 0)
        except Exception:
            log.exception("warranty expiry failed for %s", license_id)
            if license_id not in summary["failed"]:
                summary["failed"].append(license_id)

    return summary


@router.post("/platform/trials/expire")
async def run_trial_sweep(
    _: None = Depends(require_scheduler),
    client: DataClient = Depends(get_data_client),
):
    """Master Spec 17.5.4: warn the owner 3 days and 1 day before a trial
    OR a paid subscription ends, then suspend what is overdue (round 18:
    the path keeps its trial-era name because scheduler.tf points at it).
    Same shared-secret auth as the other sweeps; the Data Tier has been
    able to suspend since Phase 6.5 but nothing called it (review E3)."""
    from .services.trials import sweep_trials

    return await sweep_trials(client)


# ==================================================================== Phase 16.5
# PDPA from the LIFF pages (the person's own rights) and the platform
# admin (requests made on their behalf, or rejected).

def _pdpa_language(claims: dict) -> str:
    return "en" if str(claims.get("language") or "").startswith("en") else "th"


@router.get("/liff/{audience}/consent")
async def liff_consent(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    row = await client.get_consent(identity["chann_uid"])
    return {**row, "current_version": pdpa_service.CONSENT_VERSION, "text": pdpa_service.CONSENT_TEXT}


@router.put("/liff/{audience}/consent")
async def liff_consent_accept(
    audience: str,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    row = await client.put_consent(identity["chann_uid"], pdpa_service.CONSENT_VERSION)
    return {**row, "current_version": pdpa_service.CONSENT_VERSION}


@router.post("/liff/{audience}/pdpa/{action}")
async def liff_pdpa_action(
    audience: str,
    action: str,
    body: dict | None = None,
    claims: dict = Depends(require_liff),
    client: DataClient = Depends(get_data_client),
):
    """export → a page of everything (signed link, 24 h); erase →
    anonymised everywhere. Erasure needs {"confirm": true}."""
    if audience not in OA_TO_ROLE:
        raise HTTPException(status_code=404, detail="unknown LIFF audience")
    if action not in ("export", "erase"):
        raise HTTPException(status_code=404, detail="unknown PDPA action")
    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[audience], claims.get("name"))
    language = str((body or {}).get("language") or _pdpa_language(claims))
    try:
        if action == "export":
            out = await pdpa_service.export_my_data(
                client, chann_uid=identity["chann_uid"], via="liff", language=language,
            )
            return {"text": out["text"], "url": out["url"], "request_id": out["request_id"]}
        if not (body or {}).get("confirm"):
            raise HTTPException(status_code=422, detail="erasure needs confirm=true")
        out = await pdpa_service.erase_me(
            client, chann_uid=identity["chann_uid"], via="liff", language=language,
        )
        return {"text": out["text"], "request_id": out["request_id"], "result": out["result"]}
    except DataTierError as exc:
        log.warning("pdpa %s failed for %s: %s", action, identity["chann_uid"], exc)
        raise HTTPException(status_code=502, detail="pdpa request failed") from exc


@router.get("/platform/pdpa/requests")
async def platform_pdpa_requests(
    status_filter: str | None = None,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    return await client.list_pdpa_requests(status=status_filter)


@router.post("/platform/pdpa/requests")
async def platform_pdpa_create(
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    chann_uid = str(body.get("chann_uid") or "").strip()
    request_type = str(body.get("request_type") or "").strip()
    if not chann_uid or request_type not in ("erasure", "export", "consent_withdraw"):
        raise HTTPException(status_code=422, detail="chann_uid and a valid request_type are required")
    return await client.create_pdpa_request(chann_uid=chann_uid, request_type=request_type, requested_via="platform_admin")


@router.get("/platform/pdpa/requests/{request_id}")
async def platform_pdpa_request(
    request_id: str,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """One request, for the admin's detail view."""
    row = await client.get_pdpa_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return row


@router.post("/platform/pdpa/requests/{request_id}/process")
async def platform_pdpa_process(
    request_id: str,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    result = await client.process_pdpa_request(request_id, processed_by=_admin_uuid(admin))
    if result.get("request_type") == "erasure":
        paths = result.get("storage_paths") or []
        deleted = 0
        if paths:
            try:
                store = pdpa_service.get_document_store()
                for path in paths:
                    try:
                        await store.delete(path=path)
                        deleted += 1
                    except Exception:  # noqa: BLE001
                        log.exception("could not delete %s during erasure", path)
            except pdpa_service.DocumentStoreNotConfigured:
                pass
        result = {**{k: v for k, v in result.items() if k != "storage_paths"}, "storage_deleted": deleted}
    return result


@router.post("/platform/pdpa/requests/{request_id}/reject")
async def platform_pdpa_reject(
    request_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    return await client.reject_pdpa_request(request_id, reason=reason, processed_by=_admin_uuid(admin))


def _admin_uuid(admin: dict) -> str | None:
    value = str(admin.get("sub") or admin.get("id") or "")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


# ======================================================================== Phase 18
# The platform's own operator: every tenant, one tenant, suspend/reopen,
# the cross-tenant audit trail, and break-glass by body (18.3).

TENANT_STATUSES = ("trial", "active", "suspended", "deleted")


@router.get("/platform/tenants")
async def platform_tenants(
    q: str | None = None,
    status_filter: str | None = None,
    plan: str | None = None,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    if status_filter and status_filter not in TENANT_STATUSES:
        raise HTTPException(status_code=422, detail="unknown status")
    # Round 21D — the console's plan filter.
    if plan and plan not in entitlements.PLAN_ORDER:
        raise HTTPException(status_code=422, detail={"error": "unknown_plan"})
    return await client.platform_tenants(q=q, status=status_filter, plan=plan)


@router.get("/platform/tenants/{license_id}")
async def platform_tenant(
    license_id: str,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    row = await client.platform_tenant(license_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    # The made-to-order chart allowance lives in the licence's settings, and
    # the console edits it on this same card — so it is merged in here
    # rather than making the page fetch a second endpoint for one number.
    # Best effort: a settings read that fails must not hide the tenant.
    row = dict(row)
    row.setdefault("ai_chart_quota", None)
    row.setdefault("ai_chart_used", 0)
    try:
        for setting in await client.list_license_settings(license_id) or []:
            key = str(setting.get("setting_key") or "")
            if key == "ai_chart_quota":
                row["ai_chart_quota"] = setting.get("setting_value")
            elif key == "ai_chart_usage":
                usage = dict(setting.get("setting_value") or {})
                row["ai_chart_used"] = usage.get("used") or 0
                row["ai_chart_month"] = usage.get("month") or ""
    except Exception:  # noqa: BLE001
        log.exception("could not read the chart allowance of %s", license_id)
    # Round 21D — what each other plan would lock (and whether it would be
    # refused), so the console can say so before the operator saves.
    try:
        row["plan_preview"] = await client.platform_plan_preview(license_id)
    except Exception:  # noqa: BLE001 — the page still opens without it
        log.exception("could not read the plan preview of %s", license_id)
        row["plan_preview"] = None
    return row


TENANT_TEXT_FIELDS = (
    "company_name", "legal_name", "company_phone", "company_email", "company_address", "tax_id",
    # Round 18: the operator's own notes — platform-only, never shown to the tenant.
    "admin_notes",
)
_BANGKOK = timezone(timedelta(hours=7))


def _trial_deadline(value) -> tuple[bool, "datetime | None"]:
    """(clear?, deadline). A bare date means the end of that Bangkok day —
    "ทดลองใช้ถึง 30 ก.ย." is the whole of the 30th, not its first second.
    Empty/None clears the deadline."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True, None
    text = str(value).strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return False, datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_BANGKOK)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return False, parsed if parsed.tzinfo else parsed.replace(tzinfo=_BANGKOK)
    except ValueError:
        raise HTTPException(status_code=422, detail={"error": "invalid_expires_at",
                                                      "message": "use YYYY-MM-DD or an ISO date-time"})


@router.patch("/platform/tenants/{license_id}")
async def platform_tenant_update(
    license_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Suspend / reopen (18.1) and, since 7 Sep 2026, edit the tenant:
    the subscription deadline and the shop's own details. A status-only
    body keeps the original status route; anything more goes through one
    audited PATCH on the Data tier. Round 18: `expires_at` is the name
    (`trial_expires_at` still accepted), `admin_notes` is editable, and a
    status of trial/active on a soft-deleted tenant restores it."""
    new_status = body.get("status")
    if new_status is not None:
        new_status = str(new_status).strip()
        if new_status not in TENANT_STATUSES:
            raise HTTPException(status_code=422, detail="status must be one of trial, active, suspended, deleted")
    changes: dict = {}
    for key in TENANT_TEXT_FIELDS:
        if key in body:
            value = body.get(key)
            changes[key] = None if value is None else str(value).strip()
    if "company_name" in changes and not changes["company_name"]:
        raise HTTPException(status_code=422, detail={"error": "company_name_required"})
    if "company_email" in changes and changes["company_email"] and "@" not in changes["company_email"]:
        raise HTTPException(status_code=422, detail={"error": "invalid_company_email"})
    expiry_key = "expires_at" if "expires_at" in body else ("trial_expires_at" if "trial_expires_at" in body else None)
    if expiry_key:
        clear, deadline = _trial_deadline(body.get(expiry_key))
        if clear:
            changes["clear_expires_at"] = True
        else:
            changes["expires_at"] = deadline.isoformat()
    actor = str(admin.get("sub") or "")
    # Round 21D — the shop's plan. The Data tier decides: an unknown code
    # is 422 `unknown_plan`, a downgrade that would leave more active
    # members than the target allows is 409 `plan_member_limit` (owner
    # decision Q2 — refused, never forced), and it writes the audit row.
    if "plan_code" in body:
        changes["plan_code"] = str(body.get("plan_code") or "").strip()
    # How many AI reports this company may have in a month, over its plan's
    # own number (Pro and up). A licence setting rather than a tenant
    # column, and only this route — the Chann administrator's — writes it
    # (owner's rule, 17 ก.ย. 2569). Empty = back to the plan's number
    # (round 21D); 0 is a value — AI reports off for this shop.
    quota: int | None = None
    clear_quota = False
    if "ai_chart_quota" in body:
        raw = body.get("ai_chart_quota")
        if raw is None or str(raw).strip() == "":
            clear_quota = True
        else:
            try:
                quota = max(0, int(str(raw).strip()))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422, detail={"error": "ai_chart_quota_must_be_a_number"},
                ) from None
    if new_status and changes:
        changes["status"] = new_status
    if not changes and not new_status and "ai_chart_quota" not in body:
        raise HTTPException(status_code=422, detail={"error": "nothing_to_update"})
    # The plan before the write, to tell the owner what changed.
    before = await client.platform_tenant(license_id) if "plan_code" in changes else None
    # Ruling 28: a top-up (0 included) only on a plan that honours one —
    # judged on the plan the shop will be on after this request. Refused
    # whole, before anything is written: a stored override on Starter is
    # inert today and would come back to life after a later upgrade.
    # Clearing one stays allowed.
    if quota is not None:
        target = changes.get("plan_code")
        if target is None:
            current = await client.platform_tenant(license_id)
            if current is None:
                raise HTTPException(status_code=404, detail="tenant not found")
            target = str(current.get("plan_code") or "pro")
        if target in entitlements.PLAN_ORDER and target not in entitlements.QUOTA_TOP_UP_PLANS:
            raise HTTPException(
                status_code=422,
                detail=entitlements.ai_quota_refusal(target, whole="plan_code" in changes),
            )
    saved: dict | None = None
    try:
        # The tenant first: a refused plan change writes nothing, the
        # top-up in the same save included.
        if changes:
            saved = await client.update_tenant(license_id, changes, actor_id=actor)
        elif new_status:
            saved = await client.set_license_status(license_id, new_status, actor_id=actor)
    except DataTierError as exc:
        refusal = exc.structured or {}
        if entitlements.is_plan_refusal(exc) or refusal.get("error") == "unknown_plan":
            # Unchanged: the console shows its `message` by the plan field.
            raise HTTPException(status_code=exc.status_code, detail=refusal) from exc
        code = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=code, detail={"error": "tenant_update_failed", "reason": exc.detail}) from exc
    if "ai_chart_quota" in body:
        try:
            if clear_quota:
                await client.delete_license_setting(license_id, "ai_chart_quota", actor_id=actor)
            else:
                await client.put_license_setting(license_id, "ai_chart_quota", quota, actor_id=actor)
        except DataTierError as exc:
            # P27: clearing an override that is not there is already done.
            if not (clear_quota and exc.status_code == 404):
                if saved is None:
                    # Nothing else was written: a failure is the honest answer.
                    code = exc.status_code if 400 <= exc.status_code < 500 else 502
                    raise HTTPException(
                        status_code=code,
                        detail={"error": "tenant_update_failed", "reason": exc.detail},
                    ) from exc
                # The tenant (maybe its plan) is already saved: say that,
                # and say the top-up was not — never "nothing changed".
                saved = dict(saved)
                saved["quota_error"] = exc.detail
                saved["plan_changed"] = before is not None and str(before.get("plan_code")) != str(saved.get("plan_code"))
        if saved is None:
            saved = await client.platform_tenant(license_id)
            if saved is None:
                raise HTTPException(status_code=404, detail="tenant not found")
    if before is not None and saved is not None and str(before.get("plan_code")) != str(saved.get("plan_code")):
        await _tell_owner_plan_changed(client, license_id, before, saved)
    return saved


async def _tell_owner_plan_changed(client: DataClient, license_id: str, before: dict, after: dict) -> None:
    """Spec §3.4: the shop's owner hears it on the Sales OA — the new plan,
    what it opened, what it locked. Task 8's one owner notice (R6), so it is
    best effort: the admin's save stands whatever the push does."""
    owner = str(after.get("owner_chann_uid") or before.get("owner_chann_uid") or "")
    company = str(after.get("company_name") or before.get("company_name") or "")
    await entitlements.owner_notice(
        client, license_id=license_id, owner_chann_uid=owner, kind="plan_changed",
        message=entitlements.plan_change_text(before.get("plan"), after.get("plan"), company, "th"),
        message_en=entitlements.plan_change_text(before.get("plan"), after.get("plan"), company, "en"),
    )


def _platform_refusal(exc: DataTierError, error: str) -> HTTPException:
    """The Data tier's own status and reason for a 4xx; 502 otherwise."""
    code = exc.status_code if 400 <= exc.status_code < 500 else 502
    return HTTPException(status_code=code, detail={"error": error, "reason": exc.structured or exc.detail})


@router.post("/platform/tenants/{license_id}/extend")
async def platform_tenant_extend(
    license_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Round 18: renew the subscription — expires_at = max(now, current
    expiry) + days (1..3650). The status stays; a suspended tenant
    reopens."""
    try:
        days = int(body.get("days"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"error": "invalid_days", "message": "days must be 1..3650"})
    if not 1 <= days <= 3650:
        raise HTTPException(status_code=422, detail={"error": "invalid_days", "message": "days must be 1..3650"})
    try:
        return await client.extend_tenant(license_id, days, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        raise _platform_refusal(exc, "tenant_extend_failed") from exc


@router.delete("/platform/tenants/{license_id}")
async def platform_tenant_delete(
    license_id: str,
    purge: bool = False,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Round 18: soft delete by default (status "deleted", members
    removed, hidden from the list, reversible through PATCH status);
    purge=true removes every row of the company for good. Purging
    needs the break_glass permission — it is the one irreversible thing
    the console can do."""
    if purge and "platform.admin.break_glass" not in admin.get("permissions", []):
        raise HTTPException(status_code=403, detail="permission required: platform.admin.break_glass")
    try:
        return await client.delete_tenant(license_id, purge=purge, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        raise _platform_refusal(exc, "tenant_delete_failed") from exc


# ------------------------------------------- members from the console (round 18)

@router.patch("/platform/tenants/{license_id}/members/{chann_uid}/role")
async def platform_member_role(
    license_id: str,
    chann_uid: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Any role but owner (owner moves through break-glass only; the
    Data tier answers 409 for either direction)."""
    role = str(body.get("role") or body.get("role_name") or "").strip()
    if not role:
        raise HTTPException(status_code=422, detail={"error": "role_required"})
    if role.lower() == "owner":
        raise HTTPException(status_code=409, detail={
            "error": "owner_via_break_glass",
            "reason": "the owner role is only assigned through break-glass transfer",
        })
    try:
        return await client.platform_set_member_role(license_id, chann_uid, role, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        raise _platform_refusal(exc, "member_role_failed") from exc


@router.patch("/platform/tenants/{license_id}/members/{chann_uid}/status")
async def platform_member_status(
    license_id: str,
    chann_uid: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Remove or reactivate. The owner's row is refused (409); a removed
    technician's open jobs return to the queue and come back in
    `unassigned_tickets`."""
    new_status = str(body.get("status") or "").strip()
    if new_status not in ("active", "removed"):
        raise HTTPException(status_code=422, detail={"error": "invalid_status", "message": "status must be active or removed"})
    try:
        return await client.platform_set_member_status(license_id, chann_uid, new_status, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        raise _platform_refusal(exc, "member_status_failed") from exc


@router.post("/platform/tenants/{license_id}/members/{chann_uid}/move")
async def platform_member_move(
    license_id: str,
    chann_uid: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Removed here, added to the target with the given role on the same
    channel — one Data-tier transaction, audited on both licenses.
    Owner rows are refused; so is someone already in the target."""
    target = str(body.get("target_license_id") or "").strip()
    role = str(body.get("role") or body.get("role_name") or "").strip()
    if not target or not role:
        raise HTTPException(status_code=422, detail={"error": "target_and_role_required"})
    if target == license_id:
        raise HTTPException(status_code=422, detail={"error": "same_company"})
    if role.lower() == "owner":
        raise HTTPException(status_code=409, detail={
            "error": "owner_via_break_glass",
            "reason": "the owner role is only assigned through break-glass transfer",
        })
    try:
        return await client.platform_move_member(
            license_id, chann_uid, target_license_id=target, role_name=role, actor_id=str(admin.get("sub") or ""),
        )
    except DataTierError as exc:
        raise _platform_refusal(exc, "member_move_failed") from exc


@router.get("/platform/audit")
async def platform_audit(
    cross_tenant: bool | None = None,
    license_id: str | None = None,
    actor_type: str | None = None,
    action: str | None = None,
    limit: int = 100,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    return await client.platform_audit(
        cross_tenant=cross_tenant, license_id=license_id, actor_type=actor_type, action=action,
        limit=max(1, min(limit, 500)),
    )


@router.post("/platform/break-glass/transfer-owner")
async def platform_break_glass(
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """18.4: force a new Owner for a tenant. Needs the break_glass
    permission on the admin's token; the Data tier records the
    cross-tenant audit row; the new owner is told on LINE."""
    if "platform.admin.break_glass" not in admin.get("permissions", []):
        raise HTTPException(status_code=403, detail="permission required: platform.admin.break_glass")
    license_id = str(body.get("license_id") or "").strip()
    target = str(body.get("target_chann_uid") or "").strip()
    if not license_id or not target:
        raise HTTPException(status_code=422, detail="license_id and target_chann_uid are required")
    try:
        member = await client.force_transfer_owner(license_id, target, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        # `exc.status_code`, not `.status` — the old attribute never existed,
        # so a 404/409 from the Data tier always surfaced as 502 with no
        # reason (review D11, 6 Sep 2026). The reason rides along so the
        # console can show it.
        raise HTTPException(
            status_code=exc.status_code if exc.status_code in (404, 409) else 502,
            detail={"error": "break_glass_failed", "reason": exc.structured or exc.detail},
        ) from exc
    try:
        from .services.notify import send_notification

        line_target = await client.line_target_of(target)
        await send_notification(
            client, license_id=license_id, target_chann_uid=target, target_line_user_id=line_target,
            type="ownership_transferred",
            message="ผู้ดูแลระบบโอนสิทธิ์เจ้าของร้านให้คุณแล้ว (กรณีฉุกเฉิน) ตอนนี้คุณเป็นเจ้าของร้านนี้",
            message_en="The platform operator transferred shop ownership to you (emergency procedure). You are now this shop's owner.",
            entity_type="license", entity_id=license_id, oa="sales",
        )
    except Exception:  # noqa: BLE001
        log.exception("break-glass: could not notify the new owner %s", target)
    return member


# ------------------------------------------------------- guide pictures (4 Sep 2026)
_HELP_IMAGE_DIR = Path(__file__).resolve().parent / "static" / "help"
_HELP_SLOT = re.compile(r"^[a-z0-9-]{1,40}$")


@router.get("/guide/images/{slot}.png")
async def guide_image(slot: str):
    """The illustrated-guide pictures, public like the guide itself (LINE
    fetches them when chat sends the guide). Slot names only — no paths."""
    from fastapi.responses import FileResponse

    if not _HELP_SLOT.match(slot):
        raise HTTPException(status_code=404, detail="unknown image")
    path = _HELP_IMAGE_DIR / f"{slot}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="unknown image")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
