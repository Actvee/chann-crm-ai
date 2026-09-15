"""Round 19 — the shop's own SLA numbers, escalation to the owner, the
approval SLA, and the chat command that sets them (owner, 15 ก.ย. 2569)."""
from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import approval_sla, job_sla
from chann_app.services.chat import handle_chat_message, parse_sla_sentence
from chann_app.services.thai_datetime import local_tz
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 15, 14, 0, tzinfo=local_tz())
BASE = {"id": "t1", "ticket_number": "T-2026-0001", "created_at": "2026-09-15T09:00:00+07:00", "customer_name": "สมชาย"}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestTheShopsOwnNumbers:
    def test_settings_rows_override_the_defaults_and_zero_switches_off(self):
        sla = job_sla.sla_from_rows([
            {"setting_key": "job_sla_unassigned_minutes", "setting_value": 30},
            {"setting_key": "job_sla_late_checkin_minutes", "setting_value": "0"},
            {"setting_key": "approval_sla_hours", "setting_value": 4},
        ])
        assert sla == {"unassigned": 30, "unaccepted": 60, "no_checkin": 0, "escalate": 60, "approval": 4}

    def test_a_shorter_limit_trips_sooner_and_off_never_trips(self):
        open_job = {**BASE, "status": "open"}
        assert [r for r, _ in job_sla.rules_tripped(open_job, NOW, {**job_sla.SLA_DEFAULTS, "unassigned": 30, "escalate": 0})] == ["unassigned"]
        assert job_sla.rules_tripped(open_job, NOW, {**job_sla.SLA_DEFAULTS, "unassigned": 0}) == []
        late = {**BASE, "status": "assigned", "assigned_to_ref": "m", "accept_status": "accepted", "scheduled_date": "2026-09-15", "scheduled_time": "13:00"}
        assert [r for r, _ in job_sla.rules_tripped(late, NOW, {**job_sla.SLA_DEFAULTS, "no_checkin": 0})] == []

    def test_past_the_escalation_window_the_rule_escalates(self):
        open_job = {**BASE, "status": "open"}
        # 5 h unassigned: limit 2 h, escalate 60 min → both
        assert [r for r, _ in job_sla.rules_tripped(open_job, NOW, dict(job_sla.SLA_DEFAULTS))] == ["unassigned", "escalated_unassigned"]
        # limit 4 h + escalate 90 min = 5.5 h > 5 h → not yet
        assert [r for r, _ in job_sla.rules_tripped(open_job, NOW, {**job_sla.SLA_DEFAULTS, "unassigned": 240, "escalate": 90})] == ["unassigned"]


class TestEscalationReachesTheOwnerOnce:
    async def test_the_owner_is_told_once_and_the_dispatcher_once(self):
        client = FakeDataClient(permission_keys=["ticket.assign"])
        client._tickets = [{**BASE, "status": "open"}]
        client._members = [
            {"id": "member-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales"},
            {"id": "m-cs", "chann_uid": "CHN-S-000009", "role": "cs", "status": "active", "display_name": "ซีเอส", "channel": "sales"},
        ]
        first = await job_sla.sweep_jobs(client, now=NOW, license_ids=["L1"])
        second = await job_sla.sweep_jobs(client, now=NOW, license_ids=["L1"])
        notes = [r[2]["body"] for r in client.recorded if r[0] == "create_note"]
        assert first["told"] == 2 and second["told"] == 0, (first, second, notes)
        assert any(b.startswith(f"{job_sla.SLA_MARK}:escalated_unassigned") for b in notes), notes
        escalations = [r for r in client.recorded if r[0] == "create_notification" and "เกิน SLA" in str(r)]
        assert escalations, [r for r in client.recorded if r[0] == "create_notification"][:3]


class TestTheApprovalSla:
    def _shop(self, waited_hours: int, sla_hours: int = 24, escalate: int = 60) -> FakeDataClient:
        client = FakeDataClient(permission_keys=["service_report.approve"])
        client._settings = [{"setting_key": "approval_sla_hours", "setting_value": sla_hours}, {"setting_key": "sla_escalate_minutes", "setting_value": escalate}]
        client._members = [
            {"id": "member-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "display_name": "เจ้าของ", "channel": "sales"},
            {"id": "m-lead", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "display_name": "หัวหน้า", "channel": "technician"},
        ]
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "completed", "customer_name": "สมชาย"}]
        client._reports = [{"id": "r1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted", "updated_at": "2026-09-14T10:00:00+07:00"}]
        client._approval_steps = [{"id": "s1", "entity_type": "service_report", "entity_id": "r1", "step_order": 1, "status": "pending",
                                   "approver_type": "user", "approver_ref": "m-lead",
                                   "created_at": (NOW - __import__("datetime").timedelta(hours=waited_hours)).isoformat()}]
        return client

    async def test_late_reports_remind_the_approver_once(self):
        client = self._shop(waited_hours=24)
        first = await approval_sla.sweep_reports(client, now=NOW, license_ids=["L1"])
        second = await approval_sla.sweep_reports(client, now=NOW, license_ids=["L1"])
        assert first["told"] == 1 and second["told"] == 0, (first, second)
        sent = [r for r in client.recorded if r[0] == "create_notification"]
        assert sent and "SR-2026-0001" in str(sent[0]) and "CHN-T-000001" in str(sent[0]), sent

    async def test_within_the_sla_nothing_is_said(self):
        client = self._shop(waited_hours=3)
        assert (await approval_sla.sweep_reports(client, now=NOW, license_ids=["L1"]))["told"] == 0

    async def test_past_the_escalation_window_the_owner_is_told(self):
        client = self._shop(waited_hours=26)
        first = await approval_sla.sweep_reports(client, now=NOW, license_ids=["L1"])
        assert first["told"] == 2, first
        sent = [str(r) for r in client.recorded if r[0] == "create_notification"]
        assert any("เกิน SLA อนุมัติ" in s and "CHN-S-000001" in s for s in sent), sent

    async def test_a_zero_sla_switches_the_sweep_off(self):
        client = self._shop(waited_hours=100, sla_hours=0)
        assert (await approval_sla.sweep_reports(client, now=NOW, license_ids=["L1"]))["told"] == 0


class TestTheChatCommand:
    def test_the_sentence_is_read_by_the_code(self):
        assert parse_sla_sentence("งานไม่มีคนรับ 1 ชม. ช่างไม่ตอบ 30 นาที เลยนัด 15 นาที แจ้งเจ้าของหลัง 1.5 ชั่วโมง อนุมัติค้าง 4 ชม.") == {
            "unassigned": 60, "unaccepted": 30, "no_checkin": 15, "escalate": 90, "approval": 4,
        }
        assert parse_sla_sentence("เลยนัด ปิด") == {"no_checkin": 0}
        assert parse_sla_sentence("set SLA unassigned 2 h, approval 8 hours") == {"unassigned": 120, "approval": 8}
        assert parse_sla_sentence("ไม่มีตัวเลข") == {}

    async def test_setting_writes_the_rows_and_shows_the_state(self):
        client = FakeDataClient(permission_keys=["setting.manage", "customer.read"])
        ai = httpx.AsyncClient(transport=_ai(json.dumps({"action": "create", "entity": "team", "fields": {"name": "SLA"}, "missing": []})))
        reply = await handle_chat_message(client, message="ตั้ง SLA งานไม่มีคนรับ 1 ชม. ช่างไม่ตอบ 30 นาที อนุมัติค้าง 4 ชม.", ctx=_ctx(), ai_client=ai)
        puts = [(r[2], r[3]) for r in client.recorded if r[0] == "put_license_setting"]
        assert puts == [("job_sla_unassigned_minutes", 60), ("job_sla_unaccepted_minutes", 30), ("approval_sla_hours", 4)], puts
        assert "บันทึก SLA แล้ว" in reply.text and "4 ชม." in reply.text, reply.text
        assert not any(r[0] == "create_technician_team" for r in client.recorded), "the router model must never see the sentence"
        shown = await handle_chat_message(client, message="ดู SLA", ctx=_ctx(), ai_client=ai)
        assert "SLA ของร้าน" in shown.text and "30 นาที" in shown.text, shown.text

    async def test_without_the_key_it_is_refused(self):
        client = FakeDataClient(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="ตั้ง SLA งานไม่มีคนรับ 1 ชม.", ctx=_ctx(), ai_client=httpx.AsyncClient(transport=_ai(json.dumps({"action": "suggest", "entity": None, "fields": {}, "missing": []}))))
        assert "สิทธิ์" in reply.text and not any(r[0] == "put_license_setting" for r in client.recorded)
