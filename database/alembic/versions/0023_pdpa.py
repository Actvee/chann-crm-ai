"""Phase 16.5 — PDPA data rights: data_subject_requests, consent and
anonymisation marks on chann_identities, two new audit verbs.

Master Spec 16.5.3. Erasure is anonymisation, never a delete — every row
stays for the FKs that point at it; only the words that identify a
person are replaced.

Revision ID: 0023_pdpa
Revises: 0022_chat_sessions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_pdpa"
down_revision = "0022_chat_sessions"
branch_labels = None
depends_on = None

# 0017's list, plus the two verbs this phase writes. Defined here again
# because the check constraint is recreated in full (tests keep this in
# step with the repository that writes the verbs).
ALLOWED = (
    "create", "update", "delete", "assign", "transfer", "cross_tenant_lookup",
    "link_document", "upsert", "status", "remove_product", "claim", "reject",
    "check_in", "check_out",
    "pdpa_erasure", "pdpa_export",
)


def upgrade() -> None:
    op.add_column("chann_identities", sa.Column("consent_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chann_identities", sa.Column("consent_version", sa.String(32), nullable=True))
    op.add_column("chann_identities", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "data_subject_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chann_uid", sa.String(32),
                  sa.ForeignKey("chann_identities.chann_uid", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_type", sa.String(32), nullable=False),  # erasure | export | consent_withdraw
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_via", sa.String(32), nullable=False),  # chat | liff | platform_admin
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("platform_admins.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rejection_reason", sa.String(512), nullable=True),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_subject_requests_chann_uid", "data_subject_requests", ["chann_uid"])
    op.create_index("ix_data_subject_requests_status", "data_subject_requests", ["status"])

    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action", "audit_log",
        "action IN (" + ", ".join(f"'{a}'" for a in ALLOWED) + ")",
    )


def downgrade() -> None:
    raise NotImplementedError("PDPA columns and requests are kept; anonymised rows cannot be restored")
