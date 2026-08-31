"""Shared fixtures for database integration tests.

`migrated_db` used to live inside `test_database_from_empty.py`, which
meant a second integration file could not reach it. It moved here the
moment a second file needed it, rather than being duplicated — two
copies of a fixture that drops and rebuilds a schema is two chances to
disagree about what "a migrated database" means.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# The Data tier is a package rooted at data/, not importable from the repo
# root. Set here rather than in each test file so a new integration test
# does not have to rediscover it.
sys.path.insert(0, str(ROOT / "data"))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set — database integration is NOT_VERIFIED in this run",
)


@pytest.fixture(scope="module")
def migrated_db():
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DATABASE_URL, future=True)

    # Start genuinely empty; a migration that only works on a warm database
    # is not a migration you can deploy to a new environment.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT / "database"), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return engine
