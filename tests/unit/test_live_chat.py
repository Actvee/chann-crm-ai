"""Phase 15 — live chat (PLAN_3OA B6): the customer opens a conversation,
the shop hears, the shop's answer reaches the customer's LINE, the
customer's next lines go into the conversation rather than a repair
job, the sweep escalates and times out, the routes gate correctly.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import live_chat  # noqa: E402
from chann_app.services.authorization import CUSTOMER_PERMISSION_KEYS, TenantPrincipal  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

MEMBERS = [
    {"id": "m-owner", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"},
    {"id": "m-cs", "chann_uid": "CHN-CS", "role": "cs", "status": "active"},
    {"id": "m-tech", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"},
]


class ChatFake(FakeDataClient):
    """FakeDataClient plus the Phase 15 surface, in memory."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._chat_sessions: list[dict] = []
        self._chat_messages: list[dict] = []
        self._sweep_result = {"escalated": [], "timed_out": []}
        self._members = list(MEMBERS)
        self._settings = []
        self._line_targets = {"CHN-S-000001": "line-cust", "CHN-CS": "line-cs", "CHN-OWNER": "line-owner"}

    async def open_chat_session(self, license_id, *, customer_chann_uid, product_id=None,
                                sla_minutes=30, timeout_minutes=120, actor_id=None):
        self.recorded.append(("open_chat_session", license_id, customer_chann_uid, sla_minutes, timeout_minutes))
        for row in self._chat_sessions:
            if row["license_id"] == license_id and row["customer_chann_uid"] == customer_chann_uid \
                    and row["status"] in ("open", "assigned"):
                return {**row, "_created": False}
        row = {
            "id": f"cs-{len(self._chat_sessions) + 1}", "license_id": license_id,
            "customer_chann_uid": customer_chann_uid, "customer_name": "สมชาย", "status": "open",
            "assigned_to": None, "sla_deadline": None,
        }
        self._chat_sessions.append(row)
        return {**row, "_created": True}

    async def list_chat_sessions(self, license_id, status=None, customer_chann_uid=None, limit=100):
        self.recorded.append(("list_chat_sessions", license_id, status, customer_chann_uid))
        rows = [r for r in self._chat_sessions if r["license_id"] == license_id]
        if status == "live":
            rows = [r for r in rows if r["status"] in ("open", "assigned")]
        elif status:
            rows = [r for r in rows if r["status"] == status]
        if customer_chann_uid:
            rows = [r for r in rows if r["customer_chann_uid"] == customer_chann_uid]
        return [dict(r) for r in rows]

    async def get_chat_session(self, license_id, session_id):
        for r in self._chat_sessions:
            if r["id"] == session_id and r["license_id"] == license_id:
                return dict(r)
        return None

    async def list_chat_messages(self, license_id, session_id, since=None, limit=200):
        return [m for m in self._chat_messages if m["session_id"] == session_id]

    async def add_chat_message(self, license_id, session_id, *, sender_type, content,
                               sender_chann_uid=None, content_en=None, sla_minutes=30, timeout_minutes=120):
        self.recorded.append(("add_chat_message", session_id, sender_type, content))
        row = {"id": f"cm-{len(self._chat_messages) + 1}", "session_id": session_id,
               "sender_type": sender_type, "content": content, "sender_chann_uid": sender_chann_uid}
        self._chat_messages.append(row)
        for s in self._chat_sessions:
            if s["id"] == session_id and sender_type == "agent" and s["status"] == "open":
                s["status"] = "assigned"
        return row

    async def assign_chat_session(self, license_id, session_id, member_id, actor_id=None):
        self.recorded.append(("assign_chat_session", session_id, member_id))
        for s in self._chat_sessions:
            if s["id"] == session_id:
                s["assigned_to"] = member_id
                s["status"] = "assigned"
                return dict(s)
        raise AssertionError("unknown session")

    async def close_chat_session(self, license_id, session_id, actor_id=None):
        self.recorded.append(("close_chat_session", session_id))
        for s in self._chat_sessions:
            if s["id"] == session_id:
                s["status"] = "closed"
                return dict(s)
        raise AssertionError("unknown session")

    async def mark_chat_read(self, license_id, session_id, reader="agent"):
        self.recorded.append(("mark_chat_read", session_id, reader))
        return {"marked": 0}

    async def sweep_chat_sessions(self):
        self.recorded.append(("sweep_chat_sessions",))
        return self._sweep_result

    async def get_company_profile(self, license_id):
        return {"company_name": "ร้านเย็นสบาย"}


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


@pytest.fixture
def pushes(monkeypatch):
    sent: list[tuple] = []

    async def fake_push(oa, to, text, client=None):
        sent.append((oa, to, text))
        return ["mid"]

    monkeypatch.setattr(live_chat, "push_text", fake_push)
    return sent


def _customer():
    return _ctx(primary_role="customer", oa="customer")


class TestStartFromChat:
    async def test_talk_to_the_shop_opens_a_conversation_and_tells_the_agents(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        reply = await handle_chat_message(client, message="คุยกับร้าน", ctx=_customer())
        assert "ร้าน" in reply.text
        opened = [r for r in client.recorded if r[0] == "open_chat_session"]
        assert opened and opened[0][2] == "CHN-S-000001"
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert {str(r) for r in told} and len(told) == 2  # owner + cs, not the technician
        assert all("ขอคุยกับร้าน" in str(r) for r in told)

    async def test_a_line_typed_with_it_is_the_first_message(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน ราคาแอร์ 12000 BTU เท่าไหร่", ctx=_customer())
        lines = [r for r in client.recorded if r[0] == "add_chat_message"]
        assert lines and lines[0][2] == "customer" and "12000" in lines[0][3]

    async def test_during_a_conversation_free_text_is_a_line_not_a_repair(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน", ctx=_customer())
        client.recorded.clear()
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น ซ่อมได้ไหม", ctx=_customer())
        assert [r for r in client.recorded if r[0] == "add_chat_message"]
        assert not [r for r in client.recorded if r[0] == "create_ticket"]
        assert "ส่งถึงร้านแล้ว" in reply.text or "ร้าน" in reply.text

    async def test_commands_still_work_during_a_conversation(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน", ctx=_customer())
        client.recorded.clear()
        reply = await handle_chat_message(client, message="งานของฉัน", ctx=_customer())
        assert not [r for r in client.recorded if r[0] == "add_chat_message"]
        assert reply.text

    async def test_ending_it_closes_and_the_owner_hears(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        await handle_chat_message(client, message="คุยกับร้าน", ctx=_customer())
        session = client._chat_sessions[0]
        session["assigned_to"] = "m-cs"
        session["status"] = "assigned"
        client.recorded.clear()
        reply = await handle_chat_message(client, message="จบการสนทนา", ctx=_customer())
        assert ("close_chat_session", session["id"]) in client.recorded
        assert "จบ" in reply.text or "ปิด" in reply.text
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert len(told) == 1 and "CHN-CS" in str(told[0])


class TestAgentReply:
    async def test_the_answer_reaches_the_customer_and_the_answerer_owns_it(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        session, created = await live_chat.start_session(
            client, license_id=LICENSE_ID, chann_uid="CHN-S-000001", first_message="สวัสดี",
        )
        assert created
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS",
            member_id="m-cs", text="สวัสดีครับ ยินดีให้บริการ",
        )
        assert ("assign_chat_session", session["id"], "m-cs") in client.recorded
        assert pushes and pushes[0][0] == "customer" and "ร้านเย็นสบาย" in pushes[0][2]
        assert "ยินดีให้บริการ" in pushes[0][2]

    async def test_a_customer_line_reaches_only_the_owner_of_the_conversation(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        session, _ = await live_chat.start_session(client, license_id=LICENSE_ID, chann_uid="CHN-S-000001")
        session = await client.assign_chat_session(LICENSE_ID, session["id"], "m-cs")
        client.recorded.clear()
        await live_chat.customer_message(
            client, license_id=LICENSE_ID, session=session, chann_uid="CHN-S-000001", text="ยังอยู่ไหม",
        )
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert len(told) == 1 and "CHN-CS" in str(told[0])


class TestSweep:
    async def test_overdue_goes_to_the_owner_and_timeouts_tell_the_customer(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        late = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
        client._sweep_result = {
            "escalated": [{"id": "cs-9", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
                           "customer_name": "สมชาย", "assigned_to": "m-cs", "sla_deadline": late}],
            "timed_out": [{"id": "cs-8", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
                           "customer_name": "สมชาย", "assigned_to": None}],
        }
        result = await live_chat.sweep(client)
        assert result == {"escalated": 1, "timed_out": 1}
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert len(told) == 1 and "CHN-CS" in str(told[0]) and "ยังไม่ได้รับคำตอบ" in str(told[0])
        assert pushes and "ปิดอัตโนมัติ" in pushes[0][2]

    async def test_an_unowned_overdue_conversation_goes_to_every_agent(self, pushes):
        client = ChatFake(role="customer", permission_keys=[])
        client._sweep_result = {
            "escalated": [{"id": "cs-9", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
                           "customer_name": "สมชาย", "assigned_to": None, "sla_deadline": None}],
            "timed_out": [],
        }
        result = await live_chat.sweep(client)
        assert result["escalated"] == 2


class TestSettings:
    async def test_minutes_come_from_license_settings_with_sane_bounds(self):
        client = ChatFake()
        client._settings = [
            {"setting_key": "chat_sla", "setting_value": "15"},
            {"setting_key": "chat_timeout_minutes", "setting_value": 99999},
        ]
        assert await live_chat.chat_settings(client, LICENSE_ID) == (15, 24 * 60)
        client._settings = [{"setting_key": "chat_sla_minutes", "setting_value": "abc"}]
        assert await live_chat.chat_settings(client, LICENSE_ID) == (30, 120)


def _harness(principal: TenantPrincipal):
    client = ChatFake(role="customer", permission_keys=[])

    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


def _customer_principal():
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="customer", is_owner=False,
        permission_keys=CUSTOMER_PERMISSION_KEYS, audience="customer",
    )


def _cs_principal(keys=("chat_session.view", "chat_session.reply")):
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-CS", role="cs", is_owner=False,
        permission_keys=frozenset(keys), audience="sales",
    )


class TestRoutes:
    def test_customer_opens_then_cs_answers(self, pushes):
        http, client = _harness(_customer_principal())
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions", json={"content": "ราคาเท่าไหร่"},
        )
        assert response.status_code == 201, response.text
        session_id = response.json()["id"]

        http_cs, _ = _harness(_cs_principal())
        http_cs.app.dependency_overrides[routers_phase2.get_data_client] = http.app.dependency_overrides[routers_phase2.get_data_client]
        listed = http_cs.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions")
        assert listed.status_code == 200 and listed.json()[0]["id"] == session_id
        assert ("sweep_chat_sessions",) in client.recorded
        answered = http_cs.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/{session_id}/messages",
            json={"content": "3,500 บาทครับ"},
        )
        assert answered.status_code == 201, answered.text
        assert pushes and "3,500" in pushes[-1][2]
        thread = http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/{session_id}/messages")
        assert thread.status_code == 200
        assert [m["sender_type"] for m in thread.json()["messages"]] == ["customer", "agent"]
        assert ("mark_chat_read", session_id, "customer") in client.recorded

    def test_a_customer_cannot_read_another_customers_conversation(self, pushes):
        http, client = _harness(_customer_principal())
        client._chat_sessions.append({
            "id": "cs-other", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-OTHER",
            "customer_name": "คนอื่น", "status": "open", "assigned_to": None, "sla_deadline": None,
        })
        response = http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-other/messages")
        assert response.status_code == 404
        mine = http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions")
        assert mine.status_code == 200 and mine.json() == []

    def test_staff_without_the_key_are_refused(self, pushes):
        http, _ = _harness(_cs_principal(keys=("ticket.read",)))
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions").status_code == 403
        assert http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions", json={"content": "x"},
        ).status_code == 403
