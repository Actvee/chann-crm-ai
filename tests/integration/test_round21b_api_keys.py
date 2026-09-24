"""Round 21B — API keys a shop owner hands to an outside system."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import redis

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


class TestTheInternalRoutes:
    def _http(self, migrated_db, monkeypatch):
        """The real Data-tier app on the real migrated schema — same
        pattern as tests/integration/test_appointment_edit_data.py:
        `require_internal_secret` 503s with ADMIN_SECRET
        REQUIRED_NOT_CONFIGURED unless a secret is set, and the app's own
        engine (settings.database_url) is not `migrated_db`, so both need
        wiring here rather than only setting the header."""
        from sqlalchemy.orm import sessionmaker
        from fastapi.testclient import TestClient

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app

        monkeypatch.setattr(settings, "admin_secret", "test-internal-secret")
        TestSession = sessionmaker(bind=migrated_db, future=True)

        def override_session():
            session = TestSession()
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
        return TestClient(app), {"X-Internal-Secret": "test-internal-secret"}

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
            # Round 21D: resolve now reads the plan through the cache;
            # this stub stands in for the rate window only, so every other
            # read is "Redis down" and the plan comes from the database.
            def get(self, k): raise redis.ConnectionError("stub: rate window only")

        monkeypatch.setattr(cache_module.cache, "_client", _Redis())

        with Session(migrated_db) as session:
            lic, owner, member = _phase2_tenant(session)
            session.commit()
            license_id, owner_uid, member_uid = str(lic.id), owner.chann_uid, member.chann_uid

        http, secret = self._http(migrated_db, monkeypatch)
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

    def test_a_second_channel_row_does_not_make_the_owner_check_unstable(self, migrated_db, monkeypatch):
        """The owner also holds a technician-channel row with a non-owner
        role (one chann_uid, two rows — one per OA, per
        UniqueConstraint("license_id", "chann_uid", "channel")). The owner
        check must still find the sales-channel owner row deterministically
        and not 403 depending on physical row order."""
        from sqlalchemy.orm import Session

        from chann_data.models import LicenseMember
        from chann_data import cache as cache_module

        class _Pipe:
            def incr(self, k): return self
            def expire(self, k, t): return self
            def execute(self): return [1, True]

        class _Redis:
            def pipeline(self): return _Pipe()
            # Round 21D: resolve now reads the plan through the cache;
            # this stub stands in for the rate window only, so every other
            # read is "Redis down" and the plan comes from the database.
            def get(self, k): raise redis.ConnectionError("stub: rate window only")

        monkeypatch.setattr(cache_module.cache, "_client", _Redis())

        with Session(migrated_db) as session:
            lic, owner, _member = _phase2_tenant(session)
            session.add(LicenseMember(
                id=uuid.uuid4(), license_id=lic.id, chann_uid=owner.chann_uid,
                role="member", channel="technician",
            ))
            session.commit()
            license_id, owner_uid = str(lic.id), owner.chann_uid

        http, secret = self._http(migrated_db, monkeypatch)
        made = http.post(f"/internal/v1/licenses/{license_id}/api-keys",
                         json={"name": "ERP", "created_by_chann_uid": owner_uid},
                         headers={**secret, "X-Actor-Id": owner_uid})
        assert made.status_code == 201, made.text


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

    def test_the_query_string_reaches_customers_and_tickets_through_the_real_app(
        self, migrated_db, monkeypatch,
    ):
        """The brief's own test only calls the repositories directly. The
        four HTTP endpoints are the actual contract an ERP calls, and none
        of them was ever hit with `?updated_since=` — so the FastAPI
        query-string-to-`datetime` parsing and the ticket repository's
        `since` threading were both unverified. Same wiring as
        TestTheInternalRoutes._http: `settings.admin_secret` monkeypatched
        and `get_session` overridden to `migrated_db`, because the app's
        own engine is not this test's session."""
        from datetime import datetime, timedelta, timezone

        from sqlalchemy.orm import Session, sessionmaker
        from fastapi.testclient import TestClient

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app
        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.phase12 import ServiceTicketRepository
        from chann_data.repositories.tenant_scope import TenantScope

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customers = CustomerRepository(session)
            old_customer = customers.create(
                scope, first_name="เก่า", phone=f"08{uuid.uuid4().int % 10**8:08d}",
            )
            new_customer = customers.create(
                scope, first_name="ใหม่", phone=f"08{uuid.uuid4().int % 10**8:08d}",
            )
            session.flush()

            tickets = ServiceTicketRepository(session)
            old_ticket = tickets.create(scope, issue_description="เก่า", contact_id=old_customer.id)
            new_ticket = tickets.create(scope, issue_description="ใหม่", contact_id=new_customer.id)
            session.flush()

            stamp = datetime.now(timezone.utc) + timedelta(seconds=1)
            old_customer.updated_at = stamp - timedelta(days=2)
            new_customer.updated_at = stamp + timedelta(minutes=1)
            old_ticket.updated_at = stamp - timedelta(days=2)
            new_ticket.updated_at = stamp + timedelta(minutes=1)
            session.flush()
            # Committed, not just flushed: the HTTP call below opens a
            # SEPARATE session on the same engine (see TestTheInternalRoutes
            # ._http) and would see nothing of an uncommitted transaction.
            session.commit()
            license_id = str(lic.id)
            new_customer_id, new_ticket_id = str(new_customer.id), str(new_ticket.id)

        monkeypatch.setattr(settings, "admin_secret", "test-internal-secret")
        TestSession = sessionmaker(bind=migrated_db, future=True)

        def override_session():
            session = TestSession()
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
        http = TestClient(app)
        secret = {"X-Internal-Secret": "test-internal-secret"}
        since = stamp.isoformat()  # aware ISO-8601 with +00:00

        customers_resp = http.get(
            f"/internal/v1/licenses/{license_id}/customers",
            params={"updated_since": since}, headers=secret,
        )
        assert customers_resp.status_code == 200, customers_resp.text
        assert [c["id"] for c in customers_resp.json()] == [new_customer_id]
        assert customers_resp.headers["X-Total-Count"] == "1"

        tickets_resp = http.get(
            f"/internal/v1/licenses/{license_id}/tickets",
            params={"updated_since": since}, headers=secret,
        )
        assert tickets_resp.status_code == 200, tickets_resp.text
        assert [t["id"] for t in tickets_resp.json()] == [new_ticket_id]
        assert tickets_resp.headers["X-Total-Count"] == "1"

        bad = http.get(
            f"/internal/v1/licenses/{license_id}/customers",
            params={"updated_since": "not-a-date"}, headers=secret,
        )
        assert bad.status_code == 422
