# External API (round 21B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shop owner generates an API key on the dashboard (or is pointed there from chat), and an outside system (accounting/ERP) uses it against a small, documented `/api/ext/v1` surface that reads and writes the shop's customers, deals, quotes, invoices, payments, tickets, warranties and products.

**Architecture:** One new table (`api_keys`) and four internal routes on the Data tier, which also runs the per-key rate counter in Redis and hands back the staff permission set. On the Application tier an `Authorization: Bearer chann_live_…` resolver builds the same `TenantPrincipal` the LIFF routes use, so a separate FastAPI sub-app (`routers_ext.py`, mounted at `/api/ext/v1`, with its own OpenAPI) can call the existing `DataClient`/service functions and keep every rule they already enforce. Key management exists on both surfaces (dashboard page + chat) per the parity rule; chat never shows a secret.

**Tech Stack:** FastAPI (Application + Data), SQLAlchemy 2 + Alembic, Redis (Data only), httpx `DataClient`, Next.js/React LIFF pages, pytest (+ Postgres for `tests/integration`), the repo's `scripts/dev/check-*.py` gate.

**Spec:** `docs/superpowers/specs/2026-09-23-external-api-design.md`

## Global Constraints

- Tier boundary: Application code never imports `sqlalchemy`, `psycopg`, `redis`, `alembic` or `chann_data` (`tests/boundary/test_tier_boundaries.py`). Only the Data tier touches Redis.
- Application policy may branch on permission keys or `principal.is_owner`, never on a role string (`test_authorization_does_not_branch_on_tenant_role_names`).
- Every `routers_phase2.py` route whose path contains `{license_id}` must call `_require_same_tenant(principal, license_id)` and `principal.require(...)`/`require_any(...)` (`test_no_tenant_route_is_missing_its_guards`).
- Every `DataClient` call must map to an existing `/internal/v1/...` route with the right method (`scripts/dev/check-client.py`); every dashboard `/api/phase2/...` call must map to an Application `/api/v1/...` route (`check-routes.py`).
- Chat and dashboard must be able to do the same things; a deliberate one-sided capability goes in `ACCEPTED` in `scripts/dev/check-parity.py` with its reason.
- `audit_log.action` has a CHECK constraint — use only existing verbs (`create`, `update`, `delete`, …).
- Migration ids ≤ 32 chars; the Data image boots only when `EXPECTED_MIGRATION_HEAD` in `data/chann_data/main.py` equals the alembic head.
- Key format exactly `chann_live_` + 32 chars `[A-Za-z0-9]`; stored as SHA-256 hex; shown once; display prefix = first 15 chars (`chann_live_ab12`).
- Rate limit 600 requests / minute / key; over the limit → 429 with `Retry-After`.
- Ext error body is always `{"error": {"code": "<snake_case>", "message": "<text>"}}`.
- Ext lists: `limit` default 50 max 200, `offset` ≥ 0, body `{"items": [...], "total": n}` and header `X-Total-Count`.
- Only the shop **owner** creates/revokes keys (dashboard route 403 `owner_only`; Data tier refuses a non-owner actor too).
- `python3 scripts/dev/render-guides.py` after any `guides.py` change; `python3 scripts/dev/render-guide-images.py` after any scene change, then LOOK at the PNG.
- Tests need `JWT_SECRET=test-jwt-secret` in the environment; integration tests need `TEST_DATABASE_URL` (a Postgres on 127.0.0.1:5435 is running as container `pg20w`: `postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test`).
- Work in the worktree `~/stage-fix/r20i` (already on `origin/main` = `c0a71a3`). Commit after each task; the round ships as one patch via `~/round21b-deploy.sh` (Task 17).
- Python for tests: `/tmp/dv/bin/python` (if `/tmp/dv` is gone after a Cloud Shell restart: `python3 -m venv /tmp/dv && /tmp/dv/bin/pip install -q -r data/requirements.txt -r application/requirements.txt -r requirements-test.txt pytest pytest-asyncio httpx`).

---

## File map

| Area | File | Responsibility |
|---|---|---|
| Data | `data/chann_data/models.py` | `ApiKey` model |
| Data | `database/alembic/versions/0037_api_keys.py` | table |
| Data | `data/chann_data/main.py` | `EXPECTED_MIGRATION_HEAD = "0037_api_keys"` |
| Data | `data/chann_data/repositories/api_keys.py` | key generation/hash, `ApiKeyRepository`, rate counter |
| Data | `data/chann_data/repositories/search.py` | `since()` helper for `updated_since` |
| Data | `data/chann_data/repositories/{phase9,phase12,invoices}.py` | `updated_since` on customers/deals/tickets/invoices |
| Data | `data/chann_data/schemas.py` | `ApiKey*` schemas |
| Data | `data/chann_data/routers/internal.py` | api-key routes + `updated_since` query params |
| App | `application/chann_app/data_client.py` | `create/list/revoke/resolve_api_key`, `updated_since` kwargs |
| App | `application/chann_app/auth/api_key.py` | `hash_api_key`, `api_principal` dependency |
| App | `application/chann_app/routers_ext.py` | the `/api/ext/v1` sub-app |
| App | `application/chann_app/main.py` | mount |
| App | `application/chann_app/routers_phase2.py` | owner's key-management routes (LIFF) |
| App | `application/chann_app/services/chat.py` | chat handlers, registry, page map |
| App | `application/chann_app/services/ai/intent.py` | prompt block `entity="api_key"` |
| App | `application/chann_app/services/guides.py` | guide step |
| Pres | `presentation/app/liff/_nav-model.tsx` | nav entry (owner only) |
| Pres | `presentation/app/liff/sales/api-keys/{page.tsx,ApiKeys.tsx}` | the page |
| Pres | `presentation/lib/i18n/{th,en}.ts` | `dashboard.apiKeys` |
| Pres | `presentation/app/globals.css` | `.api-key-value` |
| Tools | `scripts/dev/check-parity.py`, `scripts/dev/render-guide-images.py`, `scripts/agent-test/scenarios/api-keys.yaml` | gate + guide picture + scenario |
| Docs | `docs/API.md`, `docs/SESSION_HANDOFF.md`, `~/CHECKLIST-หลัง-deploy.md` | contract, handoff, tester checklist |
| Tests | `tests/unit/test_round21b_api_keys.py`, `tests/unit/test_round21b_ext_api.py`, `tests/unit/test_round21b_api_chat.py`, `tests/integration/test_round21b_api_keys.py`, `tests/boundary/test_tier_boundaries.py` | |

---

### Task 1: `api_keys` table (model + migration + head)

**Files:**
- Modify: `data/chann_data/models.py` (append after `class LicenseInvite`, ~line 490)
- Create: `database/alembic/versions/0037_api_keys.py`
- Modify: `data/chann_data/main.py:25` (`EXPECTED_MIGRATION_HEAD`)
- Test: `tests/integration/test_round21b_api_keys.py`

**Interfaces:**
- Produces: `chann_data.models.ApiKey` with columns `id, license_id, name, key_prefix, key_hash, created_by_chann_uid, last_used_at, revoked_at, created_at, updated_at`.

- [ ] **Step 1: Write the failing integration test**

```python
"""Round 21B — API keys a shop owner hands to an outside system."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


class TestTheTable:
    def test_migration_creates_api_keys_with_a_unique_hash(self, migrated_db):
        from sqlalchemy import inspect

        columns = {c["name"] for c in inspect(migrated_db).get_columns("api_keys")}
        assert {"id", "license_id", "name", "key_prefix", "key_hash",
                "created_by_chann_uid", "last_used_at", "revoked_at",
                "created_at", "updated_at"} <= columns
        uniques = [u["column_names"] for u in inspect(migrated_db).get_unique_constraints("api_keys")]
        assert ["key_hash"] in uniques
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/stage-fix/r20i && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21b_api_keys.py -q`
Expected: FAIL — `NoSuchTableError: api_keys` (or the head check in conftest complaining the head is not what `main.py` expects).

- [ ] **Step 3: Add the model**

Append to `data/chann_data/models.py` right after `class LicenseInvite` (imports `String, DateTime, ForeignKey, UUID, Mapped, mapped_column, TimestampMixin, _uuid` already exist in the file):

```python
class ApiKey(TimestampMixin, Base):
    """Round 21B — a key the shop owner hands to an outside system so it
    can call /api/ext/v1 in the shop's name.

    The plaintext exists only in the create response. `key_hash` (SHA-256
    of the whole key) is the lookup; `key_prefix` is what the owner sees
    in a list to tell keys apart. Revoking sets `revoked_at` and leaves the
    row, so the audit trail and "last used" survive the revocation.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licenses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by_chann_uid: Mapped[str | None] = mapped_column(String(32))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Add the migration**

Create `database/alembic/versions/0037_api_keys.py`:

```python
"""API keys for outside systems (round 21B).

Owner, 23 ก.ย. 2569: "ทำ API เลย … การ authori เอาแค่ให้เจ้าของร้าน
generate api code ให้คนภายนอกสำหรับใช้ Api ก็พอแล้ว". One table: the key's
hash (the lookup), a display prefix, who made it, when it was last used
and when it was revoked. No permission list — a key acts as the shop's
staff, and the exposed surface is bounded by the ext router itself.

Revision ID: 0037_api_keys
Revises: 0036_warranty_contacts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0037_api_keys"
down_revision = "0036_warranty_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "license_id", UUID(as_uuid=True),
            sa.ForeignKey("licenses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_by_chann_uid", sa.String(32)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_license_id", "api_keys", ["license_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_license_id", table_name="api_keys")
    op.drop_table("api_keys")
```

- [ ] **Step 5: Bump the expected head**

In `data/chann_data/main.py` change `EXPECTED_MIGRATION_HEAD = "0036_warranty_contacts"` to `EXPECTED_MIGRATION_HEAD = "0037_api_keys"`.

- [ ] **Step 6: Run the test to verify it passes**

Same command as Step 2. Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): api_keys table — the key an owner hands to an outside system" && git log --oneline -1
```

---

### Task 2: `ApiKeyRepository` — generate, hash, create, list, revoke, resolve, rate window

**Files:**
- Create: `data/chann_data/repositories/api_keys.py`
- Test: `tests/unit/test_round21b_api_keys.py` (pure functions), `tests/integration/test_round21b_api_keys.py` (repository on Postgres)

**Interfaces:**
- Produces:
  - `generate_key() -> str`, `hash_key(raw: str) -> str`, `display_prefix(raw: str) -> str`, constants `KEY_PREFIX = "chann_live_"`, `RATE_LIMIT_PER_MINUTE = 600`.
  - `class ApiKeyNotFound(Exception)`, `class ApiKeyConflict(Exception)`.
  - `ApiKeyRepository(session).create(scope, *, name, created_by_chann_uid) -> tuple[ApiKey, str]` (row, plaintext).
  - `.list_for_license(scope) -> list[ApiKey]` newest first, revoked included.
  - `.revoke(scope, key_id) -> ApiKey` (idempotent; 404 for another shop's id).
  - `.find_live_by_hash(key_hash) -> ApiKey | None` (revoked → None).
  - `.touch(key: ApiKey, now: datetime) -> None` stamps `last_used_at` when older than 60 s.
  - `rate_window_remaining(redis_client, key_id: str, *, now: datetime, limit: int = RATE_LIMIT_PER_MINUTE) -> int` — `INCR api_rl:{key_id}:{YYYYmmddHHMM}` + `EXPIRE 120`; returns `limit - count`; on any Redis error logs a warning and returns `limit`.

- [ ] **Step 1: Write the failing unit test (pure functions + rate window with a fake redis)**

Create `tests/unit/test_round21b_api_keys.py`:

```python
"""Round 21B — the key itself: shape, hash, display prefix, rate window."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories import api_keys as ak  # noqa: E402


class _FakePipe:
    def __init__(self, store, fail=False):
        self.store, self.fail, self.ops = store, fail, []

    def incr(self, key):
        self.ops.append(("incr", key)); return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl)); return self

    def execute(self):
        if self.fail:
            raise ConnectionError("redis down")
        out = []
        for op in self.ops:
            if op[0] == "incr":
                self.store[op[1]] = self.store.get(op[1], 0) + 1
                out.append(self.store[op[1]])
            else:
                out.append(True)
        return out


class _FakeRedis:
    def __init__(self, fail=False):
        self.store, self.fail = {}, fail

    def pipeline(self):
        return _FakePipe(self.store, self.fail)


class TestTheKey:
    def test_a_key_has_the_prefix_and_32_safe_characters(self):
        raw = ak.generate_key()
        assert raw.startswith("chann_live_")
        assert re.fullmatch(r"chann_live_[A-Za-z0-9]{32}", raw)
        assert ak.generate_key() != raw

    def test_the_hash_is_sha256_hex_and_the_prefix_is_short(self):
        raw = "chann_live_" + "a" * 32
        assert ak.hash_key(raw) == __import__("hashlib").sha256(raw.encode()).hexdigest()
        assert ak.display_prefix(raw) == "chann_live_aaaa"


class TestTheRateWindow:
    def test_counts_down_within_the_minute_and_expires_the_bucket(self):
        r = _FakeRedis()
        now = datetime(2026, 9, 23, 10, 15, 30, tzinfo=timezone.utc)
        first = ak.rate_window_remaining(r, "k1", now=now, limit=3)
        second = ak.rate_window_remaining(r, "k1", now=now, limit=3)
        assert (first, second) == (2, 1)
        assert list(r.store) == ["api_rl:k1:202609231015"]
        assert ak.rate_window_remaining(r, "k1", now=now, limit=3) == 0
        assert ak.rate_window_remaining(r, "k1", now=now, limit=3) == -1

    def test_a_new_minute_is_a_new_bucket(self):
        r = _FakeRedis()
        t1 = datetime(2026, 9, 23, 10, 15, 59, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 23, 10, 16, 0, tzinfo=timezone.utc)
        ak.rate_window_remaining(r, "k1", now=t1, limit=3)
        assert ak.rate_window_remaining(r, "k1", now=t2, limit=3) == 2

    def test_redis_down_never_blocks_a_request(self):
        r = _FakeRedis(fail=True)
        now = datetime(2026, 9, 23, 10, 15, 30, tzinfo=timezone.utc)
        assert ak.rate_window_remaining(r, "k1", now=now, limit=600) == 600
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/stage-fix/r20i && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21b_api_keys.py -q`
Expected: FAIL — `ModuleNotFoundError: chann_data.repositories.api_keys`.

- [ ] **Step 3: Write the repository module**

Create `data/chann_data/repositories/api_keys.py`:

```python
"""Round 21B — API keys: the credential an outside system presents.

A key is high-entropy (32 random alphanumerics behind a fixed prefix),
so SHA-256 is the right hash: it is the lookup index, and there is no
low-entropy password for argon2 to protect. The plaintext is returned
from `create` once and never stored.

The rate window lives here too, because Redis is a Data-tier concern:
one INCR per request on a per-minute bucket, expiring itself. A Redis
outage must never turn into a refused request — the limit is protection
against a runaway client, not an authority (cache.py's FALLBACK_DB rule).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import ApiKey
from .tenant_scope import TenantScope

log = logging.getLogger(__name__)

KEY_PREFIX = "chann_live_"
KEY_BODY_LEN = 32
PREFIX_DISPLAY_LEN = len(KEY_PREFIX) + 4
RATE_LIMIT_PER_MINUTE = 600
BUCKET_TTL_S = 120
TOUCH_EVERY = timedelta(seconds=60)
_ALPHABET = string.ascii_letters + string.digits


class ApiKeyNotFound(Exception):
    pass


class ApiKeyConflict(Exception):
    pass


def generate_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_BODY_LEN))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    return raw[:PREFIX_DISPLAY_LEN]


def rate_window_remaining(redis_client, key_id: str, *, now: datetime,
                          limit: int = RATE_LIMIT_PER_MINUTE) -> int:
    bucket = f"api_rl:{key_id}:{now.astimezone(timezone.utc):%Y%m%d%H%M}"
    try:
        pipe = redis_client.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, BUCKET_TTL_S)
        count, _ = pipe.execute()
    except Exception:  # noqa: BLE001 — the counter is protection, not authority
        log.warning("api rate window unavailable; not limiting", exc_info=True)
        return limit
    return limit - int(count)


class ApiKeyRepository:
    def __init__(self, session):
        self._s = session

    def create(self, scope: TenantScope, *, name: str, created_by_chann_uid: str | None) -> tuple[ApiKey, str]:
        name = " ".join((name or "").split())
        if not name:
            raise ApiKeyConflict("name is required")
        for _ in range(5):
            raw = generate_key()
            digest = hash_key(raw)
            clash = self._s.execute(select(ApiKey.id).where(ApiKey.key_hash == digest)).first()
            if clash is None:
                break
        else:  # pragma: no cover — 2^190 odds
            raise ApiKeyConflict("could not allocate a unique key")
        row = ApiKey(
            id=uuid.uuid4(), license_id=scope.license_id, name=name,
            key_prefix=display_prefix(raw), key_hash=digest,
            created_by_chann_uid=created_by_chann_uid,
        )
        self._s.add(row)
        self._s.flush()
        return row, raw

    def list_for_license(self, scope: TenantScope) -> list[ApiKey]:
        return list(self._s.execute(
            select(ApiKey).where(ApiKey.license_id == scope.license_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        ).scalars())

    def get(self, scope: TenantScope, key_id: uuid.UUID) -> ApiKey:
        row = self._s.execute(select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.license_id == scope.license_id,
        )).scalars().first()
        if row is None:
            raise ApiKeyNotFound("api key not found")
        return row

    def revoke(self, scope: TenantScope, key_id: uuid.UUID) -> ApiKey:
        row = self.get(scope, key_id)
        if row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            self._s.flush()
        return row

    def find_live_by_hash(self, key_hash: str) -> ApiKey | None:
        row = self._s.execute(select(ApiKey).where(ApiKey.key_hash == key_hash)).scalars().first()
        if row is None or row.revoked_at is not None:
            return None
        return row

    def touch(self, row: ApiKey, now: datetime) -> None:
        if row.last_used_at is None or now - row.last_used_at >= TOUCH_EVERY:
            row.last_used_at = now
            self._s.flush()
```

- [ ] **Step 4: Run the unit test — passes**

Same command as Step 2. Expected: `6 passed`.

- [ ] **Step 5: Add the repository integration test**

Append to `tests/integration/test_round21b_api_keys.py`:

```python
class TestTheRepository:
    def test_create_list_resolve_revoke(self, migrated_db):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy.orm import Session

        from chann_data.repositories.api_keys import ApiKeyNotFound, ApiKeyRepository, hash_key
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            repo = ApiKeyRepository(session)
            row, raw = repo.create(scope, name="  ระบบบัญชี  Express ", created_by_chann_uid=owner.chann_uid)
            assert row.name == "ระบบบัญชี Express" and raw.startswith("chann_live_")
            assert row.key_prefix == raw[:15] and row.key_hash == hash_key(raw)
            assert [k.id for k in repo.list_for_license(scope)] == [row.id]

            live = repo.find_live_by_hash(hash_key(raw))
            assert live is not None and live.id == row.id
            now = datetime.now(timezone.utc)
            repo.touch(live, now)
            assert live.last_used_at == now
            repo.touch(live, now + timedelta(seconds=10))
            assert live.last_used_at == now  # not re-stamped within a minute

            other = TenantScope(uuid.uuid4())
            try:
                repo.revoke(other, row.id)
                raise AssertionError("another shop revoked our key")
            except ApiKeyNotFound:
                pass
            repo.revoke(scope, row.id)
            assert repo.find_live_by_hash(hash_key(raw)) is None
            assert repo.list_for_license(scope)[0].revoked_at is not None
            session.rollback()
```

- [ ] **Step 6: Run the integration file — passes**

Run: `cd ~/stage-fix/r20i && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21b_api_keys.py -q`
Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): ApiKeyRepository — generate, hash, revoke, resolve and the per-minute window" && git log --oneline -1
```

---

### Task 3: Data internal routes for api keys (create / list / revoke / resolve)

**Files:**
- Modify: `data/chann_data/schemas.py` (append)
- Modify: `data/chann_data/routers/internal.py` (import block ~line 110–130; routes appended at the end of the file)
- Test: `tests/integration/test_round21b_api_keys.py`

**Interfaces:**
- Produces internal routes (all behind `require_internal_secret` via the router):
  - `POST /internal/v1/licenses/{license_id}/api-keys` body `{"name": str, "created_by_chann_uid": str}` → 201 `ApiKeyCreatedOut` (= `ApiKeyOut` + `key`). 403 `{"detail": "owner_only"}` when `X-Actor-Id` is not an active owner member of that license; 409 on empty name.
  - `GET /internal/v1/licenses/{license_id}/api-keys` → `list[ApiKeyOut]` (revoked included, `revoked_at` set).
  - `POST /internal/v1/licenses/{license_id}/api-keys/{key_id}/revoke` → `ApiKeyOut`; 404 if not this shop's; 403 `owner_only` for a non-owner actor.
  - `POST /internal/v1/api-keys/resolve` body `{"key_hash": str}` → `ApiKeyResolveOut {key: ApiKeyOut, license_status: str, permission_keys: list[str], limit: int, remaining: int}`; 404 when unknown or revoked.
- Consumes: `ApiKeyRepository`, `rate_window_remaining`, `cache.client` (redis), `DEFAULT_ROLE_TEMPLATES["admin"]` from `chann_data.permissions`, `LicenseMember` (`is_owner`, `status`, `chann_uid`, `license_id`).

- [ ] **Step 1: Write the failing HTTP test**

Append to `tests/integration/test_round21b_api_keys.py` (the Data app is `chann_data.main.app`; the internal secret is `settings.admin_secret`):

```python
class TestTheInternalRoutes:
    def _http(self, migrated_db):
        from fastapi.testclient import TestClient

        from chann_data.config import settings
        from chann_data.main import app

        return TestClient(app), {"X-Internal-Secret": settings.admin_secret}

    def test_owner_creates_lists_resolves_and_revokes(self, migrated_db, monkeypatch):
        from sqlalchemy.orm import Session

        from chann_data.repositories.api_keys import hash_key
        from chann_data import cache as cache_module

        class _Pipe:
            def __init__(self): self.n = 0
            def incr(self, k): return self
            def expire(self, k, t): return self
            def execute(self): return [1, True]

        class _Redis:
            def pipeline(self): return _Pipe()

        monkeypatch.setattr(cache_module.cache, "_client", _Redis())

        with Session(migrated_db) as session:
            lic, owner, member = _phase2_tenant(session)
            session.commit()
            license_id, owner_uid, member_uid = str(lic.id), owner.chann_uid, member.chann_uid

        http, secret = self._http(migrated_db)
        # A plain member may not make one.
        refused = http.post(f"/internal/v1/licenses/{license_id}/api-keys",
                            json={"name": "ERP", "created_by_chann_uid": member_uid},
                            headers={**secret, "X-Actor-Id": member_uid})
        assert refused.status_code == 403 and refused.json()["detail"] == "owner_only"

        made = http.post(f"/internal/v1/licenses/{license_id}/api-keys",
                         json={"name": "ERP", "created_by_chann_uid": owner_uid},
                         headers={**secret, "X-Actor-Id": owner_uid})
        assert made.status_code == 201, made.text
        body = made.json()
        raw = body["key"]
        assert raw.startswith("chann_live_") and body["key_prefix"] == raw[:15]

        listed = http.get(f"/internal/v1/licenses/{license_id}/api-keys", headers=secret).json()
        assert [row["id"] for row in listed] == [body["id"]] and "key" not in listed[0]

        resolved = http.post("/internal/v1/api-keys/resolve", json={"key_hash": hash_key(raw)}, headers=secret)
        assert resolved.status_code == 200, resolved.text
        r = resolved.json()
        assert r["key"]["id"] == body["id"] and r["license_status"] == "trial"
        assert r["limit"] == 600 and r["remaining"] == 599
        assert "customer.read" in r["permission_keys"] and not any(k.startswith("platform.") for k in r["permission_keys"])

        unknown = http.post("/internal/v1/api-keys/resolve", json={"key_hash": "0" * 64}, headers=secret)
        assert unknown.status_code == 404

        revoked = http.post(f"/internal/v1/licenses/{license_id}/api-keys/{body['id']}/revoke",
                            headers={**secret, "X-Actor-Id": owner_uid})
        assert revoked.status_code == 200 and revoked.json()["revoked_at"]
        assert http.post("/internal/v1/api-keys/resolve", json={"key_hash": hash_key(raw)}, headers=secret).status_code == 404
```

`_phase2_tenant` returns `(license, owner_member, member)`; if its owner row's `status` is not `"active"` in your checkout, read `tests/integration/test_database_from_empty.py` and use whatever it sets — the route's owner test is `is_owner and status == "active"`.

- [ ] **Step 2: Run it — fails with 404 (no route)**

Same integration command. Expected: FAIL on the first POST (`404`).

- [ ] **Step 3: Schemas**

Append to `data/chann_data/schemas.py`:

```python
class ApiKeyCreateIn(BaseModel):
    name: str
    created_by_chann_uid: str | None = None


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    name: str
    key_prefix: str
    created_by_chann_uid: str | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    """The one response that carries the plaintext key."""
    key: str


class ApiKeyResolveIn(BaseModel):
    key_hash: str


class ApiKeyResolveOut(BaseModel):
    key: ApiKeyOut
    license_status: str
    permission_keys: list[str]
    limit: int
    remaining: int
```

- [ ] **Step 4: Routes**

In `data/chann_data/routers/internal.py` add to the imports (next to the `from ..repositories.invoices import (` block):

```python
from ..repositories.api_keys import (
    RATE_LIMIT_PER_MINUTE, ApiKeyConflict, ApiKeyNotFound, ApiKeyRepository, rate_window_remaining,
)
from ..permissions import DEFAULT_ROLE_TEMPLATES
```

and `ApiKeyCreateIn, ApiKeyCreatedOut, ApiKeyOut, ApiKeyResolveIn, ApiKeyResolveOut` to the `from ..schemas import (` list. Then append at the end of the file:

```python
# ------------------------------------------------------------ round 21B: API keys


def _api_key_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApiKeyNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ApiKeyConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error")


def _require_owner_actor(session: Session, license_id: uuid.UUID, actor_id: str) -> None:
    """Keys are the owner's to make and to take away — the one rule the
    spec keeps. Checked here so chat and the dashboard cannot disagree."""
    row = session.execute(select(LicenseMember).where(
        LicenseMember.license_id == license_id,
        LicenseMember.chann_uid == actor_id,
    )).scalars().first()
    if row is None or not row.is_owner or str(row.status or "") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner_only")


def _api_key_out(row) -> ApiKeyOut:
    return ApiKeyOut.model_validate(row, from_attributes=True)


@router.post("/licenses/{license_id}/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
def create_api_key(
    license_id: uuid.UUID, payload: ApiKeyCreateIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    _require_owner_actor(session, license_id, x_actor_id)
    scope = TenantScope(license_id=license_id)
    try:
        row, raw = ApiKeyRepository(session).create(
            scope, name=payload.name, created_by_chann_uid=payload.created_by_chann_uid or x_actor_id,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="api_key", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"name": row.name, "key_prefix": row.key_prefix}),
        )
        session.commit()
        out = ApiKeyCreatedOut.model_validate({**_api_key_out(row).model_dump(), "key": raw})
        return out
    except Exception as exc:
        session.rollback()
        raise _api_key_http_error(exc)


@router.get("/licenses/{license_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(license_id: uuid.UUID, session: Session = Depends(get_session)):
    rows = ApiKeyRepository(session).list_for_license(TenantScope(license_id=license_id))
    return [_api_key_out(r) for r in rows]


@router.post("/licenses/{license_id}/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(
    license_id: uuid.UUID, key_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    _require_owner_actor(session, license_id, x_actor_id)
    try:
        row = ApiKeyRepository(session).revoke(TenantScope(license_id=license_id), key_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="api_key", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="delete",
            field_changes=diff_fields({"revoked_at": None}, {"revoked_at": str(row.revoked_at)}),
        )
        session.commit()
        return _api_key_out(row)
    except Exception as exc:
        session.rollback()
        raise _api_key_http_error(exc)


@router.post("/api-keys/resolve", response_model=ApiKeyResolveOut)
def resolve_api_key(payload: ApiKeyResolveIn, session: Session = Depends(get_session)):
    """One call per outside request: find the key, count it against its
    minute, stamp last_used_at. 404 for unknown and revoked alike — the
    caller cannot tell them apart, on purpose."""
    repo = ApiKeyRepository(session)
    row = repo.find_live_by_hash(payload.key_hash)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api key not found")
    now = datetime.now(timezone.utc)
    remaining = rate_window_remaining(cache.client, str(row.id), now=now)
    license_row = session.get(License, row.license_id)
    repo.touch(row, now)
    session.commit()
    return ApiKeyResolveOut(
        key=_api_key_out(row),
        license_status=str(getattr(license_row, "status", None) or "active"),
        permission_keys=sorted(DEFAULT_ROLE_TEMPLATES["admin"] or ()),
        limit=RATE_LIMIT_PER_MINUTE, remaining=remaining,
    )
```

`License`, `LicenseMember`, `select`, `Session`, `Header`, `datetime`, `timezone`, `cache`, `AuditRepository`, `diff_fields`, `TenantScope`, `log` are already imported in `internal.py` (check the import block at the top; add any that are missing).

- [ ] **Step 5: Run the integration file — passes**

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): internal api-key routes — owner-only create/revoke, list, and resolve with the minute window" && git log --oneline -1
```

---

### Task 4: `updated_since` on customers, deals, tickets, invoices (Data tier)

**Files:**
- Modify: `data/chann_data/repositories/search.py` (add `since`)
- Modify: `data/chann_data/repositories/invoices.py` (`_narrow`, `list_for_license`, `count_for_license`)
- Modify: `data/chann_data/repositories/phase9.py` (`CustomerRepository` and `DealRepository` `list_for_license`/`count_for_license`, lines ~377/400 and ~709/723)
- Modify: the ticket repository's `list_for_license`/`count_for_license` (the class `GET /internal/v1/licenses/{license_id}/tickets` in `internal.py` calls — `grep -n "def list_tickets" data/chann_data/routers/internal.py` and follow the repository it uses)
- Modify: `data/chann_data/routers/internal.py` — the four list routes gain `updated_since: datetime | None = None` and pass it through
- Test: `tests/integration/test_round21b_api_keys.py`

**Interfaces:**
- Produces: `search.since(query, model, updated_since: datetime | None)`; keyword `updated_since` on the four repositories' `list_for_license`/`count_for_license` and on the four internal list routes (`?updated_since=2026-09-23T00:00:00+07:00`).

- [ ] **Step 1: Write the failing integration test**

Append:

```python
class TestUpdatedSince:
    def test_only_rows_touched_after_the_stamp_come_back(self, migrated_db):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from sqlalchemy.orm import Session

        from chann_data.repositories.invoices import InvoiceRepository
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customers = CustomerRepository(session)
            old = customers.create(scope, first_name="เก่า", phone=f"08{uuid.uuid4().int % 10**8:08d}")
            new = customers.create(scope, first_name="ใหม่", phone=f"08{uuid.uuid4().int % 10**8:08d}")
            session.flush()
            stamp = datetime.now(timezone.utc) + timedelta(seconds=1)
            old.updated_at = stamp - timedelta(days=2)
            new.updated_at = stamp + timedelta(minutes=1)
            session.flush()
            assert [c.id for c in customers.list_for_license(scope, updated_since=stamp)] == [new.id]
            assert customers.count_for_license(scope, updated_since=stamp) == 1
            assert customers.count_for_license(scope, updated_since=None) == 2

            deals = DealRepository(session)
            d1 = deals.create(scope, contact_id=new.id)
            session.flush()
            d1.updated_at = stamp - timedelta(days=1)
            session.flush()
            assert deals.count_for_license(scope, updated_since=stamp) == 0

            invoices = InvoiceRepository(session)
            inv = invoices.create(scope, deal_id=d1.id, contact_id=new.id, total=Decimal("10"))
            session.flush()
            inv.updated_at = stamp + timedelta(minutes=2)
            session.flush()
            assert [r.id for r in invoices.list_for_license(scope, updated_since=stamp)] == [inv.id]
            session.rollback()
```

(If `DealRepository.create` needs more arguments in your checkout, copy the call from `tests/integration/test_round21a_record_actions.py`.)

- [ ] **Step 2: Run — fails with `unexpected keyword argument 'updated_since'`**

- [ ] **Step 3: The helper**

Append to `data/chann_data/repositories/search.py`:

```python
def since(query, model, updated_since):
    """`updated_at >= stamp`, or the query untouched. One place, so every
    list that offers an ERP a sync cursor means the same thing by it."""
    if updated_since is None:
        return query
    return query.where(model.updated_at >= updated_since)
```

- [ ] **Step 4: Thread the keyword through the four repositories**

For each of `InvoiceRepository`, `CustomerRepository`, `DealRepository`, the ticket repository:
1. add `updated_since: datetime | None = None` (keyword-only) to `_narrow` (where one exists), `list_for_license` and `count_for_license`;
2. inside `_narrow` (or at the top of both list/count when there is no `_narrow`) apply `query = since(query, <Model>, updated_since)` where `<Model>` is the class the repository already selects from (`Invoice`, the customer model, the deal model, the ticket model — read the `select(...)` in each `list_for_license`);
3. pass `updated_since=updated_since` from `list_for_license`/`count_for_license` into `_narrow`.

For `InvoiceRepository` the concrete edit is: `_narrow(..., quote_id=None, updated_since=None)` ending with `return since(query, Invoice, updated_since)`; both callers add `updated_since=updated_since`. Import: `from .search import page, since` (page is already imported there; extend the line).

- [ ] **Step 5: The four internal list routes**

In `internal.py`, for `list_customers`, `list_deals`, `list_tickets` and `list_invoices` (the `GET /licenses/{license_id}/<plural>` routes): add the parameter `updated_since: datetime | None = None` and include `updated_since=updated_since` in the `narrow = dict(...)`/kwargs passed to both `list_for_license` and `count_for_license`. FastAPI parses ISO-8601 from the query string.

- [ ] **Step 6: Run the integration file — passes (`4 passed`)**; then the existing suites still pass: `JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21a_record_actions.py tests/unit/test_round20x_invoice_from_deal.py -q` (if that file name differs, run `tests/unit -q -k "invoice"`).

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): updated_since on customers, deals, tickets and invoices — the ERP's sync cursor" && git log --oneline -1
```

---

### Task 5: `DataClient` — api-key calls and `updated_since`

**Files:**
- Modify: `application/chann_app/data_client.py` (append the four methods after `revoke_invite`; extend `list_customers_with_total`, `list_deals_with_total`, `list_tickets_with_total`, `list_invoices_with_total`)
- Test: `tests/unit/test_round21b_ext_api.py` (new; the `DataClient` part)

**Interfaces:**
- Produces:
  - `create_api_key(license_id: str, payload: dict, actor_id: str | None = None) -> dict` (POST `/internal/v1/licenses/{id}/api-keys`)
  - `list_api_keys(license_id: str) -> list[dict]` (GET)
  - `revoke_api_key(license_id: str, key_id: str, actor_id: str | None = None) -> dict` (POST `.../api-keys/{key_id}/revoke`)
  - `resolve_api_key(key_hash: str) -> dict | None` (POST `/internal/v1/api-keys/resolve`; 404 → `None`)
  - keyword `updated_since: datetime | None = None` on the four `*_with_total` methods, sent as `params["updated_since"] = updated_since.isoformat()`.

- [ ] **Step 1: Failing test — the client sends what the Data routes expect**

Create `tests/unit/test_round21b_ext_api.py` with this first class (more classes are added in Tasks 6–9):

```python
"""Round 21B — the external API: key resolver, ext routes, client calls."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.data_client import DataClient  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


def _client_recording(status_code=200, body=None):
    """A real DataClient over a MockTransport that records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=body if body is not None else {})

    client = DataClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://data")
    client._base = "http://data"
    return client, seen


class TestTheClient:
    async def test_the_four_key_calls_hit_the_data_routes(self):
        client, seen = _client_recording(201, {"id": "k1", "key": "chann_live_x"})
        await client.create_api_key("L1", {"name": "ERP", "created_by_chann_uid": "CHN-OWNER"}, actor_id="CHN-OWNER")
        client, seen2 = _client_recording(200, [])
        await client.list_api_keys("L1")
        client, seen3 = _client_recording(200, {"id": "k1"})
        await client.revoke_api_key("L1", "k1", actor_id="CHN-OWNER")
        client, seen4 = _client_recording(404, {"detail": "api key not found"})
        assert await client.resolve_api_key("0" * 64) is None
        assert (seen[0].method, seen[0].url.path) == ("POST", "/internal/v1/licenses/L1/api-keys")
        assert seen[0].headers["X-Actor-Id"] == "CHN-OWNER"
        assert (seen2[0].method, seen2[0].url.path) == ("GET", "/internal/v1/licenses/L1/api-keys")
        assert (seen3[0].method, seen3[0].url.path) == ("POST", "/internal/v1/licenses/L1/api-keys/k1/revoke")
        assert (seen4[0].method, seen4[0].url.path) == ("POST", "/internal/v1/api-keys/resolve")
        assert json.loads(seen4[0].content) == {"key_hash": "0" * 64}

    async def test_updated_since_rides_as_an_iso_query_param(self):
        stamp = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
        for name in ("list_customers_with_total", "list_deals_with_total",
                     "list_tickets_with_total", "list_invoices_with_total"):
            client, seen = _client_recording(200, [])
            await getattr(client, name)("L1", updated_since=stamp)
            assert seen[0].url.params["updated_since"] == "2026-09-23T00:00:00+00:00", name
```

- [ ] **Step 2: Run — fails (`AttributeError: create_api_key`)**

Run: `cd ~/stage-fix/r20i && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21b_ext_api.py -q`

- [ ] **Step 3: Implement**

Append after `revoke_invite` in `data_client.py`:

```python
    # ------------------------------------------------------------ round 21B: API keys

    async def create_api_key(self, license_id: str, payload: dict, actor_id: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/api-keys",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_api_keys(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/api-keys", headers=self._headers,
        )
        return self._unwrap(resp)

    async def revoke_api_key(self, license_id: str, key_id: str, actor_id: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/api-keys/{key_id}/revoke",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def resolve_api_key(self, key_hash: str) -> dict | None:
        """The key row, the shop's status, the staff permission set and the
        minute window — or None for an unknown/revoked key (the Data tier
        answers 404 for both, on purpose)."""
        resp = await self._client.post(
            f"{self._base}/internal/v1/api-keys/resolve", headers=self._headers, json={"key_hash": key_hash},
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)
```

Then in each of `list_customers_with_total`, `list_deals_with_total`, `list_tickets_with_total`, `list_invoices_with_total`: add the keyword-only parameter `updated_since: datetime | None = None` and, where the method builds `params`, add

```python
        if updated_since:
            params["updated_since"] = updated_since.isoformat()
```

(`datetime` is imported at the top of `data_client.py`; if not, add `from datetime import datetime`.)

- [ ] **Step 4: Run — `2 passed`**. Then `cd ~/stage-fix/r20i && /tmp/dv/bin/python scripts/dev/check-client.py` must print `every client call maps to a Data Tier route with the right method`.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): DataClient learns the api-key routes and the updated_since cursor" && git log --oneline -1
```

---

### Task 6: `auth/api_key.py` — Bearer key → `TenantPrincipal`

**Files:**
- Create: `application/chann_app/auth/api_key.py`
- Test: `tests/unit/test_round21b_ext_api.py` (class `TestTheResolver`)

**Interfaces:**
- Produces:
  - `hash_api_key(raw: str) -> str` (SHA-256 hex; the same function as the Data tier's `hash_key`, duplicated on purpose — the Application tier may not import `chann_data`).
  - `KEY_PREFIX = "chann_live_"`.
  - `async def api_principal(request: Request, authorization: str = Header(default=""), client: DataClient = Depends(get_data_client)) -> TenantPrincipal` — raises `HTTPException(401, {"code": "missing_or_malformed_key", "message": …})`, `401 unknown_or_revoked_key`, `429 rate_limited` (headers `Retry-After`), `423` from `refuse_if_suspended`; sets `request.state.rate_limit = (limit, max(remaining, 0))`; returns `TenantPrincipal(license_id, chann_uid=f"api:{key_id}", role="api", is_owner=False, permission_keys=frozenset(permission_keys), audience="api", license_status=…)`.
- Consumes: `DataClient.resolve_api_key` (Task 5); `refuse_if_suspended`, `TenantPrincipal` from `chann_app.services.authorization`; `get_data_client` from `chann_app.routers_admin`.

- [ ] **Step 1: Failing tests**

Append to `tests/unit/test_round21b_ext_api.py`:

```python
from chann_app.auth import api_key as api_key_auth  # noqa: E402


class _ResolvingFake(FakeDataClient):
    """A FakeDataClient that answers resolve_api_key from a table."""
    def __init__(self, *, resolved=None, **kw):
        super().__init__(**kw)
        self._resolved = resolved
        self.resolve_calls: list[str] = []

    async def resolve_api_key(self, key_hash):
        self.resolve_calls.append(key_hash)
        return self._resolved


RAW = "chann_live_" + "A" * 32
RESOLVED = {
    "key": {"id": "11111111-1111-1111-1111-111111111111", "license_id": LICENSE_ID, "name": "ERP",
            "key_prefix": "chann_live_AAAA", "created_by_chann_uid": "CHN-OWNER",
            "last_used_at": None, "revoked_at": None, "created_at": "2026-09-23T00:00:00+00:00"},
    "license_status": "active", "permission_keys": ["customer.read", "invoice.create"],
    "limit": 600, "remaining": 599,
}


def _probe_app(fake):
    from fastapi import Depends, Request

    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request, principal=Depends(api_key_auth.api_principal)):
        return {"license_id": principal.license_id, "chann_uid": principal.chann_uid,
                "audience": principal.audience, "keys": sorted(principal.permission_keys),
                "rate": list(request.state.rate_limit)}

    @app.post("/probe")
    async def probe_write(principal=Depends(api_key_auth.api_principal)):
        return {"ok": True}

    async def override():
        yield fake

    from chann_app.routers_admin import get_data_client
    app.dependency_overrides[get_data_client] = override
    return TestClient(app)


class TestTheResolver:
    def test_the_hash_matches_the_data_tier(self):
        import hashlib
        assert api_key_auth.hash_api_key(RAW) == hashlib.sha256(RAW.encode()).hexdigest()

    def test_no_header_or_wrong_shape_is_401_and_never_asks_data(self):
        fake = _ResolvingFake(resolved=RESOLVED)
        http = _probe_app(fake)
        assert http.get("/probe").status_code == 401
        assert http.get("/probe", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert http.get("/probe", headers={"Authorization": RAW}).status_code == 401
        assert fake.resolve_calls == []
        body = http.get("/probe").json()
        assert body["detail"]["code"] == "missing_or_malformed_key"

    def test_a_good_key_becomes_an_api_principal_with_the_staff_set(self):
        fake = _ResolvingFake(resolved=RESOLVED)
        http = _probe_app(fake)
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 200, r.text
        assert r.json() == {"license_id": LICENSE_ID, "chann_uid": "api:11111111-1111-1111-1111-111111111111",
                            "audience": "api", "keys": ["customer.read", "invoice.create"], "rate": [600, 599]}
        assert fake.resolve_calls == [api_key_auth.hash_api_key(RAW)]

    def test_unknown_or_revoked_is_401(self):
        http = _probe_app(_ResolvingFake(resolved=None))
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "unknown_or_revoked_key"

    def test_over_the_window_is_429_with_retry_after(self):
        http = _probe_app(_ResolvingFake(resolved={**RESOLVED, "remaining": -1}))
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
        assert 1 <= int(r.headers["Retry-After"]) <= 60

    def test_a_suspended_shop_reads_but_does_not_write(self):
        http = _probe_app(_ResolvingFake(resolved={**RESOLVED, "license_status": "suspended"}))
        assert http.get("/probe", headers={"Authorization": f"Bearer {RAW}"}).status_code == 200
        assert http.post("/probe", headers={"Authorization": f"Bearer {RAW}"}).status_code == 423
```

- [ ] **Step 2: Run — fails (`ImportError: chann_app.auth.api_key`)**

- [ ] **Step 3: Implement**

Create `application/chann_app/auth/api_key.py`:

```python
"""Round 21B — the outside system's credential, turned into the same
principal the LIFF routes use.

`Authorization: Bearer chann_live_…` → SHA-256 → one Data call that
finds the key, counts it against its minute and stamps last_used_at →
a TenantPrincipal with audience "api". Everything after that is the
existing permission and tenant machinery; nothing here is bespoke.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status

from ..data_client import DataClient
from ..routers_admin import get_data_client
from ..services.authorization import TenantPrincipal, refuse_if_suspended

KEY_PREFIX = "chann_live_"
BEARER = "Bearer "


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _error(code: int, name: str, message: str, headers: dict | None = None) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": name, "message": message}, headers=headers)


async def api_principal(
    request: Request,
    authorization: str = Header(default=""),
    client: DataClient = Depends(get_data_client),
) -> TenantPrincipal:
    raw = authorization[len(BEARER):].strip() if authorization.startswith(BEARER) else ""
    if not raw.startswith(KEY_PREFIX) or len(raw) != len(KEY_PREFIX) + 32:
        raise _error(status.HTTP_401_UNAUTHORIZED, "missing_or_malformed_key",
                     "Send the shop's API key as: Authorization: Bearer chann_live_…")
    found = await client.resolve_api_key(hash_api_key(raw))
    if found is None:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unknown_or_revoked_key",
                     "This key is not known or has been revoked. Make a new one on the dashboard.")
    limit, remaining = int(found.get("limit") or 0), int(found.get("remaining") or 0)
    if remaining < 0:
        wait = 60 - datetime.now(timezone.utc).second or 60
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited",
                     f"More than {limit} requests this minute. Try again in {wait} s.",
                     headers={"Retry-After": str(wait)})
    license_status = str(found.get("license_status") or "active")
    refuse_if_suspended(license_status, request.method)
    request.state.rate_limit = (limit, max(remaining, 0))
    key = found["key"]
    return TenantPrincipal(
        license_id=str(key["license_id"]),
        chann_uid=f"api:{key['id']}",
        role="api",
        is_owner=False,
        permission_keys=frozenset(found.get("permission_keys") or ()),
        audience="api",
        license_status=license_status,
    )
```

- [ ] **Step 4: Run — the resolver class passes (`8 passed` in the file so far)**

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): a Bearer api key resolves to the same TenantPrincipal the dashboard uses" && git log --oneline -1
```

---

### Task 7: `routers_ext.py` — the sub-app, error shape, `/me`, customers, deals

**Files:**
- Create: `application/chann_app/routers_ext.py`
- Test: `tests/unit/test_round21b_ext_api.py` (class `TestExtCustomersAndDeals`)

**Interfaces:**
- Produces: `ext_app: FastAPI` (title "Chann CRM AI — Public API", `docs_url="/docs"`, `openapi_url="/openapi.json"`), every route depending on `api_principal`; helpers `_page(limit, offset)`, `_listing(response, rows, total, limit, offset) -> dict`, `_propagate` re-exported from `routers_phase2`.
- Routes (all relative to the mount point):
  - `GET /me` → `{"shop": {"license_id","license_code","company_name","status"}, "key": {"id","name","key_prefix"}, "permissions": [...]}`
  - `GET /customers?q&stage&updated_since&limit&offset`, `GET /customers/{id}`, `POST /customers` (`CustomerCreate`), `PATCH /customers/{id}` (`CustomerPatch`)
  - `GET /deals?stage&q&updated_since&limit&offset`, `GET /deals/{id}` (deal + `items` = its product lines), `POST /deals` (`DealCreate`), `PATCH /deals/{id}` (`DealPatch`), `POST /deals/{id}/stage` (`DealStage`)
- Consumes: `api_principal` (Task 6); `DataClient.list_customers_with_total/get_customer/create_customer/update_customer/list_deals_with_total/get_deal/create_deal/update_deal/transition_deal_stage` and the deal-lines getter the dashboard uses (`grep -n "deals/{deal_id}/products" application/chann_app/data_client.py` — the GET one; call it `list_deal_products` below and rename to the real name).

- [ ] **Step 1: Failing tests**

Append to `tests/unit/test_round21b_ext_api.py`:

```python
from chann_app import routers_ext  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

ALL_STAFF = ["customer.read", "customer.create", "customer.update", "deal.read", "deal.create",
             "deal.update", "quote.read", "invoice.read", "invoice.create", "invoice.update",
             "ticket.read", "ticket.create", "warranty.read", "warranty.create",
             "product.read", "product.manage"]


def _ext(keys=ALL_STAFF, fake=None):
    """The ext app with the resolver replaced by a ready-made api principal."""
    client = fake or FakeDataClient(role="sales", permission_keys=list(keys))

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="api:k1", role="api", is_owner=False,
            permission_keys=frozenset(keys), audience="api",
        )

    from chann_app.routers_admin import get_data_client
    routers_ext.ext_app.dependency_overrides[get_data_client] = override_client
    routers_ext.ext_app.dependency_overrides[api_key_auth.api_principal] = override_principal
    return TestClient(routers_ext.ext_app), client


class TestExtCustomersAndDeals:
    def test_me_names_the_shop_and_the_permissions(self):
        http, _ = _ext()
        r = http.get("/me")
        assert r.status_code == 200, r.text
        assert r.json()["shop"]["license_id"] == LICENSE_ID
        assert "customer.read" in r.json()["permissions"]

    def test_lists_are_items_plus_total_with_the_header(self):
        http, client = _ext()
        client._customers = [
            {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี",
             "phone": "0812345678", "stage": "contact"},
            {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมหญิง", "last_name": "ดีใจ",
             "phone": "0898765432", "stage": "lead"},
        ]
        r = http.get("/customers?limit=1")
        assert r.status_code == 200, r.text
        assert r.headers["X-Total-Count"] == "2"
        assert r.json()["total"] == 2 and len(r.json()["items"]) == 1

    def test_limit_over_200_is_a_validation_error_in_the_ext_shape(self):
        http, _ = _ext()
        r = http.get("/customers?limit=201")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_a_missing_permission_is_forbidden_in_the_ext_shape(self):
        http, _ = _ext(keys=["deal.read"])
        r = http.get("/customers")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    def test_get_create_patch_customer(self):
        http, client = _ext()
        made = http.post("/customers", json={"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        assert made.status_code == 201, made.text
        cid = made.json()["id"]
        assert http.get(f"/customers/{cid}").status_code == 200
        assert http.get("/customers/nope").status_code == 404
        assert http.get("/customers/nope").json()["error"]["code"] == "not_found"
        patched = http.patch(f"/customers/{cid}", json={"email": "a@b.co"})
        assert patched.status_code == 200
        assert [w for w in client.recorded if w[0] == "update_customer"], client.recorded
        # The audit actor is the key, not a person.
        assert any(w[0] == "create_customer" and w[-1] == "api:k1" for w in client.recorded)

    def test_deal_create_lines_and_stage(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        deal = http.post("/deals", json={"customer_id": customer["id"], "amount": "1500.00"})
        assert deal.status_code == 201, deal.text
        did = deal.json()["id"]
        got = http.get(f"/deals/{did}")
        assert got.status_code == 200 and "items" in got.json()
        moved = http.post(f"/deals/{did}/stage", json={"stage": "lost", "lost_reason": "ราคา"})
        assert moved.status_code in (200, 409), moved.text  # 409 only if the fake refuses new→lost
        assert http.get("/deals?updated_since=not-a-date").status_code == 422
```

(`FakeDataClient` seeds ids `c1`… and records `("create_customer", license_id, payload, actor_id)`-style tuples; if a recorded tuple's last element is not the actor in your checkout, read `create_customer` in `tests/unit/test_phase6_chat.py` and adjust the two `recorded` assertions to its shape.)

- [ ] **Step 2: Run — fails (`ImportError: routers_ext`)**

- [ ] **Step 3: Implement the sub-app**

Create `application/chann_app/routers_ext.py`:

```python
"""Round 21B — the external API: /api/ext/v1.

A separate FastAPI app so its OpenAPI shows only this surface. Every
route depends on `api_principal` (the Bearer key), then calls the same
DataClient methods and service functions the dashboard routes call, so
tenant scoping, status machines and "a deal needs products" are all
enforced once, in the code that already enforces them.

Owner, 23 ก.ย. 2569: "ทำ API เลย" — for the customer's accounting/ERP
system first, which is why every list has `updated_since`.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth.api_key import api_principal
from .data_client import DataClient, DataTierError
from .routers_admin import get_data_client
from .routers_phase2 import _propagate
from .services.authorization import TenantPrincipal

DESCRIPTION = """
API สำหรับระบบภายนอก (บัญชี / ERP) ของร้านที่ใช้ Chann CRM AI

* ขอ key จากเจ้าของร้าน: แดชบอร์ด > จัดการร้าน > API
* ส่ง `Authorization: Bearer chann_live_…` ทุกคำขอ
* รายการทุกชนิดรับ `limit` (≤200), `offset` และตอบ `{"items": [...], "total": n}`
* `updated_since` (ISO-8601) บนลูกค้า ดีล งานซ่อม ใบแจ้งหนี้ — ใช้ sync เป็นรอบ
* จำกัด 600 คำขอ/นาที/key — เกินตอบ 429 พร้อม `Retry-After`

Errors are always `{"error": {"code": "...", "message": "..."}}`.
"""

ext_app = FastAPI(
    title="Chann CRM AI — Public API", version="1.0", description=DESCRIPTION,
    docs_url="/docs", redoc_url=None, openapi_url="/openapi.json",
)
router = APIRouter(dependencies=[Depends(api_principal)])

_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
          409: "conflict", 422: "validation_error", 423: "read_only", 429: "rate_limited",
          502: "upstream_error", 503: "unavailable"}


def _error_body(status_code: int, detail: Any) -> dict:
    if isinstance(detail, dict) and "code" in detail:
        return {"error": {"code": str(detail["code"]), "message": str(detail.get("message") or "")}}
    if isinstance(detail, dict):
        code = str(detail.get("reason_code") or detail.get("error") or _CODES.get(status_code, "error"))
        return {"error": {"code": code, "message": str(detail.get("message") or detail)}}
    return {"error": {"code": _CODES.get(status_code, "error"), "message": str(detail)}}


@ext_app.exception_handler(HTTPException)
async def _http_error(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.status_code, exc.detail),
                        headers=dict(exc.headers or {}))


@ext_app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
    return JSONResponse(status_code=422, content={"error": {
        "code": "validation_error", "message": f"{where}: {first.get('msg', 'invalid')}".strip(": "),
    }})


@ext_app.middleware("http")
async def _rate_headers(request: Request, call_next):
    response = await call_next(request)
    rate = getattr(request.state, "rate_limit", None)
    if rate:
        response.headers["X-RateLimit-Limit"] = str(rate[0])
        response.headers["X-RateLimit-Remaining"] = str(rate[1])
    return response


def _page(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> tuple[int, int]:
    return limit, offset


def _listing(response: Response, rows: list, total: int, limit: int, offset: int) -> dict:
    response.headers["X-Total-Count"] = str(total)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def _or_404(row: dict | None, what: str) -> dict:
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"{what} not found"})
    return row


# ------------------------------------------------------------------ me


@router.get("/me", summary="ร้านและ key นี้ · Who am I")
async def me(principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client)):
    try:
        shop = await client.get_company_profile(principal.license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    key_id = principal.chann_uid.split(":", 1)[-1]
    return {
        "shop": {"license_id": principal.license_id, "license_code": (shop or {}).get("license_code"),
                 "company_name": (shop or {}).get("company_name"), "status": principal.license_status},
        "key": {"id": key_id},
        "permissions": sorted(principal.permission_keys),
    }


# ------------------------------------------------------------------ customers


class CustomerCreate(BaseModel):
    first_name: str
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    stage: str | None = Field(default=None, description="lead | contact (default: contact)")


class CustomerPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None


@router.get("/customers", summary="รายชื่อลูกค้า · List customers")
async def list_customers(
    response: Response, q: str | None = None, stage: str | None = None,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("customer.read")
    limit, offset = page
    try:
        rows, total = await client.list_customers_with_total(
            principal.license_id, stage, limit=limit, q=q, offset=offset, updated_since=updated_since,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/customers/{customer_id}", summary="ลูกค้าหนึ่งราย · One customer")
async def get_customer(customer_id: str, principal: TenantPrincipal = Depends(api_principal),
                       client: DataClient = Depends(get_data_client)):
    principal.require("customer.read")
    try:
        return _or_404(await client.get_customer(principal.license_id, customer_id), "customer")
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/customers", status_code=201, summary="เพิ่มลูกค้า · Create a customer")
async def create_customer(payload: CustomerCreate, principal: TenantPrincipal = Depends(api_principal),
                          client: DataClient = Depends(get_data_client)):
    principal.require("customer.create")
    body = payload.model_dump(exclude_none=True)
    body.setdefault("stage", "contact")
    try:
        return await client.create_customer(principal.license_id, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/customers/{customer_id}", summary="แก้ไขลูกค้า · Update a customer")
async def update_customer(customer_id: str, payload: CustomerPatch,
                          principal: TenantPrincipal = Depends(api_principal),
                          client: DataClient = Depends(get_data_client)):
    principal.require("customer.update")
    try:
        return await client.update_customer(
            principal.license_id, customer_id, payload.model_dump(exclude_unset=True), actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


# ------------------------------------------------------------------ deals


class DealCreate(BaseModel):
    customer_id: str
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class DealPatch(BaseModel):
    amount: Decimal | None = None
    currency: str | None = None
    expected_close_date: date | None = None
    notes: str | None = None


class DealStage(BaseModel):
    stage: str = Field(description="new | proposed | won | lost")
    lost_reason: str | None = None


@router.get("/deals", summary="รายการดีล · List deals")
async def list_deals(
    response: Response, q: str | None = None, stage: str | None = None,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("deal.read")
    limit, offset = page
    try:
        rows, total = await client.list_deals_with_total(
            principal.license_id, stage, limit=limit, q=q, offset=offset, updated_since=updated_since,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/deals/{deal_id}", summary="ดีลพร้อมรายการสินค้า · One deal with its lines")
async def get_deal(deal_id: str, principal: TenantPrincipal = Depends(api_principal),
                   client: DataClient = Depends(get_data_client)):
    principal.require("deal.read")
    try:
        deal = _or_404(await client.get_deal(principal.license_id, deal_id), "deal")
        items = await client.list_deal_products(principal.license_id, str(deal["id"]))
    except DataTierError as exc:
        raise _propagate(exc)
    return {**deal, "items": items}


@router.post("/deals", status_code=201, summary="สร้างดีล · Create a deal")
async def create_deal(payload: DealCreate, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    principal.require("deal.create")
    body = payload.model_dump(mode="json", exclude_none=True)
    body["contact_id"] = body.pop("customer_id")
    try:
        return await client.create_deal(principal.license_id, body, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/deals/{deal_id}", summary="แก้ไขดีล · Update a deal")
async def update_deal(deal_id: str, payload: DealPatch, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    principal.require("deal.update")
    try:
        return await client.update_deal(
            principal.license_id, deal_id, payload.model_dump(mode="json", exclude_unset=True),
            actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/deals/{deal_id}/stage", summary="ย้ายสถานะดีล · Move a deal's stage")
async def set_deal_stage(deal_id: str, payload: DealStage, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    principal.require("deal.update")
    try:
        return await client.transition_deal_stage(
            principal.license_id, deal_id, payload.stage, lost_reason=payload.lost_reason,
            allow_reopen=False, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


ext_app.include_router(router)
```

Before running: confirm the deal-lines getter's real name (`grep -n 'deals/{deal_id}/products' application/chann_app/data_client.py` → the `GET` method) and replace `list_deal_products` with it; confirm `get_company_profile` returns `license_code`/`company_name` (it is what the company page reads).

- [ ] **Step 4: Run — the class passes.** If `FakeDataClient` lacks `transition_deal_stage` or the lines getter, add minimal fakes to `tests/unit/test_phase6_chat.py`'s `FakeDataClient` following its `recorded.append((...))` convention (return the deal dict with `stage` updated; return `[]` for lines).

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): /api/ext/v1 — the sub-app, one error shape, /me, customers and deals" && git log --oneline -1
```

---

### Task 8: ext quotes, invoices, payments, PDF links

**Files:**
- Modify: `application/chann_app/routers_ext.py` (append before `ext_app.include_router(router)`)
- Test: `tests/unit/test_round21b_ext_api.py` (class `TestExtBilling`)

**Interfaces:**
- Routes: `GET /quotes?deal_id&status&q`, `GET /quotes/{id}` (`{"quote", "deal", "customer", "items"}`), `GET /quotes/{id}/pdf`; `GET /invoices?status&deal_id&quote_id&overdue&updated_since`, `GET /invoices/{id}`, `POST /invoices` (`InvoiceCreate {deal_id?, quote_id?, note?, issue: bool=false}`), `POST /invoices/{id}/payments` (`PaymentIn {amount?, full: bool=false, method?, reference?, note?, paid_at?}`), `GET /invoices/{id}/pdf`, `GET /invoices/{id}/receipt-pdf`.
- Consumes from `routers_phase2`: `_create_invoice(client, principal, license_id, *, quote_id, deal_id, note)`, `_invoice_or_404(client, principal, license_id, invoice_id)`, `_invoice_document_error`, `_with_reason`; from `services.invoices`: `issue_invoice_document`, `record_payment`, `PaymentInvalid`; from `auth.document_link`: `issue_document_token(license_id, document_id)`; `settings.public_base_url`.

- [ ] **Step 1: Failing tests**

Append:

```python
class TestExtBilling:
    def _billable(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        deal = http.post("/deals", json={"customer_id": customer["id"]}).json()
        return http, client, customer, deal

    def test_an_invoice_from_a_deal_without_lines_is_a_409_in_the_ext_shape(self):
        http, client, customer, deal = self._billable()
        r = http.post("/invoices", json={"deal_id": deal["id"]})
        assert r.status_code in (409, 422), r.text
        assert r.json()["error"]["code"]

    def test_invoice_list_get_and_pdf_link(self):
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None,
                             "generated_document_id": "doc-1", "receipt_document_id": None}]
        listed = http.get("/invoices?status=issued")
        assert listed.status_code == 200 and listed.json()["total"] == 1
        one = http.get("/invoices/INV-A")
        assert one.status_code == 200 and one.json()["invoice_id"] == "INV-2026-0001"
        pdf = http.get("/invoices/INV-A/pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.json()["url"].startswith("http") and "expires_at" in pdf.json()
        assert http.get("/invoices/INV-A/receipt-pdf").status_code == 404

    def test_a_payment_needs_an_amount_unless_full(self):
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None}]
        r = http.post("/invoices/INV-A/payments", json={"method": "transfer"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"

    def test_without_invoice_read_the_list_is_forbidden(self):
        http, _ = _ext(keys=["customer.read"])
        assert http.get("/invoices").status_code == 403
```

(`test_round20v_invoices.py` shows how the fake records a payment; if `POST …/payments` with `{"full": true}` is worth a green test in your checkout, add it using that file's `_shop()` seeding — optional.)

- [ ] **Step 2: Run — fails (404 on `/invoices`)**

- [ ] **Step 3: Implement**

Insert before `ext_app.include_router(router)`:

```python
# ------------------------------------------------------------------ documents


def _document_link(license_id: str, document_id: str | None, what: str) -> dict:
    """A signed https link, the same one the dashboard's "เปิด PDF" uses."""
    from .auth.document_link import issue_document_token
    from .config import settings

    if not document_id:
        raise HTTPException(status_code=404, detail={"code": "not_issued", "message": f"{what} has no document yet"})
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail={"code": "unavailable", "message": "no public base URL configured"})
    token = issue_document_token(license_id, document_id)
    ttl_days = 7
    expires = datetime.now(tz=__import__("datetime").timezone.utc) + __import__("datetime").timedelta(days=ttl_days)
    return {"url": f"{base}/api/v1/documents/{token}", "expires_at": expires.isoformat()}


# ------------------------------------------------------------------ quotes


@router.get("/quotes", summary="รายการใบเสนอราคา · List quotes")
async def list_quotes(
    response: Response, deal_id: str | None = None, status_: str | None = Query(default=None, alias="status"),
    q: str | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("quote.read")
    limit, offset = page
    try:
        rows, total = await client.list_quotes_with_total(
            principal.license_id, status_, limit=limit, q=q, offset=offset, deal_id=deal_id,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/quotes/{quote_id}", summary="ใบเสนอราคาพร้อมรายการ · One quote with its lines")
async def get_quote(quote_id: str, principal: TenantPrincipal = Depends(api_principal),
                    client: DataClient = Depends(get_data_client)):
    principal.require("quote.read")
    try:
        quote = _or_404(await client.get_quote(principal.license_id, quote_id), "quote")
        deal = await client.get_deal(principal.license_id, str(quote["deal_id"]))
        customer = await client.get_customer(principal.license_id, str(deal["contact_id"])) if deal and deal.get("contact_id") else None
        items = await client.list_deal_products(principal.license_id, str(quote["deal_id"]))
    except DataTierError as exc:
        raise _propagate(exc)
    return {"quote": quote, "deal": deal, "customer": customer, "items": items}


@router.get("/quotes/{quote_id}/pdf", summary="ลิงก์ PDF ใบเสนอราคา · Quote PDF link")
async def quote_pdf(quote_id: str, principal: TenantPrincipal = Depends(api_principal),
                    client: DataClient = Depends(get_data_client)):
    principal.require("quote.read")
    try:
        quote = _or_404(await client.get_quote(principal.license_id, quote_id), "quote")
    except DataTierError as exc:
        raise _propagate(exc)
    return _document_link(principal.license_id, quote.get("generated_document_id"), "quote")


# ------------------------------------------------------------------ invoices


class InvoiceCreate(BaseModel):
    deal_id: str | None = None
    quote_id: str | None = None
    note: str | None = None
    issue: bool = Field(default=False, description="true = ออกเอกสาร PDF ทันที (draft → issued)")


class PaymentIn(BaseModel):
    amount: Decimal | None = None
    full: bool = False
    method: str | None = Field(default=None, description="cash | transfer | card | other")
    reference: str | None = None
    note: str | None = None
    paid_at: datetime | None = None


@router.get("/invoices", summary="รายการใบแจ้งหนี้ · List invoices")
async def list_invoices(
    response: Response, status_: str | None = Query(default=None, alias="status"),
    deal_id: str | None = None, quote_id: str | None = None, overdue: bool = False,
    updated_since: datetime | None = None, page: tuple[int, int] = Depends(_page),
    principal: TenantPrincipal = Depends(api_principal), client: DataClient = Depends(get_data_client),
):
    principal.require("invoice.read")
    limit, offset = page
    try:
        rows, total = await client.list_invoices_with_total(
            principal.license_id, status=status_, deal_id=deal_id, quote_id=quote_id, overdue=overdue,
            limit=limit, offset=offset, updated_since=updated_since,
        )
    except DataTierError as exc:
        raise _propagate(exc)
    return _listing(response, rows, total, limit, offset)


@router.get("/invoices/{invoice_id}", summary="ใบแจ้งหนี้พร้อมการชำระ · One invoice with payments")
async def get_invoice(invoice_id: str, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    return await _invoice_or_404(client, principal, principal.license_id, invoice_id)


@router.post("/invoices", status_code=201, summary="ออกใบแจ้งหนี้จากดีลหรือใบเสนอราคา · Create an invoice")
async def create_invoice(payload: InvoiceCreate, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _create_invoice, _invoice_document_error
    from .services import invoices as invoice_service

    principal.require("invoice.create")
    invoice = await _create_invoice(
        client, principal, principal.license_id, quote_id=payload.quote_id, deal_id=payload.deal_id, note=payload.note,
    )
    if not payload.issue:
        return invoice
    principal.require("invoice.update")
    try:
        company = await client.get_company_profile(principal.license_id)
        invoice, _document = await invoice_service.issue_invoice_document(
            client, license_id=principal.license_id, invoice=invoice, company=company,
            actor_id=principal.chann_uid, allow_reissue=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))
    return invoice


@router.post("/invoices/{invoice_id}/payments", status_code=201, summary="บันทึกรับชำระ · Record a payment")
async def record_payment(invoice_id: str, payload: PaymentIn, principal: TenantPrincipal = Depends(api_principal),
                         client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_document_error, _invoice_or_404, _with_reason
    from .services import invoices as invoice_service

    principal.require("invoice.update")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    if not payload.full and payload.amount in (None, ""):
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "amount is required unless full=true"})
    try:
        return await invoice_service.record_payment(
            client, license_id=principal.license_id, invoice=invoice, amount=payload.amount,
            method=payload.method, reference=payload.reference, note=payload.note,
            paid_at=payload.paid_at, actor_id=principal.chann_uid, full=payload.full,
        )
    except invoice_service.PaymentInvalid as exc:
        raise HTTPException(status_code=422, detail={"code": "payment_invalid", "message": str(exc)})
    except DataTierError as exc:
        raise _with_reason(exc)
    except Exception as exc:  # noqa: BLE001
        raise _invoice_document_error(exc, code=str(invoice.get("invoice_id") or ""))


@router.get("/invoices/{invoice_id}/pdf", summary="ลิงก์ PDF ใบแจ้งหนี้ · Invoice PDF link")
async def invoice_pdf(invoice_id: str, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    return _document_link(principal.license_id, invoice.get("generated_document_id"), "invoice")


@router.get("/invoices/{invoice_id}/receipt-pdf", summary="ลิงก์ PDF ใบเสร็จ · Receipt PDF link")
async def receipt_pdf(invoice_id: str, principal: TenantPrincipal = Depends(api_principal),
                      client: DataClient = Depends(get_data_client)):
    from .routers_phase2 import _invoice_or_404

    principal.require("invoice.read")
    invoice = await _invoice_or_404(client, principal, principal.license_id, invoice_id)
    return _document_link(principal.license_id, invoice.get("receipt_document_id"), "receipt")
```

Tidy the two `__import__("datetime")` calls in `_document_link` into a proper `from datetime import timedelta, timezone` at the top of the module. Confirm the document download path: `grep -n '@router.get("/documents/{token}")' application/chann_app/routers_phase2.py` — the ext link must be `<public_base_url>/api/v1/documents/<token>` (the same URL `get_document_link` in `routers_phase2.py` builds; copy its exact f-string). The test that `_invoice_or_404` needs the principal to be staff: `TenantPrincipal.is_customer` is False for audience "api", so it passes.

- [ ] **Step 4: Run — `TestExtBilling` passes**; for the PDF test set `settings.public_base_url` in the test with `monkeypatch.setattr(settings, "public_base_url", "https://app.example")` (import `from chann_app.config import settings`) if the fixture does not already.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): ext quotes, invoices, payments and signed PDF links" && git log --oneline -1
```

---

### Task 9: ext tickets, warranties, products

**Files:**
- Modify: `application/chann_app/routers_ext.py`
- Test: `tests/unit/test_round21b_ext_api.py` (class `TestExtServiceAndCatalogue`)

**Interfaces:**
- Routes: `GET /tickets?status&customer_id&q&updated_since`, `GET /tickets/{id}`, `POST /tickets` (`TicketCreate {customer_id, problem, address?, appointment_at?}`); `GET /warranties?q&status`, `GET /warranties/{id}`, `POST /warranties` (`WarrantyCreate {serial_number, product_id?, customer_id?, purchase_date?, warranty_months?}`); `GET /products?q&category&include_archived`, `GET /products/{id}`, `POST /products`, `PATCH /products/{id}`.
- Consumes: `DataClient.list_tickets_with_total/get_ticket/create_ticket`, `list_warranties_with_total`, the warranty getter/creator and product create/update/get methods the dashboard routes call — find each with `grep -n '@router.post("/licenses/{license_id}/warranties"' application/chann_app/routers_phase2.py` (and `products`, `tickets`) and call the same `client.` method with the same payload keys.

- [ ] **Step 1: Failing tests**

```python
class TestExtServiceAndCatalogue:
    def test_tickets_list_get_create(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        made = http.post("/tickets", json={"customer_id": customer["id"], "problem": "แอร์ไม่เย็น"})
        assert made.status_code == 201, made.text
        assert http.get("/tickets").status_code == 200
        assert http.get(f"/tickets/{made.json()['id']}").status_code == 200
        assert http.get("/tickets/nope").status_code == 404

    def test_products_list_and_create_need_the_manage_key_to_write(self):
        http, client = _ext(keys=["product.read"])
        assert http.get("/products").status_code == 200
        assert http.post("/products", json={"name": "แอร์", "unit_price": "15900"}).status_code == 403

    def test_warranties_list_and_register(self):
        http, client = _ext()
        made = http.post("/warranties", json={"serial_number": "SN-ERP-1", "product_id": None})
        assert made.status_code == 201, made.text
        assert http.get("/warranties?q=SN-ERP").status_code == 200
```

- [ ] **Step 2: Run — fails (404)**

- [ ] **Step 3: Implement** — same shape as Task 7's customers block for each resource: `principal.require("ticket.read")` / `"ticket.create"`, `"warranty.read"` / `"warranty.create"`, `"product.read"` / `"product.manage"`; list via the `*_with_total` methods with `_listing`; get via `_or_404`; create/patch via the dashboard's own `client.` method with `actor_id=principal.chann_uid`. `TicketCreate.customer_id` maps to the payload key the dashboard's ticket form sends (`contact_id`); `appointment_at` to its field name (read `POST /licenses/{license_id}/tickets` in `routers_phase2.py`).

- [ ] **Step 4: Run — passes**; add fakes to `FakeDataClient` where missing, following its conventions.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): ext tickets, warranties and products" && git log --oneline -1
```

---

### Task 10: Mount the sub-app; boundary tests for the ext surface

**Files:**
- Modify: `application/chann_app/main.py:19-22`
- Modify: `tests/boundary/test_tier_boundaries.py` (append a class)

- [ ] **Step 1: Failing boundary tests**

Append to `tests/boundary/test_tier_boundaries.py`:

```python
class TestExternalApi:
    """Round 21B — the outside surface is small, documented and locked."""

    def _ext(self):
        import sys
        sys.path.insert(0, str(ROOT / "application"))
        from chann_app.main import app
        from chann_app.routers_ext import ext_app
        return app, ext_app

    def test_the_sub_app_is_mounted_at_the_versioned_path(self):
        app, ext_app = self._ext()
        mounts = [getattr(r, "path", "") for r in app.routes if r.__class__.__name__ == "Mount"]
        assert "/api/ext/v1" in mounts

    def test_its_openapi_lists_only_ext_routes_and_no_platform_or_member_paths(self):
        _app, ext_app = self._ext()
        paths = set(ext_app.openapi()["paths"])
        assert "/me" in paths and "/customers" in paths and "/invoices/{invoice_id}/payments" in paths
        assert not any(p.startswith(("/platform", "/liff", "/licenses")) for p in paths)
        assert not any("member" in p or "role" in p or "setting" in p or "chat" in p for p in paths)

    def test_every_ext_route_refuses_a_request_without_a_key(self):
        from fastapi.testclient import TestClient
        _app, ext_app = self._ext()
        http = TestClient(ext_app, raise_server_exceptions=False)
        for route in ext_app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if not methods or path in ("/docs", "/openapi.json", "/docs/oauth2-redirect"):
                continue
            probe = re.sub(r"\{[^}]+\}", "x", path)
            for method in methods:
                r = http.request(method, probe)
                assert r.status_code == 401, (method, path, r.status_code)
                assert r.json()["error"]["code"] == "missing_or_malformed_key"

    def test_the_ext_module_and_the_key_resolver_import_no_persistence(self):
        for rel in ("application/chann_app/routers_ext.py", "application/chann_app/auth/api_key.py"):
            assert not (_imported_roots(ROOT / rel) & PERSISTENCE_MODULES), rel
```

- [ ] **Step 2: Run — the mount test fails**

Run: `cd ~/stage-fix/r20i && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/boundary/test_tier_boundaries.py -q -k ExternalApi`

- [ ] **Step 3: Mount**

In `application/chann_app/main.py` add `from . import routers_ext` to the imports and, after the four `include_router` lines:

```python
# Round 21B: the outside surface, with its own OpenAPI at /api/ext/v1/docs.
app.mount("/api/ext/v1", routers_ext.ext_app)
```

- [ ] **Step 4: Run — `4 passed`**; then the whole boundary file and `scripts/dev/check-routes.py` (must still say every dashboard call maps).

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): mount /api/ext/v1 and lock its surface with boundary tests" && git log --oneline -1
```

---

### Task 11: The owner's key-management routes (LIFF, `routers_phase2.py`)

**Files:**
- Modify: `application/chann_app/routers_phase2.py` (append near the invite routes, ~line 5278)
- Test: `tests/unit/test_round21b_ext_api.py` (class `TestOwnerKeyRoutes`)

**Interfaces:**
- Produces (`/api/v1`, LIFF headers as every dashboard route):
  - `GET /licenses/{license_id}/api-keys` → `{"keys": [ApiKeyOut…without revoked], "docs_url": str | null}`
  - `POST /licenses/{license_id}/api-keys` body `{"name": str}` → 201 `ApiKeyCreatedOut` (plaintext `key` present once)
  - `POST /licenses/{license_id}/api-keys/{key_id}/revoke` → `ApiKeyOut`
  - all three: `_require_same_tenant`, `principal.require("setting.manage")`, then `_owner_only(principal)` → 403 `{"error": "owner_only", "reason_code": "owner_only", "message": …}`.
- `_owner_only(principal: TenantPrincipal) -> None` helper next to `_staff_only`.

- [ ] **Step 1: Failing tests**

Append to `tests/unit/test_round21b_ext_api.py`:

```python
from chann_app import routers_phase2  # noqa: E402


class _KeyFake(FakeDataClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._api_keys: list[dict] = []

    async def create_api_key(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_api_key", license_id, payload, actor_id))
        row = {"id": f"k{len(self._api_keys) + 1}", "license_id": license_id, "name": payload["name"],
               "key_prefix": "chann_live_ab12", "created_by_chann_uid": actor_id, "last_used_at": None,
               "revoked_at": None, "created_at": "2026-09-23T00:00:00+00:00"}
        self._api_keys.append(row)
        return {**row, "key": "chann_live_" + "ab12" + "x" * 28}

    async def list_api_keys(self, license_id):
        self.recorded.append(("list_api_keys", license_id))
        return list(self._api_keys)

    async def revoke_api_key(self, license_id, key_id, actor_id=None):
        self.recorded.append(("revoke_api_key", license_id, key_id, actor_id))
        for row in self._api_keys:
            if row["id"] == key_id:
                row["revoked_at"] = "2026-09-23T01:00:00+00:00"
                return row
        from chann_app.data_client import DataTierError
        raise DataTierError(404, "api key not found")


def _liff(is_owner=True, keys=("setting.manage",)):
    client = _KeyFake(role="sales", permission_keys=list(keys))

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(license_id=LICENSE_ID, chann_uid="CHN-OWNER", role="owner",
                               is_owner=is_owner, permission_keys=frozenset(keys), audience="sales")

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


class TestOwnerKeyRoutes:
    def test_the_owner_makes_sees_and_revokes_a_key(self, monkeypatch):
        from chann_app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        http, client = _liff()
        made = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert made.status_code == 201, made.text
        assert made.json()["key"].startswith("chann_live_")
        listed = http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys")
        assert listed.status_code == 200
        assert listed.json()["docs_url"] == "https://app.example/api/ext/v1/docs"
        assert [k["id"] for k in listed.json()["keys"]] == ["k1"] and "key" not in listed.json()["keys"][0]
        gone = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys/k1/revoke")
        assert gone.status_code == 200 and gone.json()["revoked_at"]
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").json()["keys"] == []

    def test_an_admin_with_setting_manage_but_not_owner_is_refused(self):
        http, client = _liff(is_owner=False)
        r = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert r.status_code == 403 and r.json()["detail"]["reason_code"] == "owner_only"
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").status_code == 403
        assert client.recorded == []

    def test_without_setting_manage_it_is_the_ordinary_403(self):
        http, _ = _liff(keys=("customer.read",))
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").status_code == 403

    def test_a_blank_name_is_422(self):
        http, _ = _liff()
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "   "}).status_code == 422
```

- [ ] **Step 2: Run — fails (404)**

- [ ] **Step 3: Implement**

In `routers_phase2.py`, next to `_staff_only` (~line 2253):

```python
def _owner_only(principal: TenantPrincipal) -> None:
    """Round 21B: API keys are the owner's alone — an admin with
    setting.manage runs the shop's settings, not its outside access."""
    if not principal.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "error": "owner_only", "reason_code": "owner_only",
            "message": "only the shop owner manages API keys",
        })
```

Append after the invite routes:

```python
# ------------------------------------------------------------ round 21B: API keys


class ApiKeyCreateBody(BaseModel):
    name: str


def _api_docs_url() -> str | None:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/api/ext/v1/docs" if base else None


@router.get("/licenses/{license_id}/api-keys")
async def list_api_keys(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The shop's live keys (revoked ones are kept for the audit trail
    but not shown) and where the outside party reads the docs."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    _owner_only(principal)
    try:
        rows = await client.list_api_keys(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return {"keys": [r for r in rows if not r.get("revoked_at")], "docs_url": _api_docs_url()}


@router.post("/licenses/{license_id}/api-keys", status_code=201)
async def create_api_key(
    license_id: str,
    payload: ApiKeyCreateBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """The one response that carries the key. Nothing stores it after this."""
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    _owner_only(principal)
    name = " ".join(payload.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    try:
        return await client.create_api_key(
            license_id, {"name": name, "created_by_chann_uid": principal.chann_uid}, actor_id=principal.chann_uid,
        )
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/api-keys/{key_id}/revoke")
async def revoke_api_key(
    license_id: str,
    key_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    _require_same_tenant(principal, license_id)
    principal.require("setting.manage")
    _owner_only(principal)
    try:
        return await client.revoke_api_key(license_id, key_id, actor_id=principal.chann_uid)
    except DataTierError as exc:
        raise _propagate(exc)
```

- [ ] **Step 4: Run — passes**; then `JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/boundary -q` (the guard test must stay green) and `/tmp/dv/bin/python scripts/dev/check-perms.py`.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): the owner makes, sees and revokes API keys from the dashboard routes" && git log --oneline -1
```

---

### Task 12: Dashboard page — จัดการร้าน > API (owner only)

Use the `ui-ux-pro-max` skill first (`python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "api key reveal once copy revoke" --domain ux` and `"destructive action confirmation" --domain ux`) and keep to what it says: one primary action, the secret shown once with a copy control, revoke separated and confirmed, 44px targets, visible labels.

**Files:**
- Modify: `presentation/app/liff/_nav-model.tsx` (`NavEntry.ownerOnly`, `mayOpen`, `ICONS.apiKeys`, the entry in the `shop` group after `roles`)
- Create: `presentation/app/liff/sales/api-keys/page.tsx`, `presentation/app/liff/sales/api-keys/ApiKeys.tsx`
- Modify: `presentation/lib/i18n/th.ts`, `presentation/lib/i18n/en.ts` (`dashboard.apiKeys`)
- Modify: `presentation/app/globals.css` (append)
- Modify: `scripts/dev/check-parity.py` (`URL_ENTITIES` + `ACCEPTED`)
- Verify: `cd presentation && npm run typecheck && npm run build`; `scripts/dev/check-routes.py`, `check-i18n-usage.py`, `check-parity.py`; `tests/boundary/test_a11y.py`.

**Interfaces:**
- Consumes the three routes of Task 11 through the proxy: `/api/phase2/licenses/${licenseId}/api-keys` (GET, POST) and `/api/phase2/licenses/${licenseId}/api-keys/${id}/revoke` (POST).
- Produces `t.dashboard.apiKeys.{title,intro,docs,empty,name,namePlaceholder,create,creating,created,keyOnce,copy,copied,revoke,revokeAsk,revoked,lastUsed,neverUsed,createdOn,ownerOnly,loadFailed}`.

- [ ] **Step 1: Nav model**

In `_nav-model.tsx`: add `ownerOnly?: boolean;` to `NavEntry` (after `exact`), make `mayOpen` start with `if (entry.ownerOnly && !isOwner) return false;`, add an icon

```tsx
  apiKeys: glyph(
    <>
      <circle cx="8" cy="12" r="3.5" />
      <path d="M11.5 12H20" />
      <path d="M17 12v3" />
      <path d="M20 12v2.5" />
    </>,
  ),
```

and, in the `shop` group right after the `roles` entry:

```tsx
        // Round 21B: the owner's outside access. Owner only — an admin
        // runs settings, not who may read the shop from outside.
        { key: "apiKeys", href: "/liff/sales/api-keys", label: t.dashboard.apiKeys.title, icon: ICONS.apiKeys, needs: ["setting.manage"], ownerOnly: true },
```

Also add `"api-keys": "api-keys"` to `DASHBOARD_PATHS` in `application/chann_app/services/chat.py` (Task 13 uses it).

- [ ] **Step 2: Strings**

`th.ts`, inside `dashboard: {` after the `teams: {…}` block:

```ts
    apiKeys: {
      title: "API สำหรับระบบภายนอก",
      intro: "สร้าง key ให้ระบบบัญชี/ERP หรือระบบอื่นเรียกข้อมูลร้านนี้ได้ key ทำงานในนามร้าน เจ้าของร้านเป็นคนสร้างและเพิกถอนเท่านั้น",
      docs: "เอกสาร API (ส่งให้ผู้พัฒนาระบบภายนอก)",
      empty: "ยังไม่มี key — สร้างอันแรกแล้วส่งให้ผู้พัฒนาระบบภายนอก",
      name: "ชื่อ key (ตั้งตามระบบที่จะใช้)",
      namePlaceholder: "เช่น ระบบบัญชี Express",
      create: "สร้าง key",
      creating: "กำลังสร้าง…",
      created: "สร้าง key แล้ว",
      keyOnce: "คัดลอกเก็บไว้ตอนนี้ — จะไม่แสดงอีก ถ้าหาย ให้สร้างใหม่แล้วเพิกถอนอันเดิม",
      copy: "คัดลอก",
      copied: "คัดลอกแล้ว",
      done: "ปิด",
      revoke: "เพิกถอน",
      revokeAsk: "เพิกถอน key \"{name}\"? ระบบภายนอกที่ใช้ key นี้จะเรียกไม่ได้ทันที",
      revoked: "เพิกถอนแล้ว",
      lastUsed: "ใช้ล่าสุด {when}",
      neverUsed: "ยังไม่เคยใช้",
      createdOn: "สร้างเมื่อ {when}",
      ownerOnly: "หน้านี้สำหรับเจ้าของร้านเท่านั้น",
    },
```

`en.ts` mirror: title "API for outside systems", intro "Make a key so an accounting/ERP or other system can read and write this shop's data. A key acts as the shop; only the owner creates or revokes one.", docs "API documentation (for the outside developer)", empty "No keys yet — make the first one and hand it to the outside developer", name "Key name (after the system that will use it)", namePlaceholder "e.g. Express accounting", create "Create key", creating "Creating…", created "Key created", keyOnce "Copy it now — it will not be shown again. If it is lost, make a new one and revoke this one.", copy "Copy", copied "Copied", done "Done", revoke "Revoke", revokeAsk "Revoke \"{name}\"? Any system using it stops working at once.", revoked "Revoked", lastUsed "Last used {when}", neverUsed "Never used", createdOn "Created {when}", ownerOnly "This page is for the shop owner only".

- [ ] **Step 3: The page**

`page.tsx`:

```tsx
import ApiKeys from "./ApiKeys";

export const dynamic = "force-dynamic";

export default function SalesApiKeysPage() {
  return <ApiKeys liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
```

`ApiKeys.tsx`:

```tsx
"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ConfirmDialog, useConfirm } from "../../_confirm";
import { Sheet } from "../../_sheet";
import { shortDate } from "../../_list-controls";
import { Empty } from "../_components";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { RecordActions } from "../_record";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";

type ApiKey = {
  id: string; name: string; key_prefix: string; created_at: string;
  last_used_at: string | null; revoked_at: string | null;
};

/**
 * Round 21B — the owner's outside access.
 *
 * One list, one primary action. The key is shown exactly once, in the
 * sheet that made it, with a copy control and the sentence that says so;
 * after "ปิด" only the prefix survives. Revoking is a separate, red,
 * confirmed action (ui-ux-pro-max: destructive-nav-separation,
 * confirmation-dialogs). Owner only: the rail hides the entry for anyone
 * else and the route answers 403 owner_only.
 */
export default function ApiKeys({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const c = t.dashboard.apiKeys;
  const failureText = useFailureText();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [docsUrl, setDocsUrl] = useState<string | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, isOwner } = session;
  const headers = token && licenseId ? proxyHeaders(token, licenseId) : undefined;

  const load = useCallback(async () => {
    if (!headers) return;
    const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys`, { headers });
    if (!res.ok) throw new Error(res.status === 403 ? c.ownerOnly : `${t.dashboard.loadFailed} (${res.status})`);
    const body = (await res.json()) as { keys: ApiKey[]; docs_url: string | null };
    setKeys(body.keys);
    setDocsUrl(body.docs_url);
  }, [headers, licenseId, c.ownerOnly, t.dashboard.loadFailed]);

  useEffect(() => {
    if (!session.ready) return;
    load().then(() => say(""), (e: Error) => say(e.message, "error"));
  }, [session.ready, load, say]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!headers || !name.trim()) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys`, {
        method: "POST", headers, body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) throw new Error(await failureText(res));
      const made = (await res.json()) as ApiKey & { key: string };
      setFresh({ name: made.name, key: made.key });
      setName("");
      setCopied(false);
      await load();
      say(c.created, "ok");
    } catch (e) {
      say((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function copyKey() {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function askRevoke(row: ApiKey) {
    ask({
      title: c.revoke,
      message: c.revokeAsk.replace("{name}", row.name),
      confirmLabel: c.revoke,
      onConfirm: async () => {
        if (!headers) return;
        const res = await fetch(`/api/phase2/licenses/${licenseId}/api-keys/${row.id}/revoke`, { method: "POST", headers });
        if (!res.ok) { say(await failureText(res), "error"); return; }
        await load();
        say(c.revoked, "ok");
      },
    });
  }

  return (
    <SalesShell session={session} title={c.title} back="/liff/sales" liffId={liffId}
      status={status} statusTone={tone} onSdkError={() => say(t.liff.sdkLoadFailed, "error")}>
      <p className="page-intro">{c.intro}</p>
      {docsUrl && (
        <p className="card-meta"><a href={docsUrl} target="_blank" rel="noreferrer">{c.docs}</a></p>
      )}
      {session.ready && !isOwner ? (
        <Empty message={c.ownerOnly} />
      ) : (
        <>
          <div className="list-head">
            <span className="count">{keys.length}</span>
            <div className="list-tools">
              <button type="button" className="btn" data-variant="primary" onClick={() => setCreating(true)} disabled={busy}>
                {c.create}
              </button>
            </div>
          </div>
          {keys.length === 0 ? (
            <Empty message={c.empty} />
          ) : (
            <ul className="list">
              {keys.map((row) => (
                <li key={row.id} className="card">
                  <div className="card-title">
                    {row.name}
                    <span className="code" style={{ marginLeft: 8 }}>{row.key_prefix}…</span>
                  </div>
                  <p className="card-meta">
                    {c.createdOn.replace("{when}", shortDate(row.created_at))} ·{" "}
                    {row.last_used_at ? c.lastUsed.replace("{when}", shortDate(row.last_used_at)) : c.neverUsed}
                  </p>
                  <RecordActions danger={
                    <button type="button" className="btn" data-variant="danger" onClick={() => askRevoke(row)} disabled={busy}>
                      {c.revoke}
                    </button>
                  } />
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <Sheet open={creating} title={fresh ? c.created : c.create} onClose={() => { setCreating(false); setFresh(null); }}>
        {fresh ? (
          <div className="api-key-reveal">
            <p className="card-meta">{fresh.name}</p>
            <output className="api-key-value" aria-live="polite">{fresh.key}</output>
            <p className="api-key-once">{c.keyOnce}</p>
            <div className="actions">
              <button type="button" className="btn" data-variant="primary" onClick={copyKey}>{copied ? c.copied : c.copy}</button>
              <button type="button" className="btn" data-variant="quiet" onClick={() => { setCreating(false); setFresh(null); }}>{c.done}</button>
            </div>
          </div>
        ) : (
          <form onSubmit={create}>
            <label className="field">
              <span>{c.name}</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder={c.namePlaceholder} maxLength={120} required />
            </label>
            <div className="actions">
              <button type="submit" className="btn" data-variant="primary" disabled={busy || !name.trim()}>
                {busy ? c.creating : c.create}
              </button>
            </div>
          </form>
        )}
      </Sheet>
      <ConfirmDialog request={confirming} onClose={closeConfirm} />
    </SalesShell>
  );
}
```

Check three signatures against the files before building and adapt: `RecordActions` props in `_record.tsx` (it takes `title` + `children` + optional `danger`; pass `title={c.title}` hidden or use the `actions record-danger` row the invoice sheet uses if `title` is required), `useConfirm().ask` request shape in `_confirm.tsx` (`ConfirmRequest` keys), and `shortDate` in `_list-controls.tsx`. `Empty` is in `sales/_components.tsx`.

- [ ] **Step 4: CSS** — append to `globals.css`:

```css
/* Round 21B — the API key, shown once. Monospace, wrapping, selectable;
   a box a thumb can hold to copy on a phone. */
.api-key-reveal { display: grid; gap: 12px; }
.api-key-value {
  display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 15px;
  padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--paper);
  word-break: break-all; user-select: all; line-height: 1.5;
}
.api-key-once { margin: 0; color: var(--ink-soft); font-size: 14px; }
```

(Use the variable names `globals.css` already defines for line/paper/soft ink — read its `:root` block and substitute.)

- [ ] **Step 5: Parity checker** — in `scripts/dev/check-parity.py` add `("api-keys", "api_key"),` to `URL_ENTITIES` and to `ACCEPTED`:

```python
    ("api_key", "create"): (
        "dashboard only — the key is a secret shown once; chat's \"สร้าง API key\" answers with the "
        "button that opens the API page rather than putting a credential in a LINE thread (round 21B)"
    ),
```

- [ ] **Step 6: Build and check**

```bash
cd ~/stage-fix/r20i/presentation && npm run typecheck && npm run build
cd ~/stage-fix/r20i && /tmp/dv/bin/python scripts/dev/check-routes.py && /tmp/dv/bin/python scripts/dev/check-i18n-usage.py && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/boundary/test_a11y.py -q
```
(`check-parity.py` goes green only after Task 13 registers the chat side.)

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): จัดการร้าน > API — the owner's page: one list, one key shown once, revoke apart and confirmed" && git log --oneline -1
```

---

### Task 13: Chat — list, revoke (confirmed), and the pointer for "create"

Model-first: the sentences reach the model first; the handler acts on `(action, entity)` the model proposes. Before writing the prompt block, measure: `cd ~/stage-fix/r20i && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py --oa sales "รายการ API key" "เพิกถอน API key ระบบบัญชี" "สร้าง API key" "ขอ key ให้โปรแกรมบัญชีหน่อย"` and record what comes back in the handoff (Task 16). Never print the key.

**Files:**
- Modify: `application/chann_app/services/chat.py` — `ACTION_PERMISSIONS` (~line 246), `ENTITY_DASHBOARD_PAGE` (~29627), `DASHBOARD_PATHS` (done in Task 12), `GROUP_LABELS` (~19903), the `_execute_intent` dispatch right after the `if entity == "invite":` block (~17192), and the new handlers next to `_handle_invite_revoke` (~16270)
- Modify: `application/chann_app/services/ai/intent.py` — a prompt block after the `entity="invite"` block (~line 437)
- Modify: `tests/unit/test_phase6_chat.py` — `FakeDataClient.list_api_keys/revoke_api_key/create_api_key` (copy `_KeyFake` from Task 11 into the class)
- Test: `tests/unit/test_round21b_api_chat.py`

**Interfaces:**
- Registry: `("read", "api_key"): "setting.manage"`, `("delete", "api_key"): "setting.manage"`, `("create", "api_key"): "setting.manage"`.
- `ENTITY_DASHBOARD_PAGE["api_key"] = ("api-keys", {"th": "API สำหรับระบบภายนอก", "en": "API"})`.
- Handlers: `_handle_api_key_list(client, *, ctx, license_id, permission_keys, language) -> ChatReply`, `_handle_api_key_revoke(client, *, ctx, license_id, intent, message, permission_keys, language) -> ChatReply`, `_handle_api_key_create_pointer(ctx, language) -> ChatReply`.
- Confirmation: the first revoke reply carries `quick_replies=[(label, f"ยืนยันเพิกถอน API key {name}"), ("ยกเลิก", "ยกเลิก")]`; the confirming message is recognised by `fields.get("confirm")` **or** the message starting with `ยืนยันเพิกถอน` (a button the system itself offered — a direct road, allowed by MODEL_FIRST.md for buttons).
- Owner check: `_is_owner_here(ctx) -> bool` = `bool((ctx.memberships[0] if ctx.memberships else {}).get("is_owner"))` (MembershipOut carries `is_owner`); non-owner → `API_KEY_OWNER_ONLY`. The Data tier refuses a non-owner actor anyway (403 `owner_only`) — map that `DataTierError` to the same sentence.

- [ ] **Step 1: Failing tests**

Create `tests/unit/test_round21b_api_chat.py`:

```python
"""Round 21B — API keys from chat: list, revoke with a confirmation, and
"create" answered with the page (a secret never sits in a LINE thread)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import ACTION_PERMISSIONS, handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

KEYS = ["setting.manage", "customer.read"]
LIST = {"action": "read", "entity": "api_key", "fields": {}, "missing": []}
REVOKE = {"action": "delete", "entity": "api_key", "fields": {"target_name": "ระบบบัญชี"}, "missing": []}
CREATE = {"action": "create", "entity": "api_key", "fields": {"name": "ระบบบัญชี"}, "missing": []}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "liff_sales_id", "2010948960-xDfbMrIP")


def _shop(owner=True, keys=KEYS):
    client = FakeDataClient(role="owner" if owner else "sales", permission_keys=list(keys))
    client._api_keys = [
        {"id": "k1", "license_id": LICENSE_ID, "name": "ระบบบัญชี", "key_prefix": "chann_live_ab12",
         "last_used_at": "2026-09-22T10:00:00+00:00", "revoked_at": None, "created_at": "2026-09-20T00:00:00+00:00"},
        {"id": "k2", "license_id": LICENSE_ID, "name": "ร้านค้าออนไลน์", "key_prefix": "chann_live_cd34",
         "last_used_at": None, "revoked_at": None, "created_at": "2026-09-21T00:00:00+00:00"},
    ]
    return client


async def _say(client, message, reading, *, owner=True):
    ctx = _ctx(primary_role="sales", oa="sales")
    ctx.memberships[0]["is_owner"] = owner
    async with httpx.AsyncClient(transport=_ai(json.dumps(reading, ensure_ascii=False))) as ai:
        return await handle_chat_message(client, message=message, ctx=ctx, ai_client=ai)


def _writes(client, name):
    return [r for r in client.recorded if r[0] == name]


class TestRegistry:
    def test_the_three_roads_are_behind_setting_manage(self):
        assert ACTION_PERMISSIONS[("read", "api_key")] == "setting.manage"
        assert ACTION_PERMISSIONS[("delete", "api_key")] == "setting.manage"
        assert ACTION_PERMISSIONS[("create", "api_key")] == "setting.manage"
        assert chat.ENTITY_DASHBOARD_PAGE["api_key"][0] == "api-keys"

    def test_the_prompt_teaches_it_to_sales_only(self):
        from chann_app.services.ai.intent import build_prompt
        sales = build_prompt(chann_uid="u", role="sales", license_id="l", permission_keys=KEYS, oa="sales")
        assert 'entity="api_key"' in sales
        tech = build_prompt(chann_uid="u", role="technician", license_id="l", permission_keys=["ticket.read"], oa="technician")
        assert 'entity="api_key"' not in tech


class TestList:
    async def test_the_owner_sees_names_prefixes_and_last_use_never_the_key(self):
        client = _shop()
        reply = await _say(client, "รายการ API key", LIST)
        assert "ระบบบัญชี" in reply.text and "chann_live_ab12" in reply.text and "ร้านค้าออนไลน์" in reply.text
        assert "ยังไม่เคยใช้" in reply.text
        assert "x" * 10 not in reply.text and len([l for l in reply.text.splitlines() if "chann_live_" in l]) == 2
        assert reply.quick_reply_url and "api-keys" in reply.quick_reply_url[1]

    async def test_no_keys_points_at_the_page(self):
        client = _shop()
        client._api_keys = []
        reply = await _say(client, "รายการ API key", LIST)
        assert "ยังไม่มี" in reply.text and reply.quick_reply_url

    async def test_an_admin_who_is_not_the_owner_is_told_so(self):
        client = _shop(owner=False)
        reply = await _say(client, "รายการ API key", LIST, owner=False)
        assert "เจ้าของร้าน" in reply.text and "chann_live_" not in reply.text

    async def test_without_setting_manage_it_is_the_permission_sentence(self):
        client = _shop(keys=["customer.read"])
        reply = await _say(client, "รายการ API key", LIST)
        assert "สิทธิ์" in reply.text and _writes(client, "list_api_keys") == []


class TestRevoke:
    async def test_revoke_asks_first_then_acts_on_the_button(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key ระบบบัญชี", REVOKE)
        assert "ระบบบัญชี" in reply.text and "ยืนยัน" in reply.text
        assert _writes(client, "revoke_api_key") == []
        sends = [send for _, send in reply.quick_replies]
        assert sends[0] == "ยืนยันเพิกถอน API key ระบบบัญชี"
        reply = await _say(client, sends[0], {**REVOKE, "fields": {"target_name": "ระบบบัญชี", "confirm": True}})
        assert "เพิกถอน" in reply.text and "แล้ว" in reply.text
        assert [w[2] for w in _writes(client, "revoke_api_key")] == ["k1"]

    async def test_cancelling_revokes_nothing(self):
        client = _shop()
        await _say(client, "เพิกถอน API key ระบบบัญชี", REVOKE)
        reply = await _say(client, "ยกเลิก", {"action": "cancel", "entity": "api_key", "fields": {}, "missing": []})
        assert _writes(client, "revoke_api_key") == []

    async def test_an_unknown_name_is_named_back_and_nothing_happens(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key ไม่มีอันนี้", {**REVOKE, "fields": {"target_name": "ไม่มีอันนี้"}})
        assert "ไม่พบ" in reply.text and "ไม่มีอันนี้" in reply.text
        assert _writes(client, "revoke_api_key") == []

    async def test_no_name_asks_which(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key", {**REVOKE, "fields": {}})
        assert "key ไหน" in reply.text and "ระบบบัญชี" in reply.text
        assert _writes(client, "revoke_api_key") == []


class TestCreate:
    async def test_create_never_makes_a_key_in_chat_and_opens_the_page(self):
        client = _shop()
        reply = await _say(client, "สร้าง API key ระบบบัญชี", CREATE)
        assert "หน้าจอ" in reply.text or "แดชบอร์ด" in reply.text
        assert reply.quick_reply_url and "api-keys" in reply.quick_reply_url[1]
        assert _writes(client, "create_api_key") == []
```

If `_ctx(...)` builds `memberships` without `is_owner`, the fixture line `ctx.memberships[0]["is_owner"] = owner` adds it — `MembershipOut` has the field, so the fake row is only catching up.

- [ ] **Step 2: Run — fails (`KeyError: ('read', 'api_key')`)**

Run: `cd ~/stage-fix/r20i && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21b_api_chat.py -q`

- [ ] **Step 3: Registry, page map, labels**

In `chat.py`:
- after the `("delete", "assignment_rule"): "setting.manage",` line add
  ```python
      # Round 21B: the owner's outside access. setting.manage gates the road;
      # the handlers add "owner only" on top, and the Data tier enforces it.
      ("read", "api_key"): "setting.manage",
      ("delete", "api_key"): "setting.manage",
      ("create", "api_key"): "setting.manage",
  ```
- in `ENTITY_DASHBOARD_PAGE` add `"api_key": ("api-keys", {"th": "API สำหรับระบบภายนอก", "en": "API"}),`
- in `GROUP_LABELS` add `"api_key": {"th": "API สำหรับระบบภายนอก", "en": "API keys"},`

- [ ] **Step 4: Strings and handlers** — add next to `_handle_invite_revoke`:

```python
# ------------------------------------------------ round 21B: API keys from chat

API_KEY_LIST_HEAD = {"th": "API key ของร้าน:", "en": "This shop's API keys:"}
API_KEY_LINE = {"th": "• {name} — {prefix}… · {last}", "en": "• {name} — {prefix}… · {last}"}
API_KEY_LAST_USED = {"th": "ใช้ล่าสุด {when}", "en": "last used {when}"}
API_KEY_NEVER_USED = {"th": "ยังไม่เคยใช้", "en": "never used"}
API_KEY_LIST_FOOT = {"th": "สร้าง/เพิกถอนได้ที่หน้า API ในแดชบอร์ด", "en": "Create or revoke on the dashboard's API page"}
API_KEY_NONE = {
    "th": "ร้านยังไม่มี API key — สร้างได้ที่หน้า API ในแดชบอร์ด (เจ้าของร้านเท่านั้น) แล้วส่งให้ผู้พัฒนาระบบภายนอก",
    "en": "No API keys yet — make one on the dashboard's API page (owner only) and hand it to the outside developer.",
}
API_KEY_OWNER_ONLY = {
    "th": "API key จัดการได้เฉพาะเจ้าของร้านครับ",
    "en": "Only the shop owner manages API keys.",
}
API_KEY_CREATE_ON_SCREEN = {
    "th": "สร้าง API key ทำที่หน้าจอเท่านั้นครับ — key จะแสดงครั้งเดียวและไม่ควรอยู่ในแชท กดปุ่มด้านล่างเพื่อเปิดหน้า API",
    "en": "API keys are made on the screen only — the key is shown once and should never sit in a chat. Tap below to open the API page.",
}
API_KEY_WHICH = {
    "th": "จะเพิกถอน key ไหนครับ — {names}\nพิมพ์ชื่อด้วย เช่น \"เพิกถอน API key {first}\"",
    "en": "Which key — {names}? Name it, e.g. \"revoke API key {first}\".",
}
API_KEY_NOT_FOUND = {"th": "ไม่พบ API key ชื่อ \"{name}\"", "en": "No API key named \"{name}\"."}
API_KEY_REVOKE_ASK = {
    "th": "จะเพิกถอน API key \"{name}\" ({prefix}…) — ระบบภายนอกที่ใช้ key นี้จะเรียกไม่ได้ทันที ยืนยันไหมครับ",
    "en": "Revoke API key \"{name}\" ({prefix}…)? Any system using it stops at once. Confirm?",
}
API_KEY_REVOKED = {"th": "เพิกถอน API key \"{name}\" แล้ว", "en": "API key \"{name}\" revoked."}
API_KEY_REVOKE_CONFIRM_PREFIX = "ยืนยันเพิกถอน API key "
API_KEY_LIST_LINES = 10


def _is_owner_here(ctx: ResolvedContext) -> bool:
    first = ctx.memberships[0] if ctx.memberships else {}
    return bool(first.get("is_owner"))


def _api_key_line(row: dict, language: str) -> str:
    last = (_t(API_KEY_LAST_USED, language).format(when=_thai_date(row["last_used_at"], language))
            if row.get("last_used_at") else _t(API_KEY_NEVER_USED, language))
    return _t(API_KEY_LINE, language).format(name=row.get("name") or "-", prefix=row.get("key_prefix") or "", last=last)


def _live_api_keys(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("revoked_at")]


async def _handle_api_key_list(
    client: DataClient, *, ctx: ResolvedContext, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not _is_owner_here(ctx):
        return ChatReply(text=_t(API_KEY_OWNER_ONLY, language))
    try:
        rows = _live_api_keys(await client.list_api_keys(str(license_id)))
    except Exception:  # noqa: BLE001
        log.exception("api key list")
        return ChatReply(text=unavailable_reply(language))
    button = _dashboard_button("api-keys", language)
    if not rows:
        return ChatReply(text=_t(API_KEY_NONE, language), quick_reply_url=button)
    lines = _capped([_api_key_line(r, language) for r in rows[:API_KEY_LIST_LINES]], len(rows), language)
    return ChatReply(
        text=f"{_t(API_KEY_LIST_HEAD, language)}\n" + "\n".join(lines) + f"\n{_t(API_KEY_LIST_FOOT, language)}",
        quick_reply_url=button,
    )


async def _handle_api_key_revoke(
    client: DataClient, *, ctx: ResolvedContext, license_id, intent: dict, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not _is_owner_here(ctx):
        return ChatReply(text=_t(API_KEY_OWNER_ONLY, language))
    fields = intent.get("fields") or {}
    text = (message or "").strip()
    confirmed = bool(fields.get("confirm")) or text.startswith(API_KEY_REVOKE_CONFIRM_PREFIX)
    name = str(fields.get("target_name") or fields.get("name") or "").strip()
    if text.startswith(API_KEY_REVOKE_CONFIRM_PREFIX):
        name = text[len(API_KEY_REVOKE_CONFIRM_PREFIX):].strip() or name
    try:
        rows = _live_api_keys(await client.list_api_keys(str(license_id)))
    except Exception:  # noqa: BLE001
        log.exception("api key list before revoke")
        return ChatReply(text=unavailable_reply(language))
    if not name:
        names = " / ".join(r.get("name") or "-" for r in rows) or "-"
        return ChatReply(text=_t(API_KEY_WHICH, language).format(names=names, first=(rows[0].get("name") if rows else "…")))
    match = next((r for r in rows if _normalise(r.get("name") or "") == _normalise(name)), None)
    if match is None:
        return ChatReply(text=_t(API_KEY_NOT_FOUND, language).format(name=name))
    if not confirmed:
        return ChatReply(
            text=_t(API_KEY_REVOKE_ASK, language).format(name=match["name"], prefix=match.get("key_prefix") or ""),
            quick_replies=[
                (_t({"th": "ยืนยันเพิกถอน", "en": "Confirm"}, language), f"{API_KEY_REVOKE_CONFIRM_PREFIX}{match['name']}"),
                (_t({"th": "ยกเลิก", "en": "Cancel"}, language), "ยกเลิก"),
            ],
        )
    try:
        await client.revoke_api_key(str(license_id), str(match["id"]), actor_id=ctx.chann_uid)
    except DataTierError as exc:
        if exc.status_code == 403:
            return ChatReply(text=_t(API_KEY_OWNER_ONLY, language))
        log.exception("api key revoke")
        return ChatReply(text=unavailable_reply(language))
    except Exception:  # noqa: BLE001
        log.exception("api key revoke")
        return ChatReply(text=unavailable_reply(language))
    return ChatReply(text=_t(API_KEY_REVOKED, language).format(name=match["name"]),
                     quick_reply_url=_dashboard_button("api-keys", language))


def _handle_api_key_create_pointer(ctx: ResolvedContext, language: str, permission_keys: list[str]) -> ChatReply:
    if "setting.manage" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    if not _is_owner_here(ctx):
        return ChatReply(text=_t(API_KEY_OWNER_ONLY, language))
    return ChatReply(text=_t(API_KEY_CREATE_ON_SCREEN, language), quick_reply_url=_dashboard_button("api-keys", language))
```

Use the module's existing helpers: `_t`, `_capped`, `_normalise`, `unavailable_reply`, `_dashboard_button`, `DataTierError` (imported at the top of `chat.py` — confirm), and for the date use whatever formatter the invite line uses (`grep -n "def _invite_line" -A8 application/chann_app/services/chat.py`); name it `_thai_date` here only if no such helper exists (then write a two-line `datetime.fromisoformat(...).strftime("%d/%m/%Y")`).

- [ ] **Step 5: Dispatch** — in `_execute_intent`, right after the `if entity == "invite":` block:

```python
    if entity == "api_key":
        if action in READ_ACTIONS:
            return await _handle_api_key_list(
                client, ctx=ctx, license_id=license_id, permission_keys=permission_keys, language=language,
            )
        if action in ("delete", "cancel", "revoke", "reject", "archive"):
            return await _handle_api_key_revoke(
                client, ctx=ctx, license_id=license_id, intent=intent, message=message,
                permission_keys=permission_keys, language=language,
            )
        if action in ("create", "update"):
            return _handle_api_key_create_pointer(ctx, language, permission_keys)
```

- [ ] **Step 6: Prompt block** — in `intent.py`, after the `entity="invite"` block:

```
- entity="api_key" — a key the OWNER hands to an outside system (an
  accounting program, an ERP, a web shop) so it can read and write this
  shop's data through the API. Not an invite (that is a person joining),
  not a role.
  action="read": which keys exist. Examples: "รายการ API key", "มี API key
    อะไรบ้าง", "api keys".
  action="delete": revoke one. fields.target_name = the key's name as the
    person said it. Examples: "เพิกถอน API key ระบบบัญชี" (target_name
    "ระบบบัญชี"), "ยกเลิก key ของโปรแกรมบัญชี", "revoke the ERP key".
  action="create": a new key. fields.name if given. Examples: "สร้าง API
    key", "ขอ key ให้โปรแกรมบัญชีหน่อย" (name "โปรแกรมบัญชี").
```

`entities_for(oa)` derives the entity list from `ACTION_PERMISSIONS` and the OA's allowed keys, so the block appears only where `setting.manage` is reachable (sales).

- [ ] **Step 7: FakeDataClient** — add to `tests/unit/test_phase6_chat.py`'s `FakeDataClient` the three methods from `_KeyFake` (Task 11), backed by `getattr(self, "_api_keys", [])`.

- [ ] **Step 8: Run**

```bash
cd ~/stage-fix/r20i && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21b_api_chat.py tests/unit/test_round21b_ext_api.py -q
/tmp/dv/bin/python scripts/dev/check-parity.py && /tmp/dv/bin/python scripts/dev/check-intent-routing.py && /tmp/dv/bin/python scripts/dev/check-guard-actions.py && /tmp/dv/bin/python scripts/dev/check-perms.py && /tmp/dv/bin/python scripts/dev/measure-capabilities.py --gaps
```
`check-parity` must print `every capability is reachable from both surfaces`; `measure-capabilities` must show the three `api_key` rows as `executes`/`declines`, never `NOT YET`.

- [ ] **Step 9: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "feat(round21b): API keys from chat — listed, revoked after a confirmation, and created only on the screen" && git log --oneline -1
```

---

### Task 14: Agent-test scenario

**Files:**
- Create: `scripts/agent-test/scenarios/api-keys.yaml`

- [ ] **Step 1: Write the scenario** (the fake backend seeds `raw.api_keys` straight onto the fake client; `ai:` is what `ask-model.py` returned in Task 13 — replace the readings below with the measured ones if they differ):

```yaml
# Round 21B — the owner's outside access from chat: the list never shows
# a key, revoking asks first, and "create" opens the page (a secret must
# never sit in a LINE thread).
name: api-keys
description: API keys are listed by prefix, revoked after a confirmation, and made only on the screen
backend: fake
actor:
  oa: sales
  role: owner
  language: th
  permissions: all

steps:
  - seed:
      raw:
        api_keys:
          - id: k1
            license_id: "11111111-1111-1111-1111-111111111111"
            name: "ระบบบัญชี"
            key_prefix: "chann_live_ab12"
            last_used_at: null
            revoked_at: null
            created_at: "2026-09-20T00:00:00+00:00"
        members:
          - id: m1
            chann_uid: "CHN-S-000001"
            role: owner
            is_owner: true
            status: active

  - send:
      message: "รายการ API key"
      ai: {action: read, entity: api_key, fields: {}, missing: []}
    expect:
      contains: ["ระบบบัญชี", "chann_live_ab12"]
      is_not: [not_sure, generic_error, permission, not_found, not_a_feature]

  - send:
      message: "เพิกถอน API key ระบบบัญชี"
      ai: {action: delete, entity: api_key, fields: {target_name: "ระบบบัญชี"}, missing: []}
    expect:
      contains: ["ยืนยัน"]
      quick_replies_include: ["ยืนยันเพิกถอน API key ระบบบัญชี"]

  - send:
      message: "ยืนยันเพิกถอน API key ระบบบัญชี"
      ai: {action: delete, entity: api_key, fields: {target_name: "ระบบบัญชี", confirm: true}, missing: []}
    expect:
      contains: ["เพิกถอน", "แล้ว"]

  - send:
      message: "สร้าง API key"
      ai: {action: create, entity: api_key, fields: {}, missing: []}
    expect:
      contains: ["หน้าจอ"]
      is_not: [not_sure, generic_error, permission]
```

The fake `ResolvedContext` from `_ctx()` builds its own `memberships`; if the runner's fake context has no `is_owner`, set it in `scripts/agent-test/agent_test_runner/backends.py` `FakeBackend.send` after `ctx = self._t._ctx(...)`: `ctx.memberships[0]["is_owner"] = role == "owner"` (with a one-line comment). Check the key names `quick_replies_include`/`contains`/`is_not` against `scripts/agent-test/scenario.schema.json` and use the ones it defines.

- [ ] **Step 2: Run**

`cd ~/stage-fix/r20i && /tmp/dv/bin/python scripts/agent-test/run.py --only api-keys` → `1 scenarios · 1 passed · 0 failed`. Then the whole set: `/tmp/dv/bin/python scripts/agent-test/run.py | tail -2`.

- [ ] **Step 3: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "test(round21b): the api-keys chat road as a shipped scenario" && git log --oneline -1
```

---

### Task 15: In-app guide step + picture `sales-api`

**Files:**
- Modify: `application/chann_app/services/guides.py` (a step after `"members"`, ~line 481)
- Modify: `application/chann_app/help_images.json`, `presentation/lib/help-images.json`, `docs/guides/help_images.template.json` (slot `sales-api`)
- Modify: `scripts/dev/render-guide-images.py` (scene `sales_api`, registered under `"sales"` in `SCENES`)
- Verify: `tests/unit/test_guides.py`, `tests/unit/test_guide_image_renderer.py`; `docs/guides/sales.md` regenerated.

- [ ] **Step 1: The step** — insert after the `"members"` step dict in the sales guide:

```python
            {
                "key": "api", "title": {"th": "เชื่อมต่อระบบภายนอก (API)", "en": "Connect an outside system (API)"},
                "body": {
                    "th": "ให้โปรแกรมบัญชี ERP หรือระบบอื่นอ่านและเขียนข้อมูลร้านได้ผ่าน API — เจ้าของร้านสร้าง key แล้วส่งให้ผู้พัฒนาระบบนั้น key ทำงานในนามร้าน",
                    "en": "Let an accounting program, an ERP or another system read and write the shop's data through the API — the owner makes a key and hands it to that system's developer; the key acts as the shop.",
                },
                "how": [
                    {"th": "แดชบอร์ด > จัดการร้าน > API > \"สร้าง key\" ตั้งชื่อตามระบบที่จะใช้ — key แสดงครั้งเดียว คัดลอกเก็บทันที", "en": "Dashboard > Shop > API > \"Create key\", named after the system — shown once, copy it at once"},
                    {"th": "ส่ง key และลิงก์เอกสาร API (ในหน้าเดียวกัน) ให้ผู้พัฒนาระบบภายนอก", "en": "Give the key and the API docs link (same page) to the outside developer"},
                    {"th": "ดูว่ามี key อะไรบ้างและใช้ล่าสุดเมื่อไหร่", "en": "See which keys exist and when each was last used", "type": "รายการ API key"},
                    {"th": "เพิกถอนเมื่อเลิกใช้หรือ key หลุด — ระบบถามยืนยันก่อน ระบบภายนอกจะเรียกไม่ได้ทันที", "en": "Revoke when no longer used or leaked — confirmed first; the outside system stops at once", "type": "เพิกถอน API key ระบบบัญชี"},
                    {"th": "สร้าง key ทำได้บนหน้าจอเท่านั้น (ในแชทจะได้ปุ่มเปิดหน้า) — key ไม่ควรอยู่ในแชท", "en": "Keys are made on the screen only (chat hands you the button) — a key should never sit in a chat", "type": "สร้าง API key"},
                    {"th": "เฉพาะเจ้าของร้าน · จำกัด 600 คำขอ/นาที/key · ร้านที่ถูกระงับ key อ่านได้แต่เขียนไม่ได้", "en": "Owner only · 600 requests/min/key · a suspended shop's key reads but cannot write"},
                ],
                "commands": ["รายการ API key", "เพิกถอน API key"],
                "example": "รายการ API key",
                "image": "sales-api",
                "image_prompt": "หน้าจอ 'API สำหรับระบบภายนอก' ธีมเขียว: รายการ key สองแถว (ชื่อ · chann_live_ab12… · ใช้ล่าสุด) ปุ่ม 'สร้าง key' และแผงที่แสดง key เต็มครั้งเดียวพร้อมปุ่มคัดลอกและประโยค 'จะไม่แสดงอีก'",
            },
```

- [ ] **Step 2: The slot** — add `"sales-api": "/api/v1/guide/images/sales-api.png",` to the `images` object in all three JSON files (keep the three identical).

- [ ] **Step 3: The scene** — in `render-guide-images.py` add before `SCENES`:

```python
def sales_api(c: Canvas):
    """Round 21B — the owner's API page: the list, and the key shown once."""
    c.dash_frame("API สำหรับระบบภายนอก", "เจ้าของร้านเท่านั้น")
    d = c.d
    for i, line in enumerate(wrap(d, "สร้าง key ให้ระบบบัญชี/ERP เรียกข้อมูลร้านนี้ได้ — key ทำงานในนามร้าน เจ้าของสร้างและเพิกถอนเท่านั้น", font(21), 800)):
        d.text((92, c.y + 14 + i * 28), line, fill=SOFT, font=font(21), anchor="lm")
    c.y += 72
    d.text((92, c.y + 18), "2 key", fill=FAINT, font=font(20, True), anchor="lm")
    bw = int(d.textlength("สร้าง key", font=font(21, True))) + 44
    d.rounded_rectangle((910 - bw, c.y, 910, c.y + 44), radius=11, fill=c.accent)
    d.text((910 - bw / 2, c.y + 22), "สร้าง key", fill=WHITE, font=font(21, True), anchor="mm")
    c.y += 62
    for name, prefix, last in (("ระบบบัญชี Express", "chann_live_ab12…", "ใช้ล่าสุด 22 ก.ย. 2569"),
                               ("ร้านค้าออนไลน์", "chann_live_cd34…", "ยังไม่เคยใช้")):
        d.rounded_rectangle((90, c.y, 910, c.y + 96), radius=16, fill=WHITE, outline=LINE, width=2)
        d.text((114, c.y + 32), name, fill=INK, font=font(25, True), anchor="lm")
        px = 114 + int(d.textlength(name, font=font(25, True))) + 16
        d.text((px, c.y + 33), prefix, fill=SOFT, font=font(20), anchor="lm")
        d.text((114, c.y + 68), f"สร้างเมื่อ 20 ก.ย. 2569 · {last}", fill=SOFT, font=font(20), anchor="lm")
        rw = int(d.textlength("เพิกถอน", font=font(20, True))) + 36
        d.rounded_rectangle((890 - rw, c.y + 28, 890, c.y + 70), radius=10, fill=WHITE, outline="#c2410c", width=2)
        d.text((890 - rw / 2, c.y + 49), "เพิกถอน", fill="#c2410c", font=font(20, True), anchor="mm")
        c.y += 108
    c.y += 6
    # The sheet after "สร้าง key": the whole key, once.
    d.rounded_rectangle((90, c.y, 910, c.y + 236), radius=18, fill=WHITE, outline=c.accent, width=3)
    d.text((114, c.y + 30), "สร้าง key แล้ว — ระบบบัญชี Express", fill=INK, font=font(24, True), anchor="lm")
    d.rounded_rectangle((114, c.y + 56, 886, c.y + 112), radius=12, fill="#f6f4ef", outline=LINE, width=2)
    d.text((134, c.y + 84), "chann_live_ab12Kq9ZtP3mWx7LcR2nVb8Yd", fill=INK, font=font(23), anchor="lm")
    d.text((114, c.y + 138), "คัดลอกเก็บไว้ตอนนี้ — จะไม่แสดงอีก ถ้าหาย ให้สร้างใหม่แล้วเพิกถอนอันเดิม", fill=SOFT, font=font(20), anchor="lm")
    for label, primary, right in (("ปิด", False, 886), ("คัดลอก", True, 760)):
        w = int(d.textlength(label, font=font(21, True))) + 44
        d.rounded_rectangle((right - w, c.y + 166, right, c.y + 212), radius=10, fill=c.accent if primary else WHITE, outline=c.accent, width=2)
        d.text((right - w / 2, c.y + 189), label, fill=WHITE if primary else c.accent, font=font(21, True), anchor="mm")
    c.y += 252
```

Register `"sales-api": sales_api` in `SCENES["sales"]`.

- [ ] **Step 4: Render, look, regenerate**

```bash
cd ~/stage-fix/r20i && /tmp/dv/bin/python scripts/dev/render-guide-images.py --only sales-api && /tmp/dv/bin/python scripts/dev/render-guides.py && /tmp/dv/bin/python scripts/dev/render-guide-images.py --check
```
Then **open `application/chann_app/static/help/sales-api.png` with the Read tool and look**: nothing clipped at the frame, the key fits in its box (shorten the sample key if it runs past x=886), no empty-box glyphs. Fix and re-render until it is right. Then `JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_guides.py tests/unit/test_guide_image_renderer.py -q`.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "docs(round21b): the guide step and picture for connecting an outside system" && git log --oneline -1
```

---

### Task 16: `docs/API.md`, handoff entry, tester checklist

**Files:**
- Create: `docs/API.md`
- Modify: `docs/SESSION_HANDOFF.md` (a "รอบ 21B" entry above "รอบ 21A")
- Modify: `~/CHECKLIST-หลัง-deploy.md` (section V, outside the repo)

- [ ] **Step 1: `docs/API.md`** — write it from the routes as built (read `routers_ext.py`, do not describe from memory); sections: ขอ key (owner, dashboard page), การยืนยันตัว (`Authorization: Bearer chann_live_…`), รูปแบบคำตอบ/ข้อผิดพลาด (`{"error":{"code","message"}}`, codes table), การแบ่งหน้า (`limit`/`offset`/`X-Total-Count`), การ sync ด้วย `updated_since` (poll every N minutes with the last stamp, overlap by one minute), rate limit (600/min, 429 + `Retry-After`, `X-RateLimit-*` headers), ตารางทรัพยากร (one row per route: method, path, permission, body/params), ตัวอย่าง curl for `/me`, `/customers?updated_since=`, `POST /invoices {deal_id, issue:true}`, `POST /invoices/{id}/payments {full:true, method:"transfer"}`, สิ่งที่ยังไม่มี (webhooks, idempotency key, key expiry, scopes, IP allowlist), and the security posture sentence from the spec §6. Link it from `docs/SYSTEM_GUIDE.md`'s document list if one exists (`grep -n "SESSION_HANDOFF" docs/SYSTEM_GUIDE.md`).

- [ ] **Step 2: Handoff entry** — above `**รอบ 21A (22 ก.ย. — DEV, ต่อจาก \`035f89f\`)` insert:

```markdown
**รอบ 21B (23 ก.ย. — DEV, ต่อจาก `c0a71a3`): API สำหรับระบบภายนอก — key ของเจ้าของร้าน, `/api/ext/v1`, และ `updated_since`**

- **เจ้าของ:** *"ทำ API เลย"* · *"การ authori เอาแค่ให้เจ้าของร้าน generate api code ให้คนภายนอกสำหรับใช้ Api ก็พอแล้ว"* ·
  ผู้ใช้หลัก = ระบบบัญชี/ERP ของลูกค้า · รอบนี้ไม่มี webhook ขาออก (รอบถัดไป) · spec: `docs/superpowers/specs/2026-09-23-external-api-design.md`
- **Data:** ตาราง `api_keys` (migration 0037 — head ใหม่ `0037_api_keys`): `key_hash` SHA-256 (key สุ่ม 32 ตัว ไม่ใช่รหัสผ่าน จึงไม่ใช้ argon2),
  `key_prefix` ไว้แสดง, `revoked_at` soft · `ApiKeyRepository` + route `POST/GET licenses/{id}/api-keys`, `POST …/{key}/revoke`
  (**เจ้าของเท่านั้น** — ตรวจ `LicenseMember.is_owner` จาก `X-Actor-Id` ที่ Data เอง จึงเหมือนกันทั้งแชทและจอ), `POST api-keys/resolve`
  = หา key + นับ `INCR api_rl:{key}:{นาที}` ใน Redis (600/นาที, Redis ล่ม = ไม่จำกัด ไม่ใช่ 500) + ประทับ `last_used_at` ในคำขอเดียว ·
  `updated_since` บน list ของ ลูกค้า/ดีล/งานซ่อม/ใบแจ้งหนี้ (`repositories/search.py::since`)
- **Application:** `auth/api_key.py` — `Authorization: Bearer chann_live_…` → `TenantPrincipal(audience="api", chann_uid="api:<key id>",
  permission_keys = ชุด admin)` · `routers_ext.py` sub-app ที่ `/api/ext/v1` มี OpenAPI ของตัวเอง (`/api/ext/v1/docs`), error รูปเดียว
  `{"error":{"code","message"}}`, list = `{"items","total"}` + `X-Total-Count`, ทรัพยากร: me, customers, deals(+stage), quotes(+pdf),
  invoices(+payments, pdf, receipt-pdf), tickets, warranties, products — ทุก route เรียก DataClient/service ตัวเดิม
  (`_create_invoice`, `record_payment`, `issue_invoice_document`) กฎเดิมจึงมีผลหมด · route LIFF `licenses/{id}/api-keys` (owner only, `_owner_only`)
- **จอ:** จัดการร้าน > **API** (`ownerOnly` ใน `NavEntry`; rail ซ่อนให้) — รายการ key · "สร้าง key" → แผงแสดง key **ครั้งเดียว** + คัดลอก ·
  เพิกถอนเป็นปุ่มแดงแยก + ยืนยัน (ui-ux-pro-max: primary-action, destructive-nav-separation, confirmation-dialogs)
- **แชท (model-first — วัดด้วย ask-model.py ก่อน: <บันทึกผล 4 ประโยคที่นี่>):** `รายการ API key` (ชื่อ · prefix · ใช้ล่าสุด — ไม่มี key เต็ม),
  `เพิกถอน API key <ชื่อ>` ถามยืนยันด้วยปุ่มก่อน, `สร้าง API key` = ปุ่มเปิดหน้าจอ (จงใจ: secret ห้ามอยู่ใน LINE — `ACCEPTED ("api_key","create")`
  ใน check-parity) · ACTION_PERMISSIONS read/delete/create → setting.manage + owner ในตัว handler
- **ยังไม่มี (ตั้งใจ):** webhook ขาออก · `Idempotency-Key` · key หมดอายุ · scope ต่อ key · IP allowlist — เขียนไว้ใน `docs/API.md`
- เทสต์: `tests/unit/test_round21b_{api_keys,ext_api,api_chat}.py`, `tests/integration/test_round21b_api_keys.py` (Postgres: ตาราง, repo,
  route ภายใน, updated_since), boundary `TestExternalApi` (mount, OpenAPI เฉพาะ ext, ทุก route ตอบ 401 เมื่อไม่มี key, ไม่ import persistence),
  scenario `api-keys` · คู่มือขั้น "เชื่อมต่อระบบภายนอก (API)" + รูป `sales-api` (ดูด้วยตาแล้ว)
- **หลัง deploy ต้องพิสูจน์ของจริง:** สร้าง key บนแดชบอร์ด DEV แล้ว `curl -sS -H "Authorization: Bearer <key>" <application>/api/ext/v1/me`
  จาก Cloud Shell ต้องได้ชื่อร้าน; `…/customers?limit=1` ต้องมี `X-Total-Count`; เพิกถอนแล้วยิงซ้ำต้อง 401
```

- [ ] **Step 3: Checklist section V** — append to `~/CHECKLIST-หลัง-deploy.md`:

```markdown
## V. รอบ 21B (`__21B_SHA__`) — API สำหรับระบบภายนอก

- [ ] **21B-1** เจ้าของร้าน: เมนู จัดการร้าน > **API** มี · พนักงานที่มีสิทธิ์ตั้งค่าร้านแต่ไม่ใช่เจ้าของ **ไม่เห็น**เมนูนี้ (เปิด URL ตรงได้ข้อความ "สำหรับเจ้าของร้านเท่านั้น")
- [ ] **21B-2** "สร้าง key" → ตั้งชื่อ → แผงแสดง key เต็ม `chann_live_…` **ครั้งเดียว** พร้อมปุ่มคัดลอกและประโยคเตือน · กด "ปิด" แล้วรายการแสดงแค่ `chann_live_ab12…`
- [ ] **21B-3** จาก Cloud Shell: `curl -sS -H "Authorization: Bearer <key>" https://chann-crm-ai-dev-application-….a.run.app/api/ext/v1/me` → ชื่อร้าน · `…/customers?limit=1` → `items`/`total` + header `X-Total-Count` · ไม่ใส่ header → 401 `missing_or_malformed_key`
- [ ] **21B-4** `…/api/ext/v1/docs` เปิดได้โดยไม่ต้อง login เห็นเฉพาะทรัพยากร 8 กลุ่ม ไม่มี platform/member/role
- [ ] **21B-5** `POST …/invoices {"deal_id": "<ดีลที่มีสินค้า>", "issue": true}` → ได้ INV-… พร้อมเอกสาร · `GET …/invoices/<id>/pdf` → ลิงก์เปิดได้ · `POST …/invoices/<id>/payments {"full": true, "method": "transfer"}` → ชำระครบ · หน้าใบแจ้งหนี้บนแดชบอร์ดเห็นตรงกัน และประวัติการใช้งานบอก actor เป็น `api:<key id>`
- [ ] **21B-6** เพิกถอน key (ปุ่มแดง → ยืนยัน) → curl เดิมตอบ 401 `unknown_or_revoked_key` ทันที
- [ ] **21B-7** Sale OA: `รายการ API key` → ชื่อ · prefix · ใช้ล่าสุด (ไม่มี key เต็ม) + ปุ่มเปิดหน้า · `เพิกถอน API key <ชื่อ>` → ถามยืนยัน → ปุ่ม "ยืนยันเพิกถอน" → เพิกถอนจริง · `สร้าง API key` → ได้ปุ่มเปิดหน้า ไม่มี key ในแชท · พนักงานที่ไม่ใช่เจ้าของ → "เฉพาะเจ้าของร้าน"
- [ ] **21B-8** `…/customers?updated_since=<เวลาก่อนแก้ลูกค้าคนหนึ่ง>` → ได้เฉพาะคนที่แก้หลังเวลานั้น
- [ ] **21B-9** คู่มือ (พิมพ์ "วิธีใช้" / Dashboard > วิธีใช้): มีขั้น "เชื่อมต่อระบบภายนอก (API)" พร้อมรูปหน้า API ที่ตรงกับหน้าจริง
```

- [ ] **Step 4: Commit the repo files**

```bash
cd ~/stage-fix/r20i && git add -A && git commit -q -m "docs(round21b): API contract, handoff entry" && git log --oneline -1
```

---

### Task 17: Full gate, fresh-clone check, deploy script, deploy, tester guide

**Files:**
- Create: `~/round21b-deploy.sh` (from `~/round20z-deploy.sh` — the last round **with** a migration)
- Verify: the whole gate; `tests/integration` on `pg20w`; fresh clone apply; DEV runtime.

- [ ] **Step 1: The full gate on this tree**

```bash
sed 's#cd ~/stage-fix/registry#cd ~/stage-fix/r20i#' ~/stage-fix/tools/gate.sh > ~/stage-fix/out/gate-21b.sh
cd ~/stage-fix/r20i && setsid nohup bash ~/stage-fix/out/gate-21b.sh > ~/stage-fix/out/gate-21b.log 2>&1 < /dev/null &
```
Wait for `=== gate done (clean)` (≈10 min; never run two gates at once). Then, separately: `TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration -q -p no:cacheprovider | tail -1` → all passed. And because chat changed: `OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/simulate-phrasings.py --real | tail -3` — read the result; record it in the handoff.

- [ ] **Step 2: Squash to one patch and validate on a fresh clone**

```bash
cd ~/stage-fix/r20i && git add -A && git diff --binary origin/main > /tmp/r21b.patch && N=$(wc -l < /tmp/r21b.patch) && cp /tmp/r21b.patch ~/round21b-v1-$N.patch && echo ~/round21b-v1-$N.patch
rm -rf /tmp/fc-21b && git clone -q ~/chann-crm-ai/.git /tmp/fc-21b && cd /tmp/fc-21b && git checkout -q c0a71a3 && git apply --3way --check ~/round21b-v1-$N.patch && echo CLEAN
```
(The worktree's local commits are squashed by diffing against `origin/main`; the deploy script applies the patch onto `~/chann-crm-ai` and makes the one real commit.)

- [ ] **Step 3: The deploy script**

Copy `~/round20z-deploy.sh` to `~/round21b-deploy.sh` and change: the header comment (round 21B, migration 0037); `PATCH_NAME`/`PATCH_LINES`; `COMMIT_SUBJECT='feat(round21b): an outside system reads and writes the shop through /api/ext/v1 with a key the owner makes, and every list can be asked for what changed since'`; `BASE_SUBJECT` = round 21A's subject (`git log -1 --pretty=%s origin/main`); the STAGE 2 verify block to these greps — `EXPECTED_MIGRATION_HEAD = "0037_api_keys"` in `data/chann_data/main.py`; `database/alembic/versions/0037_api_keys.py` exists with `down_revision = "0036_warranty_contacts"`; `class ApiKeyRepository` in `data/chann_data/repositories/api_keys.py`; `"/api-keys/resolve"` in `data/chann_data/routers/internal.py`; `async def api_principal(` in `application/chann_app/auth/api_key.py`; `app.mount("/api/ext/v1"` in `application/chann_app/main.py`; `ownerOnly: true` in `presentation/app/liff/_nav-model.tsx`; `("read", "api_key")` in `application/chann_app/services/chat.py`; `("api_key", "create")` in `scripts/dev/check-parity.py`; `"sales-api"` in `application/chann_app/help_images.json`; the three test files exist — `info "symbol ครบ 12 จุด (21B)"`; STAGE 5's migration comment (`0037 adds a table; the running data tier does not read it, so the order database → migrate → data is safe`) and `info "migration ผ่าน (head ควรเป็น 0037_api_keys)"`; the commit body (the owner's two sentences and what was built, in the round-20z style); the closing `TXT` block naming checklist section V and the `sed -i "s/__21B_SHA__/${SHORT}/" "$HOME/CHECKLIST-หลัง-deploy.md"` line before it; every `round20z-deploy.sh` mention → `round21b-deploy.sh`. `bash -n ~/round21b-deploy.sh` must pass.

- [ ] **Step 4: Deploy (owner's standing authorisation: green clone → deploy)**

```bash
cd ~/chann-crm-ai && git status --porcelain | head -3 && git log --oneline -1   # must be clean and on c0a71a3
cd ~/chann-crm-ai && setsid nohup env ALLOW_APPLY=YES bash /home/thanawinmax2/round21b-deploy.sh > ~/deploy-21b.log 2>&1 < /dev/null &
```
Wait (≈35 min) for `DEPLOY OK — <sha>`; on `HALT` read the log, fix in `~/stage-fix/r20i`, regenerate the patch and rerun (the script resumes at build once the commit is on origin).

- [ ] **Step 5: Runtime proof (the acceptance evidence)**

```bash
AU=$(gcloud run services describe chann-crm-ai-dev-application --project=chann1-1 --region=asia-southeast1 --format='value(status.url)')
curl -fsS "$AU/health" | grep -o '"git_commit":"[0-9a-f]*"'
curl -sS "$AU/api/ext/v1/me" | head -c 200; echo            # → {"error":{"code":"missing_or_malformed_key",…}}
curl -sS "$AU/api/ext/v1/openapi.json" | /tmp/dv/bin/python -c 'import json,sys; print(sorted(json.load(sys.stdin)["paths"]))'
```
A real key exists only after the owner creates one on the dashboard; 21B-3…6 in the checklist are theirs.

- [ ] **Step 6: Tester guide artifact** — read `https://claude.ai/artifact/V16FfyaeNmXGdfBq2vcU6d` with the Artifact tool, update the DEV SHA, the round note in the callout, and add rows `21B-1…9` to section 17 (same shape as the `21A-…` rows), republish to the same URL.

- [ ] **Step 7: Reset the worktree**

```bash
cd ~/stage-fix/r20i && git fetch -q origin && git reset -q --hard origin/main && git log --oneline -1
```
