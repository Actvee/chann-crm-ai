"""Phase 15 — live chat sessions and their messages.

Master Spec 15.4: the customer opens a conversation, every Sales/CS
person is told, whoever answers first owns it, the customer reads the
answers in their LINE, and the conversation closes itself when nobody
has spoken for a while. The SLA is on the SHOP: a deadline exists only
while the customer's message is the latest one.

Everything here is tenant-scoped except the two sweeps, which are the
platform's own clock ticking over every shop at once (same shape as the
reminder sweep) — they return rows, and the Application tier decides
who to tell.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ChatMessage, ChatSession
from .tenant_scope import TenantScope

LIVE_STATUSES = ("open", "assigned")
SENDER_TYPES = ("customer", "agent", "ai", "system")


class ChatSessionNotFound(Exception):
    pass


class ChatSessionConflict(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatSessionRepository:
    def __init__(self, session: Session):
        self._s = session

    # --------------------------------------------------------- sessions

    def live_for_customer(self, scope: TenantScope, customer_chann_uid: str) -> ChatSession | None:
        return self._s.execute(
            select(ChatSession).where(
                ChatSession.license_id == scope.license_id,
                ChatSession.customer_chann_uid == customer_chann_uid,
                ChatSession.status.in_(LIVE_STATUSES),
            ).order_by(ChatSession.created_at.desc())
        ).scalars().first()

    def open_session(
        self, scope: TenantScope, *, customer_chann_uid: str,
        product_id: uuid.UUID | None = None, sla_minutes: int = 30, timeout_minutes: int = 60,
    ) -> tuple[ChatSession, bool]:
        """The customer's live conversation with this shop — the one that
        is running, or a new one. Returns (session, created)."""
        existing = self.live_for_customer(scope, customer_chann_uid)
        if existing is not None:
            existing.timeout_at = _now() + timedelta(minutes=max(1, timeout_minutes))
            if product_id and not existing.product_id:
                existing.product_id = product_id
            self._s.flush()
            return existing, False
        # The same person coming back (same LINE identity) continues the
        # conversation they had — the shop sees one thread with its whole
        # history, not a new empty one each time (owner, 4 Sep). Reopening
        # counts as "created" for the caller: the agents are told again.
        previous = self._s.execute(
            select(ChatSession).where(
                ChatSession.license_id == scope.license_id,
                ChatSession.customer_chann_uid == customer_chann_uid,
            ).order_by(ChatSession.updated_at.desc())
        ).scalars().first()
        if previous is not None:
            previous.status = "open" if previous.assigned_to is None else "assigned"
            previous.closed_at = None
            previous.escalated_at = None
            previous.sla_deadline = _now() + timedelta(minutes=max(1, sla_minutes))
            previous.timeout_at = _now() + timedelta(minutes=max(1, timeout_minutes))
            if product_id:
                previous.product_id = product_id
            previous.updated_at = _now()
            self._s.flush()
            return previous, True
        row = ChatSession(
            license_id=scope.license_id, customer_chann_uid=customer_chann_uid,
            status="open", product_id=product_id,
            sla_deadline=_now() + timedelta(minutes=max(1, sla_minutes)),
            timeout_at=_now() + timedelta(minutes=max(1, timeout_minutes)),
        )
        self._s.add(row)
        self._s.flush()
        return row, True

    def get(self, scope: TenantScope, session_id: uuid.UUID) -> ChatSession | None:
        return self._s.execute(
            select(ChatSession).where(
                ChatSession.id == session_id, ChatSession.license_id == scope.license_id,
            )
        ).scalars().first()

    def require(self, scope: TenantScope, session_id: uuid.UUID) -> ChatSession:
        row = self.get(scope, session_id)
        if row is None:
            raise ChatSessionNotFound("chat session not found in this tenant")
        return row

    def list_for_license(
        self, scope: TenantScope, *, status: str | None = None,
        customer_chann_uid: str | None = None, limit: int = 100,
    ) -> list[ChatSession]:
        query = select(ChatSession).where(ChatSession.license_id == scope.license_id)
        if status == "live":
            query = query.where(ChatSession.status.in_(LIVE_STATUSES))
        elif status:
            query = query.where(ChatSession.status == status)
        if customer_chann_uid:
            query = query.where(ChatSession.customer_chann_uid == customer_chann_uid)
        query = query.order_by(ChatSession.updated_at.desc()).limit(max(1, min(limit, 500)))
        return list(self._s.execute(query).scalars())

    def assign(self, scope: TenantScope, session_id: uuid.UUID, member_id: uuid.UUID) -> ChatSession:
        row = self.require(scope, session_id)
        if row.status not in LIVE_STATUSES:
            raise ChatSessionConflict("conversation is closed")
        row.assigned_to = member_id
        row.status = "assigned"
        self._s.flush()
        return row

    def close(self, scope: TenantScope, session_id: uuid.UUID, *, status: str = "closed") -> ChatSession:
        row = self.require(scope, session_id)
        if row.status in LIVE_STATUSES:
            row.status = status
            row.closed_at = _now()
            row.sla_deadline = None
            row.timeout_at = None
            self._s.flush()
        return row

    # --------------------------------------------------------- messages

    def add_message(
        self, scope: TenantScope, session_id: uuid.UUID, *,
        sender_type: str, content: str, sender_chann_uid: str | None = None,
        content_en: str | None = None, sla_minutes: int = 30, timeout_minutes: int = 60,
    ) -> ChatMessage:
        if sender_type not in SENDER_TYPES:
            raise ChatSessionConflict(f"unknown sender type: {sender_type!r}")
        content = (content or "").strip()
        if not content:
            raise ChatSessionConflict("empty message")
        row = self.require(scope, session_id)
        # The customer speaks only into a live conversation; the shop may
        # answer a parked or closed one — that answer is what invites the
        # customer back (owner, 4 Sep), so it must be kept.
        if sender_type == "customer" and row.status not in LIVE_STATUSES:
            raise ChatSessionConflict("conversation is closed")
        message = ChatMessage(
            session_id=row.id, license_id=scope.license_id, sender_type=sender_type,
            sender_chann_uid=sender_chann_uid, content=content, content_en=content_en,
        )
        self._s.add(message)
        now = _now()
        if row.status not in LIVE_STATUSES:
            row.updated_at = now
            self._s.flush()
            return message
        row.timeout_at = now + timedelta(minutes=max(1, timeout_minutes))
        if sender_type == "customer":
            # The shop's clock starts when the customer is left waiting —
            # and only then; a second customer message does not push the
            # deadline back.
            if row.sla_deadline is None:
                row.sla_deadline = now + timedelta(minutes=max(1, sla_minutes))
        elif sender_type == "agent":
            # Answered: no deadline until the customer speaks again (15.4
            # "SLA reset"), and a fresh escalation next time it slips.
            row.sla_deadline = None
            row.escalated_at = None
            if row.status == "open":
                row.status = "assigned"
        row.updated_at = now
        self._s.flush()
        return message

    def list_messages(
        self, scope: TenantScope, session_id: uuid.UUID, *, since: datetime | None = None,
        limit: int = 200,
    ) -> list[ChatMessage]:
        self.require(scope, session_id)
        query = select(ChatMessage).where(
            ChatMessage.license_id == scope.license_id, ChatMessage.session_id == session_id,
        )
        if since is not None:
            query = query.where(ChatMessage.created_at > since)
        query = query.order_by(ChatMessage.created_at.asc()).limit(max(1, min(limit, 1000)))
        return list(self._s.execute(query).scalars())

    def mark_read(self, scope: TenantScope, session_id: uuid.UUID, *, reader: str) -> int:
        """`reader` is "agent" (the shop read the customer's lines) or
        "customer" (the customer read the shop's). Returns how many."""
        self.require(scope, session_id)
        senders = ("customer",) if reader == "agent" else ("agent", "ai", "system")
        rows = list(self._s.execute(
            select(ChatMessage).where(
                ChatMessage.license_id == scope.license_id,
                ChatMessage.session_id == session_id,
                ChatMessage.sender_type.in_(senders),
                ChatMessage.is_read.is_(False),
            )
        ).scalars())
        for row in rows:
            row.is_read = True
        self._s.flush()
        return len(rows)

    def summaries(self, scope: TenantScope, session_ids: list[uuid.UUID]) -> dict:
        """Per session: the last line and how many customer lines the shop
        has not read — what a list of conversations needs to show."""
        if not session_ids:
            return {}
        unread = dict(self._s.execute(
            select(ChatMessage.session_id, func.count(ChatMessage.id)).where(
                ChatMessage.license_id == scope.license_id,
                ChatMessage.session_id.in_(session_ids),
                ChatMessage.sender_type == "customer",
                ChatMessage.is_read.is_(False),
            ).group_by(ChatMessage.session_id)
        ).all())
        out: dict = {}
        for session_id in session_ids:
            last = self._s.execute(
                select(ChatMessage).where(
                    ChatMessage.license_id == scope.license_id,
                    ChatMessage.session_id == session_id,
                ).order_by(ChatMessage.created_at.desc()).limit(1)
            ).scalars().first()
            out[session_id] = {
                "last_message": last.content if last else None,
                "last_sender_type": last.sender_type if last else None,
                "last_message_at": last.created_at if last else None,
                "unread_from_customer": int(unread.get(session_id, 0)),
            }
        return out

    # --------------------------------------------------------- sweeps (cross-tenant)

    def sla_overdue(self, *, now: datetime | None = None) -> list[ChatSession]:
        """Live conversations the shop has left past the deadline and not
        yet been told about. The caller marks them escalated."""
        now = now or _now()
        return list(self._s.execute(
            select(ChatSession).where(
                ChatSession.status.in_(LIVE_STATUSES),
                ChatSession.sla_deadline.is_not(None),
                ChatSession.sla_deadline < now,
                ChatSession.escalated_at.is_(None),
            ).order_by(ChatSession.sla_deadline.asc())
        ).scalars())

    def mark_escalated(self, row: ChatSession, *, now: datetime | None = None) -> None:
        row.escalated_at = now or _now()
        self._s.flush()

    def time_out(self, *, now: datetime | None = None) -> list[ChatSession]:
        """Close every live conversation nobody has touched past its
        timeout. Returns the ones closed, so the customer can be told."""
        now = now or _now()
        rows = list(self._s.execute(
            select(ChatSession).where(
                ChatSession.status.in_(LIVE_STATUSES),
                ChatSession.timeout_at.is_not(None),
                ChatSession.timeout_at < now,
            )
        ).scalars())
        for row in rows:
            row.status = "timeout"
            row.closed_at = now
            row.sla_deadline = None
            row.timeout_at = None
        self._s.flush()
        return rows
