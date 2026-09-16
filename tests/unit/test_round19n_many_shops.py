"""Round 19n — a customer who deals with more than one shop.

The machinery for several shops has existed since 3 ก.ย. (a chooser in
chat, a stored choice per OA, a switcher on the screens). What was missing
is what happens when the person's ATTENTION is in one shop and the thing
they are answering about lives in another:

* shop B pushes "รายงาน SR-2026-0007 อนุมัติแล้ว", the customer replies
  about it, and the reply lands in shop A — where that report does not
  exist, so they are told it does not exist;
* the screens pick memberships[0] when nothing has been chosen, so a
  customer of two shops silently opens one of them.

The record decides which shop answers, and only among the person's OWN
shops: nothing here reads a tenant they are not a member of.
"""
from __future__ import annotations

import pytest

from chann_app.config import settings
from chann_app.services.identity import ResolvedContext, TenantResolution
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, LICENSE_ID
from test_round18e_followups import _reads

pytestmark = pytest.mark.asyncio

SHOP_A = LICENSE_ID
SHOP_B = "22222222-2222-2222-2222-222222222222"
ME = "CHN-S-000001"
CUSTOMER_KEYS = ["customer.read", "ticket.create", "ticket.read", "warranty.read", "warranty.create"]

#: What the person is scoped by, rather than a shop: the pending form, the
#: stored shop choice, the identity itself. One store, or a form opened in
#: shop A would be invisible the moment the record pulled us into shop B.
PERSON_SCOPED = frozenset({
    "get_pending_intent", "set_pending_intent", "clear_pending_intent",
    "get_active_tenant", "set_active_tenant", "resolve_identity",
    "get_last_entity_ref", "set_last_entity_ref", "get_last_customer_ref",
    "set_last_customer_ref", "memberships_of", "get_display_preferences",
    "consent_state", "record_consent", "get_profile", "update_profile",
    "identity_signature", "set_identity_signature", "remember_turn",
    "recent_turns", "get_customer_identity",
})


class TwoShops:
    """One LINE account, two shops, one fake per shop.

    A call carrying a license id goes to that shop's fake; anything scoped
    to the person goes to the first, which both share. Written as a
    delegating wrapper rather than a second fake so each shop behaves
    exactly like the one every other test uses.
    """

    def __init__(self, shop_a: FakeDataClient, shop_b: FakeDataClient):
        self._by_id = {str(SHOP_A): shop_a, str(SHOP_B): shop_b}
        self._person = shop_a

    @property
    def a(self) -> FakeDataClient:
        return self._by_id[str(SHOP_A)]

    @property
    def b(self) -> FakeDataClient:
        return self._by_id[str(SHOP_B)]

    @property
    def recorded(self) -> list:
        return [*self.a.recorded, *self.b.recorded]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args, **kwargs):
            target = self._person
            if name not in PERSON_SCOPED:
                given = kwargs.get("license_id")
                if given is None and args:
                    given = args[0]
                if str(given) in self._by_id:
                    target = self._by_id[str(given)]
            return await getattr(target, name)(*args, **kwargs)

        return call


def _shop(license_id: str, name: str) -> dict:
    return {
        "license_id": license_id, "license_code": "COA" if license_id == SHOP_A else "COB",
        "company_name": name, "chann_uid": ME, "role": "customer", "status": "active",
    }


def _in_shop_a() -> ResolvedContext:
    """Chose shop A; shop B is the alternative."""
    return ResolvedContext(
        chann_uid=ME, primary_role="customer", display_name="ลูกค้า",
        resolution=TenantResolution.SINGLE, memberships=[_shop(SHOP_A, "ร้าน ก")],
        oa="customer", alternatives=[_shop(SHOP_B, "ร้าน ข")],
    )


def _two_shops() -> TwoShops:
    a = FakeDataClient(permission_keys=CUSTOMER_KEYS, role="customer")
    b = FakeDataClient(permission_keys=CUSTOMER_KEYS, role="customer")
    b._tickets = [{
        "id": "t-b", "ticket_number": "T-2026-0007", "status": "assigned",
        "accept_status": "accepted", "customer_chann_uid": ME, "visibility": "public",
        "customer_name": "ลูกค้า", "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
        "scheduled_date": "2026-09-20", "scheduled_time": "13:00",
    }]
    return TwoShops(a, b)


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


READ_TICKET = {"action": "read", "entity": "ticket", "fields": {"code": "T-2026-0007"}, "missing": []}


class TestTheRecordDecidesWhichShopAnswers:
    """Round 19p: it ASKS. Owner, 16 ก.ย. 2569: "ถ้าจะสลับต้องถามผู้ใช้ให้
    ยืนยันก่อน และเตรียมกรณีโดนขัดจังหวะระหว่างถามด้วย" — which shop a
    message lands in decides whose records are read and written."""

    async def test_a_job_from_the_other_shop_is_offered_not_taken(self):
        client = _two_shops()
        reply = await handle_chat_message(
            client, message="งาน T-2026-0007 ถึงไหนแล้วครับ", ctx=_in_shop_a(),
            ai_client=_reads(READ_TICKET),
        )
        assert "ร้าน ข" in reply.text and "T-2026-0007" in reply.text, reply.text
        assert "ไหม" in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "set_active_tenant"], client.recorded
        assert any(label.startswith("สลับไป") for label, _send in reply.quick_replies), reply.quick_replies

    async def test_saying_yes_switches_and_answers_what_was_asked(self):
        client = _two_shops()
        ctx = _in_shop_a()
        await handle_chat_message(
            client, message="งาน T-2026-0007 ถึงไหนแล้วครับ", ctx=ctx, ai_client=_reads(READ_TICKET),
        )
        reply = await handle_chat_message(client, message="ใช่", ctx=ctx, ai_client=_reads(READ_TICKET))
        stored = [w for w in client.recorded if w[0] == "set_active_tenant"]
        assert stored and str(stored[-1][3]) == SHOP_B, client.recorded
        # The sentence they already typed is answered in the shop that holds it.
        assert "T-2026-0007" in reply.text, reply.text

    async def test_saying_no_keeps_the_shop_they_are_in(self):
        client = _two_shops()
        ctx = _in_shop_a()
        await handle_chat_message(
            client, message="งาน T-2026-0007 ถึงไหนแล้วครับ", ctx=ctx, ai_client=_reads(READ_TICKET),
        )
        reply = await handle_chat_message(client, message="ไม่ต้อง", ctx=ctx, ai_client=_reads(READ_TICKET))
        assert not [w for w in client.recorded if w[0] == "set_active_tenant"], client.recorded
        assert "ร้าน ก" in reply.text, reply.text

    async def test_an_interruption_drops_the_question_and_is_answered_here(self):
        client = _two_shops()
        ctx = _in_shop_a()
        await handle_chat_message(
            client, message="งาน T-2026-0007 ถึงไหนแล้วครับ", ctx=ctx, ai_client=_reads(READ_TICKET),
        )
        reply = await handle_chat_message(
            client, message="ข้อมูลของฉัน", ctx=ctx,
            ai_client=_reads({"action": "read", "entity": "profile", "fields": {}, "missing": []}),
        )
        assert not [w for w in client.recorded if w[0] == "set_active_tenant"], client.recorded
        assert "ข้อมูลของคุณ" in reply.text, reply.text
        assert [w for w in client.recorded if w[0] == "clear_pending_intent"], client.recorded

    async def test_a_job_of_the_shop_we_are_in_does_not_move_anything(self):
        client = _two_shops()
        client.a._tickets = [{
            "id": "t-a", "ticket_number": "T-2026-0001", "status": "assigned",
            "accept_status": "accepted", "customer_chann_uid": ME, "visibility": "public",
            "customer_name": "ลูกค้า", "issue_description": "ตู้เย็นเสีย", "service_address": "1/1",
        }]
        reply = await handle_chat_message(
            client, message="งาน T-2026-0001 ถึงไหนแล้วครับ", ctx=_in_shop_a(),
            ai_client=_reads({"action": "read", "entity": "ticket",
                              "fields": {"code": "T-2026-0001"}, "missing": []}),
        )
        assert "ร้าน ข" not in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "set_active_tenant"]

    async def test_a_code_no_shop_of_theirs_holds_stays_where_it_is(self):
        client = _two_shops()
        reply = await handle_chat_message(
            client, message="งาน T-2026-9999 ถึงไหนแล้วครับ", ctx=_in_shop_a(),
            ai_client=_reads({"action": "read", "entity": "ticket",
                              "fields": {"code": "T-2026-9999"}, "missing": []}),
        )
        assert "ร้าน ข" not in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "set_active_tenant"]

    async def test_an_ordinary_sentence_never_looks_at_the_other_shop(self):
        client = _two_shops()
        await handle_chat_message(
            client, message="สวัสดีครับ", ctx=_in_shop_a(),
            ai_client=_reads({"action": "read", "entity": "ticket", "fields": {}, "missing": []}),
        )
        # Nothing was read out of shop B: the sentence named no record.
        assert not client.b.recorded, client.b.recorded


class TestKnowingWhichShopsYouAreWith:
    async def test_the_list_marks_the_one_being_talked_to(self):
        client = _two_shops()
        reply = await handle_chat_message(
            client, message="ผมอยู่กับร้านไหนบ้าง", ctx=_in_shop_a(),
            ai_client=_reads({"action": "switch", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert "ร้าน ก (คุยอยู่ตอนนี้)" in reply.text, reply.text
        assert "ร้าน ข" in reply.text, reply.text
        assert ("ร้าน ข", "ใช้ร้าน COB") in reply.quick_replies, reply.quick_replies

    async def test_the_profile_card_names_the_other_shop_too(self):
        client = _two_shops()
        reply = await handle_chat_message(
            client, message="ข้อมูลของฉัน", ctx=_in_shop_a(),
            ai_client=_reads({"action": "read", "entity": "profile", "fields": {}, "missing": []}),
        )
        assert "ร้าน ก (คุยอยู่ตอนนี้)" in reply.text, reply.text
        assert "ร้าน ข" in reply.text, reply.text

    async def test_one_shop_is_named_without_a_chooser(self):
        client = _two_shops()
        alone = ResolvedContext(
            chann_uid=ME, primary_role="customer", display_name="ลูกค้า",
            resolution=TenantResolution.SINGLE, memberships=[_shop(SHOP_A, "ร้าน ก")],
            oa="customer", alternatives=[],
        )
        reply = await handle_chat_message(
            client, message="ผมอยู่กับร้านไหนบ้าง", ctx=alone,
            ai_client=_reads({"action": "switch", "entity": "shop", "fields": {}, "missing": []}),
        )
        assert "ร้าน ก" in reply.text, reply.text
        # Round 19p: with one shop the answer also says how to add another.
        assert ("ผูกอีกร้าน", "ผูกอีกร้าน") in reply.quick_replies, reply.quick_replies


class TestLinkingASecondShop:
    """Typing a shop's code is choosing that shop: the customer who links a
    second one is plainly talking to the one they just typed."""

    async def test_the_shop_just_linked_becomes_the_active_one(self):
        from chann_app.services import registration

        class Linking(FakeDataClient):
            async def link_customer(self, chann_uid, company_code):
                self.recorded.append(("link_customer", chann_uid, company_code))
                return {"license_id": SHOP_B, "company_name": "ร้าน ข"}

            async def my_shops(self, chann_uid):
                return [
                    {"license_id": SHOP_A, "license_code": "COA", "company_name": "ร้าน ก"},
                    {"license_id": SHOP_B, "license_code": "COB", "company_name": "ร้าน ข"},
                ]

        client = Linking(permission_keys=CUSTOMER_KEYS, role="customer")
        await registration._link_and_continue(
            client, _in_shop_a(), company_code="COB", company_name="ร้าน ข", language="th",
        )
        stored = [w for w in client.recorded if w[0] == "set_active_tenant"]
        assert stored and str(stored[-1][3]) == SHOP_B, client.recorded


class TestTheRelayDoesNotSwallowARecord:
    """The line after "ติดต่อร้าน" goes to the shop as a chat line — unless
    it names a job, which is an instruction about that job. Found by
    converse against the real model: turn 1 opened the contact prompt and
    turn 2's "งาน T-2026-0007 ถึงไหนแล้ว" was relayed instead of answered."""

    async def test_a_ticket_code_is_answered_not_relayed(self):
        client = _two_shops()
        await client.set_pending_intent(
            ME, "customer", action="contact", entity="customer_contact",
            fields={}, missing=["message"], ttl_seconds=600,
        )
        reply = await handle_chat_message(
            client, message="งาน T-2026-0007 ถึงไหนแล้วครับ", ctx=_in_shop_a(),
            ai_client=_reads(READ_TICKET),
        )
        assert "T-2026-0007" in reply.text, reply.text
        assert not [w for w in client.recorded if w[0] == "add_chat_message"], client.recorded
