"""A licence has a plan (round 21D).

Owner, 24 ก.ย. 2569: "ตัวควบคุมฟีเจอร์ตาม plan" — four plans, Starter · Pro ·
Enterprise · Enterprise Plus, from the sales table in
docs/superpowers/specs/2026-09-24-sales-plans-source.html.

Every existing shop becomes Pro in one statement (the server default), then
the backfill moves UP so nothing a shop uses today disappears (owner
decision Q1): an unrevoked API key, an active approval chain of more than
one step, or more than 15 active people → Enterprise; more than 50 →
Enterprise Plus. "Active people" is distinct chann_uid with an active
license_members row, any channel — a removed row does not count.

Owner decision Q2 (same day): no shop may be over its plan's user limit.
The rules above are built so that never happens here; `plan_moves` checks
it anyway and moves any shop that would be over to the smallest plan that
fits, printing the licence. The deploy log is the record of the counts.

The plan codes and limits are frozen here, not imported: a migration runs
the code of its own day (0017 set the precedent). A unit test keeps them
equal to chann_data.plans.

Revision ID: 0039_license_plan
Revises: 0038_deal_closed_at
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_license_plan"
down_revision = "0038_deal_closed_at"
branch_labels = None
depends_on = None

PLAN_CODES = ("starter", "pro", "enterprise", "enterprise_plus")
MEMBER_LIMITS = (("starter", 5), ("pro", 15), ("enterprise", 50), ("enterprise_plus", None))

_ACTIVE_PEOPLE = (
    "SELECT license_id, count(DISTINCT chann_uid) AS people FROM license_members "
    "WHERE status = 'active' GROUP BY license_id"
)
# Guarded with CASE, not AND: SQL does not promise to short-circuit, and
# jsonb_array_length raises on anything that is not an array.
_STEPS = (
    "CASE WHEN jsonb_typeof(rules_json->'steps') = 'array' "
    "THEN jsonb_array_length(rules_json->'steps') ELSE 0 END"
)


def plan_moves(rows) -> list[tuple]:
    """(license_id, plan_code, people) rows → (license_id, plan_code, people,
    target) for every licence over its plan's limit. Pure, so it is tested
    without a database."""
    limits = dict(MEMBER_LIMITS)
    order = [code for code, _ in MEMBER_LIMITS]
    moves = []
    for license_id, code, people in rows:
        limit = limits.get(code)
        if limit is None or people <= limit:
            continue
        target = next(c for c in order[order.index(code):] if limits[c] is None or people <= limits[c])
        moves.append((license_id, code, people, target))
    return moves


def upgrade() -> None:
    op.add_column(
        "licenses",
        sa.Column("plan_code", sa.String(24), nullable=False, server_default="pro"),
    )
    op.create_check_constraint(
        "ck_licenses_plan_code", "licenses",
        "plan_code IN ('starter', 'pro', 'enterprise', 'enterprise_plus')",
    )
    op.execute(
        "UPDATE licenses SET plan_code = 'enterprise' WHERE "
        "id IN (SELECT license_id FROM api_keys WHERE revoked_at IS NULL) "
        f"OR id IN (SELECT license_id FROM approval_workflows WHERE is_active AND {_STEPS} > 1) "
        f"OR id IN (SELECT license_id FROM ({_ACTIVE_PEOPLE}) AS c WHERE c.people > 15)"
    )
    op.execute(
        "UPDATE licenses SET plan_code = 'enterprise_plus' WHERE "
        f"id IN (SELECT license_id FROM ({_ACTIVE_PEOPLE}) AS c WHERE c.people > 50)"
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        f"SELECT l.id, l.plan_code, c.people FROM licenses l JOIN ({_ACTIVE_PEOPLE}) AS c ON c.license_id = l.id"
    )).all()
    for license_id, code, people, target in plan_moves(rows):
        print(f"0039_license_plan: plan_moves licence={license_id} plan={code} people={people} "
              f"over_limit target={target}", flush=True)
        conn.execute(sa.text("UPDATE licenses SET plan_code = :p WHERE id = :id"), {"p": target, "id": license_id})
    counts = dict(conn.execute(sa.text(
        "SELECT plan_code, count(*) FROM licenses GROUP BY plan_code"
    )).all())
    print("0039_license_plan: plan backfill: " + " ".join(
        f"{code}={counts.get(code, 0)}" for code in PLAN_CODES
    ), flush=True)


def downgrade() -> None:
    op.drop_constraint("ck_licenses_plan_code", "licenses", type_="check")
    op.drop_column("licenses", "plan_code")
