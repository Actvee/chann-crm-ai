"""Owner rule, 4 Sep (chat-sla-v1): the shop has 15 minutes; past that the
customer is told someone will get back to them and the conversation is
parked; when the shop finally answers, the customer is invited to reopen
it and sees what the shop said. Check-in records where the technician
stood (GPS from the screen, a LINE location in chat).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import live_chat  # noqa: E402
from chann_app.services.chat import handle_chat_message, handle_incoming_location  # noqa: E402
from test_live_chat import ChatFake, LICENSE_ID  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402


class ParkFake(ChatFake):
    async def close_chat_session(self, license_id, session_id, actor_id=None, status="closed"):
        self.recorded.append(("close_chat_session", session_id, status))
        for s in self._chat_sessions:
            if s["id"] == session_id:
                s["status"] = status
                return dict(s)
        raise AssertionError("unknown session")

    async def open_chat_session(self, license_id, *, customer_chann_uid, product_id=None,
                                sla_minutes=15, timeout_minutes=60, actor_id=None):
        # Reopen the previous conversation, as the Data tier does now.
        for row in self._chat_sessions:
            if row["license_id"] == license_id and row["customer_chann_uid"] == customer_chann_uid:
                created = row["status"] not in ("open", "assigned")
                if created:
                    row["status"] = "assigned" if row.get("assigned_to") else "open"
                return {**row, "_created": created}
        return await super().open_chat_session(
            license_id, customer_chann_uid=customer_chann_uid, product_id=product_id,
            sla_minutes=sla_minutes, timeout_minutes=timeout_minutes, actor_id=actor_id,
        )

    async def list_chat_messages(self, license_id, session_id, since=None, limit=200):
        return [dict(m, is_read=m.get("is_read", False)) for m in self._chat_messages if m["session_id"] == session_id]


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


@pytest.fixture
def pushes(monkeypatch):
    sent: list[tuple] = []

    async def fake_push(oa, to, text, client=None):
        sent.append((oa, to, text, None))
        return ["mid"]

    async def fake_push_messages(oa, to, messages, client=None):
        sent.append((oa, to, messages[0].get("text", ""), messages[0].get("quickReply")))
        return ["mid"]

    monkeypatch.setattr(live_chat, "push_text", fake_push)
    monkeypatch.setattr(live_chat, "push_messages", fake_push_messages)
    return sent


class TestFifteenMinutes:
    async def test_the_default_answer_time_is_fifteen_minutes(self):
        assert await live_chat.chat_settings(ChatFake(), LICENSE_ID) == (15, 60)

    async def test_overdue_parks_the_conversation_and_tells_the_customer(self, pushes):
        client = ParkFake(role="customer", permission_keys=[])
        session, _, _ = await live_chat.start_session(client, license_id=LICENSE_ID, chann_uid="CHN-S-000001")
        client._sweep_result = {"escalated": [dict(session, sla_deadline=None)], "timed_out": []}
        await live_chat.sweep(client)
        assert ("close_chat_session", session["id"], "unanswered") in client.recorded
        assert client._chat_sessions[0]["status"] == "unanswered"
        customer_pushes = [p for p in pushes if p[1] == "line-cust"]
        assert customer_pushes and "ติดต่อกลับ" in customer_pushes[-1][2]
        # The agents still hear that the answer slipped.
        told = [r for r in client.recorded if r[0] == "create_notification" and r[3] == "sla_warning"]
        assert told


class TestTheShopAnswersLater:
    async def test_the_answer_is_kept_and_the_customer_is_invited_back(self, pushes):
        client = ParkFake(role="customer", permission_keys=[])
        session, _, _ = await live_chat.start_session(client, license_id=LICENSE_ID, chann_uid="CHN-S-000001")
        await client.close_chat_session(LICENSE_ID, session["id"], status="unanswered")
        parked = await client.get_chat_session(LICENSE_ID, session["id"])
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=parked, agent_chann_uid="CHN-CS", member_id="m-cs",
            text="ขอโทษที่ตอบช้า ราคา 15,900 บาทครับ",
        )
        assert [r for r in client.recorded if r[0] == "add_chat_message" and r[2] == "agent"]
        assert not [r for r in client.recorded if r[0] == "assign_chat_session"]
        assert client._chat_sessions[0]["status"] == "unanswered"
        last = pushes[-1]
        assert "15,900" in last[2] and "เปิดแชท" in last[2]
        assert last[3] and last[3]["items"][0]["action"]["text"] == "คุยกับร้าน"

    async def test_reopening_shows_what_the_shop_said_meanwhile(self, pushes):
        client = ParkFake(role="customer", permission_keys=[])
        session, _, _ = await live_chat.start_session(client, license_id=LICENSE_ID, chann_uid="CHN-S-000001")
        await client.close_chat_session(LICENSE_ID, session["id"], status="unanswered")
        client._chat_messages.append({
            "id": "cm-late", "session_id": session["id"], "sender_type": "agent",
            "content": "ราคา 15,900 บาทครับ", "sender_chann_uid": "CHN-CS", "is_read": False,
            "created_at": "2026-09-04T10:00:00+07:00",
        })
        reply = await handle_chat_message(client, message="คุยกับร้าน", ctx=_ctx(primary_role="customer", oa="customer"))
        assert "15,900" in reply.text
        assert ("mark_chat_read", session["id"], "customer") in client.recorded
        assert client._chat_sessions[0]["status"] in ("open", "assigned")


class TestCheckInWithLocation:
    async def test_a_line_location_from_the_technician_checks_in_with_gps(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                            "accept_status": "accepted", "assigned_to_ref": "member-1",
                            "customer_name": "สมชาย", "service_address": "99/1"}]
        reply = await handle_incoming_location(
            client, ctx=_ctx(primary_role="technician", oa="technician"), oa="technician",
            latitude=13.7563, longitude=100.5018, language="th",
        )
        calls = [r for r in client.recorded if r[0] == "check_in_ticket"]
        assert calls and calls[0][2] == "t1"
        assert calls[0][4] == 13.7563 and calls[0][5] == 100.5018
        assert "T-2026-0001" in reply.text and "ตำแหน่ง" in reply.text

    async def test_a_location_with_no_job_is_answered_not_stored(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        client._tickets = []
        reply = await handle_incoming_location(
            client, ctx=_ctx(primary_role="technician", oa="technician"), oa="technician",
            latitude=13.7, longitude=100.5, language="th",
        )
        assert not [r for r in client.recorded if r[0] == "check_in_ticket"]
        assert reply.text

    async def test_a_customers_location_is_acknowledged_only(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await handle_incoming_location(
            client, ctx=_ctx(primary_role="customer", oa="customer"), oa="customer",
            latitude=13.7, longitude=100.5, language="th",
        )
        assert reply.text and not [r for r in client.recorded if r[0] == "check_in_ticket"]
