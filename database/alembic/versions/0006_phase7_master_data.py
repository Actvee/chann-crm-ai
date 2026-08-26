"""Phase 7 — master data: products, sales groups, technician teams.

Revision ID: 0006_phase7_master_data
Revises: 0005_phase65_tenant_registration
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_phase7_master_data"
down_revision = "0005_phase65_tenant_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("sku", sa.String(64)),
        sa.Column("category", sa.String(64)),
        # NUMERIC not FLOAT — prices must not drift between machines.
        sa.Column("unit_price", sa.Numeric(18, 2)),
        sa.Column("description", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        # Composite only. Spec 7.3 also marks product_id globally UNIQUE, but
        # that contradicts 7.5's test_multi_tenant_product, which requires two
        # tenants to be able to use the same product_id.
        sa.UniqueConstraint("license_id", "product_id", name="uq_products_license_product"),
    )
    op.create_index("ix_products_license", "products", ["license_id"])
    op.create_index("ix_products_category", "products", ["category"])
    # Listing a catalogue means "everything not archived"; keeping the index
    # partial stops archived history from slowing that down forever.
    op.create_index(
        "ix_products_active",
        "products",
        ["license_id", "product_name"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "sales_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("license_id", "group_name", name="uq_sales_groups_name"),
    )
    op.create_index("ix_sales_groups_license", "sales_groups", ["license_id"])

    op.create_table(
        "sales_group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        # Deleting a group removes its membership rows...
        sa.ForeignKeyConstraint(["group_id"], ["sales_groups.id"], ondelete="CASCADE"),
        # ...but never the people. Spec 7.5: "ลบกลุ่ม → ไม่ลบ member".
        sa.ForeignKeyConstraint(["member_id"], ["license_members.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("group_id", "member_id", name="uq_sales_group_member"),
    )
    op.create_index("ix_sales_group_members_group", "sales_group_members", ["group_id"])
    op.create_index("ix_sales_group_members_member", "sales_group_members", ["member_id"])

    op.create_table(
        "technician_teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("license_id", "team_name", name="uq_technician_teams_name"),
    )
    op.create_index("ix_technician_teams_license", "technician_teams", ["license_id"])

    op.create_table(
        "technician_team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Deliberately not unique per team: 7.5 requires a team to be able to
        # have more than one lead.
        sa.Column("is_lead", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["team_id"], ["technician_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["license_members.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("team_id", "member_id", name="uq_technician_team_member"),
    )
    op.create_index("ix_technician_team_members_team", "technician_team_members", ["team_id"])
    op.create_index(
        "ix_technician_team_members_member", "technician_team_members", ["member_id"]
    )


def downgrade() -> None:
    op.drop_table("technician_team_members")
    op.drop_table("technician_teams")
    op.drop_table("sales_group_members")
    op.drop_table("sales_groups")
    op.drop_table("products")
