"""Round 19g — the shop opens the conversation with a customer it knows, from
chat ("คุยกับลูกค้า สมชาย") or from the dashboard (tester note s-setup-6)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chann_app import routers_phase2
from chann_app.config import settings
from chann_app.services import live_chat
from chann_app.services.authorization import TenantPrincipal
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ctx
from test_round18e_followups import CUSTOMERS, KEYS, _reads

sys.path.insert(0, str(Path(__file__).resolve().parent))
pytestmark = pytest.mark.asyncio

SHOP_KEYS = [*KEYS, "chat_session.view", "chat_session.reply"]
LINKED = [dict(CUSTOMERS[0], customer_chann_uid="CHN-S-000009"), dict(CUSTOMERS[1])]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


@pytest.fixture
def pushes(monkeypatch):
    sent: list[tuple] = []

    async def fake_push_text(oa, to, text, client=None, quick_reply=None):
        sent.append((oa, to, text))
        return ["mid"]

    async def fake_push_messages(oa, to, messages, client=None):
        sent.append((oa, to, messages))
        return ["mid"]

    async def line_of(chann_uid):
        return f"line-{chann_uid}"

    monkeypatch.setattr(live_chat, "push_text", fake_push_text)
    monkeypatch.setattr(live_chat, "push_messages", fake_push_messages)
    return sent, line_of


async def _say(client, message, reading):
    since = len(client.recorded)
    reply = await handle_chat_message(client, message=message, ctx=_ctx(), ai_client=_reads(reading))
    return reply, client.recorded[since:]


def _shop(pushes) -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, customers=[dict(c) for c in LINKED])
    client.line_target_of = pushes[1]  # type: ignore[method-assign]
    return client


class TestTheShopOpensAChatFromChat:
    async def test_by_name_the_customer_is_invited(self, pushes):
        client = _shop(pushes)
        reply, calls = await _say(client, "คุยกับลูกค้า สมชาย", {"action": "read", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        assert [c for c in calls if c[0] == "open_chat_session"], reply.text
        assert "เปิดห้องแชทกับ สมชาย ใจดี แล้ว" in reply.text and "แชทลูกค้า" in reply.text, reply.text
        invites = [p for p in pushes[0] if p[1] == "line-CHN-S-000009"]
        assert invites and "อยากคุยกับคุณ" in str(invites[0][2]), pushes[0]

    async def test_a_first_line_after_the_colon_reaches_the_customer(self, pushes):
        client = _shop(pushes)
        reply, calls = await _say(client, "คุยกับลูกค้า สมชาย: พรุ่งนี้ช่างไปได้ไหม", {"action": "read", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        sent = [c for c in calls if c[0] == "add_chat_message"]
        assert sent, (reply.text, calls)
        assert "พรุ่งนี้ช่างไปได้ไหม" in reply.text, reply.text
        assert any("พรุ่งนี้ช่างไปได้ไหม" in str(p[2]) for p in pushes[0]), pushes[0]

    async def test_by_job_code(self, pushes):
        client = _shop(pushes)
        client._tickets = [{"id": "t5", "ticket_number": "T-2026-0005", "status": "assigned", "customer_chann_uid": "CHN-S-000009", "customer_name": "สมชาย ใจดี", "issue_description": "พัดลมไม่หมุน"}]
        reply, calls = await _say(client, "เริ่มแชทกับลูกค้าของงาน T-2026-0005", {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0005"}, "missing": []})
        assert [c for c in calls if c[0] == "open_chat_session"], reply.text
        assert "สมชาย ใจดี" in reply.text, reply.text

    async def test_a_customer_without_line_cannot_be_chatted_with(self, pushes):
        client = _shop(pushes)
        reply, calls = await _say(client, "คุยกับลูกค้า สมหญิง", {"action": "read", "entity": "customer", "fields": {"target_name": "สมหญิง"}, "missing": []})
        assert not [c for c in calls if c[0] == "open_chat_session"], reply.text
        assert "ยังไม่ได้ผูก LINE" in reply.text, reply.text

    async def test_looking_a_customer_up_is_still_a_lookup(self, pushes):
        client = _shop(pushes)
        reply, calls = await _say(client, "ข้อมูลลูกค้า สมชาย", {"action": "read", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        assert not [c for c in calls if c[0] == "open_chat_session"], reply.text

    async def test_without_the_reply_key_it_is_refused(self, pushes):
        client = FakeDataClient(permission_keys=KEYS, customers=[dict(c) for c in LINKED])
        reply, calls = await _say(client, "คุยกับลูกค้า สมชาย", {"action": "read", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        assert not [c for c in calls if c[0] == "open_chat_session"], reply.text


class TestTheShopOpensAChatFromTheDashboard:
    def test_the_start_route_opens_and_invites(self, pushes, monkeypatch):
        client = _shop(pushes)

        async def aclose():
            pass
        client.aclose = aclose  # type: ignore[attr-defined]

        async def member_of(c, license_id, principal):
            return "MEMBER-1"
        monkeypatch.setattr(routers_phase2, "_member_of", member_of)

        async def override_client():
            yield client

        async def override_principal():
            return TenantPrincipal(
                license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="cs", is_owner=False,
                permission_keys=frozenset(SHOP_KEYS), audience="sales",
            )
        app = FastAPI()
        app.include_router(routers_phase2.router)
        app.dependency_overrides[routers_phase2.get_data_client] = override_client
        app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
        http = TestClient(app)
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/start", json={"customer_chann_uid": "CHN-S-000009"})
        assert response.status_code == 201, response.text
        assert response.json().get("id")
        assert [c for c in client.recorded if c[0] == "open_chat_session"]
        assert any(p[1] == "line-CHN-S-000009" for p in pushes[0]), pushes[0]
