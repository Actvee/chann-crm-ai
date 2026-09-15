"""Round 18d, audit verify (15 ก.ย. 2569) — the router's gates.

Four items from the audit, each held as the specific behaviour the fix
produces and, where it is cheap, the sentence that must NOT trigger it:

  1. _deterministic_reason names the typed roads a sentence keeps —
     "issue" for a quote document, "pdf" for a report file, "bulk" for a
     pasted list — and a plain deal read stays the model's. The note
     sentence ("บันทึกว่าเขาจะมาดูสินค้าวันที่ 22") is read by the model
     and then written as a note by the code, whatever the model made of
     the date inside it.
  2. A help menu waiting is not a form: the next sentence is fresh unless
     it picks a topic.
  3. A polite prefix in front of an EDIT verb is an order, not a call for
     the guide.
  4. "กฎมอบหมายคืออะไร ใช้ยังไง" asks about the assignment rule; it is not
     sent to be translated into one.
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
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import (  # noqa: E402
    NOTE_SAVED,
    NOTE_TRIGGERS,
    POLICY_NEEDS_TEXT,
    QUOTE_ISSUE_TRIGGERS,
    REPORT_PDF_TRIGGERS,
    _deterministic_reason,
    _is_help_request,
    _note_verb_at_the_start,
    handle_chat_message,
)
from chann_data.permissions import PERMISSION_KEYS  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    # The same fixture test_phase6_chat.py applies to itself: without a key
    # the model-first read is skipped and every sentence falls to the tables.
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _counting_ai(content: str):
    """Like _ai, but also hands back the list of requests the model saw —
    so a test can say "the model was asked" or "it was never asked"."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "provider": "fireworks",
        })

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


HELP_MENU = {"action": "help", "entity": "help_menu", "fields": {}, "missing": []}
CUSTOMER_FORM = {"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย"}, "missing": ["phone"]}
BULK_LIST = "ลูกค้าใหม่\n1.สมชาย ใจดี 0812345678\n2.สมหญิง ดีใจ 0898765432"


# ---------------------------------------------------------------- item 1
class TestTheGateNamesTheTypedRoads:
    """Item 1 — _deterministic_reason: "issue", "pdf", "bulk", and the note
    sentence's road through the model."""

    def test_a_quote_document_is_issued_by_the_typed_handler(self):
        """Item 1: "ออกเอกสาร Q-2026-0001" on the sales OA is "issue"."""
        assert "ออกเอกสาร" in QUOTE_ISSUE_TRIGGERS
        assert _deterministic_reason("ออกเอกสาร Q-2026-0001", "sales", None) == "issue"
        # A reissue phrase is the same road.
        assert _deterministic_reason("ออกเอกสารใหม่ Q-2026-0001", "sales", None) == "issue"

    def test_reading_a_quote_is_not_issuing_it(self):
        """Item 1: "ดูใบเสนอราคา Q-2026-0001" carries the code but no issue verb — the model's."""
        assert _deterministic_reason("ดูใบเสนอราคา Q-2026-0001", "sales", None) is None
        # And the issue verb without a quote code is not "issue" either.
        assert _deterministic_reason("ออกเอกสาร", "sales", None) != "issue"
        # The sales-only road: the technician OA does not issue quotes.
        assert _deterministic_reason("ออกเอกสาร Q-2026-0001", "technician", None) != "issue"

    def test_a_report_pdf_is_a_file_not_the_reports_text(self):
        """Item 1: "ขอ pdf SR-2026-0001" is "pdf"."""
        assert "ขอ pdf" in REPORT_PDF_TRIGGERS
        assert _deterministic_reason("ขอ pdf SR-2026-0001", "sales", None) == "pdf"
        assert _deterministic_reason("ออกรายงาน SR-2026-0001", "sales", None) == "pdf"

    def test_reading_a_report_is_not_a_pdf(self):
        """Item 1: "ดูรายงาน SR-2026-0001" names the report without asking for a file."""
        assert _deterministic_reason("ดูรายงาน SR-2026-0001", "sales", None) is None

    def test_a_pasted_customer_list_is_still_bulk(self):
        """Item 1: the pasted list stayed "bulk" when the new reasons were added in front of it."""
        assert _deterministic_reason(BULK_LIST, "sales", None) == "bulk"

    def test_one_customer_is_not_a_list(self):
        """Item 1: a single "เพิ่มลูกค้า …" is the model's, not "bulk"."""
        assert _deterministic_reason("เพิ่มลูกค้า สมชาย ใจดี 0812345678", "sales", None) is None

    def test_a_plain_deal_read_is_none_of_them(self):
        """Item 1: "ขอดูดีล D-2026-0001" is neither issue, note nor pdf — it goes to the model."""
        why = _deterministic_reason("ขอดูดีล D-2026-0001", "sales", None)
        assert why not in ("issue", "note", "pdf"), why
        assert why is None, why

    def test_the_note_sentence_is_read_by_the_model_first(self):
        """Item 1: "บันทึกว่าเขาจะมาดูสินค้าวันที่ 22" has no deterministic reason; the code reads its opening verb."""
        sentence = "บันทึกว่าเขาจะมาดูสินค้าวันที่ 22"
        assert "บันทึกว่า" in NOTE_TRIGGERS
        assert _deterministic_reason(sentence, "sales", None) is None
        assert _note_verb_at_the_start(sentence) == "บันทึกว่า"
        # Only the OPENING verb counts: a sentence that merely contains it
        # is not a note command.
        assert _note_verb_at_the_start("ลูกค้าบอกว่าจะจดว่ามาวันที่ 22") is None
        assert _note_verb_at_the_start("นัดเขามาดูสินค้าวันที่ 22") is None

    @pytest.mark.asyncio
    async def test_the_note_sentence_is_written_as_a_note_not_an_appointment(self):
        """Item 1: the model booked an appointment for the date; the code writes the note against the record in view."""
        client = FakeDataClient(
            permission_keys=["note.create", "followup.create", "customer.read"],
            customers=[{"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
                        "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}],
        )
        await client.set_last_entity_ref(
            "CHN-S-000001", "sales", license_id=LICENSE_ID,
            entity_type="customer", entity_id="CUST-1", code="C-2026-0001",
        )
        # What the model returned on the audit: an appointment on the 22nd.
        ai, seen = _counting_ai(json.dumps({
            "action": "create", "entity": "followup",
            "fields": {"title": "มาดูสินค้า", "due_at": "2026-09-22"}, "missing": [],
        }))
        reply = await handle_chat_message(
            client, message="บันทึกว่าเขาจะมาดูสินค้าวันที่ 22", ctx=_ctx(), ai_client=ai,
        )
        assert seen, "the sentence is read by the model first"
        notes = [r for r in client.recorded if r[0] == "create_note"]
        assert len(notes) == 1, client.recorded
        assert notes[0][2] == {"entity_type": "customer", "entity_id": "CUST-1", "body": "เขาจะมาดูสินค้าวันที่ 22"}
        assert not [r for r in client.recorded if r[0] == "create_follow_up"], client.recorded
        assert reply.text == NOTE_SAVED["th"].format(code="C-2026-0001"), reply.text


# ---------------------------------------------------------------- item 2
class TestAHelpMenuIsNotAForm:
    """Item 2 — a help_menu pending holds only a topic pick or a step
    request; any other sentence is fresh."""

    def test_a_fresh_sentence_after_the_menu_is_not_pending(self):
        """Item 2: "เปิดงานให้ สมชาย" after "วิธีใช้" is not "pending"."""
        assert _deterministic_reason("เปิดงานให้ สมชาย", "sales", HELP_MENU) != "pending"
        assert _deterministic_reason("เปิดงานให้ สมชาย", "sales", HELP_MENU) is None

    def test_a_digit_picks_a_topic_from_the_menu(self):
        """Item 2: "2" after the menu IS "pending" (a topic pick)."""
        assert chat._menu_digit("2") == 2
        assert _deterministic_reason("2", "sales", HELP_MENU) == "pending"
        assert chat._menu_digit("ข้อ 2") == 2
        assert _deterministic_reason("ข้อ 2", "sales", HELP_MENU) == "pending"

    def test_a_step_request_stays_with_the_menu(self):
        """Item 2: "วิธีใช้ ข้อ 3" / "วิธีใช้ทั้งหมด" after the menu are the menu's."""
        assert chat._is_help_step_request("วิธีใช้ ข้อ 3")
        assert _deterministic_reason("วิธีใช้ ข้อ 3", "sales", HELP_MENU) == "pending"
        assert chat._is_help_step_request("วิธีใช้ทั้งหมด")
        assert _deterministic_reason("วิธีใช้ทั้งหมด", "sales", HELP_MENU) == "pending"

    def test_a_real_form_still_holds_a_fresh_sentence(self):
        """Item 2: with a customer form missing its phone, "เปิดงานให้ สมชาย" IS "pending"."""
        assert _deterministic_reason("เปิดงานให้ สมชาย", "sales", CUSTOMER_FORM) == "pending"
        # And "2" answers a form too — the exemption is the help menu's alone.
        assert _deterministic_reason("2", "sales", CUSTOMER_FORM) == "pending"

    @pytest.mark.asyncio
    async def test_after_the_guide_a_job_for_somchai_is_read_not_closed(self):
        """Item 2: "วิธีใช้" then "เปิดงานให้ สมชาย" — the model is asked; "ปิดงาน" inside the sentence closes nothing."""
        client = FakeDataClient(
            permission_keys=["ticket.create", "ticket.update", "customer.read"],
            customers=[{"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
                        "last_name": "ใจดี", "phone": "0812345678", "stage": "lead",
                        "address": "12 ถนนสุขุมวิท"}],
        )
        ai, seen = _counting_ai(json.dumps({
            "action": "create", "entity": "ticket",
            "fields": {"customer_name": "สมชาย"}, "missing": [],
        }))
        await handle_chat_message(client, message="วิธีใช้", ctx=_ctx(), ai_client=ai)
        menu = await client.get_pending_intent("CHN-S-000001", "sales")
        assert menu and menu.get("entity") == "help_menu", menu
        assert not seen, "the guide is answered without the model"
        reply = await handle_chat_message(client, message="เปิดงานให้ สมชาย", ctx=_ctx(), ai_client=ai)
        assert seen, "a fresh sentence after the menu is read by the model"
        # The pre-fix reply was the close-job road's "ไม่แน่ใจว่างานไหนครับ
        # พิมพ์เลขงานด้วย เช่น "ปิดงาน T-2026-0001"" — nothing of it may remain.
        assert "ปิดงาน" not in reply.text and "ไม่แน่ใจว่างานไหน" not in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] in ("set_ticket_status", "check_out_ticket")], client.recorded


# ---------------------------------------------------------------- item 3
class TestAPoliteEditIsNotACallForTheGuide:
    """Item 3 — _is_help_request: "ช่วย" + an edit verb is an order."""

    def test_a_polite_phone_edit_is_not_help(self):
        """Item 3: "ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ" is False."""
        assert _is_help_request("ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ", "sales") is False
        # Other edit verbs behind the same polite prefix.
        assert _is_help_request("ช่วยเปลี่ยนเบอร์ สมหญิง หน่อย", "sales") is False
        assert _is_help_request("ช่วยอัปเดตที่อยู่ สมชาย หน่อยครับ", "sales") is False

    def test_the_polite_edit_goes_to_the_model(self):
        """Item 3: the gate finds no deterministic reason for the polite edit — it is read."""
        assert _deterministic_reason("ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ", "sales", None) is None

    @pytest.mark.asyncio
    async def test_the_polite_edit_is_read_and_the_phone_is_written(self):
        """Item 3: "ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ" reaches the model and writes the phone — the guide was the pre-fix answer."""
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.update"],
            customers=[{"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
                        "last_name": "ดีใจ", "phone": "0898765432", "stage": "lead"}],
        )
        ai, seen = _counting_ai(json.dumps({
            "action": "update", "entity": "customer",
            "fields": {"target_name": "สมหญิง", "phone": "0811111111"}, "missing": [],
        }))
        reply = await handle_chat_message(
            client, message="ช่วยแก้เบอร์ สมหญิง ให้เป็น 0811111111 หน่อยครับ", ctx=_ctx(), ai_client=ai,
        )
        assert seen, "a polite edit is an order: it is read by the model, not answered with the guide"
        saved = [r for r in client.recorded if r[0] == "update_customer"]
        assert saved and saved[-1][2] == "CUST-2" and saved[-1][3].get("phone") == "0811111111", (reply.text, client.recorded)
        # Nothing of the nine-topic guide in the answer.
        assert "วิธีใช้" not in reply.text and "ข้อ 1" not in reply.text, reply.text
        assert (await client.get_pending_intent("CHN-S-000001", "sales") or {}).get("entity") != "help_menu"

    def test_a_polite_create_is_not_help_either(self):
        """Item 3: "ช่วยเพิ่มลูกค้า สมชาย หน่อย" is False (the create exemption that was already there)."""
        assert _is_help_request("ช่วยเพิ่มลูกค้า สมชาย หน่อย", "sales") is False

    def test_the_guide_itself_is_still_help(self):
        """Item 3: "วิธีใช้" is True."""
        assert _is_help_request("วิธีใช้", "sales") is True
        # "วิธีใช้" is also a rich-menu tile, so the gate names it "button";
        # either way it is the direct road, never the model's.
        assert _deterministic_reason("วิธีใช้", "sales", None) in ("help", "button")
        assert _deterministic_reason("ช่วยแนะนำการใช้หน่อยครับ", "sales", None) == "help"

    def test_a_polite_call_for_the_guide_is_still_help(self):
        """Item 3: "ช่วยแนะนำการใช้หน่อยครับ" — polite, no create or edit verb — stays True."""
        assert _is_help_request("ช่วยแนะนำการใช้หน่อยครับ", "sales") is True
        assert _is_help_request("รบกวนสอนใช้หน่อย", "sales") is True
        assert _is_help_request("ช่วยหน่อย", "sales") is True


# ---------------------------------------------------------------- item 4
class TestAQuestionAboutTheRuleIsNotARule:
    """Item 4 — _handle_assignment_policy, reached through the typed
    ASSIGN_POLICY_TRIGGERS dispatch on the sales OA: a question is
    answered with the how-to line, never sent to be translated."""

    def _owner(self) -> FakeDataClient:
        client = FakeDataClient(permission_keys=list(PERMISSION_KEYS))
        client._is_owner = True
        client._teams = [{"id": "team-1", "team_name": "AC Team"}]
        return client

    def _rule_json(self) -> str:
        # A valid rule: if the question were sent to the translator, this is
        # what would come back and be offered for confirmation.
        return json.dumps({
            "version": 1, "scope": "technician",
            "match_criteria": [{"field": "product.category", "operator": "equals",
                                "value": "AIR_CONDITIONER", "assign_to_team": "AC Team"}],
            "selection_strategy": "least_load",
            "capacity_constraint": {"max_per_day": 5, "mode": "hard_block"},
        })

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentence,trigger", [
        ("ตั้งกฎมอบหมายได้ไหม", "ตั้งกฎมอบหมาย"),
        ("กฎมอบหมายตอนนี้ตั้งไว้ว่ายังไง", "กฎมอบหมาย"),
        ("ตั้งกฎมอบหมาย ต้องพิมพ์ยังไงครับ", "ตั้งกฎมอบหมาย"),
    ])
    async def test_a_question_with_words_after_the_trigger_is_still_a_question(self, sentence, trigger):
        """Item 4: text after the trigger used to be the whole test ("not policy") — a question that carries some is held by the new guard, not translated."""
        # The precondition that makes this discriminating: the policy text
        # the handler cuts after the trigger is NOT empty, so only the
        # question test can keep it from the translator.
        assert sentence.lower().startswith(trigger) and sentence[len(trigger):].strip(" :·-"), sentence
        assert chat._looks_like_a_question(sentence) is True, sentence
        client = self._owner()
        ai, seen = _counting_ai(self._rule_json())
        reply = await handle_chat_message(client, message=sentence, ctx=_ctx(), ai_client=ai)
        assert not seen, f"{sentence!r} was sent to be translated into a rule"
        assert reply.text == POLICY_NEEDS_TEXT["th"], (sentence, reply.text)
        assert not reply.quick_replies, reply.quick_replies
        assert not [
            r for r in client.recorded if r[0] in ("upsert_assignment_rule", "set_pending_intent")
        ], client.recorded
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    @pytest.mark.asyncio
    async def test_the_question_is_not_translated_into_a_rule(self):
        """Item 4: the owner asks what the rule is — no model call, no draft held, no rule saved."""
        client = self._owner()
        ai, seen = _counting_ai(self._rule_json())
        reply = await handle_chat_message(
            client, message="กฎมอบหมายคืออะไร ใช้ยังไง", ctx=_ctx(), ai_client=ai,
        )
        assert not seen, "the question must not be sent to be translated into a rule"
        assert reply.text == POLICY_NEEDS_TEXT["th"], reply.text
        assert "บันทึกกฎ" not in reply.text and "นี่คือกฎ" not in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] == "upsert_assignment_rule"], client.recorded
        assert not [
            r for r in client.recorded
            if r[0] == "set_pending_intent" and (r[3] or {}).get("entity") == "assignment_rule"
        ], client.recorded
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    @pytest.mark.asyncio
    async def test_a_policy_statement_is_still_translated_and_offered(self):
        """Item 4: "ตั้งกฎมอบหมาย ช่างแอร์ให้ทีม AC Team วันละ 5 งาน" is a statement — translated, shown back, not yet saved."""
        client = self._owner()
        ai, seen = _counting_ai(self._rule_json())
        reply = await handle_chat_message(
            client, message="ตั้งกฎมอบหมาย ช่างแอร์ให้ทีม AC Team วันละ 5 งาน", ctx=_ctx(), ai_client=ai,
        )
        assert len(seen) == 1, "exactly one model call: the policy translation"
        assert reply.text.startswith("นี่คือกฎที่ได้"), reply.text
        assert "ยืนยันกฎ" in [q[0] for q in reply.quick_replies], reply.quick_replies
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending and pending.get("entity") == "assignment_rule", pending
        assert not [r for r in client.recorded if r[0] == "upsert_assignment_rule"], client.recorded
