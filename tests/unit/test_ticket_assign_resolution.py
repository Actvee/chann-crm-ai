"""Dispatching resolves WHICH job and TO WHOM, separately.

Owner's transcript, 10 ก.ย. 2569 — right after the shop was told
"แจ้งซ่อมใหม่ T-2026-0004":

    "มอบหมายงานช่าง"                          → ระบุเลขงานด้วย เช่น "มอบหมาย …"
    (replying to that very message) "มอบหมายให้ช่าง" → the same refusal
    "มอบหมาย ticket 0001 ให้ช่าง"              → the same refusal

Three different failures wearing one reply. The command says three things
— which job, to whom, and that it should be dispatched — and the handler
required all three to arrive in one exact shape or gave up naming only the
first. Worse, the third line HAD the number; it just was not spelled
`T-YYYY-NNNN`, which is not how anyone reads a number off a notification.

What is asserted here:

* a full code still wins outright, and an unknown one is still "not found";
* a bare running number resolves inside the licence's own year;
* the job the conversation is already about is used when none is named —
  including a reply, which `handle_reply` seeds through the same
  last-entity-ref every other command reads;
* "ให้ช่าง" with nobody named lists the technicians instead of refusing;
* the old refusal survives for the one case it was right about: nothing
  names a job and there is nothing waiting;
* and a QUESTION about assignments still assigns nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services.chat import (  # noqa: E402
    _ticket_run_numbers,
    handle_chat_message,
    handle_reply,
)
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402

KEYS = ["ticket.assign", "ticket.read", "ticket.update", "customer.read"]


def _ticket(number, tid, **over):
    row = {
        "id": tid, "ticket_number": number, "status": "open",
        "customer_name": "สมชาย", "customer_phone": "0812345678",
        "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-12", "scheduled_time": "09:00:00",
    }
    row.update(over)
    return row


def _shop(tickets=None, technicians=2):
    client = FakeDataClient(permission_keys=KEYS)
    client._tickets = tickets if tickets is not None else [_ticket("T-2026-0004", "tk-4")]
    client._members = [
        {"id": f"m-{i}", "chann_uid": f"CHN-TECH-{i}", "role": "technician", "status": "active"}
        for i in range(1, technicians + 1)
    ]
    client._profiles = {
        f"CHN-TECH-{i}": {"first_name": "ช่าง", "last_name": f"หมายเลข{i}"}
        for i in range(1, technicians + 1)
    }
    return client


def _assigned(client):
    return [r for r in client.recorded if r[0] == "assign_ticket"]


class TestWhichJob:
    async def test_a_bare_running_number_resolves_within_this_shops_year(self):
        client = _shop()
        reply = await handle_chat_message(
            client, message="มอบหมาย ticket 0004 ให้ช่าง", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0004" in reply.text
        assert reply.quick_replies, "the technicians should be offered"

    async def test_the_thai_word_for_a_job_works_the_same_way(self):
        client = _shop()
        reply = await handle_chat_message(
            client, message="มอบหมาย งาน 0004 ให้ช่าง", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0004" in reply.text

    async def test_a_full_code_still_wins_and_an_unknown_one_is_not_found(self):
        client = _shop()
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-9999 ให้ CHN-TECH-1", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-9999" in reply.text
        assert not _assigned(client)

    async def test_the_job_the_conversation_is_about_is_used_when_none_is_named(self):
        client = _shop(tickets=[
            _ticket("T-2026-0004", "tk-4"),
            _ticket("T-2026-0005", "tk-5", status="assigned", assigned_to_ref="m-2"),
        ])
        ctx = _ctx(oa="sales")
        # Looking at 0005 makes it the record in hand, even though 0004 is
        # the one waiting — the conversation outranks the queue.
        await handle_chat_message(client, message="ข้อมูลงาน T-2026-0005", ctx=ctx)
        reply = await handle_chat_message(client, message="มอบหมายให้ช่าง", ctx=ctx)
        assert "T-2026-0005" in reply.text

    async def test_replying_to_the_new_fault_message_resolves_that_job(self):
        client = _shop()
        ctx = _ctx(oa="sales")
        # What the Data tier holds for the "แจ้งซ่อมใหม่ T-2026-0004" push.
        client._mapping = {"entity_type": "service_ticket", "entity_id": "tk-4"}
        reply = await handle_reply(
            client, message_id="line-msg-1", reply_text="มอบหมายให้ช่าง", ctx=ctx,
        )
        assert "T-2026-0004" in reply.text
        assert reply.quick_replies

    async def test_one_job_waiting_is_the_one_meant_when_nothing_else_says(self):
        client = _shop()
        reply = await handle_chat_message(
            client, message="มอบหมายงานช่าง", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0004" in reply.text

    async def test_several_waiting_jobs_are_offered_rather_than_guessed(self):
        client = _shop(tickets=[
            _ticket("T-2026-0004", "tk-4"), _ticket("T-2026-0006", "tk-6"),
        ])
        reply = await handle_chat_message(
            client, message="มอบหมายงานช่าง", ctx=_ctx(oa="sales"),
        )
        payloads = [p for _, p in reply.quick_replies]
        assert any("T-2026-0004" in p for p in payloads)
        assert any("T-2026-0006" in p for p in payloads)
        assert not _assigned(client)

    async def test_nothing_names_a_job_and_nothing_waits_keeps_the_old_refusal(self):
        client = _shop(tickets=[
            _ticket("T-2026-0004", "tk-4", status="completed", assigned_to_ref="m-1"),
        ])
        reply = await handle_chat_message(
            client, message="มอบหมายงานช่าง", ctx=_ctx(oa="sales"),
        )
        assert "ระบุเลขงาน" in reply.text
        assert not _assigned(client)

    def test_a_phone_number_is_not_a_job_number(self):
        assert _ticket_run_numbers("มอบหมาย 0812345678 ให้ช่าง", "มอบหมาย") == []

    def test_an_identifier_is_not_split_into_a_job_number(self):
        # "CHN-TECH-1" ends in a digit run; reading "-1" as a job turned
        # the target into a member nobody is.
        assert _ticket_run_numbers("มอบหมาย T-2026-0004 ให้ CHN-TECH-1", "มอบหมาย") == []


class TestToWhom:
    async def test_ให้ช่าง_with_nobody_named_lists_the_technicians(self):
        client = _shop(technicians=2)
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ช่าง", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0004" in reply.text
        assert [label for label, _ in reply.quick_replies] == ["ช่าง หมายเลข1", "ช่าง หมายเลข2"]
        assert not _assigned(client)

    async def test_a_shop_with_one_technician_is_offered_that_one_by_name(self):
        client = _shop(technicians=1)
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ช่าง", ctx=_ctx(oa="sales"),
        )
        assert [label for label, _ in reply.quick_replies] == ["ช่าง หมายเลข1"]
        assert not _assigned(client), "offering is not assigning — it is still confirmed by a tap"

    async def test_the_offered_button_actually_dispatches(self):
        client = _shop(technicians=2)
        ctx = _ctx(oa="sales")
        offered = await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ช่าง", ctx=ctx,
        )
        tap = offered.quick_replies[0][1]
        await handle_chat_message(client, message=tap, ctx=ctx)
        calls = _assigned(client)
        assert len(calls) == 1
        assert calls[0][2] == "tk-4"
        assert calls[0][3] == "technician"
        assert calls[0][4] == "m-1"

    async def test_a_shop_with_no_technician_is_told_so_not_refused_for_naming_nobody(self):
        client = _shop(technicians=0)
        reply = await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ช่าง", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0004" in reply.text
        assert "ช่าง" in reply.text
        assert not _assigned(client)

    async def test_a_named_person_is_still_dispatched_to_directly(self):
        client = _shop(technicians=2)
        await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ CHN-TECH-2", ctx=_ctx(oa="sales"),
        )
        calls = _assigned(client)
        assert len(calls) == 1 and calls[0][4] == "m-2"

    async def test_a_team_name_is_not_mistaken_for_the_word_technician(self):
        client = _shop(technicians=2)
        client._teams = [{"id": "team-1", "team_name": "งานหลวง"}]
        await handle_chat_message(
            client, message="มอบหมาย T-2026-0004 ให้ทีม งานหลวง", ctx=_ctx(oa="sales"),
        )
        calls = _assigned(client)
        assert len(calls) == 1
        assert calls[0][3] == "technician_team" and calls[0][4] == "team-1"


class TestAQuestionIsNotACommand:
    async def test_asking_who_has_been_given_what_assigns_nothing(self):
        client = _shop()
        await handle_chat_message(
            client, message="มอบหมายงานให้ใครไปแล้วบ้าง", ctx=_ctx(oa="sales"),
        )
        assert not _assigned(client)

    async def test_asking_whether_a_job_is_assigned_assigns_nothing(self):
        client = _shop()
        await handle_chat_message(
            client, message="T-2026-0004 มอบหมายให้ใครหรือยัง", ctx=_ctx(oa="sales"),
        )
        assert not _assigned(client)
