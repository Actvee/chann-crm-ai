"""Phase 8 — profile fields on chann_identities.

Revision ID: 0007_phase8_profiles
Revises: 0006_phase7_master_data
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_phase8_profiles"
down_revision = "0006_phase7_master_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chann_identities", sa.Column("first_name", sa.String(255)))
    op.add_column("chann_identities", sa.Column("last_name", sa.String(255)))
    op.add_column("chann_identities", sa.Column("phone", sa.String(32)))
    op.add_column("chann_identities", sa.Column("email", sa.String(255)))
    op.add_column("chann_identities", sa.Column("address", sa.Text()))
    op.add_column(
        "chann_identities",
        sa.Column("registered", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("chann_identities", sa.Column("registered_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("chann_identities", "registered_at")
    op.drop_column("chann_identities", "registered")
    op.drop_column("chann_identities", "address")
    op.drop_column("chann_identities", "email")
    op.drop_column("chann_identities", "phone")
    op.drop_column("chann_identities", "last_name")
    op.drop_column("chann_identities", "first_name")
