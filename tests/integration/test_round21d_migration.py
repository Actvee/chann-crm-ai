"""Migration 0039_license_plan on a real database.

Its own module so it owns the schema: it steps back to 0038, writes rows
the old way, and upgrades (the 0026 pattern). Spec §11.3 / owner decision
Q1 (24 ก.ย. 2569): a plain shop → pro; an unrevoked API key, a multi-step
approval chain or > 15 active members → enterprise; > 50 → enterprise_plus.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _alembic(target: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    verb = "upgrade" if target == "head" else "downgrade"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", verb, target],
        cwd=str(ROOT / "database"), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic {verb} {target} failed:\n{result.stdout}\n{result.stderr}"
    return result


def _licence(conn, text, tag: str, name: str):
    lic = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO licenses (id, license_code, company_name, status, auto_accept_new_customers, created_at, updated_at) "
        "VALUES (:id, :code, :name, 'active', false, now(), now())"
    ), {"id": lic, "code": f"P{tag}{name}"[:32], "name": f"Plan {name} {tag}"})
    return lic


def _people(conn, text, lic, tag: str, n: int, *, status: str = "active", prefix: str = "a"):
    for i in range(n):
        uid = f"CHN-9{prefix}{tag}{i:03d}"
        conn.execute(text(
            "INSERT INTO chann_identities (chann_uid, line_user_id, primary_role, created_at, updated_at) "
            "VALUES (:uid, :line, 'sales', now(), now()) ON CONFLICT DO NOTHING"
        ), {"uid": uid, "line": f"line-{uid}"})
        conn.execute(text(
            "INSERT INTO license_members (id, license_id, chann_uid, role, channel, status, joined_at, created_at, updated_at) "
            "VALUES (:id, :lic, :uid, 'member', 'sales', :status, now(), now(), now())"
        ), {"id": uuid.uuid4(), "lic": lic, "uid": uid, "status": status})


def test_backfill_up_and_the_column_comes_off_again(migrated_db):
    from sqlalchemy import inspect, text

    _alembic("0038_deal_closed_at")
    tag = uuid.uuid4().hex[:5]
    with migrated_db.begin() as conn:
        plain = _licence(conn, text, tag, "plain")
        _people(conn, text, plain, tag, 3, prefix="p")
        keyed = _licence(conn, text, tag, "key")
        conn.execute(text(
            "INSERT INTO api_keys (id, license_id, name, key_prefix, key_hash, created_at, updated_at) "
            "VALUES (:id, :lic, 'ERP', 'chann_live_ab', :hash, now(), now())"
        ), {"id": uuid.uuid4(), "lic": keyed, "hash": uuid.uuid4().hex + uuid.uuid4().hex[:32]})
        revoked = _licence(conn, text, tag, "revoked")
        conn.execute(text(
            "INSERT INTO api_keys (id, license_id, name, key_prefix, key_hash, revoked_at, created_at, updated_at) "
            "VALUES (:id, :lic, 'old', 'chann_live_cd', :hash, now(), now(), now())"
        ), {"id": uuid.uuid4(), "lic": revoked, "hash": uuid.uuid4().hex + uuid.uuid4().hex[:32]})
        chain = _licence(conn, text, tag, "chain")
        conn.execute(text(
            "INSERT INTO approval_workflows (id, license_id, entity_type, rules_json, is_active, created_at, updated_at) "
            "VALUES (:id, :lic, 'service_report', CAST(:rules AS jsonb), true, now(), now())"
        ), {"id": uuid.uuid4(), "lic": chain,
            "rules": '{"steps": [{"order": 1, "approver_type": "role", "approver_ref": "cs"},'
                     ' {"order": 2, "approver_type": "role", "approver_ref": "admin"}]}'})
        one_step = _licence(conn, text, tag, "onestep")
        conn.execute(text(
            "INSERT INTO approval_workflows (id, license_id, entity_type, rules_json, is_active, created_at, updated_at) "
            "VALUES (:id, :lic, 'service_report', CAST(:rules AS jsonb), true, now(), now())"
        ), {"id": uuid.uuid4(), "lic": one_step,
            "rules": '{"steps": [{"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"}]}'})
        sixteen = _licence(conn, text, tag, "sixteen")
        _people(conn, text, sixteen, tag, 16, prefix="s")
        fifteen_plus_removed = _licence(conn, text, tag, "fifteen")
        _people(conn, text, fifteen_plus_removed, tag, 15, prefix="f")
        _people(conn, text, fifteen_plus_removed, tag, 4, status="removed", prefix="r")
        fifty_one = _licence(conn, text, tag, "big")
        _people(conn, text, fifty_one, tag, 51, prefix="b")

    result = _alembic("head")
    assert "plan backfill:" in (result.stdout + result.stderr)   # the per-plan counts are printed

    with migrated_db.connect() as conn:
        codes = dict(conn.execute(text(
            "SELECT id, plan_code FROM licenses WHERE id = ANY(:ids)"
        ), {"ids": [plain, keyed, revoked, chain, one_step, sixteen, fifteen_plus_removed, fifty_one]}).all())
        assert codes == {
            plain: "pro", keyed: "enterprise", revoked: "pro", chain: "enterprise",
            one_step: "pro", sixteen: "enterprise",
            # Removed rows do not count: 15 active is still Pro.
            fifteen_plus_removed: "pro", fifty_one: "enterprise_plus",
        }
        constraints = {r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'licenses'::regclass"
        )).all()}
        assert "ck_licenses_plan_code" in constraints
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    from chann_data.main import EXPECTED_MIGRATION_HEAD
    assert head == EXPECTED_MIGRATION_HEAD == "0039_license_plan"

    # The CHECK refuses a fifth plan.
    with pytest.raises(Exception, match="ck_licenses_plan_code"):
        with migrated_db.begin() as conn:
            conn.execute(text("UPDATE licenses SET plan_code = 'gold' WHERE id = :id"), {"id": plain})

    # A new licence gets the server default.
    with migrated_db.begin() as conn:
        fresh = _licence(conn, text, tag, "fresh")
    with migrated_db.connect() as conn:
        assert conn.execute(text("SELECT plan_code FROM licenses WHERE id = :id"), {"id": fresh}).scalar_one() == "pro"

    # Downgrade drops the column and its constraint; upgrade restores head
    # for the rest of the module.
    _alembic("0038_deal_closed_at")
    assert "plan_code" not in {c["name"] for c in inspect(migrated_db).get_columns("licenses")}
    _alembic("head")
