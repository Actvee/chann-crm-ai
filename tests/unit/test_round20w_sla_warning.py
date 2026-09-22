"""Round 20W — the "nobody answered" warning that never arrived.

Tester (22 ก.ย. 2569): "มันจะแจ้งแค่ครั้งแรกตอนลูกค้าขอคุยกับเจ้าหน้าที่ แล้วถ้าไม่มี
เจ้าหน้าที่ตอบก็ยังไม่มีแจ้งเตือนซ้ำ". The warning has existed since Phase
15 (⏰ ลูกค้า … ยังไม่ได้รับคำตอบ, once at the SLA, then the chat is parked).
DEV's log for 21 ก.ย. shows why nobody saw it: every scheduler sweep said
"0 overdue → 0 told" while a dashboard-driven sweep a minute earlier had
died with httpcore.ReadError — the Data tier had already stamped the
rows "escalated" before the Application tier told anyone, so the
warning was gone for good.

Two things had to change: the row is CLAIMED by the sweep that will do
the telling and released again if the telling fails; and the dashboard
sweep no longer borrows a request's client, which is closed the moment
the request that started it returns.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import live_chat  # noqa: E402
from test_live_chat import LICENSE_ID, ChatFake, pushes  # noqa: E402,F401


def _late() -> dict:
    return {
        "id": "cs-9", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
        "customer_name": "สมชาย", "assigned_to": "m-cs",
        "sla_deadline": (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat(),
    }


class TestTheWarningIsClaimedNotStamped:
    async def test_a_claimed_row_is_told_and_parked(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        client._sweep_result = {"escalated": [_late()], "timed_out": []}
        result = await live_chat.sweep(client)
        assert result["escalated"] == 1
        assert ("claim_chat_escalation", LICENSE_ID, "cs-9") in client.recorded
        assert ("close_chat_session", "cs-9") in client.recorded
        assert not [r for r in client.recorded if r[0] == "release_chat_escalation"]

    async def test_a_row_another_sweep_already_holds_is_left_alone(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        client._sweep_result = {"escalated": [_late()], "timed_out": []}
        client._claimed = {"cs-9"}
        result = await live_chat.sweep(client)
        assert result["escalated"] == 0
        assert not [r for r in client.recorded if r[0] in ("create_notification", "close_chat_session")]
        assert not pushes, "no second warning, no second parking"

    async def test_a_failed_telling_gives_the_claim_back(self, pushes, monkeypatch):
        """The exact shape of 21 ก.ย.: the sweep dies after the Data tier
        moved. The claim goes back, so the next sweep tries again."""
        client = ChatFake(role="customer", permission_keys=[])
        client._sweep_result = {"escalated": [_late()], "timed_out": []}

        async def boom(*a, **k):
            raise RuntimeError("connection closed under us")

        monkeypatch.setattr(live_chat, "_tell", boom)
        result = await live_chat.sweep(client)
        assert result["escalated"] == 0
        assert ("release_chat_escalation", LICENSE_ID, "cs-9") in client.recorded
        assert not [r for r in client.recorded if r[0] == "close_chat_session"], "not parked either"
        assert "cs-9" not in client._claimed

    async def test_nobody_reachable_still_parks_and_keeps_the_claim(self, pushes):
        """A shop with no agent on LINE is the existing warning-log case,
        not a failure: the conversation is parked and not retried."""
        client = ChatFake(role="customer", permission_keys=[])
        client._members = []
        client._sweep_result = {"escalated": [_late()], "timed_out": []}
        result = await live_chat.sweep(client)
        assert result["escalated"] == 0
        assert ("close_chat_session", "cs-9") in client.recorded
        assert not [r for r in client.recorded if r[0] == "release_chat_escalation"]


class TestTheDashboardSweepOwnsItsClient:
    def test_it_never_borrows_the_requests_client(self, monkeypatch):
        made: list = []

        class _Fresh:
            def __init__(self):
                self.closed = False
                made.append(self)

            async def aclose(self):
                self.closed = True

        used: list = []

        async def fake_sweep(client):
            used.append(client)
            return {"escalated": 0, "timed_out": 0}

        monkeypatch.setattr(routers_phase2, "DataClient", _Fresh)
        monkeypatch.setattr(routers_phase2.live_chat, "sweep", fake_sweep)
        monkeypatch.setattr(routers_phase2, "_last_sweep_at", 0.0)

        request_client = object()

        async def run():
            routers_phase2._sweep_soon(request_client)  # type: ignore[arg-type]
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(run())
        assert used and used[0] is made[0] and used[0] is not request_client
        assert made[0].closed, "the fresh client is closed after the sweep"

    def test_a_sweep_that_raises_still_closes_its_client(self, monkeypatch):
        made: list = []

        class _Fresh:
            def __init__(self):
                self.closed = False
                made.append(self)

            async def aclose(self):
                self.closed = True

        async def fake_sweep(client):
            raise RuntimeError("data tier away")

        monkeypatch.setattr(routers_phase2, "DataClient", _Fresh)
        monkeypatch.setattr(routers_phase2.live_chat, "sweep", fake_sweep)
        monkeypatch.setattr(routers_phase2, "_last_sweep_at", 0.0)

        async def run():
            routers_phase2._sweep_soon(object())  # type: ignore[arg-type]
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(run())
        assert made and made[0].closed
