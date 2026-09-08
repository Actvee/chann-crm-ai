"""Migration 0026_member_channel backfills the channel from the role and
replaces the (license, uid) unique with (license, uid, channel).

Its own module so it owns the schema: the test steps the database back
to 0025, writes rows the old way, and upgrades — nothing else in the
module sees the intermediate state.
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


def test_backfill_from_role_and_the_new_unique(migrated_db):
    from sqlalchemy import text

    _alembic("0025_integrity")
    tag = uuid.uuid4().hex[:6]
    lic = uuid.uuid4()
    rows = {"owner": uuid.uuid4(), "cs": uuid.uuid4(), "technician": uuid.uuid4()}
    with migrated_db.begin() as conn:
        for uid in rows:
            conn.execute(text(
                "INSERT INTO chann_identities (chann_uid, line_user_id, primary_role, created_at, updated_at) "
                "VALUES (:uid, :line, 'sales', now(), now())"
            ), {"uid": f"CHN-MIG-{uid}-{tag}", "line": f"line-mig-{uid}-{tag}"})
        conn.execute(text(
            "INSERT INTO licenses (id, license_code, company_name, status, auto_accept_new_customers, created_at, updated_at) "
            "VALUES (:id, :code, :name, 'active', false, now(), now())"
        ), {"id": lic, "code": f"MIG{tag}", "name": f"Mig {tag}"})
        for role, row_id in rows.items():
            conn.execute(text(
                "INSERT INTO license_members (id, license_id, chann_uid, role, status, joined_at, created_at, updated_at) "
                "VALUES (:id, :lic, :uid, :role, 'active', now(), now(), now())"
            ), {"id": row_id, "lic": lic, "uid": f"CHN-MIG-{role}-{tag}", "role": role})

    _alembic("head")

    with migrated_db.connect() as conn:
        channels = dict(conn.execute(text(
            "SELECT role, channel FROM license_members WHERE license_id = :lic"
        ), {"lic": lic}).all())
        assert channels == {"owner": "sales", "cs": "sales", "technician": "technician"}
        constraints = {r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'license_members'::regclass"
        )).all()}
        assert "uq_license_member_channel" in constraints and "ck_license_members_channel" in constraints
        assert "uq_license_member" not in constraints
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert head == "0026_member_channel"

    # The same person may now hold a second row on the other channel, and
    # still not two on the same one.
    with migrated_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO license_members (id, license_id, chann_uid, role, channel, status, joined_at, created_at, updated_at) "
            "VALUES (:id, :lic, :uid, 'technician', 'technician', 'active', now(), now(), now())"
        ), {"id": uuid.uuid4(), "lic": lic, "uid": f"CHN-MIG-owner-{tag}"})
    with pytest.raises(Exception, match="uq_license_member_channel"):
        with migrated_db.begin() as conn:
            conn.execute(text(
                "INSERT INTO license_members (id, license_id, chann_uid, role, channel, status, joined_at, created_at, updated_at) "
                "VALUES (:id, :lic, :uid, 'cs', 'sales', 'active', now(), now(), now())"
            ), {"id": uuid.uuid4(), "lic": lic, "uid": f"CHN-MIG-owner-{tag}"})
