"""Master Spec 17.5.4 — the trial sweep (review E3), the nightly expiry
covering warranties too (E5), and the digest looking a day ahead (E11)."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_admin  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import trials  # noqa: E402

TODAY = date(2026, 9, 6)
LIC_A = "11111111-1111-1111-1111-111111111111"
LIC_B = "22222222-2222-2222-2222-222222222222"
LIC_C = "33333333-3333-3333-3333-333333333333"


class _Client:
    def __init__(self, *, expiring=None, overdue=None, announced=None, expiring_raises=None):
        self._expiring = expiring or {}
        self._overdue = overdue or []
        self._announced = announced or {}
        self._expiring_raises = expiring_raises
        self.notifications: list[dict] = []
        self.expire_calls = 0
        self.line_targets = {"CHN-A": "Ua", "CHN-B": "Ub", "CHN-C": None}

    async def aclose(self):
        pass

    async def trials_expiring(self, on_day):
        if self._expiring_raises:
            raise self._expiring_raises
        return list(self._expiring.get(on_day, []))

    async def expire_due_trials(self):
        self.expire_calls += 1
        return list(self._overdue)

    async def announced_today(self, license_id, notification_type):
        return set(self._announced.get((license_id, notification_type), []))

    async def line_target_of(self, chann_uid):
        return self.line_targets.get(chann_uid)

    async def get_display_preferences(self, chann_uid):
        return {"language": "en" if chann_uid == "CHN-B" else "th"}

    async def create_notification(self, license_id, **kw):
        self.notifications.append({"license_id": license_id, **kw})
        return {"id": f"n-{len(self.notifications)}", **kw}

    async def list_members(self, license_id):
        return [{"chann_uid": "CHN-C", "role": "owner", "status": "active"}] if license_id == LIC_C else []

    async def record_message_entity(self, *a, **k):
        pass


@pytest.fixture
def no_push(monkeypatch):
    from chann_app.services import notify

    pushed = []

    async def fake_push(oa, to, text, client=None):
        pushed.append((oa, to, text))
        return ["mid"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    return pushed


class TestWarnings:
    async def test_three_and_one_day_notices_go_to_the_owner(self, no_push):
        client = _Client(expiring={
            TODAY + timedelta(days=3): [{"id": LIC_A, "company_name": "ร้านเอ", "trial_expires_at": "2026-09-09T02:00:00+00:00", "owner_chann_uid": "CHN-A"}],
            TODAY + timedelta(days=1): [{"id": LIC_B, "company_name": "Shop B", "trial_expires_at": "2026-09-07T02:00:00+00:00", "owner_chann_uid": "CHN-B"}],
        })
        summary = await trials.sweep_trials(client, today=TODAY)
        assert summary["warned"] == {"3": 1, "1": 1} and summary["expired"] == 0
        types = [(n["target_chann_uid"], n["type"], n["entity_id"]) for n in client.notifications]
        assert (
            ("CHN-A", "trial_expiring", LIC_A) in types and ("CHN-B", "trial_expiring", LIC_B) in types
        )
        a = next(n for n in client.notifications if n["target_chann_uid"] == "CHN-A")
        assert "3 วัน" in a["message"] and "09/09/2026" in a["message"] and "ร้านเอ" in a["message"]
        # The owner who reads English is pushed the English text.
        assert any(oa == "sales" and to == "Ub" and "1 day" in text for oa, to, text in no_push)

    async def test_a_retry_the_same_day_does_not_repeat(self, no_push):
        client = _Client(
            expiring={TODAY + timedelta(days=1): [{"id": LIC_B, "company_name": "B", "owner_chann_uid": "CHN-B"}]},
            announced={(LIC_B, "trial_expiring"): [LIC_B]},
        )
        summary = await trials.sweep_trials(client, today=TODAY)
        assert summary["warned"]["1"] == 0 and summary["skipped"] == 1 and not client.notifications

    async def test_the_owner_is_found_through_members_when_the_row_has_none(self, no_push):
        client = _Client(expiring={TODAY + timedelta(days=3): [{"id": LIC_C, "company_name": "C"}]})
        summary = await trials.sweep_trials(client, today=TODAY)
        assert summary["warned"]["3"] == 1
        assert client.notifications[0]["target_chann_uid"] == "CHN-C"


class TestExpiry:
    async def test_overdue_trials_are_suspended_and_the_owner_told(self, no_push):
        client = _Client(overdue=[{"id": LIC_A, "company_name": "ร้านเอ", "status": "suspended", "created_by_chann_uid": "CHN-A"}])
        summary = await trials.sweep_trials(client, today=TODAY)
        assert client.expire_calls == 1
        assert summary["expired"] == 1 and summary["expired_ids"] == [LIC_A]
        note = client.notifications[-1]
        assert note["type"] == "trial_expired" and note["target_chann_uid"] == "CHN-A"
        assert "หมดอายุแล้ว" in note["message"]

    async def test_a_failed_warning_query_does_not_stop_the_suspension(self, no_push):
        client = _Client(overdue=[{"id": LIC_A, "company_name": "A", "created_by_chann_uid": "CHN-A"}], expiring_raises=RuntimeError("data tier down"))
        summary = await trials.sweep_trials(client, today=TODAY)
        assert summary["failed"] == 2 and summary["expired"] == 1


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "reminder_sweep_secret", "test-sweep-secret")
    fake = _Client()

    async def override():
        yield fake

    fastapi_app = FastAPI()
    fastapi_app.include_router(routers_admin.router)
    fastapi_app.dependency_overrides[routers_admin.get_data_client] = override
    return fastapi_app, fake


class TestRoutes:
    def test_the_trial_sweep_is_guarded_like_the_others(self, app):
        fastapi_app, fake = app
        assert TestClient(fastapi_app).post("/api/v1/platform/trials/expire").status_code == 401
        assert TestClient(fastapi_app).post("/api/v1/platform/trials/expire", headers={"X-Sweep-Secret": "nope"}).status_code == 401
        response = TestClient(fastapi_app).post("/api/v1/platform/trials/expire", headers={"X-Sweep-Secret": "test-sweep-secret"})
        assert response.status_code == 200 and response.json()["expired"] == 0
        assert fake.expire_calls == 1

    def test_the_digest_looks_one_day_ahead_by_default(self, app, monkeypatch):
        import chann_app.services.reminders as reminders_module

        seen = {}

        async def fake_sweep(client, *, days=0):
            seen["days"] = days
            return {"tenants": 0}

        monkeypatch.setattr(reminders_module, "sweep_due_follow_ups", fake_sweep)
        fastapi_app, _ = app
        TestClient(fastapi_app).post("/api/v1/platform/reminders/sweep", headers={"X-Sweep-Secret": "test-sweep-secret"})
        assert seen["days"] == 1
        TestClient(fastapi_app).post("/api/v1/platform/reminders/sweep?days=0", headers={"X-Sweep-Secret": "test-sweep-secret"})
        assert seen["days"] == 0

    def test_the_nightly_expiry_covers_warranties_too(self, app):
        fastapi_app, fake = app
        calls = []

        async def list_licenses(status=None, exclude_status=None):
            return [{"id": LIC_A, "status": "trial"}, {"id": LIC_B, "status": "active"}]

        async def expire_quotes(license_id):
            calls.append(("quotes", license_id))
            return {"expired": 2}

        async def expire_warranties(license_id):
            calls.append(("warranties", license_id))
            if license_id == LIC_B:
                raise RuntimeError("one tenant down")
            return {"expired": 1}

        fake.list_licenses = list_licenses
        fake.expire_overdue_quotes = expire_quotes
        fake.expire_overdue_warranties = expire_warranties
        response = TestClient(fastapi_app).post("/api/v1/platform/quotes/expire-overdue", headers={"X-Sweep-Secret": "test-sweep-secret"})
        body = response.json()
        assert body["expired"] == 4 and body["warranties_expired"] == 1 and body["failed"] == [LIC_B]
        assert ("warranties", LIC_A) in calls and ("warranties", LIC_B) in calls


class TestSchedulerTerraform:
    def test_the_jobs_are_declared(self):
        text = (ROOT / "infrastructure" / "terraform" / "scheduler.tf").read_text()
        assert "/api/v1/platform/trials/expire" in text
        assert "/api/v1/platform/reminders/sweep?days=1" in text
        assert 'time_zone   = "Asia/Bangkok"' in text
