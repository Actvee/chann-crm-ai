"""Round 18d, audit verify (15 ก.ย. 2569) — the technician's day and the
dispatcher's hallway, held as tests.

Items 10–14 of the round: a check-in that must ASK when another job is
already in progress; a check-out that offers the jobs in progress as
buttons instead of a code format; "งานนี้" after a job card meaning that
job; a bare name finishing "มอบหมาย T-…" instead of going to the model as
a fresh sentence; and an automatic assignment that names the person, not
the member id.

The model is STUBBED throughout: each test authors the reading the router
gets, and asserts the reply text, the buttons and the rows the fake Data
tier saw — never merely that nothing blew up.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402

from chann_app.services.chat import (  # noqa: E402
    CHECKIN_PICK_ONE,
    TICKET_ASSIGN_WHO,
    TICKET_PICK_ONE,
    handle_chat_message,
)
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    """Every model answer below is written by the test, not by a model."""
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _model(intent: dict) -> httpx.AsyncClient:
    """The model road: one fixed reading for every call."""
    return httpx.AsyncClient(transport=_ai(json.dumps({"missing": [], **intent})))


def _model_by_sentence(readings: list[tuple[str, dict]], default: dict | None = None) -> httpx.AsyncClient:
    """A reading per sentence: the first key found in the user message wins.

    parse_intent sends the person's sentence verbatim as the last "user"
    message, so a two-turn conversation (ask, then answer) can be given the
    two readings a real model would return for the two sentences."""
    fallback = default or {"action": "suggest", "entity": None, "fields": {}, "missing": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        said = next(
            (str(m.get("content") or "") for m in reversed(body.get("messages") or []) if m.get("role") == "user"),
            "",
        )
        picked = next((r for key, r in readings if key in said), fallback)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps({"missing": [], **picked})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "provider": "fireworks",
        })

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _job(n: int, status: str, **extra) -> dict:
    """One of the technician's own jobs (get_member always answers member-1)."""
    return {
        "id": f"t{n}", "ticket_number": f"T-2026-{n:04d}", "status": status,
        "assigned_to_ref": "member-1", "assigned_target_type": "technician", "accept_status": "accepted",
        "customer_name": f"ลูกค้า {n}", "service_address": f"{n}/1 ถ.ทดสอบ", **extra,
    }


TECHNICIAN = sorted(DEFAULT_ROLE_TEMPLATES["technician"])


def _technician(tickets: list[dict]) -> FakeDataClient:
    """A technician with the role's default keys: the model road's check-in
    and check-out are gated on service_report.create, not ticket.update."""
    client = FakeDataClient(role="technician", permission_keys=TECHNICIAN)
    client._tickets = tickets
    return client


def _tech_ctx():
    return _ctx(oa="technician", primary_role="technician")


def _recorded(client: FakeDataClient, name: str) -> list[tuple]:
    return [r for r in client.recorded if r[0] == name]


CHECK_IN = {"action": "check_in", "entity": "service_report", "fields": {}}
CHECK_OUT = {"action": "check_out", "entity": "service_report", "fields": {}}
SUGGEST = {"action": "suggest", "entity": None, "fields": {}}
READ_TICKET = {"action": "read", "entity": "ticket", "fields": {}}


# ---------------------------------------------------------------- item 10
class TestACheckInWithAnotherJobInProgressAsks:
    """Item 10 — "ถึงแล้วครับ" while one job is in progress and another is
    assigned: two candidates means asking, not checking the second one in."""

    def _busy_day(self) -> FakeDataClient:
        return _technician([_job(1, "in_progress"), _job(2, "assigned")])

    @pytest.mark.asyncio
    async def test_the_model_road_asks_which_job_and_records_nothing(self):
        """Item 10: model reads check_in; one in progress + one assigned → CHECKIN_PICK_ONE with both codes."""
        client = self._busy_day()
        reply = await handle_chat_message(client, message="ถึงแล้วครับ", ctx=_tech_ctx(), ai_client=_model(CHECK_IN))
        assert reply.text == CHECKIN_PICK_ONE["th"], reply.text
        assert reply.quick_replies == [
            ("T-2026-0002", "เช็คอิน T-2026-0002"),
            ("T-2026-0001", "เช็คอิน T-2026-0001"),
        ], reply.quick_replies
        assert not _recorded(client, "check_in_ticket"), client.recorded

    @pytest.mark.asyncio
    async def test_the_typed_road_asks_the_same(self):
        """Item 10: the model shrugs (suggest) and the CHECKIN_TRIGGERS road reaches the same question."""
        client = self._busy_day()
        reply = await handle_chat_message(client, message="ถึงแล้วครับ", ctx=_tech_ctx(), ai_client=_model(SUGGEST))
        assert reply.text == CHECKIN_PICK_ONE["th"], reply.text
        assert {p for _, p in reply.quick_replies} == {"เช็คอิน T-2026-0001", "เช็คอิน T-2026-0002"}, reply.quick_replies
        assert not _recorded(client, "check_in_ticket"), client.recorded

    @pytest.mark.asyncio
    async def test_with_nothing_in_progress_the_one_assigned_job_is_checked_in(self):
        """Item 10 control: only T-2026-0002 assigned (T-2026-0001 finished) → the check-in is recorded."""
        client = _technician([_job(1, "completed"), _job(2, "assigned")])
        reply = await handle_chat_message(client, message="ถึงแล้วครับ", ctx=_tech_ctx(), ai_client=_model(CHECK_IN))
        checked = _recorded(client, "check_in_ticket")
        assert checked and checked[0][2] == "t2" and checked[0][3] == "member-1", client.recorded
        assert "เช็คอิน T-2026-0002 แล้ว" in reply.text, reply.text
        assert reply.text != CHECKIN_PICK_ONE["th"]

    @pytest.mark.asyncio
    async def test_an_explicit_code_is_not_asked_again(self):
        """Item 10: "เช็คอิน T-2026-0002" names the job — the busy one does not turn it into a question."""
        client = self._busy_day()
        reply = await handle_chat_message(
            client, message="เช็คอิน T-2026-0002", ctx=_tech_ctx(),
            ai_client=_model({**CHECK_IN, "fields": {"code": "T-2026-0002"}}),
        )
        checked = _recorded(client, "check_in_ticket")
        assert checked and checked[0][2] == "t2", client.recorded
        assert "เช็คอิน T-2026-0002 แล้ว" in reply.text, reply.text


# ---------------------------------------------------------------- item 11
class TestACheckOutWithoutACodeOffersTheJobsInProgress:
    """Item 11 — "ปิดงาน" with two jobs in progress: the jobs as buttons whose
    postback is "ปิดงาน <code>", nothing written, no draft opened."""

    def _two_open_visits(self) -> FakeDataClient:
        return _technician([_job(1, "in_progress"), _job(2, "in_progress")])

    @pytest.mark.asyncio
    async def test_ปิดงาน_with_two_jobs_in_progress_offers_both_as_buttons(self):
        """Item 11: TICKET_PICK_ONE with a "ปิดงาน <code>" button per job in progress; nothing recorded."""
        client = self._two_open_visits()
        reply = await handle_chat_message(client, message="ปิดงาน", ctx=_tech_ctx(), ai_client=_model(CHECK_OUT))
        assert reply.text.startswith(TICKET_PICK_ONE["th"]), reply.text
        assert reply.quick_replies == [
            ("T-2026-0001", "ปิดงาน T-2026-0001"),
            ("T-2026-0002", "ปิดงาน T-2026-0002"),
        ], reply.quick_replies
        assert not _recorded(client, "check_out_ticket"), client.recorded
        assert not _recorded(client, "set_pending_intent"), client.recorded
        assert await client.get_pending_intent("CHN-S-000001", "technician") is None

    @pytest.mark.asyncio
    async def test_only_the_jobs_in_progress_are_offered(self):
        """Item 11: a job merely assigned (not checked in) is not a check-out candidate."""
        client = _technician([_job(1, "in_progress"), _job(2, "in_progress"), _job(3, "assigned")])
        reply = await handle_chat_message(client, message="ปิดงาน", ctx=_tech_ctx(), ai_client=_model(CHECK_OUT))
        assert reply.text.startswith(TICKET_PICK_ONE["th"]), reply.text
        assert [p for _, p in reply.quick_replies] == ["ปิดงาน T-2026-0001", "ปิดงาน T-2026-0002"], reply.quick_replies

    @pytest.mark.asyncio
    async def test_the_button_then_starts_the_report_for_that_job(self):
        """Item 11: tapping "ปิดงาน T-2026-0001" opens the guided report for that job, not a code error."""
        client = self._two_open_visits()
        ctx = _tech_ctx()
        ai = _model({**CHECK_OUT, "fields": {"code": "T-2026-0001"}})
        first = await handle_chat_message(client, message="ปิดงาน", ctx=ctx, ai_client=ai)
        assert first.text.startswith(TICKET_PICK_ONE["th"]), first.text
        await handle_chat_message(client, message="ปิดงาน T-2026-0001", ctx=ctx, ai_client=ai)
        pending = await client.get_pending_intent("CHN-S-000001", "technician")
        assert pending and pending["entity"] == "service_report" and pending["fields"]["code"] == "T-2026-0001", pending
        assert not _recorded(client, "check_out_ticket"), client.recorded

    @pytest.mark.asyncio
    async def test_one_job_in_progress_needs_no_button(self):
        """Item 11 control: a single job in progress is inferred and the report starts at once."""
        client = _technician([_job(1, "in_progress"), _job(2, "assigned")])
        reply = await handle_chat_message(client, message="ปิดงาน", ctx=_tech_ctx(), ai_client=_model(CHECK_OUT))
        assert not reply.text.startswith(TICKET_PICK_ONE["th"]), reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "technician")
        assert pending and pending["entity"] == "service_report" and pending["fields"]["code"] == "T-2026-0001", pending


# ---------------------------------------------------------------- item 12
class Testงานนี้AfterAJobWasShownIsThatJob:
    """Item 12 — "งานนี้นัดวันไหน" right after a job card is that job's card,
    not the job list."""

    def _two_jobs(self, **client_kw) -> FakeDataClient:
        client = FakeDataClient(**client_kw)
        client._tickets = [
            _job(1, "assigned", scheduled_date="2026-09-17", scheduled_time="10:00", customer_name="สมชาย ใจดี"),
            _job(2, "assigned", scheduled_date="2026-09-18", scheduled_time="14:00", customer_name="สมหญิง รักดี"),
        ]
        return client

    @pytest.mark.asyncio
    async def test_technician_งานนี้นัดวันไหน_after_ดูงาน_is_the_card(self):
        """Item 12: technician OA — "ดูงาน T-2026-0001" then "งานนี้นัดวันไหน" (model: read/ticket {}) → that ticket's card with its date."""
        client = self._two_jobs(role="technician", permission_keys=TECHNICIAN)
        ctx = _tech_ctx()
        ai = _model(READ_TICKET)
        shown = await handle_chat_message(client, message="ดูงาน T-2026-0001", ctx=ctx, ai_client=ai)
        assert "T-2026-0001" in shown.text and "สมชาย ใจดี" in shown.text, shown.text
        assert ("set_last_entity_ref", "CHN-S-000001", "technician", "ticket", "t1", "T-2026-0001") in client.recorded
        asked = await handle_chat_message(client, message="งานนี้นัดวันไหน", ctx=ctx, ai_client=ai)
        assert "T-2026-0001" in asked.text and "นัด: 17 ก.ย. 2569" in asked.text, asked.text
        assert "T-2026-0002" not in asked.text and "สมหญิง" not in asked.text, asked.text

    @pytest.mark.asyncio
    async def test_sales_งานนี้นัดวันไหน_after_the_card_is_that_job_not_the_list(self):
        """Item 12: sales OA — the "is that job" branch itself: the ref in view names the job the model left blank."""
        client = self._two_jobs(permission_keys=["ticket.read", "ticket.update"])
        ctx = _ctx()
        ai = _model(READ_TICKET)
        shown = await handle_chat_message(client, message="ดูงาน T-2026-0001", ctx=ctx, ai_client=ai)
        assert "T-2026-0001" in shown.text and "T-2026-0002" not in shown.text, shown.text
        asked = await handle_chat_message(client, message="งานนี้นัดวันไหน", ctx=ctx, ai_client=ai)
        assert "T-2026-0001" in asked.text and "นัด: 17 ก.ย. 2569" in asked.text, asked.text
        assert "T-2026-0002" not in asked.text, asked.text

    @pytest.mark.asyncio
    async def test_sales_without_a_job_in_view_the_same_reading_is_the_list(self):
        """Item 12 control: no card shown first → read/ticket {} on the sales OA is still the job list."""
        client = self._two_jobs(permission_keys=["ticket.read", "ticket.update"])
        reply = await handle_chat_message(client, message="งานนี้นัดวันไหน", ctx=_ctx(), ai_client=_model(READ_TICKET))
        assert "T-2026-0001" in reply.text and "T-2026-0002" in reply.text, reply.text


# ---------------------------------------------------------------- item 13
ASSIGN_CODE_ONLY = {"action": "assign", "entity": "ticket", "fields": {"code": "T-2026-0001"}}
ASSIGN_TO_SOMSAK = {"action": "assign", "entity": "ticket", "fields": {"code": "T-2026-0001", "target_name": "สมศักดิ์"}}


class TestTheTypedNameFinishesTheAssignment:
    """Item 13 — "มอบหมาย T-2026-0001" asks who and HOLDS the job; the bare
    answer "สมศักดิ์" completes it; a question as the answer does not."""

    def _dispatcher(self) -> FakeDataClient:
        client = FakeDataClient(permission_keys=["ticket.assign", "ticket.read", "ticket.update"])
        client._is_owner = True
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open",
                            "customer_name": "สมชาย ใจดี", "product_category": "AC"}]
        client._members = [{"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"}]
        client._profiles = {"CHN-T-000001": {"first_name": "สมศักดิ์", "last_name": "ใจดี"}}
        return client

    def _model(self) -> httpx.AsyncClient:
        # The reading a model gives each sentence: the bare command names no
        # one; the rebuilt "มอบหมาย T-2026-0001 ให้ สมศักดิ์" names สมศักดิ์.
        return _model_by_sentence([("ให้ สมศักดิ์", ASSIGN_TO_SOMSAK), ("มอบหมาย", ASSIGN_CODE_ONLY)])

    @pytest.mark.asyncio
    async def test_มอบหมาย_without_a_name_asks_who_and_holds_the_job(self):
        """Item 13: the reply asks who; pending entity ticket_assign_target carries the code; nothing assigned."""
        client = self._dispatcher()
        reply = await handle_chat_message(client, message="มอบหมาย T-2026-0001", ctx=_ctx(), ai_client=self._model())
        assert reply.text == TICKET_ASSIGN_WHO["th"].format(code="T-2026-0001"), reply.text
        assert ("สมศักดิ์ ใจดี", "มอบหมาย T-2026-0001 ให้ CHN-T-000001") in reply.quick_replies, reply.quick_replies
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending and pending["entity"] == "ticket_assign_target", pending
        assert pending["fields"]["code"] == "T-2026-0001" and pending["fields"]["trigger"] == "มอบหมาย", pending
        assert not _recorded(client, "assign_ticket"), client.recorded

    @pytest.mark.asyncio
    async def test_the_bare_name_then_assigns_to_that_technician(self):
        """Item 13: "สมศักดิ์" as the answer → assign_ticket to member m-tech-1 is recorded and the hold is released."""
        client = self._dispatcher()
        ctx = _ctx()
        ai = self._model()
        await handle_chat_message(client, message="มอบหมาย T-2026-0001", ctx=ctx, ai_client=ai)
        reply = await handle_chat_message(client, message="สมศักดิ์", ctx=ctx, ai_client=ai)
        assigned = _recorded(client, "assign_ticket")
        assert [a[:5] for a in assigned] == [("assign_ticket", LICENSE_ID, "t1", "technician", "m-tech-1")], client.recorded
        assert "มอบหมาย T-2026-0001 ให้ สมศักดิ์ ใจดี แล้ว" in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    @pytest.mark.asyncio
    async def test_a_question_as_the_answer_does_not_assign(self):
        """Item 13: "ใครว่างบ้าง" in answer to "who?" is a question — no assignment is written."""
        client = self._dispatcher()
        ctx = _ctx()
        ai = self._model()
        await handle_chat_message(client, message="มอบหมาย T-2026-0001", ctx=ctx, ai_client=ai)
        reply = await handle_chat_message(client, message="ใครว่างบ้าง", ctx=ctx, ai_client=ai)
        assert not _recorded(client, "assign_ticket"), client.recorded
        assert "มอบหมาย T-2026-0001 ให้" not in reply.text, reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert (pending or {}).get("entity") != "ticket_assign_target", pending


# ---------------------------------------------------------------- item 14
ASSIGN_AUTO = {"action": "assign", "entity": "ticket", "fields": {"code": "T-2026-0001", "target_name": "อัตโนมัติ"}}


class TestAutomaticAssignmentNamesThePerson:
    """Item 14 — the engine speaks in member ids; the reply names the person
    (_member_label), never "m-tech-1" or the CHN uid."""

    def _shop(self, *, members: list[dict]) -> FakeDataClient:
        client = FakeDataClient(permission_keys=["ticket.assign", "ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open",
                            "product_category": "AC", "product_name": "แอร์ 18000 BTU"}]
        client._members = members
        client._assignment_rules = [{"id": "r1", "scope": "technician", "is_active": True, "rules_json": {}}]
        client._assignment_outcome = {"member_id": "m-tech-1", "reason": "selected by least_load; load 0/5"}
        return client

    @pytest.mark.asyncio
    async def test_the_members_display_name_is_in_the_reply(self):
        """Item 14: "มอบหมาย T-2026-0001 อัตโนมัติ" → the engine picks m-tech-1 → the reply says สมศักดิ์, not the id."""
        client = self._shop(members=[{"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician",
                                      "status": "active", "display_name": "สมศักดิ์"}])
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-0001 อัตโนมัติ", ctx=_ctx(), ai_client=_model(ASSIGN_AUTO),
        )
        assert client.assignment_requests[-1]["entity_id"] == "t1", client.assignment_requests
        assert [a[:5] for a in _recorded(client, "assign_ticket")] == [("assign_ticket", LICENSE_ID, "t1", "technician", "m-tech-1")], client.recorded
        assert "มอบหมาย T-2026-0001 ให้ สมศักดิ์ แล้ว" in reply.text and "กฎมอบหมาย" in reply.text, reply.text
        assert "m-tech-1" not in reply.text and "CHN-T-000001" not in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_the_profile_name_when_the_member_row_has_none(self):
        """Item 14: a member row without display_name is named from the profile (first + last name), still not the id."""
        client = self._shop(members=[{"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active"}])
        client._profiles = {"CHN-T-000001": {"first_name": "สมศักดิ์", "last_name": "ใจดี"}}
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-0001 อัตโนมัติ", ctx=_ctx(), ai_client=_model(SUGGEST),
        )
        assert [a[:5] for a in _recorded(client, "assign_ticket")] == [("assign_ticket", LICENSE_ID, "t1", "technician", "m-tech-1")], client.recorded
        assert "มอบหมาย T-2026-0001 ให้ สมศักดิ์ ใจดี แล้ว" in reply.text, reply.text
        assert "m-tech-1" not in reply.text and "CHN-T-000001" not in reply.text, reply.text
