"""Round 19p — "เปิดให้ช่างรับ T-…" on the shop's LINE, and several CS.

Owner, 16 ก.ย. 2569: "Sale OA ตอนนี้ใช้คำสั่งพิมว่า เปิดให้ช่างรับ T-2026….
ได้ไหมเพราะพอใช้แล้วแจ้งว่า งาน T… ยังไม่เปิดให้รับ" — and then "จะทำยังไง
ถ้า CS มีหลายคน".

The DEV log says what happened: `oa=sales road=model action=claim
entity=ticket chars=101` at 07:49 and again at 10:19 and 10:20. 101 is
exactly TICKET_HELD_BY_SHOP — the TECHNICIAN's refusal ("รอ CS มอบหมาย
หรือเปิดรับก่อน"), which told the CS to wait for themselves. The sentence
contains "รับ", and the model read it as the speaker taking the job.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services import chat
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ctx
from test_round18e_followups import KEYS, _reads

pytestmark = pytest.mark.asyncio

SHOP_KEYS = sorted(set(KEYS) | {"ticket.assign", "ticket.update", "ticket.read", "ticket.create"})
REPORTED = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "open", "accept_status": "pending",
    "assigned_to_ref": None, "owner_member_id": "member-1", "visibility": "private",
    "customer_name": "สมชาย ใจดี", "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
}
CS_TEAM = [
    {"id": "member-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active",
     "display_name": "เจ้าของ", "channel": "sales"},
    {"id": "m-cs-2", "chann_uid": "CHN-S-000002", "role": "cs", "status": "active",
     "display_name": "สมหญิง", "channel": "sales"},
    {"id": "m-tech-1", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active",
     "display_name": "สมศักดิ์", "first_name": "สมศักดิ์", "channel": "technician"},
]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _shop() -> FakeDataClient:
    client = FakeDataClient(permission_keys=SHOP_KEYS, role="sales")
    client._tickets = [dict(REPORTED)]
    client._members = [dict(m) for m in CS_TEAM]
    client._is_owner = True
    client._role = "owner"
    client._member_id = "member-1"
    client._line_targets = {"CHN-S-000002": "line-cs2", "CHN-T-000001": "line-tech"}
    # _technician_named reads the NAME from the profile, not the member row.
    client._profiles = {
        "CHN-S-000001": {"chann_uid": "CHN-S-000001", "first_name": "เจ้าของ"},
        "CHN-S-000002": {"chann_uid": "CHN-S-000002", "first_name": "สมหญิง"},
        "CHN-T-000001": {"chann_uid": "CHN-T-000001", "first_name": "สมศักดิ์"},
    }
    return client


async def _say(client, message, reading):
    since = len(client.recorded)
    reply = await handle_chat_message(
        client, message=message, ctx=_ctx(oa="sales", primary_role="sales"), ai_client=_reads(reading),
    )
    return reply, client.recorded[since:]


class TestOpeningAJobToTheTechnicians:
    """Whatever verb the model puts on the sentence, the shop means the
    pool. Only a NAME makes it an assignment."""

    @pytest.mark.parametrize("action", ["release", "assign", "claim", "update"])
    async def test_every_reading_opens_the_job(self, action):
        client = _shop()
        reading = {"action": action, "entity": "ticket",
                   "fields": {"code": "T-2026-0001"},
                   "missing": ["target_name"] if action == "assign" else []}
        reply, wrote = await _say(client, "เปิดให้ช่างรับ T-2026-0001", reading)
        assert [w for w in wrote if w[0] == "release_ticket"], (reply.text, wrote)
        assert "ยังไม่เปิดให้รับ" not in reply.text, reply.text
        assert "T-2026-0001" in reply.text, reply.text

    async def test_the_code_is_read_from_the_sentence_when_the_model_drops_it(self):
        client = _shop()
        reply, wrote = await _say(
            client, "เปิดให้ช่างรับ T-2026-0001",
            {"action": "claim", "entity": "ticket", "fields": {}, "missing": []},
        )
        assert [w for w in wrote if w[0] == "release_ticket"], (reply.text, wrote)

    async def test_naming_a_technician_is_still_an_assignment(self):
        client = _shop()
        reply, wrote = await _say(
            client, "มอบหมาย T-2026-0001 ให้ สมศักดิ์",
            {"action": "assign", "entity": "ticket",
             "fields": {"code": "T-2026-0001", "target_name": "สมศักดิ์"}, "missing": []},
        )
        assert not [w for w in wrote if w[0] == "release_ticket"], (reply.text, wrote)
        assert [w for w in wrote if w[0] == "assign_ticket"], (reply.text, wrote)

    async def test_the_prompt_teaches_the_word(self):
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        assert 'action="release"' in INTENT_SYSTEM_PROMPT
        assert "เปิดให้ช่างรับ" in INTENT_SYSTEM_PROMPT


class TestSeveralPeopleOnTheQueue:
    """Owner: "จะทำยังไงถ้า CS มีหลายคน". Everyone holding ticket.assign is
    told when a customer reports a job; nothing told them when one of them
    had acted, so two people worked the same item."""

    async def test_the_other_dispatchers_hear_who_opened_it(self):
        client = _shop()
        _reply, wrote = await _say(
            client, "เปิดให้ช่างรับ T-2026-0001",
            {"action": "release", "entity": "ticket", "fields": {"code": "T-2026-0001"}, "missing": []},
        )
        handled = [
            w for w in wrote
            if w[0] == "create_notification" and "ticket_dispatch_handled" in str(w)
        ]
        assert handled, wrote
        assert any("เจ้าของ" in str(w) for w in handled), handled
        assert not any("CHN-S-000001" == str(w[2]) for w in handled), "the actor must not be told"

    async def test_the_other_dispatchers_hear_who_it_was_assigned_to(self):
        client = _shop()
        _reply, wrote = await _say(
            client, "มอบหมาย T-2026-0001 ให้ สมศักดิ์",
            {"action": "assign", "entity": "ticket",
             "fields": {"code": "T-2026-0001", "target_name": "สมศักดิ์"}, "missing": []},
        )
        handled = [
            w for w in wrote
            if w[0] == "create_notification" and "ticket_dispatch_handled" in str(w)
        ]
        assert handled, wrote
        assert any("สมศักดิ์" in str(w) for w in handled), handled


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
class TestTheSentenceItself:
    @pytest.mark.parametrize("said", [
        "เปิดให้ช่างรับ T-2026-0001", "ปล่อยงาน T-2026-0001 ให้ช่างรับ", "เปิดงานให้ช่างมารับ",
    ])
    def test_these_are_the_pool(self, said):
        assert chat._asks_to_open_to_technicians(said), said

    @pytest.mark.parametrize("said", ["รับงาน T-2026-0001", "ขอรับงานนี้", "มอบหมายให้ สมศักดิ์"])
    def test_these_are_not(self, said):
        assert not chat._asks_to_open_to_technicians(said), said


class TestACustomerAddsASecondShop:
    """The owner's own transcript, 16 ก.ย. 2569, 17:23–17:26:

        "ตอนนี้ผูกกับร้านไหน"  → the contact card (an address)
        "ผมอยู่กับร้านไหน"     → "ผมตอบคำถามนี้เองไม่ได้ครับ" (relayed)
        "ลงทะเบียนอีกร้าน"     → "พิมพ์รหัสเชิญของร้านนั้น" (a STAFF code)
        "COV9URCZ"             → "ไม่พบหมายเลข COV9URCZ ในระบบครับ"
        "ร้านทดสอบ"            → the contact card again

    The last one is the root of it: a shop code is "CO" + six characters of
    the no-confusables alphabet, and the pattern that recognised one was
    eight characters OF that alphabet — which has no "O". No real code
    could ever match, on any road.
    """

    def test_a_real_shop_code_is_recognised(self):
        from chann_app.services.registration import as_company_code

        assert as_company_code("COV9URCZ") == "COV9URCZ"
        assert as_company_code("cov9urcz") == "COV9URCZ"
        # A zero typed for the O of the prefix: the alphabet has neither,
        # so it can only be that.
        assert as_company_code("C0V9URCZ") == "COV9URCZ"
        assert as_company_code("SN12345678") == ""

    def test_the_pattern_matches_what_the_generator_makes(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
        from chann_data.repositories.phase65 import CODE_ALPHABET

        from chann_app.services.registration import as_company_code

        for tail in ("V9URCZ", "AAAAAA", "23456789"[:6], "ZZZZZZ"):
            assert all(c in CODE_ALPHABET for c in tail)
            assert as_company_code("CO" + tail) == "CO" + tail


class TestAskingWhichShopWithOnlyOne:
    async def test_the_answer_is_the_shop_not_a_contact_card(self):
        client = FakeDataClient(permission_keys=["customer.read", "ticket.create", "ticket.read"], role="customer")
        reply = await handle_chat_message(
            client, message="ตอนนี้ผูกกับร้านไหน", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads({"action": "read", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert "บริษัททดสอบ" in reply.text, reply.text
        assert "ติดต่อ" not in reply.text.split("\n")[0], reply.text

    async def test_asking_to_add_one_says_what_a_customer_types(self):
        client = FakeDataClient(permission_keys=["customer.read", "ticket.create", "ticket.read"], role="customer")
        reply = await handle_chat_message(
            client, message="ลงทะเบียนอีกร้าน", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads({"action": "switch", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert "รหัสร้าน" in reply.text and "รหัสเชิญ" not in reply.text, reply.text
        assert "S/N" in reply.text, reply.text
        assert [w for w in client.recorded if w[0] == "set_pending_intent"], client.recorded


class LinkingShop(FakeDataClient):
    """A shop the customer is not with yet."""

    SECOND = "22222222-2222-2222-2222-222222222222"

    async def link_customer(self, chann_uid, company_code):
        self.recorded.append(("link_customer", chann_uid, company_code))
        return {"license_id": self.SECOND, "company_name": "ร้านทดสอบ"}

    async def my_shops(self, chann_uid):
        return [{"license_id": self.SECOND, "license_code": "COV9URCZ", "company_name": "ร้านทดสอบ"}]

    async def search_shops(self, query):
        return [{"license_id": self.SECOND, "company_code": "COV9URCZ", "company_name": "ร้านทดสอบ"}]


class TestLinkingTheSecondShop:
    def _customer(self):
        return LinkingShop(permission_keys=["customer.read", "ticket.create", "ticket.read"], role="customer")

    async def test_a_shop_code_links_even_when_already_with_a_shop(self):
        client = self._customer()
        reply = await handle_chat_message(
            client, message="COV9URCZ", ctx=_ctx(oa="customer", primary_role="customer"),
            ai_client=_reads({"action": "read", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert [w for w in client.recorded if w[0] == "link_customer"], reply.text
        assert "ร้านทดสอบ" in reply.text, reply.text
        # Round 19n: the shop just typed becomes the one being talked to.
        assert [w for w in client.recorded if w[0] == "set_active_tenant"], client.recorded

    async def test_the_shop_named_after_the_question_links(self):
        client = self._customer()
        ctx = _ctx(oa="customer", primary_role="customer")
        await client.set_pending_intent(
            ctx.chann_uid, "customer", action="link", entity="link_another_shop",
            fields={}, missing=["shop"], ttl_seconds=600,
        )
        reply = await handle_chat_message(
            client, message="ร้านทดสอบ", ctx=ctx,
            ai_client=_reads({"action": "read", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert [w for w in client.recorded if w[0] == "link_customer"], reply.text

    async def test_a_code_for_the_shop_they_are_already_with_says_so(self):
        from chann_app.services.identity import ResolvedContext, TenantResolution

        client = self._customer()
        ctx = ResolvedContext(
            chann_uid="CHN-S-000001", primary_role="customer", display_name="ลูกค้า",
            resolution=TenantResolution.SINGLE, oa="customer",
            memberships=[{
                "license_id": "11111111-1111-1111-1111-111111111111", "license_code": "COABCDEF",
                "company_name": "บริษัททดสอบ", "chann_uid": "CHN-S-000001",
                "role": "customer", "status": "active",
            }],
        )
        reply = await handle_chat_message(
            client, message="COABCDEF", ctx=ctx,
            ai_client=_reads({"action": "read", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert not [w for w in client.recorded if w[0] == "link_customer"], client.recorded
        assert "บริษัททดสอบ" in reply.text and "อยู่แล้ว" in reply.text, reply.text
