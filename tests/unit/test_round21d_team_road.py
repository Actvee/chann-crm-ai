"""Round 21D fix round 1 (controller Ruling 25) — SALES groups are always
on; only TECHNICIAN teams are `feature.service`.

`entity=team` carries both roads. The central gate decides on the model's
own reading only: an explicit `scope: technician` is refused there; any
other team reading is left to the dispatch, and the technician-team
handler — the only model road that writes `technician_teams` — declines on
plan itself. So a sales group works on Starter, and no technician-team
action gets past a decline, whatever the words were. The readings below
are the ones ask-model.py returned on 24 ก.ย. 2569 (--oa sales)."""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

SERVICE_LOCKED = "🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter"

CREATE_GROUP = {"action": "create", "entity": "team", "fields": {"team_name": "เหนือ", "scope": "sales"}, "missing": []}
LIST_GROUPS = {"action": "read", "entity": "team", "fields": {"scope": "sales"}, "missing": []}
DELETE_GROUP = {"action": "delete", "entity": "team", "fields": {"team_name": "เหนือ"}, "missing": []}
CREATE_TEAM = {"action": "create", "entity": "team", "fields": {"team_name": "ทีมเหนือ", "scope": "technician"},
               "missing": ["members"]}
DELETE_TEAM = {"action": "delete", "entity": "team", "fields": {"team_name": "ทีมเหนือ"}, "missing": []}
LIST_TECHS = {"action": "read", "entity": "team", "fields": {"scope": "technician"}, "missing": []}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "chann_sales_contact", "")


def _owner() -> FakeDataClient:
    client = FakeDataClient(permission_keys=["team.manage", "customer.read", "deal.read", "setting.manage",
                                             "member.manage", "view_reports", "ticket.read"], role="owner")
    client._is_owner = True
    return client


async def _say(client, message, reading, plan):
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload(plan)
    return await handle_chat_message(
        client, message=message, ctx=ctx,
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(reading, ensure_ascii=False))),
    )


def _wrote(client, name):
    return [r for r in client.recorded if r[0] == name]


@pytest.mark.parametrize("plan", ["starter", "pro"])
class TestSalesGroupsAreAlwaysOn:
    async def test_create(self, plan):
        client = _owner()
        reply = await _say(client, "สร้างกลุ่มขาย เหนือ", CREATE_GROUP, plan)
        assert "🔒" not in reply.text
        assert _wrote(client, "create_sales_group") and not _wrote(client, "create_technician_team")

    async def test_list(self, plan):
        client = _owner()
        client._sales_groups = [{"id": "sg-1", "group_name": "เหนือ"}]
        reply = await _say(client, "กลุ่มขายทั้งหมด", LIST_GROUPS, plan)
        assert "🔒" not in reply.text and "เหนือ" in reply.text

    async def test_delete_with_no_scope_in_the_reading(self, plan):
        client = _owner()
        client._sales_groups = [{"id": "sg-1", "group_name": "เหนือ"}]
        reply = await _say(client, "ลบกลุ่มขาย เหนือ", DELETE_GROUP, plan)
        assert "🔒" not in reply.text and "เหนือ" in reply.text
        assert not _wrote(client, "delete_technician_team")


class TestTechnicianTeamsOnStarter:
    async def test_create_is_refused(self):
        client = _owner()
        reply = await _say(client, "สร้างทีมช่าง ทีมเหนือ", CREATE_TEAM, "starter")
        assert reply.text.startswith(SERVICE_LOCKED)
        assert not _wrote(client, "create_technician_team")

    async def test_delete_with_no_scope_in_the_reading_is_refused(self):
        client = _owner()
        reply = await _say(client, "ลบทีมช่าง ทีมเหนือ", DELETE_TEAM, "starter")
        assert reply.text.startswith(SERVICE_LOCKED)
        assert not _wrote(client, "delete_technician_team")

    async def test_the_technician_roster_is_the_plan_not_an_empty_list(self):
        reply = await _say(_owner(), "รายชื่อช่าง", LIST_TECHS, "starter")
        assert reply.text.startswith(SERVICE_LOCKED)
        assert ("ขอรหัสเชิญช่าง", "ขอรหัสเชิญช่าง") not in reply.quick_replies


SHRUG = {"action": "suggest", "suggestions": [], "entity": None, "fields": {}, "missing": []}
STILL_OPEN = 'กลุ่มขายใช้ได้ทุกแพ็กเกจ — พิมพ์ "ดูกลุ่มขาย" หรือ "สร้างกลุ่มขาย <ชื่อกลุ่ม>"'


class TestTheGenericTeamWordOnStarter:
    """Ruling 30 (final fix round 1): a bare "ทีม" / "teams" the model had
    no reading for keeps its plan decline, and the decline says where the
    always-on sales groups are (Ruling 25). No new trigger word: the
    phrase the reply names is one the model already reads as
    read/team scope=sales (ask-model.py, 24 ก.ย. 2569)."""

    @pytest.mark.parametrize("word", ["ทีม", "มีทีมอะไรบ้าง", "teams"])
    async def test_the_decline_points_at_the_sales_groups(self, word):
        client = _owner()
        reply = await _say(client, word, SHRUG, "starter")
        assert reply.text.startswith(SERVICE_LOCKED), reply.text
        assert reply.text.endswith(STILL_OPEN), reply.text
        assert not _wrote(client, "create_sales_group") and not _wrote(client, "create_technician_team")

    async def test_a_technician_word_is_not_told_about_sales_groups(self):
        reply = await _say(_owner(), "ทีมช่าง", SHRUG, "starter")
        assert reply.text.startswith(SERVICE_LOCKED) and "กลุ่มขาย" not in reply.text

    async def test_the_phrase_it_names_reaches_the_groups(self):
        client = _owner()
        client._sales_groups = [{"id": "sg-1", "group_name": "เหนือ"}]
        reply = await _say(client, "ดูกลุ่มขาย", LIST_GROUPS, "starter")
        assert "🔒" not in reply.text and "เหนือ" in reply.text

    async def test_on_pro_the_word_lists_technician_teams_as_before(self):
        reply = await _say(_owner(), "ทีม", SHRUG, "pro")
        assert "🔒" not in reply.text and "กลุ่มขายใช้ได้ทุกแพ็กเกจ" not in reply.text


class TestTechnicianTeamsOnPro:
    async def test_create_is_made(self):
        client = _owner()
        reply = await _say(client, "สร้างทีมช่าง ทีมเหนือ", CREATE_TEAM, "pro")
        assert "🔒" not in reply.text
        assert _wrote(client, "create_technician_team")

    async def test_the_roster_answers(self):
        reply = await _say(_owner(), "รายชื่อช่าง", LIST_TECHS, "pro")
        assert "🔒" not in reply.text


class TestSurveySummary:
    async def test_starter_hears_the_plan(self):
        reading = {"action": "read", "entity": "report", "fields": {"type": "customer_satisfaction"}, "missing": []}
        reply = await _say(_owner(), "ความพึงพอใจเดือนนี้", reading, "starter")
        assert reply.text.startswith(SERVICE_LOCKED)
