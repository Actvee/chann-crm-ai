"""Round 18d, audit verify 15 ก.ย. 2569 — items 5 to 9, held as tests.

  5. "ช่วยเปิด…" switched auto-accept OFF, because "ปิด" is a substring of
     "เปิด" and the off-arm was tested first.
  6. "ช่างมาพรุ่งนี้ไม่ได้นะ" read by the model as update/ticket moved the
     visit TO tomorrow; the day named is the one that does NOT suit.
  7. "ร้านอยู่ที่ไหน" typed after the contact tile was forwarded to the shop
     as if it were the customer's question; it is the card again. A real
     question for the shop ("ร้านเปิดวันอาทิตย์ไหม") still goes through.
  8. "งานทั้งหมดของฉันมีกี่งาน" came back type=jobs → scope open; the
     person said whose.
  9. "ยังไม่ถึงหน้างาน" with the model's "suggest" was answered "not sure";
     the guide promises "ยังไม่ได้บันทึก". The same answer at the second
     site of the hunk: a job-step reading the permission gate cannot place
     (check_in on "ticket") used to get the capability menu.

The model is STUBBED: every reading below is authored by the test. Each
test asserts the reply text AND the rows the fake data tier saw.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services.chat import (  # noqa: E402
    AMEND_ASK_NEW_DATE,
    AMEND_MOVE_REQUESTED,
    CUSTOMER_CONTACT_INFO,
    JOB_ACTION_NOT_REQUESTED,
    _as_the_technician_means_it,
    _disclaimed_job_step,
    handle_chat_message,
)
from chann_app.services.onboarding import SETTING_KEY  # noqa: E402
from chann_app.services.thai_datetime import local_today  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402

# Every way this system changes a record; conversation state is not a write.
WRITES = (
    "create_", "update_", "delete_", "set_quote", "transition_", "archive_",
    "register_", "claim_", "add_", "remove_", "put_", "assign_", "reject_",
    "promote_", "check_in_", "check_out_", "set_ticket_status",
    "set_follow_up_status", "open_approval_steps", "publish_",
    "open_chat_session", "close_chat_session", "mark_survey_sent",
    "set_display_preferences", "set_identity_signature",
)
NOT_WRITES = ("set_last_", "set_pending", "clear_pending", "set_active_tenant", "mark_chat_read")


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _reading(intent: dict) -> httpx.AsyncClient:
    """What the model would return, as an OpenRouter transport."""
    return httpx.AsyncClient(transport=_ai(json.dumps(intent, ensure_ascii=False)))


def _written(client) -> list[str]:
    return [c[0] for c in client.recorded if c[0].startswith(WRITES) and not c[0].startswith(NOT_WRITES)]


# ------------------------------------------------------------------ item 5

class TestThePoliteOpenTurnsAutoAcceptOn:
    """Item 5: "ช่วยเปิด…" records True, "…ปิด…" records False (hunk "เปิด first")."""

    UPDATE = {"action": "update", "entity": "setting", "fields": {"field": "auto_accept"}, "missing": []}

    def _client(self):
        return FakeDataClient(permission_keys=["setting.manage", "setting.read", "customer.read"])

    @pytest.mark.asyncio
    async def test_a_polite_open_records_true(self):
        """Item 5: the sentence that used to switch it OFF puts the value True."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ช่วยเปิดรับลูกค้าใหม่อัตโนมัติให้หน่อยครับ", ctx=_ctx(), ai_client=_reading(self.UPDATE),
        )
        put = [r for r in client.recorded if r[0] == "put_license_setting"]
        assert len(put) == 1, (reply.text, client.recorded[-4:])
        assert put[0][1:] == (LICENSE_ID, SETTING_KEY, True), put
        assert reply.text.startswith("รับลูกค้าใหม่อัตโนมัติ: เปิด"), reply.text

    @pytest.mark.asyncio
    async def test_a_polite_close_records_false(self):
        """Item 5: the same sentence with ปิด puts the value False."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ช่วยปิดรับลูกค้าใหม่อัตโนมัติให้หน่อยครับ", ctx=_ctx(), ai_client=_reading(self.UPDATE),
        )
        put = [r for r in client.recorded if r[0] == "put_license_setting"]
        assert len(put) == 1, (reply.text, client.recorded[-4:])
        assert put[0][1:] == (LICENSE_ID, SETTING_KEY, False), put
        assert reply.text.startswith("รับลูกค้าใหม่อัตโนมัติ: ปิด"), reply.text

    @pytest.mark.asyncio
    async def test_the_bare_close_records_false(self):
        """Item 5: "ปิดรับลูกค้าใหม่อัตโนมัติ" is the off switch."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ปิดรับลูกค้าใหม่อัตโนมัติ", ctx=_ctx(), ai_client=_reading(self.UPDATE),
        )
        put = [r for r in client.recorded if r[0] == "put_license_setting"]
        assert put and put[-1][3] is False, (reply.text, client.recorded[-4:])
        assert reply.text.startswith("รับลูกค้าใหม่อัตโนมัติ: ปิด"), reply.text


# ------------------------------------------------------------------ item 6

class TestADayThatDoesNotSuitIsNotTheNewDay:
    """Item 6: "ช่างมาพรุ่งนี้ไม่ได้นะ" read as update/ticket asks for a day
    that suits (hunk "the day named is the one that does NOT suit")."""

    UPDATE = {"action": "update", "entity": "ticket", "fields": {"scheduled_date": "tomorrow"}, "missing": []}

    def _client(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [{
            "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "assigned_to_ref": "m-tech-1",
            "assigned_target_type": "technician", "accept_status": "accepted", "customer_chann_uid": "CHN-S-000001",
            "customer_name": "สมหญิง", "issue_description": "แอร์ไม่เย็น",
            "scheduled_date": (local_today() + timedelta(days=1)).isoformat(), "scheduled_time": "10:00",
        }]
        client._members = [{"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active",
                            "display_name": "สมศักดิ์"}]
        return client

    @pytest.mark.asyncio
    async def test_the_unsuitable_day_is_not_booked(self):
        """Item 6: no move request is filed on tomorrow; the reply asks which day suits."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ช่างมาพรุ่งนี้ไม่ได้นะ", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reading(self.UPDATE),
        )
        assert reply.text == AMEND_ASK_NEW_DATE["th"], reply.text
        assert _written(client) == [], _written(client)
        assert not [r for r in client.recorded if r[0] in ("update_ticket", "set_ticket_status", "create_note")]
        # The job is held so the next line typed is the new day.
        pending = await client.get_pending_intent("CHN-S-000001", "customer")
        assert pending and pending["entity"] == "customer_ticket" and pending["missing"] == ["schedule"], pending
        assert pending["fields"].get("reschedule") == 1 and pending["fields"].get("code") == "T-2026-0001", pending

    @pytest.mark.asyncio
    async def test_a_day_that_is_wanted_still_reaches_the_shop(self):
        """Item 6 opposite: "ขอเลื่อนนัดเป็นพรุ่งนี้" names the day it wants and is filed."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ขอเลื่อนนัดเป็นพรุ่งนี้", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reading(self.UPDATE),
        )
        notes = [r for r in client.recorded if r[0] == "create_note"]
        assert notes and "ลูกค้าขอเลื่อนนัดเป็น" in notes[-1][2]["body"], (reply.text, notes)
        assert reply.text.startswith(AMEND_MOVE_REQUESTED["th"].split("{code}")[0]), reply.text
        assert reply.text != AMEND_ASK_NEW_DATE["th"]

    @pytest.mark.parametrize("message", ["พรุ่งนี้ไม่สะดวกให้ช่างมาครับ", "ช่างมาวันศุกร์ไม่ได้ครับ"])
    @pytest.mark.asyncio
    async def test_any_unsuitable_day_is_asked_not_booked(self, message):
        """Item 6: "ไม่สะดวก" / "ไม่ได้" with whichever day named — the model's
        update reading is handed back to the rule road, which asks. Before the
        hunk the model road's amend read the day out of the whole sentence and
        filed the move to it."""
        client = self._client()
        reply = await handle_chat_message(
            client, message=message, ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reading(self.UPDATE),
        )
        assert reply.text == AMEND_ASK_NEW_DATE["th"], reply.text
        notes = [r for r in client.recorded if r[0] == "create_note"]
        assert notes == [], notes
        assert _written(client) == [], _written(client)
        pending = await client.get_pending_intent("CHN-S-000001", "customer")
        assert pending and pending["fields"].get("reschedule") == 1 and pending["missing"] == ["schedule"], pending


# ------------------------------------------------------------------ item 7

class TestWhereIsTheShopIsTheCardAgain:
    """Item 7: after the contact tile, "ร้านอยู่ที่ไหน" is the card and is
    NOT forwarded; "ร้านเปิดวันอาทิตย์ไหมครับ" IS forwarded (hunk
    "ร้านเปิดวันอาทิตย์ไหม is still a question only the shop can answer")."""

    PROFILE = {
        "legal_name": None, "company_name": "Test Co", "tax_id": None,
        "company_address": "99 ถ.สุขุมวิท กรุงเทพฯ", "company_phone": "021234567",
        "company_email": None, "vat_rate": None,
    }

    async def _turns(self, *messages):
        client = FakeDataClient(role="customer", permission_keys=[], company_profile=self.PROFILE)
        ctx = _ctx(primary_role="customer", oa="customer")
        reply = None
        for message in messages:
            reply = await handle_chat_message(client, ctx=ctx, message=message, language="th")
        forwarded = [c[0] for c in client.recorded if c[0] in ("open_chat_session", "add_chat_message")]
        return client, (reply.text or ""), forwarded

    @pytest.mark.asyncio
    async def test_where_is_the_shop_answers_with_the_card_and_is_not_forwarded(self):
        """Item 7: "ร้านอยู่ที่ไหน" after the tile shows the shop card again."""
        client, text, forwarded = await self._turns("ติดต่อร้าน", "ร้านอยู่ที่ไหน")
        assert forwarded == [], forwarded
        card = CUSTOMER_CONTACT_INFO["th"].format(
            company="Test Co", lines="· โทร 021234567\n· ที่อยู่ 99 ถ.สุขุมวิท กรุงเทพฯ",
        )
        assert text == card, text
        # The card re-arms the prompt, so the NEXT line still goes to the shop.
        pending = await client.get_pending_intent("CHN-S-000001", "customer")
        assert pending and pending["entity"] == "customer_contact", pending

    @pytest.mark.asyncio
    async def test_open_on_sunday_is_still_forwarded(self):
        """Item 7 opposite: a question only the shop can answer reaches the shop."""
        client, text, forwarded = await self._turns("ติดต่อร้าน", "ร้านเปิดวันอาทิตย์ไหมครับ")
        assert "open_chat_session" in forwarded and "add_chat_message" in forwarded, (forwarded, text[:70])
        sent = [c for c in client.recorded if c[0] == "add_chat_message"]
        assert sent[-1][3] == "customer" and sent[-1][4] == "ร้านเปิดวันอาทิตย์ไหมครับ", sent
        assert "ติดต่อ Test Co ได้ที่" not in text, text


# ------------------------------------------------------------------ item 8

class TestOfMineIsScopeMine:
    """Item 8: "ของฉัน" in the sentence is scope mine whatever the model's
    fields said (hunk "the person said whose")."""

    OPEN = {"action": "read", "entity": "report", "fields": {"type": "jobs", "scope": "open"}, "missing": []}

    @pytest.mark.parametrize("message", ["งานทั้งหมดของฉันมีกี่งาน", "งานทั้งหมดของผมมีกี่งาน"])
    def test_of_mine_overrides_the_models_open(self, message):
        """Item 8: the fields' open/jobs words lose to "ของฉัน" in the sentence."""
        ctx = _ctx(oa="technician", primary_role="technician")
        out = _as_the_technician_means_it(dict(self.OPEN), ctx, message)
        assert out == {"action": "read", "entity": "ticket", "fields": {"scope": "mine"}, "missing": []}, out

    def test_without_of_mine_the_fields_scope_is_kept(self):
        """Item 8 opposite: no "ของฉัน", so the model's scope word stands."""
        ctx = _ctx(oa="technician", primary_role="technician")
        out = _as_the_technician_means_it(dict(self.OPEN), ctx, "งานทั้งหมดมีกี่งาน")
        assert out["entity"] == "ticket" and out["fields"] == {"scope": "open"}, out
        team = _as_the_technician_means_it(
            {"action": "read", "entity": "report", "fields": {"type": "team"}, "missing": []}, ctx, "งานของทีมมีกี่งาน",
        )
        assert team["fields"] == {"scope": "team"}, team


# ------------------------------------------------------------------ item 9

class TestADisclaimedJobStepIsNotRecorded:
    """Item 9: "ยังไม่ถึงหน้างาน" on the technician OA is answered
    JOB_ACTION_NOT_REQUESTED and records nothing (hunk "_disclaimed_job_step")."""

    SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}
    # check_in on "ticket" has no ACTION_PERMISSIONS row: the gate cannot
    # place it, which is the hunk's second site.
    CHECK_IN_ON_TICKET = {"action": "check_in", "entity": "ticket", "fields": {}, "missing": []}

    def _client(self):
        client = FakeDataClient(
            role="technician",
            permission_keys=["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read"],
        )
        client._tickets = [{
            "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
            "assigned_to_ref": "member-1", "assigned_target_type": "technician", "customer_name": "สมชาย",
            "customer_phone": "0812345678", "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
            "scheduled_date": local_today().isoformat(), "scheduled_time": "10:00",
        }]
        return client

    @pytest.mark.asyncio
    async def test_not_there_yet_with_suggest_is_acknowledged_not_recorded(self):
        """Item 9: the model's "suggest" for the disclaimer gets the guide's answer."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ยังไม่ถึงหน้างาน", ctx=_ctx(oa="technician", primary_role="technician"),
            ai_client=_reading(self.SUGGEST),
        )
        assert reply.text == JOB_ACTION_NOT_REQUESTED["th"], reply.text
        assert _written(client) == [], _written(client)
        assert not [r for r in client.recorded if r[0] == "check_in_ticket"]

    @pytest.mark.asyncio
    async def test_not_there_yet_read_as_a_step_the_gate_cannot_place_is_acknowledged(self):
        """Item 9: the model's check_in on "ticket" (no permission row) reaches
        the gate's refusal — before the hunk the capability menu, now the
        guide's "ยังไม่ได้บันทึก" answer — and nothing is checked in."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ยังไม่ถึงหน้างาน", ctx=_ctx(oa="technician", primary_role="technician"),
            ai_client=_reading(self.CHECK_IN_ON_TICKET),
        )
        assert reply.text == JOB_ACTION_NOT_REQUESTED["th"], reply.text
        assert _written(client) == [], _written(client)
        assert not [r for r in client.recorded if r[0] == "check_in_ticket"]
        assert client._tickets[0]["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_arrived_with_suggest_is_not_the_disclaimer_answer(self):
        """Item 9 opposite: "ถึงหน้างานแล้ว" has no disclaimer and checks in."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ถึงหน้างานแล้ว", ctx=_ctx(oa="technician", primary_role="technician"),
            ai_client=_reading(self.SUGGEST),
        )
        assert reply.text != JOB_ACTION_NOT_REQUESTED["th"], reply.text
        checked = [r for r in client.recorded if r[0] == "check_in_ticket"]
        assert checked and checked[-1][2] == "t1", (reply.text, client.recorded[-4:])

    def test_the_helper_needs_the_technician_oa_a_disclaimer_and_a_job_word(self):
        """Item 9: _disclaimed_job_step is the technician OA + disclaimer + job-step word."""
        assert _disclaimed_job_step("technician", "ยังไม่ถึงหน้างาน") is True
        assert _disclaimed_job_step("technician", "ยังไม่ได้ปิดงาน") is True
        assert _disclaimed_job_step("sales", "ยังไม่ถึงหน้างาน") is False
        assert _disclaimed_job_step("technician", "ถึงหน้างานแล้ว") is False
        assert _disclaimed_job_step("technician", "ยังไม่ได้กินข้าว") is False
