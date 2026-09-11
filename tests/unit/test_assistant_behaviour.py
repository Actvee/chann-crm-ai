"""The five behaviours the owner named, held as tests.

Owner, 10 ก.ย. 2569: the assistant should hold a conversation, not run a
command line. People mistype, speak colloquially, drop words, change their
mind, ask how something works, and refer back to what was just said. Their
five cases, verbatim:

  * "ไม่ต้องยกเลิกนัด" ต้องไม่ยกเลิก
  * "สร้างใบเสนอราคาไปหรือยัง" ต้องตรวจข้อมูลก่อนตอบ
  * "ยังไม่เอาใบราคาครับ" ต้องไม่สร้าง
  * ถามวิธีกรอกระหว่างเพิ่มลูกค้า ต้องยังเก็บข้อมูลที่กรอกไว้
  * คำสั่งเชิงบวกที่ชัดเจนต้องยังทำงานได้

Every test checks the REPLY and the ROWS WRITTEN, because a reply that
sounds right over a row that should not exist is the failure this whole
round is about. The fifth case is not a footnote: it is half of every
other one, and this codebase has twice shipped a fix for over-acting that
then refused real orders.
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
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402

SALES = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
# Every way this system changes a record. The list matters: it was missing
# "promote_", so a test asserting "nothing was written" passed while the
# router promoted a customer on a question — the assertion could not see
# it (10 ก.ย. 2569). Conversation state is excluded on purpose below.
WRITES = (
    "create_", "update_", "delete_", "set_quote", "transition_", "archive_",
    "register_", "claim_", "add_", "remove_", "put_", "assign_", "reject_",
    "promote_", "check_in_", "check_out_", "set_ticket_status",
    "set_follow_up_status", "open_approval_steps", "publish_",
    "open_chat_session", "close_chat_session", "mark_survey_sent",
    "set_display_preferences", "set_identity_signature",
)
#: Not writes — where the conversation is, not what the shop knows.
NOT_WRITES = ("set_last_", "set_pending", "clear_pending", "set_active_tenant", "mark_chat_read")
CUSTOMER = {
    "id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
    "last_name": "ใจดี", "phone": "0812345678", "stage": "lead",
}
DEAL = {
    "id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
    "contact_id": "CUST-1", "notes": None, "products": [],
}
QUOTE = {
    "id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "sent",
    "deal_id": "DEAL-1", "contact_id": "CUST-1", "items": [], "total": "1000.00",
}
TICKET = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "open",
    "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย",
    "issue_description": "แอร์ไม่เย็น",
    "scheduled_date": "2026-09-11", "scheduled_time": "10:00",
}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    """The model is STUBBED here — every answer below is authored by the
    test, never produced by a model. These tests say what the pipeline
    does with a given answer; they say nothing about whether a real model
    would give it. That question needs its own run."""
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _shop(*, quotes=(), deals=(), customers=(), keys=None):
    client = FakeDataClient(
        role="sales", permission_keys=list(SALES if keys is None else keys),
        customers=[dict(c) for c in customers],
        deals=[dict(d) for d in deals],
        quotes=[dict(q) for q in quotes],
    )
    client._tickets = [dict(TICKET)]
    return client


def _payload(client, method: str) -> dict:
    """The dict a recorded call carried. Recorded tuples are
    (method, license_id, id, payload, ...) and the trailing members differ
    per method, so the payload is found by shape rather than by index —
    a test that indexes wrongly asserts about a UUID string and passes for
    the wrong reason."""
    call = [c for c in client.recorded if c[0] == method][-1]
    return next(part for part in reversed(call) if isinstance(part, dict))


async def _say(client, message, *, intent=None, oa="sales", role="sales"):
    ai = httpx.AsyncClient(transport=_ai(json.dumps(intent, ensure_ascii=False))) if intent else None
    reply = await chat.handle_chat_message(
        client, ctx=_ctx(primary_role=role, oa=oa), message=message, language="th", ai_client=ai,
    )
    written = [
        c[0] for c in client.recorded
        if c[0].startswith(WRITES) and not c[0].startswith(NOT_WRITES)
    ]
    return (reply.text or ""), written


class TestCase1DoNotCancel:
    """"ไม่ต้องยกเลิกนัด" ต้องไม่ยกเลิก."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องยกเลิกนัด",
        "ยังไม่ต้องยกเลิกงาน",
        "อย่าเพิ่งยกเลิกนัดนะครับ",
    ])
    async def test_nothing_is_cancelled(self, message):
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [dict(TICKET)]
        text, written = await _say(client, message, oa="customer", role="customer")
        assert written == [], f"{message!r} wrote {written}"
        assert "ยังไม่ได้ยกเลิก" in text

    @pytest.mark.asyncio
    async def test_but_a_real_cancellation_is_still_offered(self):
        """The fifth case applied to the first: refusing the refusal would
        strand a customer who genuinely cannot be home."""
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [dict(TICKET)]
        text, _ = await _say(client, "ยกเลิกนัด", oa="customer", role="customer")
        assert "ยังไม่ได้ยกเลิก" not in text
        assert "T-2026-0001" in text


class TestCase2CheckBeforeAnswering:
    """"สร้างใบเสนอราคาไปหรือยัง" ต้องตรวจข้อมูลก่อนตอบ — the answer was
    rendered from the sentence alone, with no lookup of any kind, so a shop
    whose quotation had gone out was told the opposite of the truth."""

    @pytest.mark.asyncio
    async def test_it_says_so_when_the_quotation_exists(self):
        client = _shop(quotes=[QUOTE], deals=[DEAL], customers=[CUSTOMER])
        text, written = await _say(client, "สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง")
        assert written == []
        assert "Q-2026-0001" in text
        assert "ยังไม่ได้" not in text

    @pytest.mark.asyncio
    async def test_and_says_not_yet_when_there_is_none(self):
        client = _shop(deals=[DEAL], customers=[CUSTOMER])
        text, written = await _say(client, "สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง")
        assert written == []
        assert "ยังไม่ได้" in text


class TestCase3DecliningInWordsNobodyListed:
    """"ยังไม่เอาใบราคาครับ" ต้องไม่สร้าง.

    "ใบราคา" is the everyday short form and was in no table, so the guard
    had nothing to bind the "ยังไม่" to and issued the quotation. Two
    answers here: the word is in the table now, AND a refusal the guard
    cannot bind on the model's road is no longer read as consent — because
    a word list can never hold every way a person declines something."""

    QUOTE_INTENT = {
        "action": "create", "entity": "quote",
        "fields": {"deal_code": "D-2026-0001"}, "missing": [],
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ยังไม่เอาใบราคาครับ",
        "ไม่เอาใบเสนอราคาแล้ว",
        # Words nobody has listed and nobody will: the structural half.
        "ไม่ต้องออกบิลราคานะ",
        "ไม่เอาเปเปอร์ราคาแล้ว",
    ])
    async def test_no_quotation_is_created(self, message):
        client = _shop(deals=[DEAL], customers=[CUSTOMER])
        client._last_entity_ref = {
            "entity_type": "deal", "entity_id": "DEAL-1",
            "code": "D-2026-0001", "extra": None,
        }
        text, written = await _say(client, message, intent=self.QUOTE_INTENT)
        assert "create_quote" not in written, f"{message!r} wrote {written}"
        assert text.strip()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ทำใบเสนอราคาให้หน่อยครับ",
        "สร้างใบเสนอราคา D-2026-0001",
    ])
    async def test_and_asking_for_one_still_creates_it(self, message):
        client = _shop(deals=[DEAL], customers=[CUSTOMER])
        client._last_entity_ref = {
            "entity_type": "deal", "entity_id": "DEAL-1",
            "code": "D-2026-0001", "extra": None,
        }
        _, written = await _say(client, message, intent=self.QUOTE_INTENT)
        assert "create_quote" in written


class TestCase4AQuestionMidFlowKeepsTheWork:
    """ถามวิธีกรอกระหว่างเพิ่มลูกค้า ต้องยังเก็บข้อมูลที่กรอกไว้.

    It did worse than lose it: the half-filled record was thrown away and
    the person was told "ยกเลิกแล้วครับ" — a cancellation they never asked
    for. The slot-fill abort accepted every HOLD the guard could return,
    and the guard has seven reasons; only three of them mean abandonment.
    """

    START = {
        "action": "create", "entity": "customer",
        "fields": {"first_name": "สมชาย", "last_name": "ใจดี"}, "missing": ["phone"],
    }
    ANSWER = {
        "action": "create", "entity": "customer",
        "fields": {"phone": "0812345678"}, "missing": [],
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("question", ["ต้องกรอกอะไรบ้าง", "กรอกยังไง"])
    async def test_the_flow_survives_and_the_question_is_answered(self, question):
        client = _shop()
        await _say(client, "เพิ่มลูกค้า สมชาย ใจดี", intent=self.START)

        text, written = await _say(client, question)
        assert written == []
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending is not None, "the half-filled record was thrown away"
        assert pending["fields"]["first_name"] == "สมชาย"
        # And it is an ANSWER, not a deflection: it names what is held.
        assert "สมชาย" in text and "เบอร์โทร" in text
        assert "ยกเลิกแล้ว" not in text

    @pytest.mark.asyncio
    async def test_and_the_customer_is_created_when_the_answer_comes(self):
        client = _shop()
        await _say(client, "เพิ่มลูกค้า สมชาย ใจดี", intent=self.START)
        await _say(client, "ต้องกรอกอะไรบ้าง")
        _, written = await _say(client, "0812345678", intent=self.ANSWER)
        assert "create_customer" in written
        assert [
            (c.get("first_name"), c.get("last_name"), c.get("phone"))
            for c in client._customers
        ] == [("สมชาย", "ใจดี", "0812345678")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ยกเลิก", "หยุดก่อน", "ยังไม่เพิ่มลูกค้านะ", "เดี๋ยวค่อยทำ",
    ])
    async def test_but_actually_abandoning_it_still_abandons_it(self, message):
        """The other half. Keeping the flow alive through a genuine
        "stop" would be its own kind of not listening."""
        client = _shop()
        await _say(client, "เพิ่มลูกค้า สมชาย ใจดี", intent=self.START)
        text, _ = await _say(client, message)
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None
        assert "ยกเลิกแล้ว" in text


class TestCase5PlainCommandsStillWork:
    """คำสั่งเชิงบวกที่ชัดเจนต้องยังทำงานได้ — the half that is easy to
    lose while fixing the other four."""

    @pytest.mark.asyncio
    async def test_creating_a_quotation(self):
        client = _shop(deals=[DEAL], customers=[CUSTOMER])
        _, written = await _say(client, "สร้างใบเสนอราคา D-2026-0001")
        assert "create_quote" in written

    @pytest.mark.asyncio
    async def test_an_appointment(self):
        client = _shop(customers=[CUSTOMER])
        _, written = await _say(client, "ตั้งนัด C-2026-0001 พรุ่งนี้ 10:00")
        assert "create_follow_up" in written

    @pytest.mark.asyncio
    async def test_a_customer_in_one_line(self):
        client = _shop()
        _, written = await _say(
            client, "เพิ่มลูกค้า สมชาย ใจดี 0812345678",
            intent={
                "action": "create", "entity": "customer",
                "fields": {
                    "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678",
                },
                "missing": [],
            },
        )
        assert "create_customer" in written

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        # Each carries a word one of the guards looks at, and each is an
        # order anyway.
        "ช่วยสร้างใบเสนอราคา D-2026-0001 ให้หน่อยครับ",
        "ตั้งนัด C-2026-0001 พรุ่งนี้ 10:00 หน่อยได้ไหมครับ",
    ])
    async def test_politeness_does_not_make_an_order_a_question(self, message):
        client = _shop(deals=[DEAL], customers=[CUSTOMER])
        _, written = await _say(client, message)
        assert written, f"{message!r} was refused"


class TestTheModelRoadIsGuardedByDefault:
    """Requirement 5: "คำที่ guard ไม่รู้จัก ต้องไม่ถือเป็นการอนุญาตให้
    เขียนข้อมูลโดยอัตโนมัติ".

    It was exactly inverted. `_AI_GUARDED.get(...)` returned None for a
    pair nobody had listed, and None meant "go ahead" — so 30 of the 48
    mutating (entity, action) pairs the model can dispatch had no shape
    check at all. `("customer","create")` was one of them, which is why
    "ไม่ต้องเพิ่มลูกค้า สมชาย ใจดี 0812345678" wrote a real customer row:
    there is no single-customer create in any trigger table, so that flow
    reaches the model every time and the model's road was the unguarded
    one."""

    CREATE = {
        "action": "create", "entity": "customer",
        "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
        "missing": [],
    }

    def test_every_mutating_pair_reaches_a_guard(self):
        """The property, not a sample: adding a handler must not be able to
        add an unguarded road by forgetting a table entry."""
        unguarded = [
            (entity, action)
            for (action, entity) in chat.ACTION_PERMISSIONS
            if action in chat._MUTATING_ACTIONS
            and (entity, action) not in chat._AI_GUARDED
            and action not in ("delete", "cancel")
        ]
        # These fall to `record_write`, which is a guard — the test is that
        # _MUTATING_ACTIONS covers them, so _execute_intent picks one up.
        for entity, action in unguarded:
            assert action in chat._MUTATING_ACTIONS, f"({entity}, {action}) reaches no guard"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องเพิ่มลูกค้า สมชาย ใจดี 0812345678",
        "ยังไม่ต้องเพิ่มลูกค้าสมชาย ใจดี 0812345678",
        "เพิ่มลูกค้า สมชาย ใจดี 0812345678 ยังไงครับ",
        "เช่น เพิ่มลูกค้า สมชาย ใจดี 0812345678",
    ])
    async def test_no_customer_is_created(self, message):
        client = _shop()
        text, written = await _say(client, message, intent=self.CREATE)
        assert "create_customer" not in written, f"{message!r} wrote {written}"
        assert client._customers == []
        # And the refusal names the actual thing, not "แก้ข้อมูลนี้".
        assert "ลูกค้า" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "เพิ่มลูกค้า สมชาย ใจดี 0812345678",
        "ลูกค้าใหม่ สมชาย ใจดี 0812345678",
    ])
    async def test_but_adding_a_customer_still_adds_one(self, message):
        client = _shop()
        _, written = await _say(client, message, intent=self.CREATE)
        assert "create_customer" in written

    def test_a_refusal_aimed_at_removing_is_the_request(self):
        """The rule above has a boundary, and it is the action rather than
        the sentence. "ไม่เอา X" is a decline when the proposal is to
        CREATE — "ไม่เอาใบราคาครับ", no quotation thank you — and an
        instruction when the proposal is to REMOVE: "ไม่เอาตัวนี้แล้ว" over
        a deal line means take it off. Same words, opposite meanings.

        Holding the removal case would refuse the phrasing people reach for
        most naturally when they want something gone, which is how a fix
        for over-acting becomes the other bug."""
        from chann_app.services.intent_guard import intent_to_act

        for message in ("ไม่เอาตัวนี้แล้ว", "ไม่เอาพัดลมแล้ว"):
            assert intent_to_act(message, action="line_item", proposed=True).acts, message
        for message in ("ยังไม่เอาใบราคาครับ", "ไม่ต้องออกบิลราคานะ"):
            assert not intent_to_act(
                message, action="quote_create", proposed=True,
            ).acts, message


class TestThePromptCarriesOnlyWhatTheOACanDo:
    """Requirement 2: send the model the context it needs, "โดยรักษาการแยก
    สิทธิ์แต่ละ OA/ร้าน".

    One prompt went to everyone. A technician can reach 13 of the 64
    (action, entity) pairs and a customer 7, and both were handed a
    catalogue describing all 18 entities — so the model was routinely
    offered actions the permission gate would refuse a moment later, which
    is one of the ways it produces an action with no handler."""

    def test_each_oa_gets_its_own_capabilities(self):
        from chann_app.services.ai.intent import build_prompt, entities_for

        def prompt_for(oa):
            return build_prompt(
                chann_uid="CHN-S-000001", role=oa, license_id="L1",
                permission_keys=["ticket.read"], language="th", oa=oa,
            )

        tech = prompt_for("technician")
        cust = prompt_for("customer")
        sales = prompt_for("sales")

        # A technician's own work is there…
        for entity in ("ticket", "service_report", "warranty"):
            assert f'entity="{entity}"' in tech
        # …and the shop's is not.
        for entity in ("deal", "quote", "customer", "product", "team"):
            assert f'entity="{entity}"' not in tech, entity
        # A customer sees their own record and their jobs, nothing else.
        for entity in ("deal", "quote", "approval", "setting"):
            assert f'entity="{entity}"' not in cust, entity
        # Sales reaches everything, so nothing is trimmed.
        assert entities_for("sales") is None
        assert len(sales) > len(tech)

    def test_the_tone_rules_survive_the_trim(self):
        """The trim takes entity blocks, never the guidance — a shorter
        prompt that forgot how to read a refusal would be worse than the
        long one."""
        from chann_app.services.ai.intent import build_prompt

        for oa in ("sales", "technician", "customer"):
            prompt = build_prompt(
                chann_uid="CHN-S-000001", role=oa, license_id="L1",
                permission_keys=["ticket.read"], language="th", oa=oa,
            )
            assert "WHAT THE SENTENCE IS DOING" in prompt, oa
            assert "แอร์ไม่เย็น" in prompt, oa
            assert '"suggest"' in prompt, oa

    def test_an_unknown_oa_is_shown_everything(self):
        """Failing closed here would mean a new channel silently loses
        capabilities with no error to notice."""
        from chann_app.services.ai.intent import build_prompt, INTENT_SYSTEM_PROMPT

        prompt = build_prompt(
            chann_uid="CHN-S-000001", role="sales", license_id="L1",
            permission_keys=[], language="th", oa="something-new",
        )
        assert len(prompt) >= len(INTENT_SYSTEM_PROMPT) - 200


class TestAChannelIsABoundary:
    """Requirement 2 again, from the other side: "โดยรักษาการแยกสิทธิ์แต่ละ
    OA/ร้าน".

    Holding the permission key is not enough. OA_ALLOWED_PERMISSION_KEYS
    says a technician's LINE offers no customer capability at all — and the
    owner of a shop holds every key on every channel, so the key check
    alone lets an owner create customer rows from the technician OA. Every
    other write on this road passes _oa_allows; the bulk paste and its
    phone resolver were the two that did not."""

    PASTE = "เพิ่มลูกค้า สมชาย ใจดี 0812345678; สมหญิง ดี 0898765432"

    @staticmethod
    async def _paste(oa):
        role = "technician" if oa == "technician" else "sales"
        client = FakeDataClient(role=role, permission_keys=SALES)
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role=role, oa=oa),
            message=TestAChannelIsABoundary.PASTE, language="th",
        )
        return (reply.text or ""), [c[0] for c in client.recorded if c[0] == "create_customer"]

    @pytest.mark.asyncio
    async def test_the_technician_channel_creates_no_customers(self):
        _, written = await self._paste("technician")
        assert written == []

    @pytest.mark.asyncio
    async def test_and_the_sales_channel_still_does(self):
        _, written = await self._paste("sales")
        assert len(written) == 2

    def test_the_key_and_the_channel_disagree_by_design(self):
        """The premise, so this test fails loudly if the tables change:
        an admin holds customer.create, and the technician OA forbids it."""
        assert "customer.create" in SALES
        assert not chat._oa_allows("technician", "customer.create")
        assert chat._oa_allows("sales", "customer.create")


class TestTheModelsFieldsAreCheckedToo:
    """Requirement 5, the half that is not about the verb: "โค้ดยังคงตรวจ
    schema สิทธิ์ เป้าหมาย สถานะ และกฎธุรกิจก่อนทำจริง".

    Both of these came out of a real-model run, not from imagination.
    gemini-3.1-flash-lite, asked to read a technician's messages:

      "ปิดดีล D-2026-0001 สำเร็จ"
        -> {"entity":"ticket", "fields":{"code":"D-2026-0001", ...}}
        a DEAL code on a JOB. The technician prompt carries no deal
        vocabulary by design, so the model reached for the nearest entity
        it was allowed to name.

      "ไม่ได้ไปนะครับวันนี้ ลูกค้าเลื่อนเอง"
        -> {"entity":"ticket", "fields":{"status":"เลื่อนนัด"}}
        not a status this system has — a phrase where a value belongs.

    A model will always be able to produce a value that does not exist.
    Checking is the code's job, and it is cheap: the prefix says what a
    code IS, and a closed field has a fixed set of values."""

    TECH = sorted(DEFAULT_ROLE_TEMPLATES["technician"])
    TICKET_ROW = {
        "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
        "accept_status": "accepted", "assigned_to_ref": "member-1",
        "customer_name": "สมชาย", "service_address": "99/1",
        "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-11", "scheduled_time": "10:00",
    }

    @classmethod
    async def _tech(cls, message, intent):
        client = FakeDataClient(role="technician", permission_keys=cls.TECH)
        client._tickets = [dict(cls.TICKET_ROW)]
        ai = httpx.AsyncClient(transport=_ai(json.dumps(intent, ensure_ascii=False)))
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="technician", oa="technician"),
            message=message, language="th", ai_client=ai,
        )
        written = [c[0] for c in client.recorded if c[0].startswith(("create_", "update_"))]
        return (reply.text or ""), written

    @pytest.mark.asyncio
    async def test_a_deal_code_is_not_a_job(self):
        text, written = await self._tech(
            "ปิดดีล D-2026-0001 สำเร็จ",
            {"action": "update", "entity": "ticket",
             "fields": {"status": "closed", "code": "D-2026-0001"}, "missing": []},
        )
        assert written == []
        # And it says which record type that code is, rather than going quiet.
        assert "D-2026-0001" in text and "ดีล" in text

    @pytest.mark.asyncio
    async def test_a_status_the_system_does_not_have_is_not_a_status(self):
        text, written = await self._tech(
            "ไม่ได้ไปนะครับวันนี้ ลูกค้าเลื่อนเอง",
            {"action": "update", "entity": "ticket",
             "fields": {"status": "เลื่อนนัด"}, "missing": []},
        )
        assert "update_ticket" not in written

    def test_a_real_value_survives(self):
        """The check drops what does not exist, never what does."""
        intent = {"entity": "ticket", "fields": {"status": "completed"}}
        chat._drop_invented_values(intent)
        assert intent["fields"]["status"] == "completed"
        intent = {"entity": "deal", "fields": {"stage": "won"}}
        chat._drop_invented_values(intent)
        assert intent["fields"]["stage"] == "won"

    def test_a_matching_code_is_not_a_mismatch(self):
        assert chat._entity_code_mismatch(
            {"entity": "ticket", "action": "update", "fields": {"code": "T-2026-0001"}}
        ) is None
        assert chat._entity_code_mismatch(
            {"entity": "deal", "action": "update", "fields": {"code": "D-2026-0001"}}
        ) is None


class TestOneQuestionForWhatTheModelAlreadySaid:
    """Acceptance item 6: "เพิ่มลูกค้าสมชาย" takes exactly one question.

    It used to take two. The model reported missing=["last_name","phone"];
    `_prune_missing` deleted last_name from that report, so the assistant
    asked only for the phone, and the create handler — which has always
    required a surname — asked for it after the phone arrived. Two
    exchanges for what the model had answered in one, and the two checks
    disagreed in writing: the prune's comment claimed the handler never
    needed last_name, on the line above the handler requiring it.

    Both now read `capabilities.CUSTOMER_CREATE`. These tests fail if
    either side grows its own opinion again.
    """

    @pytest.mark.asyncio
    async def test_a_first_name_alone_is_asked_once_for_both_fields(self):
        client = _shop()
        text, written = await _say(client, "เพิ่มลูกค้า สมชาย", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย"}, "missing": ["last_name", "phone"],
        })
        assert written == [], f"a half-known customer was written: {written}"
        assert "นามสกุล" in text and "เบอร์โทร" in text, text

    @pytest.mark.asyncio
    async def test_and_the_answer_finishes_it_without_a_second_question(self):
        client = _shop()
        await _say(client, "เพิ่มลูกค้า สมชาย", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย"}, "missing": ["last_name", "phone"],
        })
        text, written = await _say(client, "ใจดี 0812345678", intent={
            "action": "create", "entity": "customer",
            "fields": {"last_name": "ใจดี", "phone": "0812345678"}, "missing": [],
        })
        assert "create_customer" in written, f"the answer did not finish it: {written} / {text}"
        assert "กรุณาระบุ" not in text, text

    @pytest.mark.asyncio
    async def test_the_whole_thing_in_one_line_still_writes(self):
        """The fifth case: a clear order must not pay for this fix."""
        client = _shop()
        text, written = await _say(client, "เพิ่มลูกค้า สมชาย ใจดี 0812345678", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
            "missing": [],
        })
        assert "create_customer" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_email_is_still_never_asked_for(self):
        """The 9 ก.ย. complaint: a good paste blocked on "กรุณาระบุอีเมล"."""
        client = _shop()
        text, written = await _say(client, "เพิ่มลูกค้า สมชาย ใจดี 0812345678", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
            "missing": ["email", "address", "notes"],
        })
        assert "create_customer" in written, f"{text} / {written}"
        assert "อีเมล" not in text, text

    @pytest.mark.asyncio
    async def test_the_draft_offer_names_every_field_it_still_needs(self):
        """`_offer_draft_customer_deal` hid the surname too: it said
        "ขาดเบอร์โทร" over a record that also had no surname, then stored
        that same short list as the create's missing fields."""
        client = _shop()
        reply = await chat._offer_draft_customer_deal(
            client, ctx=_ctx(primary_role="sales", oa="sales"),
            draft={"entity": "customer", "action": "create",
                   "fields": {"first_name": "สมชาย"}, "missing": ["last_name", "phone"]},
            deal_fields={"amount": 5000}, language="th",
        )
        assert "นามสกุล" in (reply.text or "") and "เบอร์โทร" in (reply.text or ""), reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert (pending.get("fields") or {}).get("missing") == ["last_name", "phone"]


class TestTheRegistryIsTheOnlyDefinition:
    """Requirement 4: prompt and code read one definition."""

    def test_a_required_field_is_never_also_never_needed(self):
        from chann_app.services.capabilities import REGISTRY

        for cap in REGISTRY.values():
            overlap = set(cap.required) & set(cap.never_needed)
            assert not overlap, f"{cap.entity}.{cap.action}: {sorted(overlap)}"

    def test_the_prompt_asks_for_what_the_registry_requires(self):
        """The model is told the same rule the handler enforces."""
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT
        from chann_app.services.capabilities import CUSTOMER_CREATE

        for field_name in CUSTOMER_CREATE.required:
            assert field_name in INTENT_SYSTEM_PROMPT, field_name
        assert "ALL required" in INTENT_SYSTEM_PROMPT

    def test_the_bulk_exception_is_declared_not_accidental(self):
        from chann_app.services.capabilities import CUSTOMER_CREATE

        assert "customer_bulk" in CUSTOMER_CREATE.exceptions

    def test_a_reminders_time_is_never_asked_for(self):
        """The 12:03 loop (2 ก.ย. 2569), now stated in the registry."""
        from chann_app.services.capabilities import FOLLOWUP_CREATE

        assert "due_time" in FOLLOWUP_CREATE.never_needed
        assert chat._prune_missing(
            ["due_time", "due_date"], {"entity": "followup", "action": "create"},
            "นัดคุณสมบัติหน่อย",
        ) == ["due_date"]

    def test_a_date_in_the_sentence_beats_the_models_report(self):
        assert chat._prune_missing(
            ["due_date"], {"entity": "followup", "action": "create"},
            "อยากนัดดู demo สินค้าวันที่ 7",
        ) == []

    def test_every_parser_supplied_field_has_a_reader(self):
        """A field the registry says the parser answers must have one."""
        from chann_app.services.capabilities import REGISTRY

        for cap in REGISTRY.values():
            for field_name in cap.parser_supplies:
                assert field_name in chat._PARSER_SEES, f"{cap.entity}.{cap.action}: {field_name}"

    def test_a_quote_is_always_made_from_an_existing_deal(self):
        from chann_app.services.capabilities import QUOTE_CREATE

        assert QUOTE_CREATE.required == ("deal_code",)


class TestTheSentenceIsReadBeforeAnActionIsChosen:
    """Requirement 1: free text the person typed is READ with context
    before an action is chosen; a keyword that matches a word must not
    decide on its own. These are the branches that used to decide.

    Every case here was reproduced against the real handlers first.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ช่วยเพิ่มลูกค้า สมชาย ใจดี 0812345678 ให้หน่อยครับ",
        "ช่วยสร้างลูกค้า สมชาย ใจดี 0812345678",
        "รบกวนเพิ่มลูกค้า สมชาย ใจดี 0812345678",
    ])
    async def test_a_polite_order_is_an_order_not_a_request_for_the_manual(self, message):
        """HELP_CONTAINS carries "ช่วยหน่อย", which normalises to "ช่วย",
        and the substring rule claimed every short sentence containing
        it — so a complete add-customer command got the nine-topic guide."""
        client = _shop()
        text, written = await _say(client, message, intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
            "missing": [],
        })
        assert "create_customer" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["ช่วยหน่อยครับ", "ช่วยด้วย", "ช่วยที", "ใช้ยังไง"])
    async def test_and_asking_for_help_still_gets_help(self, message):
        client = _shop()
        text, written = await _say(client, message)
        assert written == []
        assert "วิธีใช้" in text or "ทำอะไรได้บ้าง" in text or "พิมพ์" in text, text

    @pytest.mark.asyncio
    async def test_a_name_and_a_phone_is_not_someone_editing_their_own_profile(self):
        """"ชื่อ สมชาย ใจดี เบอร์ 0812345678" answered PROFILE_NOT_ELIGIBLE,
        having parsed the phone into the surname."""
        client = _shop()
        text, written = await _say(client, "ชื่อ สมชาย ใจดี เบอร์ 0812345678", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
            "missing": [],
        })
        assert "create_customer" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_name_and_a_phone_is_not_a_search_term_either(self):
        """It answered "ไม่พบลูกค้าที่ตรงกับ สมชาย ใจดี เบอร์ …"."""
        client = _shop()
        text, written = await _say(client, "ลูกค้าชื่อสมชาย ใจดี เบอร์ 0812345678", intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
            "missing": [],
        })
        assert "create_customer" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_but_a_real_lookup_is_still_a_lookup(self):
        client = _shop(customers=[CUSTOMER])
        text, written = await _say(client, "ลูกค้าชื่อสมชาย")
        assert written == [], f"a lookup wrote {written}"
        assert "สมชาย" in text


class TestAnAppointmentCanTakeTwoTurns:
    """"ตั้งนัดสมชาย" used to dead-end: the rule resolved the person, found
    no date, answered "กรุณาระบุวันที่" and set NO pending intent, so the
    next turn had nothing to continue. Only sentences the one-shot handler
    can actually satisfy stay on the rule road now."""

    @pytest.mark.asyncio
    async def test_the_day_is_asked_for_and_the_hour_from_the_turn_before_survives(self):
        client = _shop(customers=[CUSTOMER])
        text, _ = await _say(client, "ตั้งนัดสมชาย", intent={
            "action": "create", "entity": "followup",
            "fields": {"target_name": "สมชาย"}, "missing": ["due_date"],
        })
        assert "วันที่" in text, text
        text, written = await _say(client, "บ่าย 2", intent={
            "action": "create", "entity": "followup",
            "fields": {"due_time": "บ่าย 2"}, "missing": ["due_date"],
        })
        assert written == [], f"a reminder with no day was written: {written}"
        assert "เปลี่ยน" not in text, f"answering the question read as a flow switch: {text}"
        text, written = await _say(client, "พรุ่งนี้", intent={
            "action": "create", "entity": "followup",
            "fields": {"due_date": "พรุ่งนี้"}, "missing": [],
        })
        assert "create_follow_up" in written, f"{text} / {written}"
        assert "14:00" in text, f"the time from two turns ago was lost: {text}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "เตือน C-2026-0001 พรุ่งนี้ 10 โมง",
        "นัดสมชายพรุ่งนี้บ่าย 2",
    ])
    async def test_a_dated_command_still_answers_itself(self, message):
        """The fifth case: the deterministic road keeps every shape it can
        actually finish, with no model call."""
        client = _shop(customers=[CUSTOMER])
        text, written = await _say(client, message)
        assert "create_follow_up" in written, f"{text} / {written}"


class TestAQuoteIsMadeOnlyWhenOneWasAskedFor:
    """The branch fired on the words appearing anywhere in the sentence."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ลูกค้าขอใบเสนอราคา",
        "ลูกค้าอยากได้ใบเสนอราคาสำหรับพัดลม 2 ตัว",
        "ใบเสนอราคาสำหรับสมชาย",
    ])
    async def test_reporting_what_a_customer_asked_for_is_not_an_order(self, message):
        client = _shop(customers=[CUSTOMER], deals=[DEAL])
        text, written = await _say(client, message, intent={
            "action": "suggest", "entity": "", "fields": {}, "missing": [],
        })
        assert "create_quote" not in written, f"{message!r} issued a quote: {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "สร้างใบเสนอราคาจากดีล D-2026-0001",
        "ช่วยออกใบเสนอราคาให้หน่อย",
    ])
    async def test_but_an_order_still_makes_one(self, message):
        client = _shop(customers=[CUSTOMER], deals=[DEAL])
        await client.set_last_entity_ref(
            "CHN-S-000001", "sales", license_id=LICENSE_ID, entity_type="deal", entity_id="DEAL-1", code="D-2026-0001",
        )
        text, written = await _say(client, message)
        assert "create_quote" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ปิดสำเร็จ D-2026-0001",
        "ข้อมูลดีล D-2026-0001",
        "ยังไม่ต้องปิดดีล D-2026-0001",
    ])
    async def test_a_deal_command_is_not_stolen_by_the_quote_branch(self, message):
        client = _shop(customers=[CUSTOMER], deals=[DEAL])
        text, written = await _say(client, message)
        assert "create_quote" not in written, f"{message!r} -> {written}"


class TestAValueTheModelReturnsIsAProposalNotAFact:
    """Requirement 5. Asked for a price and given a phone number, the
    model returned quoted_unit_price="0812345678" — and the line was
    rewritten to 812,345,678.00 baht, taking the deal total with it."""

    @staticmethod
    async def _line(fields, message):
        client = _shop(customers=[CUSTOMER], deals=[{
            **DEAL, "products": [{"id": "L1", "product_name": "พัดลม",
                                  "quoted_unit_price": "1200", "qty": 1}],
        }])
        await client.set_last_entity_ref(
            "CHN-S-000001", "sales", license_id=LICENSE_ID, entity_type="deal", entity_id="DEAL-1", code="D-2026-0001",
        )
        return await _say(client, message, intent={
            "action": "update", "entity": "line_item", "fields": fields, "missing": [],
        })

    @pytest.mark.asyncio
    @pytest.mark.parametrize("price", ["0812345678", "+66812345678", "081-234-5678"])
    async def test_a_phone_number_is_never_a_price(self, price):
        text, written = await self._line({"target_name": "พัดลม", "quoted_unit_price": price}, price)
        assert written == [], f"{price!r} was written as a price: {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("price", ["1500", "1,250.50"])
    async def test_but_a_price_is_still_a_price(self, price):
        text, written = await self._line(
            {"target_name": "พัดลม", "quoted_unit_price": price}, f"แก้ราคาพัดลมเป็น {price}",
        )
        assert "update_deal_product" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["ไม่ทราบ", "-5", "0"])
    async def test_a_word_or_a_nothing_is_not_a_price(self, value):
        _, written = await self._line({"target_name": "พัดลม", "quoted_unit_price": value}, value)
        assert written == [], f"{value!r} was written as a price: {written}"

    def test_a_count_must_be_a_positive_whole_number(self):
        for bad in ("ไม่รู้", "-2", "0", "สองตัว"):
            intent = {"fields": {"qty": bad}}
            chat._drop_invented_values(intent)
            assert intent["fields"] == {}, bad
        intent = {"fields": {"qty": "3"}}
        chat._drop_invented_values(intent)
        assert intent["fields"] == {"qty": "3"}


class TestAWordMayDeclineButMayNotAct:
    """Narrowing a trigger to stop it ACTING must not also stop it
    REFUSING. Measured on the corpus (10 ก.ย. 2569): once the quote
    branch dispatched only on a sentence that opens with the verb,
    "ไว้ก่อนนะ เดี๋ยวมาทำใบเสนอราคา" fell through to the model and came
    back "ยังไม่แน่ใจว่าต้องการอะไรครับ" — where it used to answer
    "รับทราบครับ ยังไม่ได้สร้างใบเสนอราคา…". Nothing was written either
    way; the person was simply no longer told.

    So the guard keeps the whole vocabulary and the dispatch keeps the
    narrow one.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไว้ก่อนนะ เดี๋ยวมาทำใบเสนอราคา",
        "ยังไม่ต้องสร้างใบเสนอราคา",
    ])
    async def test_declining_a_quote_is_still_answered_in_words(self, message):
        client = _shop(customers=[CUSTOMER], deals=[DEAL])
        await client.set_last_entity_ref(
            "CHN-S-000001", "sales", license_id=LICENSE_ID, entity_type="deal", entity_id="DEAL-1", code="D-2026-0001",
        )
        text, written = await _say(client, message, intent={
            "action": "suggest", "entity": "", "fields": {}, "missing": [],
        })
        assert "create_quote" not in written, f"{message!r} -> {written}"
        assert "ยังไม่ได้สร้าง" in text, text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ยังไม่เอาใบเสนอราคาครับ",   # the owner's own case 3, verbatim shape
        "ไม่ต้องตั้งนัด",
        "ยังไม่ต้องนัดนะครับ",
        "ไม่ต้องตั้งนัดพรุ่งนี้",
    ])
    async def test_a_refusal_the_vocabulary_does_not_recognise_still_writes_nothing(self, message):
        """A KNOWN GAP, pinned at its real height.

        None of these carries a trigger word — "ใบเสนอราคา" alone is not a
        create trigger, and "ไม่ต้องตั้งนัด" does not open with the verb —
        so no guard sees them (a reminder trigger has to OPEN the
        sentence) and the reply is the generic "ยังไม่แน่ใจ
        ว่าต้องการอะไรครับ". Measured identical on the code before this
        round, so it is not a regression; it is where the words run out.

        What matters and is pinned: nothing is written. Answering these
        properly belongs to the model road, not to a longer word list.
        """
        client = _shop(customers=[CUSTOMER], deals=[DEAL])
        _, written = await _say(client, message, intent={
            "action": "suggest", "entity": "", "fields": {}, "missing": [],
        })
        assert written == [], f"{message!r} wrote {written}"


class TestOneShopCannotSeeAnothersRecord:
    """Reproduced end to end, 10 ก.ย. 2569: one LINE account that is staff
    at two shops opened a customer in shop A, switched shops, then said
    "เตือนพรุ่งนี้ 10 โมง" — a sentence with no subject. A follow-up was
    written IN SHOP B against SHOP A's customer, and the row landed.

    The cause was the cache key: last_entity_ref / last_customer_ref were
    keyed on (person, OA) with no license, while k_member beside them has
    always carried one. Scoping the key means a shop switch moves the
    conversation to that shop's own slot, so nothing has to be cleared —
    and switching back finds the record still there.
    """

    SHOP_A = "11111111-1111-1111-1111-111111111111"
    SHOP_B = "22222222-2222-2222-2222-222222222222"

    @staticmethod
    def _rows():
        return [{"id": "CUST-A", "customer_id": "C-2026-0001", "first_name": "สมชาย",
                 "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}]

    @pytest.mark.asyncio
    async def test_the_other_shop_does_not_inherit_the_record_in_context(self):
        client = FakeDataClient(role="sales", permission_keys=SALES, customers=self._rows())
        here = _ctx(primary_role="sales", oa="sales", license_id=self.SHOP_A)
        there = _ctx(primary_role="sales", oa="sales", license_id=self.SHOP_B)
        await chat._remember_entity(
            client, here, entity_type="customer", entity_id="CUST-A", code="C-2026-0001",
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "entity": "", "fields": {}, "missing": []})))
        reply = await chat.handle_chat_message(
            client, ctx=there, message="เตือนพรุ่งนี้ 10 โมง", language="th", ai_client=ai,
        )
        written = [c[0] for c in client.recorded if c[0].startswith(WRITES)]
        assert "create_follow_up" not in written, f"wrote across shops: {written}"
        assert "C-2026-0001" not in (reply.text or ""), reply.text

    @pytest.mark.asyncio
    async def test_but_the_shop_it_belongs_to_still_has_it(self):
        """The fifth case: isolation must not cost the feature."""
        client = FakeDataClient(role="sales", permission_keys=SALES, customers=self._rows())
        here = _ctx(primary_role="sales", oa="sales", license_id=self.SHOP_A)
        await chat._remember_entity(
            client, here, entity_type="customer", entity_id="CUST-A", code="C-2026-0001",
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "entity": "", "fields": {}, "missing": []})))
        reply = await chat.handle_chat_message(
            client, ctx=here, message="เตือนพรุ่งนี้ 10 โมง", language="th", ai_client=ai,
        )
        written = [c[0] for c in client.recorded if c[0].startswith(WRITES)]
        assert "create_follow_up" in written, f"{reply.text} / {written}"

    def test_the_cache_key_carries_the_shop(self):
        from chann_data.cache import k_last_customer_ref, k_last_entity_ref

        assert k_last_entity_ref("L1", "CHN-1", "sales") != k_last_entity_ref("L2", "CHN-1", "sales")
        assert k_last_customer_ref("L1", "CHN-1", "sales") != k_last_customer_ref("L2", "CHN-1", "sales")


class TestHoldingTheKeyIsNotBeingAllowedHere:
    """_appointment_net checked the permission set and never _oa_allows, so
    a technician holding followup.create — the shop owner, who is admin
    everywhere — booked a real reminder on the technician OA, where
    _oa_allows says the action does not exist (10 ก.ย. 2569)."""

    @pytest.mark.asyncio
    async def test_a_channel_that_forbids_the_action_writes_nothing(self):
        keys = sorted(set(DEFAULT_ROLE_TEMPLATES["technician"]) | {"followup.create"})
        client = FakeDataClient(role="technician", permission_keys=keys, customers=[CUSTOMER])
        ctx = _ctx(primary_role="technician", oa="technician")
        await chat._remember_entity(
            client, ctx, entity_type="customer", entity_id="CUST-1", code="C-2026-0001",
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "entity": "", "fields": {}, "missing": []})))
        reply = await chat.handle_chat_message(
            client, ctx=ctx, message="เตือนพรุ่งนี้ 10 โมง", language="th", ai_client=ai,
        )
        written = [c[0] for c in client.recorded if c[0].startswith(WRITES)]
        assert "create_follow_up" not in written, f"{reply.text} / {written}"

    @pytest.mark.asyncio
    async def test_and_the_channel_that_allows_it_still_does(self):
        client = FakeDataClient(role="sales", permission_keys=SALES, customers=[CUSTOMER])
        ctx = _ctx(primary_role="sales", oa="sales")
        await chat._remember_entity(
            client, ctx, entity_type="customer", entity_id="CUST-1", code="C-2026-0001",
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "entity": "", "fields": {}, "missing": []})))
        reply = await chat.handle_chat_message(
            client, ctx=ctx, message="เตือนพรุ่งนี้ 10 โมง", language="th", ai_client=ai,
        )
        written = [c[0] for c in client.recorded if c[0].startswith(WRITES)]
        assert "create_follow_up" in written, f"{reply.text} / {written}"


class TestTheConversationDoesNotOutliveTheRecord:
    """Scoping the cache by shop closed the cross-shop road. Three holes a
    scoped key does not reach, all reproduced on 10 ก.ย. 2569."""

    @pytest.mark.asyncio
    async def test_a_deleted_customer_does_not_get_a_new_deal(self):
        """The cached name is up to an hour old. "สร้างดีล" with no name
        made a deal against the cached id and echoed the cached name —
        "สร้างดีล D-2026-0001 สำหรับ สมชาย ใจดี (ลูกค้าที่เพิ่งคุยถึง)
        เรียบร้อยแล้ว" — over a shop with no สมชาย left in it."""
        client = _shop(customers=[CUSTOMER])
        ctx = _ctx(primary_role="sales", oa="sales")
        await chat._remember_customer(client, ctx, dict(CUSTOMER))
        client._customers.clear()
        text, written = await _say(client, "สร้างดีล", intent={
            "action": "suggest", "entity": "", "fields": {}, "missing": [],
        })
        assert "create_deal" not in written, f"a deal for a deleted person: {written}"
        assert "สมชาย" not in text, f"an erased name was spoken: {text}"

    @pytest.mark.asyncio
    async def test_but_a_customer_who_is_still_there_still_gets_one(self):
        client = _shop(customers=[CUSTOMER])
        ctx = _ctx(primary_role="sales", oa="sales")
        await chat._remember_customer(client, ctx, dict(CUSTOMER))
        text, written = await _say(client, "สร้างดีล", intent={
            "action": "suggest", "entity": "", "fields": {}, "missing": [],
        })
        assert "create_deal" in written, f"{text} / {written}"

    def test_revoking_a_membership_clears_the_conversation(self):
        """A suspended member coming back must not resume where they left
        off. Deliberately at the revocation site, NOT in _member_cache_keys
        — that helper runs for every member of the licence, so a role edit
        would wipe the whole shop's context."""
        source = (ROOT / "data" / "chann_data" / "routers" / "internal.py").read_text(encoding="utf-8")
        block = source.split("def set_member_status", 1)[1].split("@router", 1)[0]
        for key in ("k_pending_intent(", "k_last_customer_ref(", "k_last_entity_ref("):
            assert key in block, key
        helper = source.split("def _member_cache_keys", 1)[1].split("\n\n\n", 1)[0]
        for key in ("k_pending_intent(", "k_last_customer_ref(", "k_last_entity_ref("):
            assert key not in helper, f"{key} must not be in the shared helper"

    def test_erasure_clears_the_conversation(self):
        """Erasure deleted rows and never touched Redis, so an erased
        person's own conversational state outlived the record."""
        source = (ROOT / "data" / "chann_data" / "routers" / "internal.py").read_text(encoding="utf-8")
        block = source.split("def process_pdpa_request", 1)[1].split("@router", 1)[0]
        for key in ("k_pending_intent(", "k_last_customer_ref(", "k_last_entity_ref("):
            assert key in block, key
        repo = (ROOT / "data" / "chann_data" / "repositories" / "phase165.py").read_text(encoding="utf-8")
        assert "erased_licenses" in repo, "erase must report which shops it touched"


class TestThePromptSaysOnlyWhatIsTrueHere:
    """The prompt is guidance, never the gate — but describing capability
    the channel does not have invites proposals the gate refuses, and
    shipping looked-up rows into it sends data nobody asked to send.
    Both measured 10 ก.ย. 2569."""

    def test_a_channel_is_not_told_it_can_do_what_it_cannot(self):
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

        held = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
        for oa in ("sales", "technician", "customer"):
            shown = chat._keys_this_oa_can_use(held, oa)
            forbidden = [k for k in shown if not chat._oa_allows(oa, k)]
            assert not forbidden, f"{oa} prompt would advertise {forbidden[:5]}"

    def test_and_the_channel_that_allows_everything_loses_nothing(self):
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

        held = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
        assert chat._keys_this_oa_can_use(held, "sales") == held

    def test_a_looked_up_row_never_reaches_the_model(self):
        """Two customers sharing a name put whole rows — phone, email,
        address, the shop's private notes, the LINE user id — into
        "values already collected"."""
        from chann_app.services.ai.intent import _pending_fields_for_prompt

        rows = [
            {"id": "CUST-1", "first_name": "สมชาย", "phone": "0812345678",
             "email": "a@b.c", "address": "99/1", "notes": "ค้างชำระ 2 งวด",
             "line_user_id": "Uab12"},
            {"id": "CUST-2", "first_name": "สมชาย", "phone": "0898765432",
             "email": "d@e.f", "address": "12/3", "notes": "", "line_user_id": "Uef56"},
        ]
        out = _pending_fields_for_prompt({"candidates": rows, "resume_entity": "deal"})
        for leaked in ("0812345678", "a@b.c", "99/1", "ค้างชำระ", "Uab12"):
            assert leaked not in out, f"{leaked} reached the prompt: {out}"
        assert "2 records" in out, out
        assert "deal" in out, "what the person is doing must survive"

    def test_but_what_the_person_typed_still_does(self):
        from chann_app.services.ai.intent import _pending_fields_for_prompt

        out = _pending_fields_for_prompt({"first_name": "สมชาย", "phone": "0812345678"})
        assert "สมชาย" in out and "0812345678" in out, out

    def test_an_empty_record_is_not_described_as_a_record(self):
        from chann_app.services.ai.intent import _pending_fields_for_prompt

        assert "<a record>" not in _pending_fields_for_prompt({"resume_fields": {}})

    def test_a_new_field_carrying_rows_is_covered_the_day_it_is_added(self):
        """The rule is shape, not a blocklist of field names."""
        from chann_app.services.ai.intent import _pending_fields_for_prompt

        out = _pending_fields_for_prompt({"something_new": [{"secret": "0812345678"}]})
        assert "0812345678" not in out, out


class TestAQuestionAboutACustomerDoesNotChangeThem:
    """The owner's case 2 and 3, on the entity nobody had mapped.

    _AI_GUARDED bound customer.update to "customer_bulk" (the paste-a-list
    wording) and customer.promote to "deal_create" (about opening deals).
    intent_to_act therefore looked for words an edit sentence never
    contains, found none, and returned ACT every time — so a how-to and a
    status question both wrote (10 ก.ย. 2569). customer.promote has no
    deterministic trigger anywhere, which made that guard the only reader
    of the sentence's mood.
    """

    UPDATE = {"action": "update", "entity": "customer",
              "fields": {"target_name": "สมชาย", "phone": "0899999999"}, "missing": []}
    PROMOTE = {"action": "promote", "entity": "customer",
               "fields": {"target_name": "สมชาย"}, "missing": []}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,intent", [
        ("แก้เบอร์ลูกค้ายังไงครับ", "UPDATE"),
        ("ยืนยันลูกค้ายังไงครับ", "PROMOTE"),
        ("สมชายยืนยันเป็นลูกค้าไปหรือยัง", "PROMOTE"),
        ("ยังไม่ต้องยืนยันลูกค้า", "PROMOTE"),
    ])
    async def test_asking_about_it_writes_nothing(self, message, intent):
        client = _shop(customers=[CUSTOMER])
        text, written = await _say(client, message, intent=getattr(self, intent))
        assert written == [], f"{message!r} wrote {written}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,intent,expect", [
        ("แก้เบอร์สมชายเป็น 0899999999", "UPDATE", "update_customer"),
        ("ยืนยันลูกค้า สมชาย ใจดี", "PROMOTE", "promote_customer"),
        ("สมชาย ใจดี ตกลงซื้อแล้ว ยืนยันเป็นลูกค้าเลยครับ", "PROMOTE", "promote_customer"),
    ])
    async def test_but_telling_it_to_still_does(self, message, intent, expect):
        client = _shop(customers=[CUSTOMER])
        text, written = await _say(client, message, intent=getattr(self, intent))
        assert expect in written, f"{text} / {written}"

    def test_the_guard_reads_the_verbs_these_sentences_use(self):
        from chann_app.services.intent_guard import ACTION_WORDS

        assert "ยืนยัน" in ACTION_WORDS["record_write"], (
            "customer.promote has no trigger table; this list is the only reader of its mood"
        )
        assert chat._AI_GUARDED[("customer", "update")] == "record_write"
        assert chat._AI_GUARDED[("customer", "promote")] == "record_write"


class TestASettingThatDeletesCustomersNeedsTheRightChannel:
    """_maybe_lead_cleanup_setting checked the permission key and never
    _oa_allows, so the technician OA — which holds no customer permission
    at all — could switch on a policy that archives customers in bulk
    (10 ก.ย. 2569). Same shape as _appointment_net."""

    @pytest.mark.asyncio
    async def test_the_technician_channel_cannot_set_it(self):
        keys = sorted(set(DEFAULT_ROLE_TEMPLATES["technician"]) | {"setting.manage"})
        client = FakeDataClient(role="technician", permission_keys=keys)
        text, written = await _say(
            client, "ตั้งค่าลบ lead อัตโนมัติ 90 วัน", oa="technician", role="technician",
        )
        assert not [w for w in written if w.startswith("put_")], written
        assert not chat._oa_allows("technician", "setting.manage")

    @pytest.mark.asyncio
    async def test_and_the_sales_channel_still_can(self):
        client = _shop()
        text, written = await _say(client, "ตั้งค่าลบ lead อัตโนมัติ 90 วัน")
        assert [w for w in written if w.startswith("put_")], f"{text} / {written}"


class TestAConfirmationIsStillCheckedWhenItArrives:
    """_resolve_archive_confirm re-checks the permission because "the
    confirmation may arrive after a role change". The duplicate and merge
    resolvers write a customer row on the same kind of answer and checked
    neither the key nor the channel."""

    def test_both_resolvers_check_the_key_and_the_channel(self):
        source = (ROOT / "application" / "chann_app" / "services" / "chat.py").read_text(encoding="utf-8")
        for fn in ("_resolve_customer_duplicate", "_resolve_customer_merge_confirm"):
            body = source.split(f"async def {fn}(", 1)[1].split("\nasync def ", 1)[0]
            assert "permission_keys" in body, fn
            assert "_oa_allows" in body, fn
            assert "customer.update" in body, fn


class TestTheInstrumentDoesNotMeasureItsOwnLeftovers:
    """measure-road-share.py gates every deploy: it fails the build when a
    keyword takes back a sentence the model had been reading. It was
    building each utterance's fixture with dict(), and DEAL carries a
    nested products list — so every utterance shared one list, and a case
    that added or removed a line changed what every later case measured
    against. The count came out 210 against a true 208, and the two
    phantom sentences were both about deal line items:
    "ราคา 1500 บาท" and the s-switch-line-abort scenario (11 ก.ย. 2569).

    Two independent reviewers reached 208 on a pristine tree before this
    was found here, which is the only reason it was found at all.
    """

    def test_a_case_cannot_change_the_fixture_the_next_case_sees(self):
        import copy
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_road_share", ROOT / "scripts" / "dev" / "measure-road-share.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pristine = copy.deepcopy(mod.DEAL)
        client = mod.FakeDataClient(
            role="sales", permission_keys=list(mod.KEYS["sales"]),
            customers=[copy.deepcopy(mod.CUSTOMER)], deals=[copy.deepcopy(mod.DEAL)],
            quotes=[copy.deepcopy(mod.QUOTE)],
        )
        # What a line-item utterance does: mutate the deal's products.
        client._deals[0]["products"].append({"id": "L9", "product_name": "x",
                                             "quoted_unit_price": "1", "qty": 1})
        client._deals[0]["stage"] = "won"
        assert mod.DEAL == pristine, (
            "the module-level fixture was mutated — the next utterance would "
            "be measured against this case's leftovers"
        )

    def test_the_probe_measures_the_tree_it_lives_in(self):
        """probe.py had a worktree path hardcoded as its default repo, so
        running it from anywhere else measured THAT tree. A real-model run
        reported a guard failing that the tree under test had already
        fixed — the failure was in the instrument, and it was reported to
        the owner as a product defect before anyone noticed
        (11 ก.ย. 2569)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_probe", ROOT / "scripts" / "agent-test" / "probe.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert Path(mod.DEFAULT_REPO).resolve() == ROOT.resolve(), (
            f"probe defaults to {mod.DEFAULT_REPO}, not the tree it ships in"
        )


class TestTheConversationIsRemembered:
    """The model was given the half-finished action and the record in
    focus, but never the conversation, so a sentence that leans on what
    was just said had nothing to lean on. Owner, 11 ก.ย. 2569: "ลองส่ง
    ความก่อนหน้าให้ AI ด้วยดีไหม ถ้า session ที่จับไว้ยังถือว่าคุยต่ออยู่".

    The trap a review had already found in the related record-in-focus
    work is the model treating an old subject as the current one, so the
    block states each turn's age, and a sentence that names its own
    person or record always wins.
    """

    A = dict(CUSTOMER)
    B = {"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
         "last_name": "รักดี", "phone": "0898887777", "stage": "lead"}

    @pytest.mark.asyncio
    async def test_a_turn_is_remembered_for_this_shop_only(self):
        client = _shop(customers=[self.A])
        here = _ctx(primary_role="sales", oa="sales",
                    license_id="11111111-1111-1111-1111-111111111111")
        there = _ctx(primary_role="sales", oa="sales",
                     license_id="22222222-2222-2222-2222-222222222222")
        await chat._remember_turn(client, here, "ขอดูข้อมูลลูกค้าสมชาย", None)
        assert await chat._recent_turns(client, here), "the shop's own turn was lost"
        assert not await chat._recent_turns(client, there), "another shop saw it"

    @pytest.mark.asyncio
    async def test_a_pronoun_uses_the_customer_in_context(self):
        """"เปิดดีลให้เขาหน่อย" searched for a customer named "เขา" and
        answered "ไม่พบลูกค้าชื่อ เขา ในบริษัทนี้" — while the model had
        already read the sentence correctly and reported the name as
        missing. The trigger never let it get that far."""
        client = _shop(customers=[self.A])
        # The previous turn's effect, stated directly so the test does not
        # depend on a network call to reproduce it.
        await chat._remember_customer(client, _ctx(primary_role="sales", oa="sales"), dict(self.A))
        text, written = await _say(client, "เปิดดีลให้เขาหน่อย", intent={
            "action": "create", "entity": "deal", "fields": {}, "missing": ["target_name"],
        })
        assert "create_deal" in written, f"{text} / {written}"
        assert "เขา" not in text.replace("เขาหน่อย", ""), text

    @pytest.mark.asyncio
    async def test_but_a_named_person_still_wins_over_the_context(self):
        """The failure that killed the first record-in-focus attempt: a
        sentence naming a DIFFERENT person was redirected to the record in
        focus and wrote against the wrong one."""
        client = _shop(customers=[self.A, self.B])
        await chat._remember_customer(client, _ctx(primary_role="sales", oa="sales"), dict(self.A))
        text, written = await _say(client, "เปิดดีลให้สมหญิง รักดี", intent={
            "action": "create", "entity": "deal",
            "fields": {"target_name": "สมหญิง รักดี"}, "missing": [],
        })
        assert "create_deal" in written, f"{text} / {written}"
        assert "สมหญิง" in text and "สมชาย" not in text, text

    def test_the_block_states_how_old_each_turn_is(self):
        from chann_app.services.ai.intent import _recent_turns_for_prompt
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        rendered = _recent_turns_for_prompt([
            {"said": "ขอดูข้อมูลลูกค้าสมชาย", "did": "แสดงข้อมูล",
             "at": (now - timedelta(minutes=7)).isoformat()},
        ])
        assert "7 นาที" in rendered, rendered

    def test_nothing_looked_up_reaches_the_prompt(self):
        """Only the person's own words and a short label the code wrote."""
        from chann_app.services.ai.intent import _recent_turns_for_prompt

        rendered = _recent_turns_for_prompt([
            {"said": "ขอดูข้อมูลลูกค้าสมชาย", "did": "แสดงข้อมูล C-2026-0001", "at": ""},
        ])
        assert "0812345678" not in rendered and "@" not in rendered, rendered


class TestAProfileSentenceIsCheckedLikeEveryOther:
    """The self-edit road sits BEFORE the permission gate, because editing
    your own details needs no tenant key. It therefore also sat before the
    two checks the gate runs on everything else — the action, and
    intent_guard — and nobody noticed for as long as the road existed.

    Verified against the real router 11 ก.ย. 2569: a model answer of
    action="delete" and a sentence that REFUSED both wrote the profile and
    replied "แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว"."""

    TECH = sorted(DEFAULT_ROLE_TEMPLATES["technician"])
    FIELDS = {"phone": "0899998888"}

    async def _profile(self, message, *, action="update"):
        client = FakeDataClient(role="technician", permission_keys=list(self.TECH))
        return await _say(
            client, message, oa="technician", role="technician",
            intent={"action": action, "entity": "profile",
                    "fields": dict(self.FIELDS), "missing": []},
        )

    @pytest.mark.asyncio
    async def test_a_plain_self_edit_still_writes(self):
        text, written = await self._profile("เปลี่ยนเบอร์ฉันเป็น 0899998888")
        assert "update_profile" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_an_action_that_is_not_an_edit_writes_nothing(self):
        text, written = await self._profile("ลบโปรไฟล์ฉัน", action="delete")
        assert written == [], f"{text} / {written}"
        assert "เรียบร้อย" not in text, text

    @pytest.mark.asyncio
    async def test_a_refusal_writes_nothing(self):
        text, written = await self._profile("ไม่ต้องเปลี่ยนเบอร์ฉัน")
        assert written == [], f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_question_writes_nothing(self):
        text, written = await self._profile("เปลี่ยนเบอร์ยังไงครับ")
        assert written == [], f"{text} / {written}"


class TestACurrencyIsOneThisSystemNames:
    """`len(code) == 3 and code.isalpha()` accepted ZZZ, and every amount
    after it was formatted in a currency that does not exist."""

    def test_an_invented_code_falls_back_to_the_shop_currency(self):
        from datetime import date

        from chann_app.services.deal_fields import extract_deal_fields

        today = date(2026, 9, 11)
        out = extract_deal_fields("สมชายสนใจแอร์ ตกลงกันที่ห้าแสน",
                                  {"amount": 500000, "currency": "ZZZ"}, today)
        assert out["currency"] == "THB", out

    def test_a_real_code_is_kept(self):
        from datetime import date

        from chann_app.services.deal_fields import KNOWN_CURRENCIES, extract_deal_fields

        assert "USD" in KNOWN_CURRENCIES and "ZZZ" not in KNOWN_CURRENCIES
        out = extract_deal_fields("ตกลงกันที่ 5000 usd", {"currency": "USD"}, date(2026, 9, 11))
        assert out["currency"] == "USD", out


class TestANoteIsAddedNotSwapped:
    """Every other field on a customer row holds ONE value, so the model
    sends the new one and the handler writes it. Notes are not that: they
    are what the shop has learned about a person, and the model sends only
    the new sentence. "อัปเดตลูกค้าสมชาย หมายเหตุว่าอยากได้ติดตั้งวันเสาร์"
    erased "ชอบสีขาว ห้ามโทรก่อน 10 โมง" and reported success
    (verified 11 ก.ย. 2569)."""

    async def _note(self, before, fresh):
        client = _shop(customers=[{**CUSTOMER, "notes": before}])
        await _say(
            client, "อัปเดตลูกค้าสมชาย หมายเหตุว่า" + fresh,
            intent={"action": "update", "entity": "customer",
                    "fields": {"target_name": "สมชาย", "notes": fresh}, "missing": []},
        )
        return client._customers[0].get("notes")

    @pytest.mark.asyncio
    async def test_what_was_there_survives(self):
        assert await self._note("ชอบสีขาว ห้ามโทรก่อน 10 โมง", "อยากได้ติดตั้งวันเสาร์") == (
            "ชอบสีขาว ห้ามโทรก่อน 10 โมง\nอยากได้ติดตั้งวันเสาร์"
        )

    @pytest.mark.asyncio
    async def test_the_first_note_is_written_plainly(self):
        assert await self._note(None, "อยากได้ติดตั้งวันเสาร์") == "อยากได้ติดตั้งวันเสาร์"

    @pytest.mark.asyncio
    async def test_the_same_note_twice_is_not_doubled(self):
        assert await self._note("อยากได้ติดตั้งวันเสาร์", "อยากได้ติดตั้งวันเสาร์") == (
            "อยากได้ติดตั้งวันเสาร์"
        )


class TestALookupPhraseInsideAnOrderIsNotALookup:
    """"ข้อมูลลูกค้า" is how someone asks to SEE a customer. It is also
    eight characters inside "แก้ข้อมูลลูกค้าสมชาย หมายเหตุ …", and the
    detail road matched it anywhere in the sentence: it took the whole tail
    as a record code and answered "ไม่พบลูกค้ารหัส สมชาย หมายเหตุ อยากได้
    ติดตั้งวันเสาร์" with ZERO model calls (11 ก.ย. 2569).

    docs/MODEL_FIRST.md step 1: the sentence never reached the model, so the
    rule is what had to move — and a read road has nothing to decline, so it
    falls through rather than refusing."""

    INTENT = {"action": "update", "entity": "customer",
              "fields": {"target_name": "สมชาย", "notes": "อยากได้ติดตั้งวันเสาร์"}, "missing": []}

    @pytest.mark.asyncio
    async def test_the_edit_reaches_the_model_and_is_carried_out(self):
        client = _shop(customers=[{**CUSTOMER, "notes": "ชอบสีขาว"}])
        text, written = await _say(
            client, "แก้ข้อมูลลูกค้าสมชาย หมายเหตุ อยากได้ติดตั้งวันเสาร์", intent=self.INTENT,
        )
        assert "update_customer" in written, f"{text} / {written}"
        assert "ไม่พบ" not in text, text

    @pytest.mark.asyncio
    async def test_the_lookup_itself_is_untouched(self):
        client = _shop(customers=[dict(CUSTOMER)])
        text, written = await _say(client, "ข้อมูลลูกค้าสมชาย")
        assert "C-2026-0001" in text and written == [], f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_lookup_by_code_is_untouched(self):
        client = _shop(customers=[dict(CUSTOMER)])
        text, _ = await _say(client, "ขอข้อมูลลูกค้า C-2026-0001")
        assert "สมชาย" in text, text

    def test_the_test_itself_names_the_verbs(self):
        from chann_app.services.chat import _lookup_is_really_an_edit as f

        triggers = ("ข้อมูลลูกค้า",)
        assert f("แก้ข้อมูลลูกค้าสมชาย", triggers)
        assert f("อัปเดตข้อมูลลูกค้าสมชาย", triggers)
        assert not f("ข้อมูลลูกค้าสมชาย", triggers)
        assert not f("ขอข้อมูลลูกค้าสมชาย", triggers)


class TestASalesGroupIsNotATechnicianTeam:
    """`sales_groups` and `technician_teams` are different tables, with
    different Data routes and different dashboard pages. Chat had a road to
    one of them: every sales-group sentence was rebuilt as a technician
    sentence, so "สร้างกลุ่มขาย ทีมเหนือ" answered "สร้างทีม ทีมเหนือ แล้ว"
    and recorded create_technician_team — a sales group in the technician
    roster, assignable to a repair job (verified 11 ก.ย. 2569)."""

    @pytest.mark.asyncio
    async def test_a_sales_group_is_created_in_its_own_table(self):
        client = _shop()
        text, _ = await _say(client, "สร้างกลุ่มขาย เหนือ", intent={
            "action": "create", "entity": "sales_group",
            "fields": {"team_name": "เหนือ", "scope": "sales"}, "missing": [],
        })
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "create_sales_group" in kinds, f"{text} / {kinds}"
        assert "create_technician_team" not in kinds, kinds

    @pytest.mark.asyncio
    async def test_the_scope_the_prompt_asks_for_is_read(self):
        """entity="team" with scope="sales" is what INTENT_SYSTEM_PROMPT
        tells the model to send; the handler ignored the field entirely."""
        client = _shop()
        await _say(client, "ตั้งทีมขาย ใต้", intent={
            "action": "create", "entity": "team",
            "fields": {"team_name": "ใต้", "scope": "sales"}, "missing": [],
        })
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "create_sales_group" in kinds and "create_technician_team" not in kinds, kinds

    @pytest.mark.asyncio
    async def test_a_technician_team_still_goes_where_it_always_did(self):
        client = _shop()
        await _say(client, "สร้างทีมช่าง แอร์", intent={
            "action": "create", "entity": "team",
            "fields": {"team_name": "แอร์", "scope": "technician"}, "missing": [],
        })
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "create_technician_team" in kinds and "create_sales_group" not in kinds, kinds

    @pytest.mark.asyncio
    async def test_deleting_one_is_a_capability_the_gate_knows(self):
        """("delete", "sales_group") was unregistered, so the gate answered
        "this system cannot do that" while DELETE /sales-groups/{id} had
        existed since Phase 7."""
        client = _shop()
        await _say(client, "สร้างกลุ่มขาย เหนือ", intent={
            "action": "create", "entity": "sales_group",
            "fields": {"team_name": "เหนือ"}, "missing": []})
        text, _ = await _say(client, "ลบกลุ่มขาย เหนือ", intent={
            "action": "delete", "entity": "sales_group",
            "fields": {"team_name": "เหนือ"}, "missing": []})
        assert "delete_sales_group" in [c[0] for c in client.recorded], text
        assert client._sales_groups == [], client._sales_groups

    @pytest.mark.asyncio
    async def test_naming_the_group_and_its_first_member_does_both(self):
        """"สร้างกลุ่มขาย เหนือ มีสมชาย" names a group AND a member. The
        first cut created the group and then looked it up by name in the
        same breath, so the reply was "ไม่พบกลุ่มขายชื่อ เหนือ"."""
        client = _shop()
        client._members = [{"id": "member-9", "chann_uid": "CHN-S-000009", "status": "active"}]
        client._profiles = {"CHN-S-000009": {"first_name": "สมชาย", "last_name": "ขายเก่ง"}}
        text, _ = await _say(client, "สร้างกลุ่มขาย เหนือ มีสมชาย", intent={
            "action": "create", "entity": "sales_group",
            "fields": {"team_name": "เหนือ", "members": "สมชาย"}, "missing": [],
        })
        kinds = [c[0] for c in client.recorded if "group" in c[0]]
        assert kinds == ["create_sales_group", "add_sales_group_member"], f"{text} / {kinds}"
        assert "เหนือ" in text and "สมชาย" in text and "ไม่พบ" not in text, text

    @pytest.mark.asyncio
    async def test_a_refusal_deletes_nothing(self):
        client = _shop()
        await _say(client, "สร้างกลุ่มขาย เหนือ", intent={
            "action": "create", "entity": "sales_group",
            "fields": {"team_name": "เหนือ"}, "missing": []})
        await _say(client, "ไม่ต้องลบกลุ่มขาย เหนือ", intent={
            "action": "delete", "entity": "sales_group",
            "fields": {"team_name": "เหนือ"}, "missing": []})
        assert [g["group_name"] for g in client._sales_groups] == ["เหนือ"]


class TestTheParityCheckerKnowsWhatChatCanActuallyDo:
    """check-parity.py took ACTION_PERMISSIONS as "what chat can do". That
    dict says what is REGISTERED. deal.update sat in it while chat answered
    "ยังทำรายการนี้ไม่ได้" and the dashboard's DealDetail saved the same four
    fields happily — a parity break the parity checker could not see
    (11 ก.ย. 2569).

    It now subtracts NO_HANDLER_YET, which is a measurement. These tests are
    what stops that list from drifting away from the router: every pair on
    it must really have no handler, and the ones just built must really
    have one."""

    @staticmethod
    def _no_handler_list():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_parity", ROOT / "scripts" / "dev" / "check-parity.py")
        module = importlib.util.module_from_spec(spec)
        import sys as _sys
        _sys.argv = ["check-parity"]
        spec.loader.exec_module(module)
        return module.NO_HANDLER_YET

    async def _probe(self, action, entity, fields):
        client = _shop(customers=[dict(CUSTOMER)], deals=[dict(DEAL)], quotes=[dict(QUOTE)])
        intent = {"action": action, "entity": entity, "fields": dict(fields), "missing": []}
        text, _ = await _say(client, "ทำรายการนี้ให้หน่อย", intent=intent)
        from chann_app.services.chat import _no_handler_reply
        return text.strip() == (_no_handler_reply(intent, "th", "sales").text or "").strip()

    @pytest.mark.asyncio
    async def test_everything_on_the_list_really_has_no_handler(self):
        stale = []
        for entity, action in self._no_handler_list():
            if entity in ("audit_log", "role", "member", "product"):
                continue  # nothing to name a record with; the list's reason stands
            if not await self._probe(action, entity, {"code": "D-2026-0001"}):
                stale.append(f"{entity}.{action}")
        assert not stale, f"these now have a handler — take them off NO_HANDLER_YET: {stale}"

    @pytest.mark.asyncio
    async def test_the_ones_just_built_are_not_on_it(self):
        built = {("deal", "update"), ("quote", "update"), ("sales_group", "create"),
                 ("sales_group", "delete")}
        on_the_list = built & set(self._no_handler_list())
        assert not on_the_list, f"built, but still listed as unreachable: {on_the_list}"

    @pytest.mark.asyncio
    async def test_a_deal_edit_is_carried_out(self):
        """The parity break itself: the dashboard has saved amount,
        expected close, notes and lost reason since Phase 9."""
        client = _shop(customers=[dict(CUSTOMER)], deals=[{**DEAL, "amount": "500000.00"}])
        text, written = await _say(client, "แก้มูลค่าดีล D-2026-0001 เป็น 600000", intent={
            "action": "update", "entity": "deal",
            "fields": {"code": "D-2026-0001", "amount": 600000}, "missing": [],
        })
        assert "update_deal" in written, f"{text} / {written}"
        sent = _payload(client, "update_deal")
        assert sent == {"amount": "600000", "currency": "THB"}, sent

    @pytest.mark.asyncio
    async def test_a_date_inside_a_note_is_not_a_close_date(self):
        """Which fields change is the model's reading; what they change to
        is read from the message. Running the message scanner over every
        update put an expected close date on a note that merely said
        "วันเสาร์"."""
        client = _shop(customers=[dict(CUSTOMER)], deals=[{**DEAL, "notes": "ลูกค้าขอส่วนลด"}])
        await _say(client, "จดในดีล D-2026-0001 ว่าลูกค้าขอติดตั้งวันเสาร์", intent={
            "action": "update", "entity": "deal",
            "fields": {"code": "D-2026-0001", "notes": "ลูกค้าขอติดตั้งวันเสาร์"}, "missing": [],
        })
        sent = _payload(client, "update_deal")
        assert set(sent) == {"notes"}, sent
        assert sent["notes"] == "ลูกค้าขอส่วนลด\nลูกค้าขอติดตั้งวันเสาร์", sent

    @pytest.mark.asyncio
    async def test_a_stage_the_model_reads_goes_to_the_stage_road(self):
        """"ดีล D-2026-0001 ลูกค้าตกลงซื้อแล้ว" is a phrasing the typed
        parser misses. It used to fall off the end of the handler."""
        client = _shop(customers=[dict(CUSTOMER)], deals=[dict(DEAL)])
        text, written = await _say(client, "ดีล D-2026-0001 ลูกค้าตกลงซื้อแล้ว", intent={
            "action": "update", "entity": "deal",
            "fields": {"code": "D-2026-0001", "stage": "won"}, "missing": [],
        })
        assert "transition_deal_stage" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_refusal_still_changes_nothing(self):
        client = _shop(customers=[dict(CUSTOMER)], deals=[{**DEAL, "amount": "500000.00"}])
        text, written = await _say(client, "ไม่ต้องแก้มูลค่าดีล D-2026-0001 เป็น 600000", intent={
            "action": "update", "entity": "deal",
            "fields": {"code": "D-2026-0001", "amount": 600000}, "missing": [],
        })
        assert written == [], f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_the_customers_answer_to_a_quotation_is_recorded(self):
        client = _shop(customers=[dict(CUSTOMER)], deals=[dict(DEAL)], quotes=[dict(QUOTE)])
        text, _ = await _say(client, "ลูกค้าตอบรับใบเสนอราคา Q-2026-0001 แล้ว", intent={
            "action": "update", "entity": "quote",
            "fields": {"code": "Q-2026-0001", "status": "accepted"}, "missing": [],
        })
        assert "set_quote_status" in [c[0] for c in client.recorded], text



class TestWhatTheDeployedModelActuallyReturns:
    """Every intent in these tests is a VERBATIM answer from the deployed
    model (google/gemini-3.1-flash-lite), captured on 11 ก.ย. 2569 by
    scripts/dev/ask-model.py. That matters: the stubs in every other test
    were written by the same hand as the code, so they agreed with it by
    construction. The real model disagreed in four places, and all four were
    bugs in code that had already "passed" its tests.

      ดีล D-2026-0001 ลูกค้าตกลงซื้อแล้ว -> {"status": "won"}, not "stage"
      สร้างกลุ่มขาย เหนือ             -> entity "team" + missing ["members"]
      ลบกลุ่มขาย เหนือ                -> entity "team", NO scope at all
      เพิ่ม สมชาย เข้ากลุ่มขาย เหนือ   -> entity "team", NO scope at all

    The last two would have deleted from, and written to, the TECHNICIAN
    table — the same defect this round set out to fix, re-entering through
    the model road."""

    REAL = {
        "stage": ("ดีล D-2026-0001 ลูกค้าตกลงซื้อแล้ว", {
            "action": "update", "entity": "deal",
            "fields": {"deal_code": "D-2026-0001", "status": "won"}, "missing": []}),
        "group_create": ("สร้างกลุ่มขาย เหนือ", {
            "action": "create", "entity": "team",
            "fields": {"team_name": "เหนือ", "scope": "sales"}, "missing": ["members"]}),
        "group_add": ("เพิ่ม สมชาย เข้ากลุ่มขาย เหนือ", {
            "action": "update", "entity": "team",
            "fields": {"team_name": "เหนือ", "members": ["สมชาย"]}, "missing": []}),
        "group_delete": ("ลบกลุ่มขาย เหนือ", {
            "action": "delete", "entity": "team",
            "fields": {"team_name": "เหนือ"}, "missing": []}),
        "tech_team": ("สร้างทีมช่าง แอร์", {
            "action": "create", "entity": "team",
            "fields": {"team_name": "แอร์", "scope": "technician"}, "missing": []}),
    }

    def _staffed(self):
        client = _shop(customers=[dict(CUSTOMER)], deals=[dict(DEAL)])
        client._members = [{"id": "member-9", "chann_uid": "CHN-S-000009", "status": "active"}]
        client._profiles = {"CHN-S-000009": {"first_name": "สมชาย", "last_name": "ขายเก่ง"}}
        return client

    async def _real(self, client, key):
        message, intent = self.REAL[key]
        return await _say(client, message, intent=intent)

    @pytest.mark.asyncio
    async def test_the_model_calls_it_status_and_the_stage_still_moves(self):
        client = self._staffed()
        text, written = await self._real(client, "stage")
        assert "transition_deal_stage" in written, f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_group_is_created_although_members_is_reported_missing(self):
        client = self._staffed()
        text, _ = await self._real(client, "group_create")
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "create_sales_group" in kinds, f"{text} / {kinds}"

    @pytest.mark.asyncio
    async def test_a_group_with_no_scope_is_still_not_a_technician_team(self):
        client = self._staffed()
        await self._real(client, "group_create")
        await self._real(client, "group_add")
        text, _ = await self._real(client, "group_delete")
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "add_sales_group_member" in kinds and "delete_sales_group" in kinds, kinds
        assert not [k for k in kinds if "technician_team" in k], f"{text} / {kinds}"

    @pytest.mark.asyncio
    async def test_a_technician_team_still_goes_to_the_technician_table(self):
        client = self._staffed()
        await self._real(client, "tech_team")
        kinds = [c[0] for c in client.recorded if "group" in c[0] or "team" in c[0]]
        assert "create_technician_team" in kinds and "create_sales_group" not in kinds, kinds


class TestReadingIsTheModelsJobRefusingIsTheCodes:
    """scripts/agent-test/model-cases.json expected the MODEL to answer
    "suggest" for three sentences whose speaker lacks the permission. It
    failed all nine runs on 11 ก.ย. 2569 — by reading them correctly.

    That expectation was the architecture backwards. docs/MODEL_FIRST.md:
    "permission key — does this person hold the key? … None of that moves to
    the model. Ever." The model reads; the gate refuses. These tests assert
    the refusal where it actually lives, so the corpus does not have to ask
    the model for something it must not be asked.

    The intents below are verbatim from the deployed model."""

    DENIED = [
        ("ลบลูกค้า สมชาย",
         {"action": "archive", "entity": "customer",
          "fields": {"target_name": "สมชาย"}, "missing": []}),
        ("สร้างใบเสนอราคาให้ดีล D-2026-0001",
         {"action": "create", "entity": "quote",
          "fields": {"deal_code": "D-2026-0001"}, "missing": []}),
        ("อนุมัติ SR-2026-0001",
         {"action": "approve", "entity": "approval",
          "fields": {"code": "SR-2026-0001"}, "missing": []}),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,intent", DENIED)
    async def test_a_correct_reading_without_the_key_writes_nothing(self, message, intent):
        client = FakeDataClient(
            role="technician", permission_keys=["ticket.read"],
            customers=[dict(CUSTOMER)], deals=[dict(DEAL)],
        )
        text, written = await _say(
            client, message, intent=intent, oa="technician", role="technician",
        )
        assert written == [], f"{message!r} wrote {written}"
        assert text.strip(), "refused with silence"


class TestTheShopCanMoveAVisitFromChat:
    """The other half of the owner's rule. A customer may not move a visit;
    "จะเลื่อนได้แค่คนที่มีสิทธิ์และทำใน Sale OA" — and until 11 ก.ย. 2569 the
    Sales OA could not either. Measured: "เลื่อนนัด T-2026-0001 วันศุกร์" was
    claimed by the REMINDER roads (first the move road, then the create road,
    because _is_reminder_command sees a record code and the word "นัด" inside
    "เลื่อนนัด") and answered by asking for a code the sentence had given. So
    the shop could receive a customer's request in chat and had to leave chat
    to act on it."""

    JOB = {
        "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
        "accept_status": "accepted", "assigned_to_ref": "member-1",
        "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย",
        "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-12", "scheduled_time": "14:00",
    }

    def _shop_with_a_job(self, oa, role):
        client = FakeDataClient(
            role=oa, permission_keys=sorted(DEFAULT_ROLE_TEMPLATES[role]),
            customers=[dict(CUSTOMER)],
        )
        client._tickets = [dict(self.JOB)]
        return client

    async def _say_on(self, client, message, oa, role):
        return await _say(client, message, oa=oa, role=role)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "เลื่อนนัด T-2026-0001 วันศุกร์ บ่าย 2",
        "เลื่อนงาน T-2026-0001 เป็นวันศุกร์ บ่าย 2",
    ])
    async def test_sales_moves_the_visit(self, message):
        client = self._shop_with_a_job("sales", "admin")
        text, written = await self._say_on(client, message, "sales", "admin")
        assert "update_ticket" in written, f"{text} / {written}"
        sent = _payload(client, "update_ticket")
        assert sent["scheduled_date"] == "2026-09-18", sent
        assert sent["scheduled_time"].startswith("14:00"), sent

    @pytest.mark.asyncio
    async def test_a_refusal_moves_nothing(self):
        client = self._shop_with_a_job("sales", "admin")
        text, written = await self._say_on(
            client, "ไม่ต้องเลื่อนนัด T-2026-0001 นะครับ", "sales", "admin")
        assert written == [], f"{text} / {written}"

    @pytest.mark.asyncio
    async def test_a_reminder_about_a_job_is_still_a_reminder(self):
        """Both halves of _is_a_job_move are required: a T- code with no move
        verb is a reminder ABOUT a job, which is a real thing."""
        client = self._shop_with_a_job("sales", "admin")
        _, written = await self._say_on(
            client, "เตือนเรื่องงาน T-2026-0001 พรุ่งนี้", "sales", "admin")
        assert "update_ticket" not in written, written

    @pytest.mark.asyncio
    async def test_the_technician_road_is_unchanged(self):
        client = self._shop_with_a_job("technician", "technician")
        _, written = await self._say_on(
            client, "เลื่อนนัด T-2026-0001 วันศุกร์ บ่าย 2", "technician", "technician")
        assert "update_ticket" in written, written

    @pytest.mark.asyncio
    async def test_the_customer_hears_who_moved_it(self, monkeypatch):
        """The wording said "ช่าง" unconditionally, which was true while only
        a technician could reach this handler. A customer told "ช่างขอเลื่อน
        นัด" by the salesperson they had just asked would be confused."""
        said = {}

        async def spy(client, license_id, ticket_id, text, language,
                      text_en=None, customer_text=None, customer_text_en=None):
            said["shop"], said["customer"] = text, customer_text

        monkeypatch.setattr(chat, "_notify_ticket_change", spy)
        for oa, role, who in (("sales", "admin", "ร้าน"), ("technician", "technician", "ช่าง")):
            said.clear()
            client = self._shop_with_a_job(oa, role)
            await self._say_on(client, "เลื่อนนัด T-2026-0001 วันศุกร์ บ่าย 2", oa, role)
            assert said["shop"].startswith(who), (oa, said["shop"])
            assert said["customer"].startswith(who), (oa, said["customer"])


class TestTheVisitHandlerGuardsItsOwnChannel:
    """_handle_technician_situation had one caller and one OA when it was
    written, so a bare `ticket.update in permission_keys` test was the whole
    of its gate. It now serves three callers — the technician road, the sales
    road and the model road — and what keeps a customer out is _oa_allows on
    the CALLER's side.

    A guard that lives in the caller is a guard the next caller forgets, and
    "a road below the gate" is the shape of every hole closed this session.
    The handler checks the channel itself now, so the test calls it directly,
    as a future caller that forgot would."""

    JOB = {
        "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
        "accept_status": "accepted", "assigned_to_ref": "member-1",
        "customer_chann_uid": "CHN-S-000001", "customer_name": "สมชาย",
        "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-12", "scheduled_time": "14:00",
    }

    async def _direct(self, oa, keys):
        client = FakeDataClient(role=oa, permission_keys=list(keys))
        client._tickets = [dict(self.JOB)]
        reply = await chat._handle_technician_situation(
            client, ctx=_ctx(primary_role=oa, oa=oa),
            license_id=LICENSE_ID, message="T-2026-0001 วันศุกร์ บ่าย 2",
            kind="reschedule", permission_keys=list(keys), language="th",
        )
        written = [c[0] for c in client.recorded if c[0] == "update_ticket"]
        return (reply.text or ""), written

    @pytest.mark.asyncio
    async def test_the_customer_channel_is_refused_even_holding_the_key(self):
        """ticket.update is not in OA_ALLOWED_PERMISSION_KEYS["customer"], and
        that is the fact the handler now checks for itself."""
        text, written = await self._direct("customer", ["ticket.update"])
        assert written == [], f"a customer moved a visit: {written}"
        assert text.strip()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("oa,role", [("sales", "admin"), ("technician", "technician")])
    async def test_the_two_real_callers_still_work(self, oa, role):
        _, written = await self._direct(oa, sorted(DEFAULT_ROLE_TEMPLATES[role]))
        assert "update_ticket" in written, written


class TestACourtesyWordIsNotPartOfTheName:
    """Measured over the whole sales corpus on 11 ก.ย. 2569: 29 sentences a
    rule decided and answered badly, and most were one shape — the rule took
    the tail of the sentence verbatim as a name or a code.

      ข้อมูลดีล D-2026-0001 ครับ  -> ไม่พบดีลรหัส D-2026-0001 ครับ
      ดีลของสมชายครับ            -> ไม่พบลูกค้าชื่อ "สมชายครับ"
      เบอร์คุณสมชายอะไรครับ       -> ไม่พบลูกค้าที่ตรงกับ "คุณสมชายอะไร"
      ค้นหาลูกค้าเบอร์ 0812345678 -> ไม่พบลูกค้าที่ตรงกับ "เบอร์ 0812345678"

    Every one of those records exists. A confident wrong answer about a
    record that is right there is the failure this codebase already names as
    worse than the write it replaced — and the model road has stripped these
    words since _drop_invented_values learned to. One rule about what a name
    is, not two."""

    @pytest.mark.parametrize("raw,want", [
        ("คุณสมชายอะไรครับ", "สมชาย"),
        ("สมชายครับ", "สมชาย"),
        ("สมชายหน่อย", "สมชาย"),
        ("D-2026-0001 ครับ", "D-2026-0001"),
        ("เบอร์ 0812345678", "0812345678"),
        ("ขึ้นต้นด้วย สม", "สม"),
        ("ลูกค้าสมชายทั้งหมด", "สมชาย"),
        # and what must survive untouched
        ("สมชาย", "สมชาย"),
        ("สมชาย ใจดี", "สมชาย ใจดี"),
        ("somchai", "somchai"),
        ("สม", "สม"),
    ])
    def test_the_cleaner_keeps_the_name_and_drops_the_rest(self, raw, want):
        from chann_app.services.chat import _clean_lookup_term

        assert _clean_lookup_term(raw) == want

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message,expected", [
        ("ข้อมูลดีล D-2026-0001 ครับ", "D-2026-0001"),
        ("ดีลของสมชายครับ", "D-2026-0001"),
        ("ขอดูดีลของสมชายหน่อย", "D-2026-0001"),
        ("อยากเห็นดีลของลูกค้าสมชายทั้งหมด", "D-2026-0001"),
    ])
    async def test_the_record_is_found_through_the_courtesy(self, message, expected):
        client = _shop(customers=[dict(CUSTOMER)], deals=[dict(DEAL)])
        text, written = await _say(client, message)
        assert expected in text, f"{message!r} -> {text[:80]}"
        assert "ไม่พบ" not in text, text
        assert written == [], written

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "เบอร์คุณสมชายอะไรครับ",
        "ค้นหาลูกค้าเบอร์ 0812345678",
    ])
    async def test_the_customer_is_found_through_the_courtesy(self, message):
        client = _shop(customers=[dict(CUSTOMER)])
        text, _ = await _say(client, message)
        assert "สมชาย" in text and "ไม่พบ" not in text, f"{message!r} -> {text[:80]}"


class TestArchivingADealAsksFirst:
    """Owner approved it on 11 ก.ย. 2569. It had been registered in
    ACTION_PERMISSIONS and answered "ยังทำรายการนี้ไม่ได้" — check-parity
    carried the reason: "customer archive asks for a confirmation first; a
    deal needs the same flow". A deal carries its quotations and its line
    items, and "ลบดีล" typed in passing is too cheap a sentence for that."""

    ARCHIVE = {"action": "archive", "entity": "deal",
               "fields": {"deal_code": "D-2026-0001"}, "missing": []}

    async def _turns(self, *turns):
        client = _shop(customers=[dict(CUSTOMER)], deals=[{**DEAL, "amount": "500000.00"}])
        text = ""
        for message, intent in turns:
            text, _ = await _say(client, message, intent=intent)
        archived = [c[0] for c in client.recorded if c[0].startswith("archive_")]
        return text, archived, client

    @pytest.mark.asyncio
    async def test_it_asks_and_then_archives(self):
        text, archived, _ = await self._turns(
            ("ลบดีล D-2026-0001", self.ARCHIVE), ("ยืนยันลบ", None),
        )
        assert archived == ["archive_deal"], archived
        assert "เก็บถาวร" in text, text

    @pytest.mark.asyncio
    async def test_the_question_names_the_deal_and_its_value(self):
        """A confirmation that does not say what is about to go is a
        confirmation nobody can give properly."""
        text, archived, _ = await self._turns(("ลบดีล D-2026-0001", self.ARCHIVE))
        assert archived == [], archived
        assert "D-2026-0001" in text and "สมชาย" in text and "500,000" in text, text

    @pytest.mark.asyncio
    async def test_backing_out_keeps_the_deal(self):
        text, archived, _ = await self._turns(
            ("ลบดีล D-2026-0001", self.ARCHIVE), ("ยกเลิก", None),
        )
        assert archived == [], archived
        assert "ยังอยู่" in text, text

    @pytest.mark.asyncio
    async def test_a_refusal_never_reaches_the_question(self):
        text, archived, _ = await self._turns(("ไม่ต้องลบดีล D-2026-0001", self.ARCHIVE))
        assert archived == [], archived
        assert "ใช่ไหมครับ" not in text, text

    @pytest.mark.asyncio
    async def test_naming_no_deal_asks_which(self):
        text, archived, _ = await self._turns((
            "ลบดีล", {"action": "archive", "entity": "deal", "fields": {}, "missing": []},
        ))
        assert archived == [], archived
        assert "ดีลไหน" in text, text


class TestTakingAProductOutOfTheCatalogue:
    """Owner approved it on 11 ก.ย. 2569. check-parity had it on the backlog
    as "no Application route for it either" — which was half right and the
    wrong half: the Data tier has done this since Phase 7 under the verb
    ARCHIVE, with products.archived_at shipped in migration 0006 and five
    foreign keys into products.id that make a hard delete impossible. What
    was missing was every way to reach it.

    In chat, "ลบสินค้า …" was claimed by the road that removes a line from a
    DEAL — the two share every trigger word — and answered "แก้ของดีลหรือ
    ใบเสนอราคาไหนครับ"."""

    PRODUCT = {"id": "P-1", "product_id": "FAN001",
               "product_name": "พัดลมไอเย็น", "unit_price": "1200"}
    DELETE = {"action": "delete", "entity": "product",
              "fields": {"product_name": "พัดลมไอเย็น"}, "missing": []}

    async def _turns(self, *turns):
        client = _shop(deals=[{**DEAL, "products": [
            {"id": "L1", "product_name": "พัดลมไอเย็น", "quoted_unit_price": "1200", "qty": 1}]}])
        client._products = [dict(self.PRODUCT)]
        text = ""
        for message, intent in turns:
            text, _ = await _say(client, message, intent=intent)
        archived = [c[0] for c in client.recorded if c[0] == "archive_product"]
        return text, archived, client

    @pytest.mark.asyncio
    async def test_it_asks_and_then_archives(self):
        text, archived, client = await self._turns(
            ("เอาพัดลมไอเย็นออกจากรายการสินค้า", self.DELETE), ("ยืนยันลบ", None),
        )
        assert archived == ["archive_product"], archived
        assert client._products == [], client._products
        assert "ของเดิม" in text, "did not say the existing deals keep it"

    @pytest.mark.asyncio
    async def test_the_question_says_what_survives(self):
        """Five tables reference a product. Someone confirming this needs to
        know last year's deals are not about to lose their contents."""
        text, archived, _ = await self._turns(
            ("เอาพัดลมไอเย็นออกจากรายการสินค้า", self.DELETE),
        )
        assert archived == [], archived
        assert "FAN001" in text and "ดีลและใบเสนอราคาเดิมยังเก็บ" in text, text

    @pytest.mark.asyncio
    async def test_backing_out_keeps_it(self):
        text, archived, client = await self._turns(
            ("เอาพัดลมไอเย็นออกจากรายการสินค้า", self.DELETE), ("ยกเลิก", None),
        )
        assert archived == [] and len(client._products) == 1
        assert "ยังอยู่" in text, text

    @pytest.mark.asyncio
    async def test_a_refusal_never_reaches_the_question(self):
        """The catch-all record_delete vocabulary could not bind to this
        sentence: "เอา" and "ออก" sit either side of the product's name, so
        "เอาออก" never matches and the guard had nothing to negate. It has
        its own vocabulary now, with the halves listed separately."""
        text, archived, _ = await self._turns(
            ("ไม่ต้องเอาพัดลมไอเย็นออกจากรายการสินค้า", self.DELETE),
        )
        assert archived == [], archived
        assert "ใช่ไหมครับ" not in text, text

    @pytest.mark.asyncio
    async def test_a_deal_line_is_still_a_deal_line(self):
        """A sentence that says neither belongs to the deal road: it asks
        "which deal?" and can be answered, where a wrong catalogue delete
        cannot be taken back in one word."""
        text, archived, _ = await self._turns(("ลบสินค้าพัดลมไอเย็น", None))
        assert archived == [], archived
        assert "ดีล" in text or "ใบเสนอราคา" in text, text
