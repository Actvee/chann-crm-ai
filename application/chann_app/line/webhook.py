"""LINE webhook handling for the three platform-level OAs.

One route per OA rather than one shared route, because the signature must be
verified against the channel secret belonging to that specific OA. A shared
route would have to guess which secret to try, and "try them all" would let a
Customer-OA message be replayed as a Technician message.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import OA_CHANNELS, channel_secret
from ..data_client import DataClient
from ..services.identity import TenantResolution, resolve_context
from .client import LineReplyError, reply_text
from .signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook/line", tags=["line"])


def _reply_stub(ctx) -> str:
    """Phase 1 responds with a deterministic stub. The AI intent engine lands
    in Phase 4; wiring a model in now would make the Phase 1 acceptance test
    non-deterministic for no gain."""
    if ctx.resolution is TenantResolution.SINGLE:
        company = ctx.memberships[0]["company_name"]
        return f"[{ctx.chann_uid}] เชื่อมต่อกับ {company} แล้ว"
    if ctx.resolution is TenantResolution.MULTIPLE:
        names = ", ".join(m["company_name"] for m in ctx.memberships)
        return f"[{ctx.chann_uid}] คุณเป็นสมาชิกหลายบริษัท: {names} — กรุณาเลือก"
    return f"[{ctx.chann_uid}] ยังไม่พบบริษัทที่ผูกไว้ กรุณาลงทะเบียน"


@router.post("/{oa}")
async def handle_webhook(
    oa: str,
    request: Request,
    x_line_signature: str = Header(default=""),
):
    if oa not in OA_CHANNELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown OA")

    raw_body = await request.body()
    secret = channel_secret(oa)
    if not secret:
        # Refusing is correct: accepting unsigned webhooks in an environment
        # where the secret is merely missing would let anyone forge messages.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LINE_{oa.upper()}_CHANNEL_SECRET is REQUIRED_NOT_CONFIGURED",
        )
    if not verify_signature(secret, raw_body, x_line_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")

    payload = await request.json()
    client = DataClient()
    replies = []
    try:
        for event in payload.get("events", []):
            if event.get("type") != "message":
                continue
            line_user_id = event.get("source", {}).get("userId")
            if not line_user_id:
                continue
            ctx = await resolve_context(client, oa, line_user_id)
            text = _reply_stub(ctx)
            try:
                await reply_text(oa, event.get("replyToken", ""), text)
            except LineReplyError as exc:
                log.error("LINE reply failed for oa=%s: %s", oa, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                )
            replies.append({"chann_uid": ctx.chann_uid, "text": text})
    finally:
        await client.aclose()

    # LINE only needs a 200. The replies are returned so the Phase 1 runtime
    # acceptance check can assert on what would have been sent.
    return {"ok": True, "oa": oa, "replies": replies}
