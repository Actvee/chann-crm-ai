"""Let the audit log record the actions the product actually performs.

Revision ID: 0017_audit_actions
Revises: 0016_service_reports

`ck_audit_log_action` has allowed six verbs since Phase 3, when six were
all there were. Every phase since has written actions outside that list —
link_document, claim, check_in, check_out, reject, remove_product, status,
upsert — and because the audit write shares a transaction with the change
it records, the constraint violation rolled back BOTH.

That is how issuing a quote appeared to fail: the PDF rendered, uploaded
to GCS and got its generated_documents row, then the audit entry for
linking it to the quote was rejected, the transaction unwound, and the
quote was left with no document. The file existed and was unreachable,
and the salesperson was told it had not been issued.

The lesson worth keeping is the ordering, not the list: a constraint that
guards a WRITE and lives in the same transaction as it will take the
write down with it. The set is widened rather than dropped because an
open action column would let a typo become a permanent, silent audit
category — but it is now defined once, next to the code that writes it,
so adding a verb without adding it here fails a test rather than
production.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_audit_actions"
down_revision = "0016_service_reports"
branch_labels = None
depends_on = None

# Kept in step with chann_data.audit_actions.AUDIT_ACTIONS by a test.
ALLOWED = (
    "create",
    "update",
    "delete",
    "assign",
    "transfer",
    "cross_tenant_lookup",
    # Phase 10
    "link_document",
    "upsert",
    "status",
    "remove_product",
    # Phase 12
    "claim",
    "reject",
    # Phase 13
    "check_in",
    "check_out",
)


def upgrade() -> None:
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_action",
        "audit_log",
        "action IN (" + ", ".join(f"'{a}'" for a in ALLOWED) + ")",
    )


def downgrade() -> None:
    # Rows written with the newer verbs would violate the old constraint,
    # so this deletes nothing and simply refuses: silently dropping audit
    # rows to satisfy a downgrade is the one thing an append-only log must
    # never do.
    raise NotImplementedError(
        "cannot narrow ck_audit_log_action without deleting audit rows"
    )
