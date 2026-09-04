"""Phase 16.5 — PDPA on the Application side: consent at registration,
"ขอข้อมูลของฉัน" (a copy of everything, as a page the person can keep),
"ขอลบข้อมูล" (anonymised everywhere, the pictures gone from storage).

The Data tier does the cross-tenant walks and the audit rows; this
module decides when to ask, what to say, and deletes the objects the
Data tier cannot reach (GCS).
"""
from __future__ import annotations

import html
import json
import logging
import uuid
from datetime import datetime, timezone

from ..data_client import DataClient, DataTierError
from .storage.base import DocumentStoreNotConfigured, get_document_store

log = logging.getLogger(__name__)

CONSENT_VERSION = "2026-09-04"
EXPORT_LINK_SECONDS = 24 * 3600

CONSENT_TEXT = {
    "th": (
        "ก่อนเริ่มใช้งาน ขอความยินยอมตาม พ.ร.บ. คุ้มครองข้อมูลส่วนบุคคล (PDPA):\n"
        "• ระบบเก็บชื่อ เบอร์โทร ที่อยู่ และประวัติงานซ่อม/การซื้อ เพื่อให้ร้านที่คุณติดต่อให้บริการได้\n"
        "• ข้อมูลใช้เฉพาะร้านที่คุณผูกไว้ ไม่เปิดเผยให้ร้านอื่น\n"
        "• ขอสำเนาข้อมูลได้ทุกเมื่อด้วย \"ขอข้อมูลของฉัน\" และขอลบได้ด้วย \"ขอลบข้อมูล\"\n\n"
        "ยอมรับไหมครับ"
    ),
    "en": (
        "Before we start, your consent under Thailand's PDPA:\n"
        "• The system keeps your name, phone, address and your repair/purchase history so the shop you contact can serve you\n"
        "• Only the shops you link to see it; never other shops\n"
        "• Ask for a copy any time with \"my data\", or for erasure with \"delete my data\"\n\n"
        "Do you accept?"
    ),
}
CONSENT_YES = ("ยอมรับ", "ยินยอม", "ตกลง", "accept", "yes", "i accept", "agree")
CONSENT_NO = ("ไม่ยอมรับ", "ไม่ยินยอม", "ไม่ตกลง", "decline", "no", "i decline")
CONSENT_DECLINED = {
    "th": "รับทราบครับ ระบบจะไม่เก็บข้อมูลและยังลงทะเบียนให้ไม่ได้ ถ้าเปลี่ยนใจ พิมพ์ \"ยอมรับ\" ได้ทุกเมื่อ",
    "en": "Understood — nothing is stored and registration cannot go ahead. Type \"accept\" any time if you change your mind.",
}
CONSENT_RECORDED = {
    "th": "ขอบคุณครับ บันทึกความยินยอมแล้ว",
    "en": "Thank you — your consent is recorded.",
}
EXPORT_READY = {
    "th": "สำเนาข้อมูลของคุณพร้อมแล้ว (ลิงก์ใช้ได้ 24 ชั่วโมง):\n{url}\n\nมีข้อมูลจาก {n} ร้าน",
    "en": "Your copy is ready (link valid 24 hours):\n{url}\n\nData from {n} shop(s)",
}
EXPORT_INLINE = {
    "th": "สำเนาข้อมูลของคุณ ({n} ร้าน):\n{summary}\n\n(ที่เก็บไฟล์ยังไม่พร้อม จึงสรุปให้ในแชท)",
    "en": "Your data ({n} shop(s)):\n{summary}\n\n(File storage is not configured, so here is the summary)",
}
ERASE_CONFIRM = {
    "th": (
        "การลบข้อมูลจะทำให้ชื่อ เบอร์ ที่อยู่ ข้อความแชท และรูปของคุณถูกลบออกจากทุกร้านที่ผูกไว้ "
        "(ประวัติงานยังอยู่แต่ไม่มีชื่อคุณ) และต้องยินยอมใหม่ถ้ากลับมาใช้\n"
        "ยืนยันพิมพ์ \"ยืนยันลบข้อมูล\""
    ),
    "en": (
        "Erasure removes your name, phone, address, chat lines and pictures from every shop you are linked to "
        "(job history stays without your name) and you will be asked for consent again if you return.\n"
        "Type \"confirm delete my data\" to proceed."
    ),
}
ERASE_DONE = {
    "th": "ลบข้อมูลของคุณจาก {n} ร้านแล้ว (ลูกค้า {c} รายการ, งาน {t}, รูป {p}) ขอบคุณที่ใช้บริการครับ",
    "en": "Your data was erased from {n} shop(s) ({c} customer records, {t} jobs, {p} pictures). Thank you.",
}
ERASE_NOTHING = {
    "th": "ไม่มีคำขอลบที่รอยืนยันครับ พิมพ์ \"ขอลบข้อมูล\" ก่อน",
    "en": "No erasure is waiting for confirmation. Type \"delete my data\" first.",
}
FAILED = {"th": "ทำรายการไม่สำเร็จ ลองใหม่อีกครั้งครับ", "en": "That did not go through — please try again."}


def _t(table: dict, language: str) -> str:
    return table["en"] if language == "en" else table["th"]


def _wants(text: str, phrases: tuple[str, ...]) -> bool:
    compact = (text or "").strip().lower().replace(" ", "")
    return bool(compact) and any(compact == p.replace(" ", "") for p in phrases)


# ------------------------------------------------------------ consent

async def has_consent(client: DataClient, chann_uid: str) -> bool:
    try:
        row = await client.get_consent(chann_uid)
    except DataTierError:
        return False
    return bool(row.get("consent_accepted_at"))


async def consent_gate(
    client: DataClient, *, chann_uid: str, oa: str, message: str, language: str,
) -> tuple[str | None, str | None]:
    """Before a registration step (linking a shop, joining one, creating
    one) the person must have consented once. Returns (reply, message):
    a reply to send instead — the consent question, a refusal, or the
    thank-you — and the message to continue with (the one held while we
    asked), or None to stop here."""
    if await has_consent(client, chann_uid):
        return None, message
    text = (message or "").strip()
    pending = await client.get_pending_intent(chann_uid, oa)
    held = ""
    if pending is not None and pending.get("entity") == "consent":
        held = str((pending.get("fields") or {}).get("message") or "")
    if _wants(text, CONSENT_YES):
        await client.put_consent(chann_uid, CONSENT_VERSION)
        await client.clear_pending_intent(chann_uid, oa)
        thanks = _t(CONSENT_RECORDED, language)
        # Continue with what they came to do, if anything was held.
        return thanks, (held or None)
    if _wants(text, CONSENT_NO):
        await client.clear_pending_intent(chann_uid, oa)
        return _t(CONSENT_DECLINED, language), None
    await client.set_pending_intent(
        chann_uid, oa, action="consent", entity="consent",
        fields={"message": text[:500]}, missing=[], ttl_seconds=3600,
    )
    return _t(CONSENT_TEXT, language), None


# ------------------------------------------------------------ export

def _render_export_html(bundle: dict) -> str:
    e = html.escape
    identity = bundle.get("identity") or {}
    parts = [
        "<!doctype html><html lang=\"th\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>สำเนาข้อมูลส่วนบุคคล</title>",
        "<style>body{font-family:'IBM Plex Sans Thai','Noto Sans Thai',system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px;line-height:1.6;color:#1f2328}"
        "h1{font-size:24px}h2{font-size:18px;margin-top:28px;border-bottom:2px solid #178a50;padding-bottom:4px}"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dcd9d2;padding:6px 8px;text-align:left;vertical-align:top}"
        ".meta{color:#5a6478;font-size:14px}</style></head><body>",
        "<h1>สำเนาข้อมูลส่วนบุคคล (PDPA)</h1>",
        f"<p class=\"meta\">ออกเมื่อ {e(str(bundle.get('exported_at') or ''))} · รหัสคำขอ {e(str(bundle.get('request_id') or ''))}</p>",
        "<h2>ตัวตน</h2><table>",
    ]
    for key, label in (("chann_uid", "รหัส"), ("display_name", "ชื่อที่แสดง"), ("first_name", "ชื่อ"), ("last_name", "นามสกุล"),
                       ("phone", "เบอร์โทร"), ("email", "อีเมล"), ("address", "ที่อยู่"), ("consent_accepted_at", "ยินยอมเมื่อ"), ("consent_version", "เวอร์ชันความยินยอม")):
        parts.append(f"<tr><th>{label}</th><td>{e(str(identity.get(key) or '—'))}</td></tr>")
    parts.append("</table>")
    for company in bundle.get("companies") or []:
        parts.append(f"<h2>{e(str(company.get('company_name') or ''))}</h2>")
        roles = ", ".join(f"{r.get('role')} ({r.get('status')})" for r in company.get("roles") or []) or "ลูกค้า"
        parts.append(f"<p class=\"meta\">บทบาท: {e(roles)}</p>")
        customer = company.get("customer")
        if customer:
            parts.append("<table>" + "".join(
                f"<tr><th>{e(k)}</th><td>{e(str(v if v is not None else '—'))}</td></tr>" for k, v in customer.items()
            ) + "</table>")
        for key, title, cols in (
            ("tickets", "งานซ่อม", ("ticket_number", "status", "issue_description", "service_address", "created_at")),
            ("warranties", "สินค้าที่ลงทะเบียน", ("warranty_number", "serial_number", "product_name", "warranty_start", "warranty_end", "status")),
            ("deals", "ดีล", ("deal_id", "stage", "created_at")),
            ("chat_messages", "ข้อความแชทของคุณ", ("created_at", "content")),
        ):
            rows = company.get(key) or []
            if not rows:
                continue
            parts.append(f"<h3>{title} ({len(rows)})</h3><table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
            for row in rows:
                parts.append("<tr>" + "".join(f"<td>{e(str(row.get(c) if row.get(c) is not None else '—'))}</td>" for c in cols) + "</tr>")
            parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _summary(bundle: dict, language: str) -> str:
    lines = []
    for company in bundle.get("companies") or []:
        name = company.get("company_name") or "?"
        t, w, d = len(company.get("tickets") or []), len(company.get("warranties") or []), len(company.get("deals") or [])
        lines.append(f"· {name}: " + (f"งาน {t} · สินค้า {w} · ดีล {d}" if language != "en" else f"{t} jobs · {w} products · {d} deals"))
    return "\n".join(lines) or ("—")


async def export_my_data(client: DataClient, *, chann_uid: str, via: str, language: str) -> dict:
    """Create + process an export request; store the page; return
    {"text": reply, "url": link or None, "bundle": bundle}."""
    request = await client.create_pdpa_request(chann_uid=chann_uid, request_type="export", requested_via=via)
    result = await client.process_pdpa_request(str(request["id"]))
    bundle = result.get("bundle") or {}
    n = len(bundle.get("companies") or [])
    url = None
    try:
        store = get_document_store()
        stored = await store.put(
            key=f"pdpa/{chann_uid}/{uuid.uuid4().hex}.html",
            content=_render_export_html(bundle).encode("utf-8"), content_type="text/html; charset=utf-8",
        )
        url = await store.signed_url(path=stored.path, expires_seconds=EXPORT_LINK_SECONDS)
    except DocumentStoreNotConfigured:
        url = None
    except Exception:
        log.exception("could not store the PDPA export")
        url = None
    if url:
        text = _t(EXPORT_READY, language).format(url=url, n=n)
    else:
        text = _t(EXPORT_INLINE, language).format(n=n, summary=_summary(bundle, language))
    return {"text": text, "url": url, "bundle": bundle, "request_id": str(request["id"])}


# ------------------------------------------------------------ erasure

async def erase_me(client: DataClient, *, chann_uid: str, via: str, language: str) -> dict:
    """Create + process an erasure; delete the storage objects the Data
    tier handed back. Returns {"text", "result"}."""
    request = await client.create_pdpa_request(chann_uid=chann_uid, request_type="erasure", requested_via=via)
    result = await client.process_pdpa_request(str(request["id"]))
    deleted = 0
    paths = result.get("storage_paths") or []
    if paths:
        try:
            store = get_document_store()
            for path in paths:
                try:
                    await store.delete(path=path)
                    deleted += 1
                except Exception:  # noqa: BLE001
                    log.exception("could not delete %s during erasure", path)
        except DocumentStoreNotConfigured:
            pass
    text = _t(ERASE_DONE, language).format(
        n=result.get("tenants", 0), c=result.get("customers", 0), t=result.get("tickets", 0), p=result.get("photos", 0),
    )
    return {"text": text, "result": {**result, "storage_deleted": deleted}, "request_id": str(request["id"])}
