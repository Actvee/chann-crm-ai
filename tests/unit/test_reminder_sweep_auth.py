"""The reminder sweep's authentication.

Deliberately a separate, static, machine-to-machine secret rather than
routing through require_admin's session-backed JWT flow — Cloud Scheduler
calls in once a day forever, and require_admin was built for a person
logging in through a browser with a short-lived, session-tracked token.
Reusing it would mean either a token that never expires (defeating the
point of the session table) or a cron job re-authenticating itself, and
this endpoint's one caller needs neither.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_admin  # noqa: E402
from chann_app.config import settings  # noqa: E402


class _FakeClient:
    """A stand-in for DataClient — the sweep's own logic is tested in
    test_reminders.py; this file only exercises the auth boundary."""

    async def aclose(self):
        pass


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "reminder_sweep_secret", "test-sweep-secret")

    async def override():
        client = _FakeClient()
        try:
            yield client
        finally:
            await client.aclose()

    fastapi_app = FastAPI()
    fastapi_app.include_router(routers_admin.router)
    fastapi_app.dependency_overrides[routers_admin.get_data_client] = override

    async def fake_sweep(client, *, days=0):
        return {"tenants": 0, "due": 0, "sent": 0, "skipped": 0, "failed": 0}

    import chann_app.services.reminders as reminders_module
    monkeypatch.setattr(reminders_module, "sweep_due_follow_ups", fake_sweep)

    return fastapi_app


class TestReminderSweepAuth:
    def test_the_correct_secret_is_accepted(self, app):
        client = TestClient(app)
        response = client.post(
            "/api/v1/platform/reminders/sweep",
            headers={"X-Sweep-Secret": "test-sweep-secret"},
        )
        assert response.status_code == 200

    def test_the_wrong_secret_is_rejected(self, app):
        client = TestClient(app)
        response = client.post(
            "/api/v1/platform/reminders/sweep",
            headers={"X-Sweep-Secret": "guessed-wrong"},
        )
        assert response.status_code == 401

    def test_no_secret_header_at_all_is_rejected(self, app):
        client = TestClient(app)
        response = client.post("/api/v1/platform/reminders/sweep")
        assert response.status_code == 401

    def test_an_unconfigured_secret_refuses_everything_rather_than_allowing_through(
        self, app, monkeypatch,
    ):
        """An empty REMINDER_SWEEP_SECRET must never mean "skip the check" —
        that would make the endpoint unintentionally public the moment
        someone forgets to set it in a new environment."""
        monkeypatch.setattr(settings, "reminder_sweep_secret", "")
        client = TestClient(app)
        response = client.post(
            "/api/v1/platform/reminders/sweep",
            headers={"X-Sweep-Secret": "anything-at-all"},
        )
        assert response.status_code == 503

    def test_a_platform_admin_jwt_alone_is_not_accepted(self, app):
        """The two auth schemes are not interchangeable: a valid admin
        Authorization bearer token must not substitute for the sweep
        secret, since that would reintroduce the session-lifetime problem
        this dependency exists to avoid."""
        client = TestClient(app)
        response = client.post(
            "/api/v1/platform/reminders/sweep",
            headers={"Authorization": "Bearer some.jwt.token"},
        )
        assert response.status_code == 401
