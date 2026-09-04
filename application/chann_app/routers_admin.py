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
        # Phase 18 — the dashboards show a read-only notice for a suspended shop.
        "license_status": (chosen[0].get("license_status") or "active") if len(chosen) == 1 else None,
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

TENANT_STATUSES = ("trial", "active", "suspended")


@router.get("/platform/tenants")
async def platform_tenants(
    q: str | None = None,
    status_filter: str | None = None,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    if status_filter and status_filter not in TENANT_STATUSES:
        raise HTTPException(status_code=422, detail="unknown status")
    return await client.platform_tenants(q=q, status=status_filter)


@router.get("/platform/tenants/{license_id}")
async def platform_tenant(
    license_id: str,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    row = await client.platform_tenant(license_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return row


@router.patch("/platform/tenants/{license_id}")
async def platform_tenant_status(
    license_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
    client: DataClient = Depends(get_data_client),
):
    """Suspend (read-only for the tenant, chat and dashboards refuse) or
    reopen. The Data tier writes the audit row with actor_type
    platform_admin and the admin's id."""
    new_status = str(body.get("status") or "").strip()
    if new_status not in TENANT_STATUSES:
        raise HTTPException(status_code=422, detail="status must be one of trial, active, suspended")
    try:
        return await client.set_license_status(license_id, new_status, actor_id=str(admin.get("sub") or ""))
    except DataTierError as exc:
        raise HTTPException(status_code=502, detail="tenant status update failed") from exc


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
        raise HTTPException(status_code=exc.status if getattr(exc, "status", None) in (404, 409) else 502,
                            detail="break-glass transfer failed") from exc
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
