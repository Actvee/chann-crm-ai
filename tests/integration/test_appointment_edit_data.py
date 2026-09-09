"""Editing and deleting an appointment, against a real Postgres.

The owner's 9 Sep report ("ตอนนี้นัดหมายเหมือนจะแก้ไข หรือลบไม่ได้") was a
missing route, not a missing button — `FollowUpRepository` had create, get,
list, due_within and set_status, and nothing that could change a date.

The rules the new methods carry only exist in SQL and in the audit
constraint, so they are proved here rather than against a fake:

  * a tenant scope that filters in Python looks identical to one that
    filters in the WHERE clause until someone else's row is in the table;
  * `ck_audit_log_action` has silently rolled back whole transactions in
    this project before, and a verb is only real once Postgres accepts it;
  * "read the new date back from the database" means exactly that — the
    ORM identity map will happily hand back the object you just edited.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, time

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture
def api(migrated_db, monkeypatch):
    """The real Data-tier app on the real migrated schema."""
    from sqlalchemy.orm import sessionmaker

    from chann_data import config as config_module
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(config_module.settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), {
            "X-Internal-Secret": "test-internal-secret",
            "X-Actor-Id": "CHN-S-APPT-1",
        }
    finally:
        app.dependency_overrides.pop(get_session, None)


def _tenant(engine, suffix: str) -> dict:
    """One license with one customer. Two of these are two tenants."""
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository
    from chann_data.repositories.phase9 import CustomerRepository
    from chann_data.repositories.tenant_scope import TenantScope

    uid = f"CHN-APPT-{suffix}"
    with Session(engine) as session:
        session.add(ChannIdentity(
            chann_uid=uid, line_user_id=f"line-appt-{suffix}", primary_role="sales",
        ))
        session.commit()
    with Session(engine) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"Appt Co {suffix}", created_by_chann_uid=uid,
        )
        session.commit()
        license_id = lic.id
    with Session(engine) as session:
        customer = CustomerRepository(session).create(
            TenantScope(license_id=license_id),
            first_name="นัด", last_name=f"หมาย{suffix}", phone=f"08000000{suffix}",
        )
        session.commit()
        return {"license_id": str(license_id), "customer_id": str(customer.id)}


@pytest.fixture(scope="module")
def tenant(migrated_db):
    return _tenant(migrated_db, "01")


@pytest.fixture(scope="module")
def other_tenant(migrated_db):
    """A second shop, so isolation is tested against a row that exists."""
    return _tenant(migrated_db, "02")


def _create(client, headers, tenant, **overrides) -> dict:
    body = {
        "entity_type": "customer",
        "entity_id": tenant["customer_id"],
        "due_date": "2026-10-01",
        "due_time": "09:00:00",
        "notes": "โทรตามเรื่องใบเสนอราคา",
    }
    body.update(overrides)
    response = client.post(
        f"/internal/v1/licenses/{tenant['license_id']}/follow-ups",
        headers=headers, json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestTheWholeRoundTrip:
    def test_create_edit_read_back_delete_and_it_is_gone(self, api, tenant, migrated_db):
        """The flow the owner could not complete, end to end.

        The read-back goes through a fresh session against the table, not
        through the response body: an endpoint that returns the new date
        while writing nothing would pass any assertion made on its own
        output.
        """
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        created = _create(client, headers, tenant)
        follow_up_id = created["id"]

        edited = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{follow_up_id}",
            headers=headers,
            json={"due_date": "2026-10-08", "due_time": "13:30:00", "notes": "เลื่อนตามที่ลูกค้าขอ"},
        )
        assert edited.status_code == 200, edited.text
        # Same appointment, not a replacement — the entire point of the fix.
        assert edited.json()["id"] == follow_up_id
        assert edited.json()["status"] == "pending"

        with Session(migrated_db) as session:
            row = session.get(FollowUp, uuid.UUID(follow_up_id))
            assert row is not None
            assert row.due_date == date(2026, 10, 8)
            assert row.due_time == time(13, 30)
            assert row.notes == "เลื่อนตามที่ลูกค้าขอ"

        removed = client.delete(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{follow_up_id}",
            headers=headers,
        )
        assert removed.status_code == 204, removed.text

        with Session(migrated_db) as session:
            assert session.get(FollowUp, uuid.UUID(follow_up_id)) is None

        gone = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{follow_up_id}",
            headers=headers, json={"due_date": "2026-10-09"},
        )
        assert gone.status_code == 404

    def test_editing_only_the_time_keeps_the_day_and_the_note(self, api, tenant, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        created = _create(client, headers, tenant)
        client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_time": "16:00:00"},
        )
        with Session(migrated_db) as session:
            row = session.get(FollowUp, uuid.UUID(created["id"]))
            assert row.due_time == time(16, 0)
            assert row.due_date == date(2026, 10, 1)
            assert row.notes == "โทรตามเรื่องใบเสนอราคา"

    def test_a_time_can_be_cleared_back_to_a_whole_day_reminder(self, api, tenant, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        created = _create(client, headers, tenant)
        response = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_time": None},
        )
        assert response.status_code == 200, response.text
        with Session(migrated_db) as session:
            assert session.get(FollowUp, uuid.UUID(created["id"])).due_time is None


class TestWhatAnEditIsNotAllowedToDo:
    def test_a_settled_appointment_cannot_be_edited(self, api, tenant):
        """Same rule set_status already enforces, for the same reason:
        other things have reported on a completed visit, and moving its
        date would rewrite that history silently."""
        client, headers = api
        created = _create(client, headers, tenant)
        done = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}"
            f"/follow-ups/{created['id']}/status",
            headers=headers, json={"status": "completed"},
        )
        assert done.status_code == 200, done.text

        response = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_date": "2026-11-01"},
        )
        assert response.status_code == 409
        assert "completed" in response.json()["detail"]

    def test_a_settled_appointment_can_still_be_deleted(self, api, tenant, migrated_db):
        """Deleting is not editing. A cancelled row filed against the wrong
        customer is exactly the row someone wants gone, and refusing here
        would leave it in the history forever."""
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        created = _create(client, headers, tenant)
        client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}"
            f"/follow-ups/{created['id']}/status",
            headers=headers, json={"status": "cancelled"},
        )
        response = client.delete(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers,
        )
        assert response.status_code == 204, response.text
        with Session(migrated_db) as session:
            assert session.get(FollowUp, uuid.UUID(created["id"])) is None

    def test_a_time_with_no_day_is_refused(self, api, tenant):
        """due_date is NOT NULL, so an explicit null is someone trying to
        clear the day — which would leave a clock reading attached to
        nothing."""
        client, headers = api
        created = _create(client, headers, tenant)
        response = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_date": None, "due_time": "10:00:00"},
        )
        assert response.status_code == 409
        assert "day" in response.json()["detail"]

    def test_a_past_date_is_allowed_here_exactly_as_it_is_on_create(
        self, api, tenant, migrated_db
    ):
        """Documented decision, not an oversight.

        The create path has never refused a past date at this tier — the
        refusal lives in chat (REMINDER_DATE_PAST) and in the dashboard's
        date input, where there is a person to tell. Backdating a visit
        that already happened is legitimate, and a repository that refused
        it would make the two paths disagree about what a follow-up is.
        """
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        created = _create(client, headers, tenant)
        response = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_date": "2020-01-01"},
        )
        assert response.status_code == 200, response.text
        with Session(migrated_db) as session:
            assert session.get(FollowUp, uuid.UUID(created["id"])).due_date == date(2020, 1, 1)

    def test_the_record_an_appointment_hangs_off_cannot_be_switched(self, api, tenant):
        """Moving an appointment onto another customer is not an edit — it
        is filing it against someone else, and every reply, notification
        and audit row already written about it would then name the wrong
        record."""
        client, headers = api
        created = _create(client, headers, tenant)
        response = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers,
            json={"entity_id": str(uuid.uuid4()), "entity_type": "deal"},
        )
        # Unknown fields never reach the repository: the schema drops them,
        # so this is a no-op edit rather than a silent re-filing.
        assert response.status_code in (200, 400, 409), response.text
        assert response.json().get("entity_type", "customer") == "customer"


class TestTenantIsolation:
    def test_another_tenants_appointment_is_invisible_not_merely_refused(
        self, api, tenant, other_tenant, migrated_db
    ):
        """404, not 403 — and the row must still be there afterwards.

        A repository that fetched by id and then checked the license would
        also return 404 here, so the delete below is the real assertion:
        it proves the WHERE clause, not the guard after it.
        """
        from sqlalchemy.orm import Session

        from chann_data.models import FollowUp

        client, headers = api
        theirs = _create(client, headers, other_tenant)

        edited = client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{theirs['id']}",
            headers=headers, json={"due_date": "2026-12-25"},
        )
        assert edited.status_code == 404

        removed = client.delete(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{theirs['id']}",
            headers=headers,
        )
        assert removed.status_code == 404

        with Session(migrated_db) as session:
            row = session.get(FollowUp, uuid.UUID(theirs["id"]))
            assert row is not None, "the other tenant's appointment was deleted"
            assert row.due_date == date(2026, 10, 1), "its date was changed across the boundary"

    def test_the_repository_scopes_the_query_itself(self, migrated_db, tenant, other_tenant):
        """The same thing one level down, past the route.

        Anything inside the Data Tier holding a scope must be safe on its
        own; a check that only exists in the router is a check one new
        caller removes.
        """
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import FollowUpRepository, Phase6NotFound
        from chann_data.repositories.tenant_scope import TenantScope

        mine = TenantScope(license_id=uuid.UUID(tenant["license_id"]))
        theirs = TenantScope(license_id=uuid.UUID(other_tenant["license_id"]))

        with Session(migrated_db) as session:
            repo = FollowUpRepository(session)
            row = repo.create(
                theirs, entity_type="customer",
                entity_id=uuid.UUID(other_tenant["customer_id"]),
                due_date=date(2026, 10, 1), due_time=time(9, 0),
            )
            session.commit()
            follow_up_id = row.id

        with Session(migrated_db) as session:
            repo = FollowUpRepository(session)
            assert repo.get(mine, follow_up_id) is None
            with pytest.raises(Phase6NotFound):
                repo.update(mine, follow_up_id, {"due_date": date(2026, 12, 25)})
            with pytest.raises(Phase6NotFound):
                repo.delete(mine, follow_up_id)
            # And the owner can still do both.
            assert repo.update(theirs, follow_up_id, {"due_date": date(2026, 12, 25)})
            repo.delete(theirs, follow_up_id)
            session.commit()

    def test_an_unknown_field_is_refused_by_the_repository(self, migrated_db, tenant):
        """The route's schema drops unknown keys, but the repository is
        also called directly — by chat, by the sweep, by whatever comes
        next — so it refuses rather than trusting its caller."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase6 import FollowUpRepository, Phase6Conflict
        from chann_data.repositories.tenant_scope import TenantScope

        scope = TenantScope(license_id=uuid.UUID(tenant["license_id"]))
        with Session(migrated_db) as session:
            repo = FollowUpRepository(session)
            row = repo.create(
                scope, entity_type="customer",
                entity_id=uuid.UUID(tenant["customer_id"]),
                due_date=date(2026, 10, 1),
            )
            session.commit()
            with pytest.raises(Phase6Conflict) as exc:
                repo.update(scope, row.id, {"license_id": uuid.uuid4()})
            assert "license_id" in str(exc.value)


class TestTheAuditTrail:
    def test_an_edit_is_audited_with_a_verb_postgres_accepts(
        self, api, tenant, migrated_db
    ):
        """`ck_audit_log_action` is why this is an integration test.

        A verb outside the constraint does not fail the write — it rolls
        the whole transaction back, so the appointment silently does not
        move. "update" and "delete" are both already in the allow-list
        (migration 0023); no new verb was invented and no migration was
        needed.
        """
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        client, headers = api
        created = _create(client, headers, tenant)
        client.patch(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers, json={"due_date": "2026-10-08"},
        )
        with Session(migrated_db) as session:
            rows = session.execute(text(
                "SELECT action, actor_id, field_changes FROM audit_log "
                "WHERE entity_type = 'follow_up' AND entity_id = :eid "
                "ORDER BY created_at"
            ), {"eid": created["id"]}).all()
        actions = [r[0] for r in rows]
        assert actions == ["create", "update"]
        assert rows[-1][1] == "CHN-S-APPT-1"
        # The old day is kept: an audit entry that only holds the new value
        # cannot answer "what did this appointment used to say".
        changes = rows[-1][2]
        assert "2026-10-01" in str(changes) and "2026-10-08" in str(changes)

    def test_a_delete_keeps_the_whole_row_in_the_audit_entry(
        self, api, tenant, migrated_db
    ):
        """Nothing else will remember it."""
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        client, headers = api
        created = _create(client, headers, tenant, notes="นัดผิดคน")
        client.delete(
            f"/internal/v1/licenses/{tenant['license_id']}/follow-ups/{created['id']}",
            headers=headers,
        )
        with Session(migrated_db) as session:
            row = session.execute(text(
                "SELECT action, field_changes FROM audit_log "
                "WHERE entity_type = 'follow_up' AND entity_id = :eid "
                "AND action = 'delete'"
            ), {"eid": created["id"]}).first()
        assert row is not None, "a deleted appointment left no trace"
        assert "นัดผิดคน" in str(row[1])
