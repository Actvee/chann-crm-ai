"""Phase 9 — deal codes become per-tenant.

Revision ID: 0012_deal_code_per_license
Revises: 0011_customer_code

An owner-approved departure from the Master Spec, which marks deal_id
plainly `UNIQUE NOT NULL` while giving quotes.quote_id an explicit
"แยกต่อบริษัท" qualifier. See the Deal model's docstring for the full
reasoning; in short, global numbering gives a newly registered tenant a
first deal called something like D-2026-0847, which looks broken to that
tenant and discloses platform-wide volume.

THIS RENUMBERS EXISTING DEALS. A code someone typed into a chat or read
out to a customer before this migration will not resolve afterwards. That
is acceptable only because this runs while the platform is still on DEV
with a handful of deals; the same change after real use would need a
lookup table of old codes instead.

Renumbering preserves creation order within each tenant, so the oldest
deal in every tenant becomes 0001 and relative order is unchanged.
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_deal_code_per_license"
down_revision = "0011_customer_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the global constraint FIRST. Renumbering with it still in place
    # would collide the moment two tenants both wanted D-2026-0001, and the
    # UPDATE is a single statement so there is no intermediate state to
    # sequence around.
    op.drop_constraint("deals_deal_id_key", "deals", type_="unique")

    op.execute(
        """
        UPDATE deals AS d
        SET deal_id = numbered.code
        FROM (
            SELECT
                id,
                'D-' || to_char(created_at, 'YYYY') || '-' ||
                lpad(
                    row_number() OVER (
                        PARTITION BY license_id, to_char(created_at, 'YYYY')
                        ORDER BY created_at, id
                    )::text,
                    4, '0'
                ) AS code
            FROM deals
        ) AS numbered
        WHERE d.id = numbered.id
        """
    )

    op.create_unique_constraint(
        "uq_deals_license_deal_id", "deals", ["license_id", "deal_id"],
    )
    op.create_index("ix_deals_deal_id", "deals", ["deal_id"])


def downgrade() -> None:
    """Reverses the schema, not the numbering.

    Going back to a global unique constraint requires codes that are
    globally distinct, and the per-tenant codes this migration created are
    deliberately not. Rather than invent a renumbering that would change
    the codes a second time, downgrade refuses: restoring from a backup is
    the honest answer if this ever has to be undone.
    """
    raise NotImplementedError(
        "0012 cannot be reversed automatically: per-tenant deal codes are not "
        "globally unique, so the original constraint cannot be restored "
        "without renumbering every deal again. Restore from a backup instead."
    )
