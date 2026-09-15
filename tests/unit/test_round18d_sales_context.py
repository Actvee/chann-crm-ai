"""Round 18d, sales context — what the conversation is on (audit verify, 15 ก.ย. 2569).

Items 15–20 of the audit: a listed deal, a created deal's customer, a name
the model would not take, a command typed over "switch?", and a picker
that must stay a picker whatever record happens to be in view. Every test
reads the REPLY, the ROWS the fake data tier saw, and the PENDING form —
a reply that sounds right over a row that should not exist (or one that
should and does not) is the failure these rounds are about.
"""
from __future__ import annotations

import datetime as dt
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
from chann_app.services.chat import _is_create_command, handle_chat_message  # noqa: E402
from chann_app.services.thai_datetime import local_today  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402

UID, OA = "CHN-S-000001", "sales"
SALES_KEYS = ["customer.read", "customer.create", "deal.read", "deal.create", "followup.create", "followup.read"]
TOMORROW = (local_today() + dt.timedelta(days=1)).isoformat()

SOMCHAI = {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}
SOMYING = {"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง", "last_name": "รักดี", "phone": "0898765432", "stage": "lead"}
OTHER_SOMCHAI = {"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมชาย", "last_name": "รักสงบ", "phone": "0822222222", "stage": "lead"}


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _model(action, entity, fields=None, missing=None):
    """What the model would return for the sentence under test."""
    return httpx.AsyncClient(transport=_ai(json.dumps(
        {"action": action, "entity": entity, "fields": fields or {}, "missing": missing or []}
    )))


def _deal(n: int, contact_id: str, stage: str = "new") -> dict:
    return {"id": f"DEAL-{n}", "deal_id": f"D-2026-{n:04d}", "stage": stage, "contact_id": contact_id, "notes": None, "products": []}


def _calls(client, name, since=0):
    return [r for r in client.recorded[since:] if r[0] == name]


class TestASingleDealListIsRemembered:
    """Item 15 — "ดีลของสมหญิง" that lists exactly one deal puts it in context."""

    def _client(self, *deals):
        return FakeDataClient(permission_keys=SALES_KEYS, customers=[dict(SOMYING)], deals=list(deals))

    @pytest.mark.asyncio
    async def test_one_deal_listed_is_the_deal_in_context(self):
        """Item 15: a one-deal list records that deal as the last entity."""
        client = self._client(_deal(1, "CUST-2"))
        reply = await handle_chat_message(
            client, message="ดีลของสมหญิง", ctx=_ctx(),
            ai_client=_model("read", "deal", {"target_name": "สมหญิง"}),
        )
        assert "D-2026-0001" in reply.text, reply.text
        assert ("set_last_entity_ref", UID, OA, "deal", "DEAL-1", "D-2026-0001") in client.recorded, client.recorded
        assert client._last_entity_ref == {"entity_type": "deal", "entity_id": "DEAL-1", "code": "D-2026-0001", "extra": None}

    @pytest.mark.asyncio
    async def test_remind_this_deal_then_binds_the_follow_up_to_it(self):
        """Item 15: "ตั้งเตือนดีลนี้พรุ่งนี้ โทรตาม" after the one-deal list has a deal."""
        client = self._client(_deal(1, "CUST-2"))
        await handle_chat_message(
            client, message="ดีลของสมหญิง", ctx=_ctx(),
            ai_client=_model("read", "deal", {"target_name": "สมหญิง"}),
        )
        since = len(client.recorded)
        reply = await handle_chat_message(
            client, message="ตั้งเตือนดีลนี้พรุ่งนี้ โทรตาม", ctx=_ctx(),
            ai_client=_model("create", "followup", {"due_date": TOMORROW, "notes": "โทรตาม"}),
        )
        created = _calls(client, "create_follow_up", since)
        assert len(created) == 1, (reply.text, client.recorded[since:])
        payload = created[0][2]
        assert payload["entity_type"] == "deal" and payload["entity_id"] == "DEAL-1", payload
        assert payload["due_date"] == TOMORROW and payload["notes"] == "โทรตาม", payload
        assert reply.text.startswith("ตั้งเตือน D-2026-0001 "), reply.text

    @pytest.mark.asyncio
    async def test_two_deals_listed_remember_neither(self):
        """Item 15 (control): a list of two deals must not put one of them in context."""
        client = self._client(_deal(1, "CUST-2"), _deal(2, "CUST-2", stage="won"))
        reply = await handle_chat_message(
            client, message="ดีลของสมหญิง", ctx=_ctx(),
            ai_client=_model("read", "deal", {"target_name": "สมหญิง"}),
        )
        assert "D-2026-0001" in reply.text and "D-2026-0002" in reply.text, reply.text
        assert not _calls(client, "set_last_entity_ref"), client.recorded
        assert getattr(client, "_last_entity_ref", None) is None
        since = len(client.recorded)
        reply = await handle_chat_message(
            client, message="ตั้งเตือนดีลนี้พรุ่งนี้ โทรตาม", ctx=_ctx(),
            ai_client=_model("create", "followup", {"due_date": TOMORROW, "notes": "โทรตาม"}),
        )
        assert not _calls(client, "create_follow_up", since), (reply.text, client.recorded[since:])
        assert "ระบุรหัสด้วยว่าเตือนเรื่องอะไร" in reply.text, reply.text


class TestTheDealsCustomerBecomesTheCustomer:
    """Item 16 — the deal's customer is now "the customer"."""

    READ_WHO = ("read", "customer", {}, ["target_name"])

    def _client(self):
        return FakeDataClient(permission_keys=SALES_KEYS, customers=[dict(SOMCHAI), dict(SOMYING)])

    @pytest.mark.asyncio
    async def test_his_phone_right_after_creating_his_deal(self):
        """Item 16: "เบอร์เขาอะไรนะ" after "สร้างดีลให้ สมชาย 5000" is สมชาย's phone."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="สร้างดีลให้ สมชาย 5000", ctx=_ctx(),
            ai_client=_model("create", "deal", {"target_name": "สมชาย", "amount": 5000}),
        )
        assert reply.text.startswith("สร้างดีล D-2026-0001 สำหรับ สมชาย ใจดี"), reply.text
        assert _calls(client, "create_deal")[0][2]["contact_id"] == "CUST-1"
        assert ("set_last_customer_ref", UID, OA, "CUST-1", "สมชาย ใจดี") in client.recorded, client.recorded
        assert client._last_customer_ref == {"customer_id": "CUST-1", "name": "สมชาย ใจดี"}
        reply = await handle_chat_message(
            client, message="เบอร์เขาอะไรนะ", ctx=_ctx(), ai_client=_model(*self.READ_WHO),
        )
        assert "สมชาย ใจดี" in reply.text and "โทร: 0812345678" in reply.text, reply.text
        assert SOMYING["phone"] not in reply.text, reply.text
        assert await client.get_pending_intent(UID, OA) is None

    @pytest.mark.asyncio
    async def test_with_nobody_in_context_the_question_is_asked(self):
        """Item 16 (control): the same question with no deal made asks which customer."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="เบอร์เขาอะไรนะ", ctx=_ctx(), ai_client=_model(*self.READ_WHO),
        )
        assert reply.text == "กรุณาระบุชื่อลูกค้า", reply.text
        assert SOMCHAI["phone"] not in reply.text and SOMYING["phone"] not in reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending == {"action": "read", "entity": "customer", "fields": {}, "missing": ["target_name"]}, pending


class TestADistrictLikeWordIsTheFirstName:
    """Item 17 — "ลูกค้าใหม่ สามเสน": the word after the head IS the name.

    The model refused the district-like word, so its reading carries no
    first_name. The branch that files the word sits in _execute_intent
    AFTER the generic missing gate, so it is reached only when the model's
    own "missing" list is empty — a reading that still lists first_name as
    missing is answered by the gate first, unchanged. These tests feed the
    reading the branch answers (probed 15 ก.ย. 2569; see the notes).
    """

    def _client(self, pending=None):
        return FakeDataClient(permission_keys=SALES_KEYS, pending_intent=pending)

    @pytest.mark.asyncio
    async def test_ลูกค้าใหม่_สามเสน_files_the_first_name(self):
        """Item 17: the reply asks for the last name and phone, not the first name."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ลูกค้าใหม่ สามเสน", ctx=_ctx(), ai_client=_model("create", "customer", {}),
        )
        assert reply.text == "กรุณาระบุนามสกุล, เบอร์โทร", reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["entity"] == "customer" and pending["action"] == "create", pending
        assert pending["fields"] == {"first_name": "สามเสน"}, pending
        assert not _calls(client, "create_customer")

    @pytest.mark.asyncio
    async def test_two_words_fill_the_last_name_too(self):
        """Item 17: "ลูกค้าใหม่ สามเสน ใจดี" carries both names and asks only for the phone."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ลูกค้าใหม่ สามเสน ใจดี", ctx=_ctx(), ai_client=_model("create", "customer", {}),
        )
        assert reply.text == "กรุณาระบุเบอร์โทร", reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["fields"] == {"first_name": "สามเสน", "last_name": "ใจดี"}, pending
        assert pending["missing"] == ["phone"], pending

    @pytest.mark.asyncio
    async def test_a_phone_beside_the_name_is_not_the_last_name(self):
        """Item 17: "ลูกค้าใหม่ สามเสน 081-234-5678" files สามเสน and the phone, and asks only for the surname."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ลูกค้าใหม่ สามเสน 081-234-5678", ctx=_ctx(),
            ai_client=_model("create", "customer", {"phone": "0812345678"}),
        )
        assert reply.text == "กรุณาระบุนามสกุล", reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["fields"] == {"first_name": "สามเสน", "phone": "0812345678"}, pending
        assert pending["missing"] == ["last_name"], pending
        assert not _calls(client, "create_customer")

    @pytest.mark.asyncio
    async def test_a_phone_alone_after_the_head_is_not_a_name(self):
        """Item 17 (control): "ลูกค้าใหม่ 0812345678" leaves the name to be asked for."""
        client = self._client()
        reply = await handle_chat_message(
            client, message="ลูกค้าใหม่ 0812345678", ctx=_ctx(), ai_client=_model("create", "customer", {}),
        )
        assert reply.text == "กรุณาระบุชื่อ, นามสกุล, เบอร์โทร", reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["fields"] == {} and pending["missing"] == ["first_name", "last_name", "phone"], pending

    def test_ลูกค้าใหม่_with_a_name_is_a_create_command_and_carrying_on_is_not(self):
        """Item 17: "ลูกค้าใหม่ มานพ" starts a customer; "ลูกค้าใหม่ต่อ" carries on."""
        assert _is_create_command("ลูกค้าใหม่ มานพ", "sales") is True
        assert _is_create_command("เพิ่มลูกค้าใหม่ มานพ", "sales") is True
        assert _is_create_command("ลูกค้าใหม่ต่อ", "sales") is False
        assert _is_create_command("ลูกค้าใหม่ก่อน", "sales") is False
        assert _is_create_command("ลูกค้าใหม่", "sales") is False

    @pytest.mark.asyncio
    async def test_a_new_customer_typed_over_a_waiting_form_is_not_that_forms_last_name(self):
        """Item 17: "ลูกค้าใหม่ มานพ" while สมชาย's form waits is a new customer — never สมชาย's surname."""
        client = self._client(pending={"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย"}, "missing": ["last_name", "phone"]})
        reply = await handle_chat_message(
            client, message="ลูกค้าใหม่ มานพ", ctx=_ctx(),
            ai_client=_model("create", "customer", {"first_name": "มานพ"}, ["last_name", "phone"]),
        )
        # _is_create_command now reads "ลูกค้าใหม่ <name>" as a command, so the
        # message is not the answer to สมชาย's form: a second customer while
        # the first is half-made is asked about (audit [5]), never filed as
        # สมชาย's surname.
        assert "กำลังเพิ่มลูกค้า สมชายอยู่" in reply.text and "จะยกเลิกแล้วเพิ่มลูกค้าแทนไหม" in reply.text, reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["entity"] == "flow_switch", pending
        assert pending["fields"]["original"]["fields"] == {"first_name": "สมชาย"}, pending
        assert pending["fields"]["command"] == "ลูกค้าใหม่ มานพ", pending
        assert pending["fields"]["original"]["fields"].get("last_name") != "มานพ", pending
        assert not _calls(client, "create_customer")


class TestTypingTheWholeNewCommandAnswersSwitch:
    """Item 18 — a complete new command typed in answer to "switch?" is the answer."""

    async def _client_with_switch_pending(self):
        client = FakeDataClient(permission_keys=["customer.create", "deal.create", "customer.read"])
        await client.set_pending_intent(
            UID, OA, action="resolve", entity="flow_switch",
            fields={"original": {"action": "create", "entity": "customer", "fields": {"first_name": "สามเสน"}, "missing": ["last_name", "phone"]},
                    "command": "สร้างดีลให้ สมหญิง"},
            missing=[],
        )
        return client

    @pytest.mark.asyncio
    async def test_a_complete_add_customer_command_is_the_answer(self):
        """Item 18: "เพิ่มลูกค้า สมหญิง รักดี 0898765432" creates her and the switch question is gone."""
        client = await self._client_with_switch_pending()
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้า สมหญิง รักดี 0898765432", ctx=_ctx(),
            ai_client=_model("create", "customer", {"first_name": "สมหญิง", "last_name": "รักดี", "phone": "0898765432"}),
        )
        created = _calls(client, "create_customer")
        assert len(created) == 1, (reply.text, client.recorded)
        assert {k: created[0][2][k] for k in ("first_name", "last_name", "phone")} == {"first_name": "สมหญิง", "last_name": "รักดี", "phone": "0898765432"}
        assert reply.text == "เพิ่มลูกค้า สมหญิง รักดี (C-2026-0001) เรียบร้อยแล้ว", reply.text
        assert "จะยกเลิก" not in reply.text
        assert await client.get_pending_intent(UID, OA) is None

    @pytest.mark.asyncio
    async def test_เพิ่มลูกค้าต่อ_still_keeps_the_form(self):
        """Item 18 (control): "เพิ่มลูกค้าต่อ" is carrying on — the form stays, nothing is created."""
        client = await self._client_with_switch_pending()
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าต่อ", ctx=_ctx(), ai_client=_model("suggest", None),
        )
        assert "จะยกเลิก" not in reply.text and "นามสกุล" in reply.text, reply.text
        pending = await client.get_pending_intent(UID, OA)
        assert pending["entity"] == "customer" and pending["fields"] == {"first_name": "สามเสน"}, pending
        assert not _calls(client, "create_customer") and not _calls(client, "create_deal")


def _two_somchai_with_a_deal_in_view():
    client = FakeDataClient(
        permission_keys=SALES_KEYS, customers=[dict(SOMCHAI), dict(OTHER_SOMCHAI)], deals=[_deal(1, "CUST-1")],
    )
    return client


async def _remember_the_deal(client):
    await client.set_last_entity_ref(UID, OA, license_id=LICENSE_ID, entity_type="deal", entity_id="DEAL-1", code="D-2026-0001")


NAME_PICK = ("create", "followup", {"target_name": "สมชาย", "due_date": TOMORROW, "due_time": "10:00"})


class TestAChoiceIsAChoiceEvenWithADealInView:
    """Item 19 — two people fit: the picker, whatever record is in context."""

    @pytest.mark.asyncio
    async def test_two_people_fit_so_the_picker_is_offered_not_the_deal(self):
        """Item 19: "นัด สมชาย พรุ่งนี้ 10 โมง" with a deal in view offers the two สมชาย and books nothing."""
        client = _two_somchai_with_a_deal_in_view()
        await _remember_the_deal(client)
        since = len(client.recorded)
        reply = await handle_chat_message(client, message="นัด สมชาย พรุ่งนี้ 10 โมง", ctx=_ctx(), ai_client=_model(*NAME_PICK))
        assert reply.quick_replies == [("สมชาย ใจดี", "1"), ("สมชาย รักสงบ", "2")], reply.quick_replies
        assert "พบลูกค้าชื่อ สมชาย หลายคน" in reply.text and "C-2026-0002" in reply.text, reply.text
        assert not _calls(client, "create_follow_up", since), client.recorded[since:]
        pending = await client.get_pending_intent(UID, OA)
        assert pending["entity"] == "customer_disambiguation", pending
        assert pending["fields"]["resume_entity"] == "followup" and pending["fields"]["resume_action"] == "create", pending
        assert [c["id"] for c in pending["fields"]["candidates"]] == ["CUST-1", "CUST-2"], pending
        # The deal is still what is in view — the picker did not touch it.
        assert client._last_entity_ref["entity_id"] == "DEAL-1"

    @pytest.mark.asyncio
    async def test_an_unknown_name_with_nothing_in_context_is_not_found(self):
        """Item 19 (control): "นัด มานพ พรุ่งนี้" with nothing in context is the not-found reply, no row."""
        client = FakeDataClient(permission_keys=SALES_KEYS, customers=[dict(SOMCHAI)])
        reply = await handle_chat_message(
            client, message="นัด มานพ พรุ่งนี้", ctx=_ctx(),
            ai_client=_model("create", "followup", {"target_name": "มานพ", "due_date": TOMORROW}),
        )
        assert reply.text == "ไม่พบลูกค้าชื่อ มานพ ในบริษัทนี้", reply.text
        assert not _calls(client, "create_follow_up"), client.recorded
        assert await client.get_pending_intent(UID, OA) is None


class TestAnsweringThePickerResumesTheRequest:
    """Item 20 — the number re-runs the request with the chosen person's code."""

    async def _after_the_picker(self):
        client = _two_somchai_with_a_deal_in_view()
        await _remember_the_deal(client)
        await handle_chat_message(client, message="นัด สมชาย พรุ่งนี้ 10 โมง", ctx=_ctx(), ai_client=_model(*NAME_PICK))
        assert (await client.get_pending_intent(UID, OA))["entity"] == "customer_disambiguation"
        return client

    @pytest.mark.asyncio
    async def test_1_books_the_first_person(self):
        """Item 20: "1" after the picker creates the follow-up for สมชาย ใจดี, not the deal."""
        client = await self._after_the_picker()
        since = len(client.recorded)
        reply = await handle_chat_message(client, message="1", ctx=_ctx(), ai_client=_model("suggest", None))
        created = _calls(client, "create_follow_up", since)
        assert len(created) == 1, (reply.text, client.recorded[since:])
        payload = created[0][2]
        assert payload["entity_type"] == "customer" and payload["entity_id"] == "CUST-1", payload
        assert payload["due_date"] == TOMORROW and payload["due_time"] == "10:00:00", payload
        assert reply.text.startswith("ตั้งเตือน C-2026-0001 "), reply.text
        assert ("clear_pending_intent", UID, OA) in client.recorded[since:]
        assert await client.get_pending_intent(UID, OA) is None

    @pytest.mark.asyncio
    async def test_2_books_the_other_person(self):
        """Item 20: "2" picks the second สมชาย — the choice, not the first row."""
        client = await self._after_the_picker()
        since = len(client.recorded)
        reply = await handle_chat_message(client, message="2", ctx=_ctx(), ai_client=_model("suggest", None))
        created = _calls(client, "create_follow_up", since)
        assert len(created) == 1, (reply.text, client.recorded[since:])
        assert created[0][2]["entity_id"] == "CUST-2", created[0][2]
        assert reply.text.startswith("ตั้งเตือน C-2026-0002 "), reply.text
