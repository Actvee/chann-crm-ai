"""Customer rows with no name take the name their person registered.

Owner, 21 ก.ย. 2569: CHN-S-000002 of "dev company one" showed as a bare
id on the shop's chat page. The customer had typed their name into their
own profile; the shop's record of them — attached by phone when they
linked, or created before the name existed — had first_name and
last_name empty, and nothing ever carried the name across. The code now
fills an empty name at link time and whenever the profile's name
changes; this migration does it once for the rows already there.

Only EMPTY names are touched: a name the shop typed is the shop's.

Revision ID: 0033_names_from_identity
Revises: 0032_company_open_hours
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_names_from_identity"
down_revision = "0032_company_open_hours"
branch_labels = None
depends_on = None

#: Shared with the test that proves the statement on a real database.
BACKFILL_SQL = """
UPDATE customers AS c
SET first_name = NULLIF(BTRIM(i.first_name), ''),
    last_name = NULLIF(BTRIM(i.last_name), '')
FROM chann_identities AS i
WHERE c.customer_chann_uid = i.chann_uid
  AND COALESCE(BTRIM(c.first_name), '') = ''
  AND COALESCE(BTRIM(c.last_name), '') = ''
  AND (COALESCE(BTRIM(i.first_name), '') <> '' OR COALESCE(BTRIM(i.last_name), '') <> '')
"""


def upgrade() -> None:
    op.execute(sa.text(BACKFILL_SQL))


def downgrade() -> None:
    # Data only; the names filled in are indistinguishable from typed
    # ones afterwards, and there is nothing to put back.
    pass
