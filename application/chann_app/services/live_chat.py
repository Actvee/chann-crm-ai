"""Phase 15 — live chat between a customer and the shop (PLAN_3OA B6).

The customer says "คุยกับร้าน" (chat or home screen) → one conversation
with this shop is opened → every Sales/CS person is told → whoever
answers first owns it → the answer is pushed to the customer's LINE →
the customer's next lines go into the conversation, not into a repair
job → the shop's clock (SLA) runs only while the customer is waiting →
nobody speaks for a while → it closes itself.

Both the chat engine and the dashboard/home routes call these; the two
sides therefore always agree on who is told and when.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..data_client import DataClient, DataTierError
from ..line.client import LineReplyError, push_text
from .notify import send_notification

log = logging.getLogger(__name__)

AGENT_ROLES = ("owner", "admin", "sales", "cs")
LIVE = ("open", "assigned")
DEFAULT_SLA_MINUTES = 30
DEFAULT_TIMEOUT_MINUTES = 120
SLA_KEYS = ("chat_sla_minutes", "chat_sla")
TIMEOUT_KEYS = ("chat_timeout_minutes", "session_timeout")


def _minutes(rows: list[dict], keys: tuple[str, ...], default: int) -> int:
    for row in rows:
        if str(row.get("setting_key")) in keys:
            try:
                value = int(float(str(row.get("setting_value"))))
            except (TypeError, ValueError):
                return default
            return max(1, min(value, 24 * 60))
    return default


async def chat_settings(client: DataClient, license_id: str) -> tuple[int, int]:
    """(sla_minutes, timeout_minutes) from license_settings, defaults otherwise."""
    try:
        rows = await client.list_license_settings(str(license_id))
    except Exception:
        log.exception("could not read chat settings for %s", license_id)
        rows = []
    return (
        _minutes(rows, SLA_KEYS, DEFAULT_SLA_MINUTES),
        _minutes(rows, TIMEOUT_KEYS, DEFAULT_TIMEOUT_MINUTES),
    )


async def company_name(client: DataClient, license_id: str) -> str:
    try:
        profile = await client.get_company_profile(str(license_id)) or {}
    except Exception:
        profile = {}
    return str(profile.get("company_name") or "").strip() or "ร้าน"


async def live_session(client: DataClient, *, license_id: str, chann_uid: str) -> dict | None:
    rows = await client.list_chat_sessions(
        str(license_id), status="live", customer_chann_uid=chann_uid, limit=1,
    )
    return rows[0] if rows else None


def _shown(session: dict) -> str:
    return str(session.get("customer_name") or "").strip() or str(session.get("customer_chann_uid") or "")


async def _agents(client: DataClient, license_id: str) -> list[dict]:
    try:
        members = await client.list_members(str(license_id))
    except Exception:
        log.exception("could not list members for a chat notification")
        return []
    return [
        m for m in members
        if str(m.get("role") or "").lower() in AGENT_ROLES
        and str(m.get("status") or "active") == "active" and m.get("chann_uid")
    ]


async def _tell(
    client: DataClient, *, license_id: str, members: list[dict], text: str, type: str,
    session_id: str, language: str, line: bool = True, text_en: str | None = None,
) -> int:
    sent = 0
    for member in members:
        uid = str(member.get("chann_uid") or "")
        try:
            line_uid = await client.line_target_of(uid) if line else None
            await send_notification(
                client, license_id=str(license_id), target_chann_uid=uid,
                target_line_user_id=line_uid, type=type, message=text, message_en=text_en,
                entity_type="chat_session", entity_id=session_id,
                delivery_line=line, language=language, oa="sales",
            )
            sent += 1
        except Exception:
            log.exception("could not tell %s about a chat session", uid)
    return sent


async def _customer_language(client: DataClient, chann_uid: str) -> str:
    try:
        prefs = await client.get_display_preferences(chann_uid) or {}
        return "en" if str(prefs.get("language") or "th") == "en" else "th"
    except Exception:  # noqa: BLE001
        return "th"


async def _push_customer(
    client: DataClient, *, chann_uid: str, text: str, text_en: str | None = None,
) -> bool:
    """A line to the customer's LINE, in the language they read."""
    try:
        line_uid = await client.line_target_of(chann_uid)
        if not line_uid:
            return False
        if text_en and await _customer_language(client, chann_uid) == "en":
            text = text_en
        await push_text("customer", line_uid, text)
        return True
    except (LineReplyError, Exception):  # noqa: BLE001
        log.exception("could not push a chat line to %s", chann_uid)
        return False


async def start_session(
    client: DataClient, *, license_id: str, chann_uid: str, display_name: str | None = None,
    first_message: str | None = None, product_id: str | None = None, language: str = "th",
) -> tuple[dict, bool]:
    """The customer's conversation with this shop — opened now, or the one
    already running. A new one is announced to every agent; a first line
    typed with it is stored and is what they see."""
    sla, timeout = await chat_settings(client, license_id)
    session = await client.open_chat_session(
        str(license_id), customer_chann_uid=chann_uid, product_id=product_id,
        sla_minutes=sla, timeout_minutes=timeout, actor_id=chann_uid,
    )
    created = bool(session.pop("_created", False))
    if first_message and first_message.strip():
        try:
            await client.add_chat_message(
                str(license_id), str(session["id"]), sender_type="customer",
                content=first_message.strip(), sender_chann_uid=chann_uid,
                sla_minutes=sla, timeout_minutes=timeout,
            )
        except DataTierError as exc:
            log.warning("first chat line refused: %s", exc.detail)
    if created:
        shown = (display_name or "").strip() or _shown(session)
        text = f"💬 ลูกค้า {shown} ขอคุยกับร้าน"
        text_en = f"💬 Customer {shown} wants to talk to the shop"
        if first_message and first_message.strip():
            quoted = f"\n\"{first_message.strip()[:200]}\""
            text += quoted
            text_en += quoted
        text += "\nตอบได้ที่ หน้าจอ > แชทลูกค้า (ตอบใน LINE ตรงไม่ถึงลูกค้า)"
        text_en += "\nAnswer under home > Customer chats (a reply in LINE itself does not reach them)"
        await _tell(
            client, license_id=license_id, members=await _agents(client, license_id),
            text=text, text_en=text_en, type="chat_session_new", session_id=str(session["id"]),
            language=language,
        )
    return session, created


async def customer_message(
    client: DataClient, *, license_id: str, session: dict, chann_uid: str, text: str,
    language: str = "th",
) -> dict:
    """A line from the customer into their running conversation. The
    agent who owns it hears in LINE; before anyone owns it, the dashboard
    badge is enough — every agent was already pushed when it opened."""
    sla, timeout = await chat_settings(client, license_id)
    message = await client.add_chat_message(
        str(license_id), str(session["id"]), sender_type="customer", content=text,
        sender_chann_uid=chann_uid, sla_minutes=sla, timeout_minutes=timeout,
    )
    agents = await _agents(client, license_id)
    assigned = str(session.get("assigned_to") or "")
    owner = [m for m in agents if str(m.get("id")) == assigned] if assigned else []
    shown = _shown(session)
    line = f"💬 {shown}: {text.strip()[:300]}"
    if owner:
        await _tell(
            client, license_id=license_id, members=owner, text=line, text_en=line, type="chat_message",
            session_id=str(session["id"]), language=language,
        )
    else:
        await _tell(
            client, license_id=license_id, members=agents, text=line, text_en=line, type="chat_message",
            session_id=str(session["id"]), language=language, line=False,
        )
    return message


async def agent_reply(
    client: DataClient, *, license_id: str, session: dict, agent_chann_uid: str,
    member_id: str | None, text: str, language: str = "th",
) -> dict:
    """The shop answers — from the dashboard, never LINE directly (15.4).
    Whoever answers owns the conversation from then on, the SLA clock
    stops, and the words reach the customer's LINE."""
    if member_id and str(session.get("assigned_to") or "") != str(member_id):
        try:
            session = await client.assign_chat_session(
                str(license_id), str(session["id"]), member_id, actor_id=agent_chann_uid,
            )
        except DataTierError as exc:
            log.warning("could not assign chat session: %s", exc.detail)
    sla, timeout = await chat_settings(client, license_id)
    message = await client.add_chat_message(
        str(license_id), str(session["id"]), sender_type="agent", content=text,
        sender_chann_uid=agent_chann_uid, sla_minutes=sla, timeout_minutes=timeout,
    )
    shop = await company_name(client, license_id)
    await _push_customer(
        client, chann_uid=str(session["customer_chann_uid"]),
        text=f"💬 {shop}: {text.strip()}\n(ตอบกลับได้เลยในแชทนี้ · พิมพ์ \"จบการสนทนา\" เมื่อเสร็จ)",
        text_en=f"💬 {shop}: {text.strip()}\n(reply right here · type \"end chat\" when done)",
    )
    return message


async def close_session(
    client: DataClient, *, license_id: str, session: dict, by: str, actor_chann_uid: str,
    language: str = "th",
) -> dict:
    """`by` is "customer" or "agent"; the other side is told."""
    closed = await client.close_chat_session(str(license_id), str(session["id"]), actor_id=actor_chann_uid)
    if by == "agent":
        shop = await company_name(client, license_id)
        await _push_customer(
            client, chann_uid=str(session["customer_chann_uid"]),
            text=f"💬 {shop} ปิดการสนทนาแล้ว ขอบคุณครับ พิมพ์ \"คุยกับร้าน\" ได้อีกเมื่อต้องการ",
            text_en=f"💬 {shop} closed the conversation. Thank you — type \"talk to the shop\" any time.",
        )
    else:
        assigned = str(session.get("assigned_to") or "")
        agents = await _agents(client, license_id)
        owner = [m for m in agents if str(m.get("id")) == assigned] if assigned else []
        if owner:
            await _tell(
                client, license_id=license_id, members=owner,
                text=f"💬 ลูกค้า {_shown(session)} จบการสนทนาแล้ว",
                text_en=f"💬 Customer {_shown(session)} ended the conversation", type="chat_message",
                session_id=str(session["id"]), language=language, line=False,
            )
    return closed


async def sweep(client: DataClient) -> dict:
    """The platform's clock: overdue answers are escalated to the agent
    who owns the conversation (or every agent while nobody does), and
    conversations nobody has touched are closed and the customer told.
    Idempotent — the Data tier marks what it hands back."""
    try:
        result = await client.sweep_chat_sessions()
    except DataTierError as exc:
        log.warning("chat sweep refused: %s", exc.detail)
        return {"escalated": 0, "timed_out": 0}
    escalated = 0
    for session in result.get("escalated") or []:
        license_id = str(session.get("license_id"))
        agents = await _agents(client, license_id)
        assigned = str(session.get("assigned_to") or "")
        owner = [m for m in agents if str(m.get("id")) == assigned] if assigned else []
        waited = ""
        deadline = session.get("sla_deadline")
        if deadline:
            try:
                dt = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
                waited = f" (เลยกำหนดตอบ {minutes} นาที)" if minutes > 0 else ""
            except ValueError:
                waited = ""
        text = f"⏰ ลูกค้า {_shown(session)} ยังไม่ได้รับคำตอบ{waited}\nตอบได้ที่ หน้าจอ > แชทลูกค้า"
        text_en = (
            f"⏰ Customer {_shown(session)} is still waiting for an answer"
            + (waited.replace("เลยกำหนดตอบ", "reply overdue by").replace("นาที", "min") if waited else "")
            + "\nAnswer under home > Customer chats"
        )
        escalated += await _tell(
            client, license_id=license_id, members=owner or agents, text=text, text_en=text_en,
            type="sla_warning", session_id=str(session.get("id")), language="th",
        )
    timed_out = 0
    for session in result.get("timed_out") or []:
        license_id = str(session.get("license_id"))
        shop = await company_name(client, license_id)
        await _push_customer(
            client, chann_uid=str(session.get("customer_chann_uid") or ""),
            text=f"💬 การสนทนากับ {shop} ปิดอัตโนมัติเพราะไม่มีข้อความสักพัก พิมพ์ \"คุยกับร้าน\" ได้อีกเมื่อต้องการ",
            text_en=f"💬 Your conversation with {shop} closed after a quiet while. Type \"talk to the shop\" any time.",
        )
        timed_out += 1
    return {"escalated": escalated, "timed_out": timed_out}
