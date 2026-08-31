"""Tenant-scoped audit log write/read — Phase 3 (Master Spec 3.3-3.4)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit_actions import AUDIT_ACTIONS
from ..models import AuditLog


class AuditRepository:
    """Single write path so every caller's audit row looks the same shape.

    Deliberately NOT tenant-scoped through TenantScope like the Phase 2
    repositories: a cross-tenant attempt is exactly the kind of event this
    table exists to record, so this repository must be reachable even when
    the caller's action was refused before reaching a normal tenant-scoped
    repository at all.
    """

    def __init__(self, session: Session):
        self._s = session

    def write(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        actor_type: str,
        action: str,
        license_id: uuid.UUID | None = None,
        actor_id: str | None = None,
        field_changes: dict | None = None,
        ai_reasoning: str | None = None,
        cross_tenant: bool = False,
    ) -> AuditLog:
        # Checked here rather than left to the database. The CHECK
        # constraint fires at flush, inside the caller's transaction, and
        # takes the change being audited down with it — which is how
        # issuing a quote came to render a PDF, store it, record it, and
        # then silently unwind all three because "link_document" was not
        # in the allowed list.
        #
        # Raising the same class the repositories already raise means the
        # caller's error handling reports it as a refusal it can name,
        # instead of a CheckViolation surfacing as an opaque 409.
        if action not in AUDIT_ACTIONS:
            raise ValueError(
                f"unknown audit action {action!r} — add it to "
                "chann_data.audit_actions.AUDIT_ACTIONS and to a migration "
                "widening ck_audit_log_action, or the write that triggers it "
                "will roll back"
            )

        row = AuditLog(
            id=uuid.uuid4(),
            license_id=license_id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            field_changes=field_changes,
            # Only ever carried for an AI actor (DB check constraint also
            # enforces this) — Phase 4 is what will actually populate it;
            # every call site today passes None.
            ai_reasoning=ai_reasoning,
            cross_tenant=cross_tenant,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def list_for_license(
        self,
        license_id: uuid.UUID,
        *,
        entity_type: str | None = None,
        actor_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        limit = max(1, min(limit, 500))
        stmt = select(AuditLog).where(AuditLog.license_id == license_id)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if actor_type is not None:
            stmt = stmt.where(AuditLog.actor_type == actor_type)
        if since is not None:
            stmt = stmt.where(AuditLog.created_at >= since)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        return list(self._s.execute(stmt).scalars())


def diff_fields(before: dict, after: dict, *, ignore: set[str] = frozenset({"updated_at"})) -> dict:
    """Build the {field: {old, new}} shape Master Spec 3.3 requires.

    Callers pass plain dicts of the fields they actually intend to audit
    (deliberately not a generic ORM-object introspector — a wrong or
    accidental field showing up in an audit row is a worse failure than a
    caller having to list which fields matter). Only returns fields whose
    value actually changed.
    """
    changed = {}
    for key in before.keys() | after.keys():
        if key in ignore:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            changed[key] = {"old": old, "new": new}
    return changed
