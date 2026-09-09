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


@pytest.fixture
def memory_cache():
    """Redis, as an in-process dictionary, for the length of one test.

    The Data tier keeps conversational scratch state in Redis on purpose
    (`pending_intent`, `last_entity_ref`: short-lived, no audit trail), and
    its documented degrade with Redis down is "ask fresh". So an
    integration test that drives a MULTI-TURN chat flow against the real
    tier cannot work without one — every message would start over, and the
    test would be asserting on the degrade path rather than the feature.

    The same stand-in, for the same reason, as the agent-test channel's
    `db` backend (`scripts/agent-test/agent_test_runner/backends.py`). Only
    the four calls `chann_data.cache` makes are implemented, so a fifth one
    appearing in the Data tier fails loudly here rather than being quietly
    emulated wrong. Real eviction, real TTL expiry under load and two
    processes sharing one cache remain out of reach.
    """
    import time

    class _MemoryRedis:
        def __init__(self):
            self._store: dict[str, tuple[float | None, str]] = {}

        def ping(self) -> bool:
            return True

        def get(self, key: str):
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at is not None and expires_at <= time.time():
                self._store.pop(key, None)
                return None
            return value

        def setex(self, key: str, ttl_s: int, value: str) -> None:
            self._store[key] = (
                time.time() + ttl_s if ttl_s and ttl_s > 0 else None, value,
            )

        def delete(self, *keys: str) -> None:
            for key in keys:
                self._store.pop(key, None)

    from chann_data.cache import cache

    previous = cache._client
    cache._client = _MemoryRedis()
    try:
        yield cache
    finally:
        cache._client = previous
