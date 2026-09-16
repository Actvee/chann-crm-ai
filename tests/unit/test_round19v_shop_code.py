"""Round 19v — the code a shop hands out is the code that links.

Owner's transcript, 16 ก.ย. 2569, 19:59 on the customer LINE:

    M.A.C      COV9URCZ
    CS(Dev)    ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง
    M.A.C      COV9URCZ
    CS(Dev)    ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง
    M.A.C      ร้านทดสอบ
    CS(Dev)    ผูกกับร้าน "ร้านทดสอบ" เรียบร้อย

The shop's own card had printed "รหัสร้าน: COV9URCZ" for that same shop,
under a footer telling the shop to give that code to customers. Two
faults, one symptom: `as_company_code` only accepted the eight-letter
shape (so "COV9URCZ" never reached the lookup), and the lookup only ever
matched `company_code` while the card advertised `license_code`.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.services.chat import handle_chat_message
from chann_app.services.registration import as_company_code


class TestTheCodeAShopHandsOut:
    """`as_company_code` is the gate in front of the lookup: what it drops,
    the Data tier never sees."""

    @pytest.mark.parametrize("typed", ["COV9URCZ", "cov9urcz", " COV9URCZ ", "COV9-URCZ"])
    def test_the_code_from_the_owners_transcript_is_accepted(self, typed):
        assert as_company_code(typed) == "COV9URCZ"

    def test_the_other_code_is_not_read_out_of_a_bare_sentence(self):
        """`company_code` is eight characters of the same alphabet — and so
        is the word "WARRANTY". A shape ordinary words have cannot link a
        shop on sight; it counts only where a code was asked for."""
        from chann_app.services.registration import as_shop_code_when_asked

        assert as_company_code("ABCD2345") == ""
        assert as_company_code("warranty") == ""
        assert as_shop_code_when_asked("ABCD2345") == "ABCD2345"
        assert as_shop_code_when_asked("abcd2345") == "ABCD2345"
        assert as_shop_code_when_asked("COV9URCZ") == "COV9URCZ"
        assert as_shop_code_when_asked("SN12345678") == ""

    def test_a_zero_typed_for_the_letter_o_is_forgiven(self):
        """CO… is generated from an alphabet with no O and no 0, so a leading
        "C0" can only be someone reading the letter as a digit."""
        assert as_company_code("C0V9URCZ") == "COV9URCZ"

    @pytest.mark.parametrize("typed", ["SN12345678", "ABC", "", "ร้านทดสอบ", "CO12345", "warranty"])
    def test_things_that_are_not_a_code_are_still_dropped(self, typed):
        assert as_company_code(typed) == ""


@pytest.mark.asyncio
class TestTheShopCardNamesBothCodes:
    async def _card(self):
        from test_phase6_chat import FakeDataClient, _ai, _ctx

        client = FakeDataClient(permission_keys=["customer.read", "deal.read"])
        client._company = {"legal_name": "บริษัท ทดสอบ จำกัด", "tax_id": "0105558012345"}
        transport = _ai(json.dumps(
            {"action": "suggest", "entity": None, "fields": {}, "missing": []}
        ))
        return await handle_chat_message(
            client, message="ข้อมูลร้าน", ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=transport),
        )

    async def test_the_customer_code_is_on_the_card_and_labelled(self):
        text = (await self._card()).text
        assert "รหัสสำหรับลูกค้า: TESTCUST" in text, text
        assert len(text.split("\n")) <= 15, "a LINE bubble this long is not read"

    async def test_the_shop_code_is_still_there_for_the_administrator(self):
        text = (await self._card()).text
        assert "รหัสร้าน: TESTCO" in text, text
        assert "แจ้งรหัสร้าน TESTCO" in text, text

    async def test_the_footer_points_at_the_customer_code(self):
        text = (await self._card()).text
        assert 'ลูกค้าพิมพ์ "รหัสสำหรับลูกค้า"' in text, text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_both_languages_of_the_footer_are_strings(self):
        """The English value was a one-tuple — a stray trailing comma — so the
        card's concatenation raised for any English speaker."""
        from chann_app.services.chat import SHOP_CARD_FOOT, SHOP_CARD_HEAD

        for table in (SHOP_CARD_FOOT, SHOP_CARD_HEAD):
            for language, value in table.items():
                assert isinstance(value, str), (language, value)


class TestTheLongerWordForShop:
    """20:00 in the same transcript: "ร้านค้าของฉัน" answered with the contact
    card. "ร้านของฉัน" was in the table; the fuller spelling was not."""

    @pytest.mark.parametrize("asked", [
        "ร้านค้าของฉัน", "ร้านของฉัน", "ร้านค้าไหนบ้าง", "ผูกกับร้านค้าไหน", "ร้านค้าที่ผูก",
    ])
    def test_it_is_read_as_the_question_about_shops(self, asked):
        from chann_app.services.chat import _asks_which_shops

        assert _asks_which_shops(asked), asked

    @pytest.mark.parametrize("asked", ["ร้านเปิดกี่โมง", "ขอที่อยู่ร้าน", "สวัสดี"])
    def test_other_sentences_about_a_shop_are_not(self, asked):
        from chann_app.services.chat import _asks_which_shops

        assert not _asks_which_shops(asked), asked


class TwoShopsAfterLinking:
    """A customer who was already with one shop and has just linked a second."""

    FIRST = "11111111-1111-1111-1111-111111111111"
    SECOND = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
class TestWhatLinkingTheSecondShopSays:
    """Owner, 16 ก.ย. 2569, right after "ผูกกับร้าน ร้านทดสอบ เรียบร้อย":
    "หลังจากผูกเสร็จแล้วจะดูรายการร้านค้าที่ผูก แต่ระบบตอบไม่ถูกต้อง และยังไม่รู้
    ว่าจะสลับร้านไปมายังไง"."""

    def _client(self, shops_after):
        from test_phase6_chat import FakeDataClient

        class _Linking(FakeDataClient):
            async def link_customer(self, chann_uid, company_code):
                self.recorded.append(("link_customer", chann_uid, company_code))
                return {"license_id": TwoShopsAfterLinking.SECOND, "company_name": "ร้านทดสอบ"}

            async def my_shops(self, chann_uid):
                return shops_after

        return _Linking(
            permission_keys=["customer.read", "ticket.create", "ticket.read"], role="customer",
        )

    async def _link(self, shops_after):
        from test_phase6_chat import _ctx
        from chann_app.services.registration import _link_and_continue

        client = self._client(shops_after)
        return await _link_and_continue(
            client, _ctx(oa="customer", primary_role="customer"),
            company_code="COV9URCZ", company_name="ร้านทดสอบ", language="th",
            license_id=TwoShopsAfterLinking.SECOND,
        )

    async def test_the_second_shop_names_the_words_for_the_list_and_the_switch(self):
        text = await self._link([
            {"license_id": TwoShopsAfterLinking.FIRST, "company_name": "ร้านแรก"},
            {"license_id": TwoShopsAfterLinking.SECOND, "company_name": "ร้านทดสอบ"},
        ])
        assert "2 ร้าน" in text, text
        assert "ร้านค้าของฉัน" in text and "เปลี่ยนร้าน" in text, text
        assert "ร้านทดสอบ" in text, text

    async def test_the_first_shop_says_nothing_about_switching(self):
        """One shop is not a choice, and the sentence would only confuse."""
        text = await self._link([
            {"license_id": TwoShopsAfterLinking.SECOND, "company_name": "ร้านทดสอบ"},
        ])
        assert "เปลี่ยนร้าน" not in text, text
        assert "ผูกกับร้าน" in text, text


@pytest.mark.asyncio
class TestTypingTheCodeTheListPrinted:
    """A customer who is not with any shop searches by name, gets
    "• ร้านสมชาย — ABCD2345  · พิมพ์รหัสร้านเพื่อผูก", and types it. That is
    a `company_code`, which the serial lookup answers "ไม่พบหมายเลข … ในระบบ"
    to — the reply asked for the one thing it then refused (round 19v)."""

    async def _say(self, text, client):
        from test_phase65_registration import _ctx
        from chann_app.services.registration import handle_registration

        return await handle_registration(client, message=text, ctx=_ctx(), audience="customer")

    def _client(self):
        from test_phase65_registration import FakeRegClient

        # known_codes makes the fake answer the way the Data tier does: a
        # code no licence has is refused, not linked.
        return FakeRegClient(known_codes={"ABCD2345"})

    async def test_the_listed_code_links(self):
        client = self._client()
        reply = await self._say("ABCD2345", client)
        assert "ผูกกับร้าน" in reply, reply
        assert "link_customer" in client.calls, client.calls

    async def test_an_ordinary_word_of_the_same_shape_is_not_a_code(self):
        """"WARRANTY" is eight letters of the code alphabet."""
        client = self._client()
        reply = await self._say("warranty", client)
        assert "ผูกกับร้าน" not in reply, reply
        assert "ไม่พบรหัสนี้" not in reply, reply
