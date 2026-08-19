"""Phase 3 — audit log.

Revision ID: 0003_phase3_audit_log
Revises: 0002_phase2_permissions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_phase3_audit_log"
down_revision = "0002_phase2_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable: a refused cross-tenant attempt still needs a row even
        # when it doesn't cleanly belong to one tenant's own log.
        sa.Column("license_id", postgresql.UUID(as_uuid=True)),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("field_changes", postgresql.JSONB()),
        sa.Column("ai_reasoning", sa.Text()),
        sa.Column("cross_tenant", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "actor_type IN ('user', 'ai', 'system', 'platform_admin')",
            name="ck_audit_log_actor_type",
        ),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete', 'assign', 'transfer', 'cross_tenant_lookup')",
            name="ck_audit_log_action",
        ),
        # Master Spec 3.3: "เก็บไว้ตลอดไป ไม่ลบ" — append-only, enforced at
        # the DB level too, not just left as an application-layer promise.
        sa.CheckConstraint(
            "ai_reasoning IS NULL OR actor_type = 'ai'",
            name="ck_audit_log_ai_reasoning_requires_ai_actor",
        ),
    )
    # (license_id, created_at DESC) — the tenant-scoped log view.
    op.create_index(
        "ix_audit_log_license_created",
        "audit_log",
        ["license_id", sa.text("created_at DESC")],
    )
    # (entity_type, entity_id) — history of one specific record.
    op.create_index(
        "ix_audit_log_entity",
        "audit_log",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_table("audit_log")
