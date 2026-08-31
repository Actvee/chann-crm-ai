"""Phase 6 data access — message-entity map, notifications, follow-ups.

Everything here is tenant-scoped through TenantScope. The one case that reads
across a tenant boundary is the message-entity lookup, and it does not: it
resolves by message_id (globally unique) and then refuses the row if it belongs
to a different license, so a leaked or guessed LINE message ID cannot pull a
record out of someone else's tenant.
"""
from __future__ import annotations

import uuid
from datetime import date, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import FollowUp, LineMessageEntityMap, Note, Notification
from .tenant_scope import TenantScope

FOLLOW_UP_STATUSES = frozenset({"pending", "completed", "cancelled"})


class Phase6Conflict(RuntimeError):
    """A request that is well-formed but not allowed in the current state."""


class Phase6NotFound(LookupError):
    """The row does not exist, or does not belong to this tenant."""


class MessageEntityMapRepository:
    def __init__(self, session: Session):
        self._s = session

    def record(
        self,
        scope: TenantScope,
        *,
        message_id: str,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> LineMessageEntityMap:
        """Idempotent on message_id.

        LINE can redeliver a webhook, so the same message_id can legitimately
        arrive twice. Re-recording the identical mapping is a no-op; the same
        message_id claiming a *different* entity is a real conflict and is
        refused rather than silently overwritten.
        """
        existing = self._s.execute(
            select(LineMessageEntityMap).where(
                LineMessageEntityMap.message_id == message_id
            )
        ).scalar_one_or_none()

        if existing is not None:
            if (
                existing.license_id != scope.license_id
                or existing.entity_type != entity_type
                or existing.entity_id != entity_id
            ):
                raise Phase6Conflict(
                    "message_id is already mapped to a different entity"
                )
            return existing

        row = LineMessageEntityMap(
            id=uuid.uuid4(),
            message_id=message_id,
            entity_type=entity_type,
            entity_id=entity_id,
            license_id=scope.license_id,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get(self, scope: TenantScope, message_id: str) -> LineMessageEntityMap | None:
        """Return the mapping only if this tenant owns it.

        Deliberately returns None rather than raising for a foreign mapping:
        the caller's correct behaviour is identical either way ("ไม่พบข้อความ
        ต้นฉบับ", spec 6.5), and distinguishing the two would tell an outsider
        that some other tenant holds that message ID.
        """
        row = self._s.execute(
            select(LineMessageEntityMap).where(
                LineMessageEntityMap.message_id == message_id
            )
        ).scalar_one_or_none()
        if row is None or row.license_id != scope.license_id:
            return None
        return row


class NotificationRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self,
        scope: TenantScope | None,
        *,
        target_chann_uid: str,
        type: str,
        message: str,
        message_en: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        delivery_line: bool = True,
        delivery_dashboard: bool = True,
    ) -> Notification:
        """scope may be None for a platform-level notification (spec 6.3)."""
        row = Notification(
            id=uuid.uuid4(),
            license_id=scope.license_id if scope is not None else None,
            target_chann_uid=target_chann_uid,
            type=type,
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            message_en=message_en,
            delivery_line=delivery_line,
            delivery_dashboard=delivery_dashboard,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def list_for_member(
        self,
        scope: TenantScope,
        chann_uid: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:
        limit = max(1, min(limit, 200))
        stmt = select(Notification).where(
            Notification.license_id == scope.license_id,
            Notification.target_chann_uid == chann_uid,
            # Dashboard-suppressed notifications went out over LINE only and
            # must not reappear in the dashboard list.
            Notification.delivery_dashboard.is_(True),
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        return list(self._s.execute(stmt).scalars())

    def unread_count(self, scope: TenantScope, chann_uid: str) -> int:
        return int(
            self._s.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.license_id == scope.license_id,
                    Notification.target_chann_uid == chann_uid,
                    Notification.delivery_dashboard.is_(True),
                    Notification.read_at.is_(None),
                )
            ).scalar_one()
        )

    def mark_read(
        self, scope: TenantScope, notification_id: uuid.UUID, chann_uid: str
    ) -> Notification:
        """Only the target may mark their own notification read.

        Checked explicitly rather than trusting the caller: 'member.manage'
        does not imply the right to clear someone else's badge.
        """
        row = self._s.execute(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.license_id == scope.license_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if row is None or row.target_chann_uid != chann_uid:
            raise Phase6NotFound("notification not found for this member")

        # Idempotent: re-reading keeps the original timestamp, so "when did
        # they first see this" stays answerable.
        if row.read_at is None:
            row.read_at = func.now()
            self._s.flush()
        return row


class FollowUpRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self,
        scope: TenantScope,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        due_date: date,
        due_time: time | None = None,
        owner_member_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> FollowUp:
        row = FollowUp(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            entity_type=entity_type,
            entity_id=entity_id,
            due_date=due_date,
            due_time=due_time,
            status="pending",
            owner_member_id=owner_member_id,
            notes=notes,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get(self, scope: TenantScope, follow_up_id: uuid.UUID) -> FollowUp | None:
        return self._s.execute(
            select(FollowUp).where(
                FollowUp.id == follow_up_id,
                FollowUp.license_id == scope.license_id,
            )
        ).scalar_one_or_none()

    def list_for_license(
        self,
        scope: TenantScope,
        *,
        status: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[FollowUp]:
        limit = max(1, min(limit, 500))
        stmt = select(FollowUp).where(FollowUp.license_id == scope.license_id)
        if status is not None:
            stmt = stmt.where(FollowUp.status == status)
        if entity_type is not None:
            stmt = stmt.where(FollowUp.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(FollowUp.entity_id == entity_id)
        stmt = stmt.order_by(FollowUp.due_date.asc()).limit(limit)
        return list(self._s.execute(stmt).scalars())

    def due_within(
        self, scope: TenantScope, *, days: int = 1, today: date | None = None
    ) -> list[FollowUp]:
        """Pending follow-ups due within `days` — spec 6.7's 1-day warning.

        `today` is injectable so the reminder sweep can be tested without
        freezing the clock. Overdue rows are included on purpose: something
        that slipped past its due date needs chasing more than something due
        tomorrow, and excluding them would silently drop them forever.
        """
        anchor = today or date.today()
        cutoff = anchor + timedelta(days=days)
        stmt = (
            select(FollowUp)
            .where(
                FollowUp.license_id == scope.license_id,
                FollowUp.status == "pending",
                FollowUp.due_date <= cutoff,
            )
            .order_by(FollowUp.due_date.asc())
        )
        return list(self._s.execute(stmt).scalars())

    def set_status(
        self, scope: TenantScope, follow_up_id: uuid.UUID, status: str
    ) -> FollowUp:
        if status not in FOLLOW_UP_STATUSES:
            raise Phase6Conflict(f"unknown follow-up status '{status}'")

        row = self._s.execute(
            select(FollowUp)
            .where(
                FollowUp.id == follow_up_id,
                FollowUp.license_id == scope.license_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise Phase6NotFound("follow-up not found")

        # A settled follow-up stays settled. Re-completing is a harmless no-op,
        # but flipping completed -> cancelled (or back) would rewrite history
        # that other things may already have reported on.
        if row.status != "pending" and row.status != status:
            raise Phase6Conflict(
                f"follow-up is already {row.status} and cannot become {status}"
            )

        row.status = status
        self._s.flush()
        return row


class NoteRepository:
    """Master Spec 6.3 — dated, attributed notes against any entity.

    Separate from the `notes` TEXT columns that live on customers, deals and
    follow-ups. Those hold one overwritable blob each and stay as they are;
    this is the append-only record that can answer "what did we agree with
    this customer in March", which the columns never could.
    """

    def __init__(self, session: Session):
        self._s = session

    def create(
        self,
        scope: TenantScope,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        body: str,
        author_chann_uid: str | None = None,
    ) -> Note:
        body = (body or "").strip()
        if not body:
            # An empty note is never what someone meant, and a blank row in a
            # history is worse than a refusal they can act on.
            raise Phase6Conflict("a note needs some text")
        row = Note(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            entity_type=entity_type,
            entity_id=entity_id,
            body=body,
            author_chann_uid=author_chann_uid,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def list_for_entity(
        self, scope: TenantScope, *, entity_type: str, entity_id: uuid.UUID,
        limit: int = 50,
    ) -> list[Note]:
        limit = max(1, min(limit, 200))
        return list(
            self._s.execute(
                select(Note)
                .where(
                    Note.license_id == scope.license_id,
                    Note.entity_type == entity_type,
                    Note.entity_id == entity_id,
                )
                # Newest first: the last thing said is the thing being looked
                # for far more often than the first.
                .order_by(Note.created_at.desc())
                .limit(limit)
            ).scalars()
        )

    def get(self, scope: TenantScope, note_id: uuid.UUID) -> Note | None:
        return self._s.execute(
            select(Note).where(Note.id == note_id, Note.license_id == scope.license_id)
        ).scalar_one_or_none()

    def update(self, scope: TenantScope, note_id: uuid.UUID, *, body: str) -> Note:
        row = self.get(scope, note_id)
        if row is None:
            raise Phase6NotFound("note not found")
        body = (body or "").strip()
        if not body:
            raise Phase6Conflict("a note needs some text")
        row.body = body
        self._s.flush()
        return row

    def delete(self, scope: TenantScope, note_id: uuid.UUID) -> Note:
        row = self.get(scope, note_id)
        if row is None:
            raise Phase6NotFound("note not found")
        self._s.delete(row)
        self._s.flush()
        return row
