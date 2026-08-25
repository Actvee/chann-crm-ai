"""Phase 6.5 — tenant registration.

Revision ID: 0005_phase65_tenant_registration
Revises: 0004_phase6_chat_foundation
"""
import secrets

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_phase65_tenant_registration"
down_revision = "0004_phase6_chat_foundation"
branch_labels = None
depends_on = None

# No 0/O/1/I/L — customers type this into a chat by hand, often off a phone
# screen or read aloud over the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _code(n: int = 8) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def upgrade() -> None:
    # Added nullable, then backfilled, then made unique — an existing DEV
    # database already has licenses, and a NOT NULL UNIQUE column cannot be
    # added to a populated table in one step.
    op.add_column("licenses", sa.Column("company_code", sa.String(8)))
    op.add_column(
        "licenses",
        sa.Column("status", sa.String(32), server_default="trial", nullable=False),
    )
    op.add_column("licenses", sa.Column("trial_expires_at", sa.DateTime(timezone=True)))
    op.add_column("licenses", sa.Column("created_by_chann_uid", sa.String(32)))

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM licenses WHERE company_code IS NULL")).fetchall()
    used: set[str] = set()
    for (license_id,) in rows:
        while True:
            candidate = _code()
            if candidate in used:
                continue
            clash = conn.execute(
                sa.text("SELECT 1 FROM licenses WHERE company_code = :c"),
                {"c": candidate},
            ).first()
            if clash is None:
                break
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE licenses SET company_code = :c WHERE id = :i"),
            {"c": candidate, "i": license_id},
        )

    # Existing tenants predate trials and are treated as already active —
    # suspending a working DEV tenant on migrate would be a nasty surprise.
    conn.execute(sa.text("UPDATE licenses SET status = 'active' WHERE status = 'trial'"))

    op.create_unique_constraint("uq_licenses_company_code", "licenses", ["company_code"])
    op.create_check_constraint(
        "ck_licenses_status",
        "licenses",
        "status IN ('trial', 'active', 'suspended')",
    )
    op.create_index("ix_licenses_created_by", "licenses", ["created_by_chann_uid"])
    # The 1-LINE-1-company rule, enforced in the database so two concurrent
    # webhook deliveries cannot both win. Partial so the many NULLs (existing
    # tenants, admin-created ones) do not collide with each other.
    op.create_index(
        "ux_licenses_one_per_creator",
        "licenses",
        ["created_by_chann_uid"],
        unique=True,
        postgresql_where=sa.text("created_by_chann_uid IS NOT NULL"),
    )

    op.create_table(
        "license_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invite_code", sa.String(16), nullable=False, unique=True),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("max_uses", sa.Integer(), server_default="1", nullable=False),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_member_id", postgresql.UUID(as_uuid=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_member_id"], ["license_members.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("used_count <= max_uses", name="ck_license_invites_uses"),
        sa.CheckConstraint("max_uses > 0", name="ck_license_invites_max_uses_positive"),
    )
    # Redeem looks up only live invites; revoked and exhausted ones never need
    # to be scanned.
    op.create_index(
        "ix_license_invites_live",
        "license_invites",
        ["license_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "customer_license_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chann_uid", sa.String(32), nullable=False),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chann_uid"], ["chann_identities.chann_uid"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("chann_uid", "license_id", name="uq_customer_license_link"),
    )
    op.create_index(
        "ix_customer_license_links_uid", "customer_license_links", ["chann_uid"]
    )


def downgrade() -> None:
    op.drop_table("customer_license_links")
    op.drop_table("license_invites")
    op.drop_index("ux_licenses_one_per_creator", table_name="licenses")
    op.drop_index("ix_licenses_created_by", table_name="licenses")
    op.drop_constraint("ck_licenses_status", "licenses", type_="check")
    op.drop_constraint("uq_licenses_company_code", "licenses", type_="unique")
    op.drop_column("licenses", "created_by_chann_uid")
    op.drop_column("licenses", "trial_expires_at")
    op.drop_column("licenses", "status")
    op.drop_column("licenses", "company_code")
