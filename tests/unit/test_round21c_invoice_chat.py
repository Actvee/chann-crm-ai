"""Round 21C — the chat side of editing, settling and sending a bill.

Three sentences the owner asked for on 23 ก.ย. 2569, each measured
against the deployed model before a line of this was written
(`scripts/dev/ask-model.py`, readings in the task-9 report):

  "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-… เป็น 3 ตัว"  — correct a bill
  "รับชำระ INV-… ครบ"                              — and say the deal closed
  "ส่งใบแจ้งหนี้ INV-… ให้ลูกค้า"                    — hand it over on LINE

The collision this round resolves: "ส่งใบเสร็จให้ลูกค้า INV-…" read as
action="receipt" (MAKE the receipt) because the prompt listed it as an
example of that action. "ออกใบเสร็จ" makes it; "ส่งใบเสร็จ" gives it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402


# --------------------------------------------------------------- the prompt


class TestThePromptTeachesSending:
    def test_send_is_an_action_of_its_own(self):
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        assert 'action="send"' in INTENT_SYSTEM_PROMPT
        assert "ส่งใบแจ้งหนี้" in INTENT_SYSTEM_PROMPT

    def test_the_prompt_teaches_a_change_apart_from_a_quantity(self):
        # Review finding 2: the invoice block taught only `qty`, so the
        # model answered "เพิ่มแอร์อีก 2 ตัว" with qty:2 — a SET that cut
        # the bill. The line_item block has taught the distinction since
        # round 18; the invoice block now says it too.
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        block = INTENT_SYSTEM_PROMPT.split('action="update": CORRECT a bill')[1].split('action="send"')[0]
        assert "qty_change" in block
        assert "อีก" in block

    def test_making_a_receipt_and_giving_one_are_told_apart(self):
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        # The receipt example that caused the collision is gone from
        # action="receipt"; "ออกใบเสร็จ" is what makes one.
        block = INTENT_SYSTEM_PROMPT.split('action="receipt"')[1].split('action="update"')[0]
        assert "ส่งใบเสร็จให้ลูกค้า" not in block
        assert "ออกใบเสร็จ" in block

    def test_both_send_capabilities_are_registered(self):
        assert chat.ACTION_PERMISSIONS[("send", "quote")] == "quote.update"
        assert chat.ACTION_PERMISSIONS[("send", "invoice")] == "invoice.update"

    def test_sending_a_document_can_be_declined(self):
        # A word may decline but may not act: "ยังไม่ต้องส่งให้ลูกค้า" must
        # reach the guard, so the pair needs a vocabulary of its own.
        from chann_app.services.intent_guard import intent_to_act

        assert chat._AI_GUARDED[("invoice", "send")] == "document_send"
        assert chat._AI_GUARDED[("quote", "send")] == "document_send"
        assert "document_send" in chat._GUARD_ACTIONS
        assert intent_to_act(
            "ยังไม่ต้องส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า", action="document_send").outcome == "hold"
        assert intent_to_act(
            "ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า", action="document_send").outcome == "act"

    def test_correcting_a_bill_can_be_declined_too(self):
        # The guard runs BEFORE the dispatcher splits `update` into an edit
        # and a payment, so its vocabulary has to cover both — otherwise
        # "ยังไม่ต้องแก้ใบแจ้งหนี้ INV-… นะ" has nothing to negate and the
        # edit goes through (the defect this entry exists to stop).
        from chann_app.services.intent_guard import ACTION_WORDS, intent_to_act

        assert chat._AI_GUARDED[("invoice", "update")] == "invoice_edit"
        assert "แก้" in ACTION_WORDS["invoice_edit"] and "รับชำระ" in ACTION_WORDS["invoice_edit"]
        assert intent_to_act(
            "ยังไม่ต้องแก้ใบแจ้งหนี้ INV-2026-0001 นะ", action="invoice_edit").outcome == "hold"
        assert intent_to_act(
            "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว",
            action="invoice_edit").outcome == "act"


# --------------------------------------------------------------- the words


class TestTheRefusalsAreSaidInWords:
    def test_a_locked_invoice_is_explained_not_just_refused(self):
        said = chat._t(chat.LINE_INVOICE_LOCKED, "th").format(code="INV-2026-0001")
        assert "INV-2026-0001" in said
        assert "ยกเลิก" in said          # the road out is named

    def test_a_void_invoice_says_so_in_its_own_words(self):
        said = chat._t(chat.LINE_INVOICE_VOID, "th").format(code="INV-2026-0001")
        assert "ยกเลิกแล้ว" in said and "แก้ไม่ได้" in said

    def test_an_unlinked_customer_is_named_and_the_way_to_fix_it_is_said(self):
        said = chat._t(chat.DOCUMENT_SEND_NO_LINE, "th").format(name="สมหญิง ร่ำรวย")
        assert "สมหญิง ร่ำรวย" in said
        assert "ไลน์" in said

    def test_the_settle_sentence_names_the_deal_and_the_value(self):
        said = chat._t(chat.INVOICE_SETTLED_DEAL, "th").format(
            deal="D-2026-0007", amount="25,000.00")
        # Ruling 23: the deal's value is pre-VAT, and the sentence says so.
        assert "D-2026-0007" in said and "25,000.00" in said and "ก่อน VAT" in said

    def test_nothing_claims_a_document_was_sent_twice(self):
        # Ruling 15: `resent` is always False today (a Data-tier filter
        # hides the rows it would be read from), so no reply may word
        # itself as "ส่งซ้ำ" from it.
        for table in (chat.DOCUMENT_SENT, chat.DOCUMENT_SEND_NO_LINE, chat.DOCUMENT_SEND_NOT_ISSUED):
            for language in ("th", "en"):
                assert "ส่งซ้ำ" not in chat._t(table, language)
                assert "again" not in chat._t(table, language).lower()


# ----------------------------------------------------------- the lines after


class TestTheLinesAfterTheChange:
    LINES = [
        {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
         "unit_price": "15000.00", "line_total": "30000.00", "notes": None},
        {"line_no": 2, "product_name": "ท่อทองแดง", "qty": 1,
         "unit_price": "500.00", "line_total": "500.00", "notes": None},
    ]

    def _invoice(self):
        return {"invoice_id": "INV-2026-0001", "data_snapshot": {"line_items": list(self.LINES)}}

    def test_a_named_product_has_its_quantity_set(self):
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์", "qty": 3},
            "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว")
        assert [(l["product_name"], l["qty"]) for l in out] == [("แอร์ 12000 BTU", 3), ("ท่อทองแดง", 1)]

    def test_a_delta_adds_to_what_is_there_it_does_not_replace_it(self):
        """Review finding 2: "เพิ่มแอร์อีก 2 ตัว" on a bill holding 2 means
        4, not 2. The module's own header records this exact defect on the
        deal road (owner's live test, 8 ก.ย.); the bill road had it too."""
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์", "qty_change": 2},
            "เพิ่มแอร์อีก 2 ตัวในใบแจ้งหนี้ INV-2026-0001")
        assert [l["qty"] for l in out] == [4, 1]

    def test_a_negative_delta_takes_away(self):
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์", "qty_change": -1},
            "ลดแอร์ในใบแจ้งหนี้ INV-2026-0001 ลง 1 ตัว")
        assert [l["qty"] for l in out] == [1, 1]

    def test_a_delta_that_would_empty_a_line_is_not_guessed_at(self):
        # Taking the last one off is a removal, which is its own verb —
        # quietly billing zero of something is not the same request.
        assert chat._invoice_lines_after(
            self._invoice(), {"target_name": "ท่อทองแดง", "qty_change": -1},
            "ลดท่อทองแดงลง 1") is None

    def test_a_set_still_sets(self):
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์", "qty": 3},
            "ใบแจ้งหนี้ INV-2026-0001 แอร์เป็น 3 ตัว")
        assert [l["qty"] for l in out] == [3, 1]

    def test_a_delta_wins_over_a_quantity_the_model_also_sent(self):
        # If the model says both, the CHANGE is the more specific reading —
        # it only ever appears when the sentence said "อีก"/"ลง".
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์", "qty": 2, "qty_change": 2},
            "เพิ่มแอร์อีก 2 ตัว")
        assert [l["qty"] for l in out] == [4, 1]

    def test_neither_a_quantity_nor_a_price_asks(self):
        assert chat._invoice_lines_after(
            self._invoice(), {"target_name": "แอร์"}, "เอาแอร์ออกจากใบแจ้งหนี้") is None

    def test_a_line_number_names_the_line_when_no_product_is_said(self):
        out = chat._invoice_lines_after(self._invoice(), {"line_no": 2, "qty": 4}, "แก้รายการที่ 2 เป็น 4")
        assert [l["qty"] for l in out] == [2, 4]

    def test_a_price_change_lands_on_the_named_line(self):
        out = chat._invoice_lines_after(
            self._invoice(), {"target_name": "ท่อทองแดง", "unit_price": "600"}, "ลดราคาท่อทองแดงเหลือ 600")
        assert out[1]["unit_price"] == "600"

    def test_a_detail_only_sentence_changes_no_line(self):
        assert chat._invoice_lines_after(
            self._invoice(), {"due_date": "2026-09-30"},
            "ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน") is None

    def test_a_price_that_is_not_a_price_is_refused_here_not_by_the_tier(self):
        # nan and inf are legal Decimals — nan raised out of the handler
        # entirely and inf was written onto the bill (re-review, minor).
        for bad in ("-100", "abc", "๑๒ บาท", "nan", "Infinity", "-Infinity"):
            assert chat._invoice_lines_after(
                self._invoice(), {"target_name": "แอร์", "unit_price": bad},
                f"เปลี่ยนราคาแอร์เป็น {bad}") is None

    def test_a_delta_alone_still_reaches_the_edit_road(self):
        # `qty_change` has to be an edit FIELD too, or a delta with no
        # product name falls through to the payment road and asks
        # "เท่าไหร่ครับ" about a sentence that named no money.
        assert chat._is_an_invoice_correction({"code": "INV-2026-0001", "qty_change": 2}, "")

    def test_a_product_that_is_not_on_the_bill_changes_nothing(self):
        assert chat._invoice_lines_after(
            self._invoice(), {"target_name": "พัดลม", "qty": 3}, "เปลี่ยนจำนวนพัดลมเป็น 3") is None


# ------------------------------------------------------------- the handlers


def _seeded(permission_keys=("invoice.read", "invoice.update", "quote.update", "deal.read")):
    client = FakeDataClient(role="sales", permission_keys=list(permission_keys))
    # A VAT-registered shop: `edit_lines` recomputes from the COMPANY's
    # rate, so the fixture has to say what the shop is registered as.
    client._company_profile.update({
        "vat_rate": "0.07", "tax_id": "0105558123456", "company_address": "99/1",
        "legal_name": "บริษัท ทดสอบ จำกัด", "company_phone": "021234567",
        "company_email": "a@b.com",
    })
    client._customers = [
        {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
         "last_name": "ใจดี", "customer_chann_uid": "CHN-CUST-1", "stage": "customer"},
        {"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง",
         "last_name": "ร่ำรวย", "customer_chann_uid": None, "stage": "customer"},
    ]
    client._invoices = [{
        "id": "INV-1", "license_id": str(LICENSE_ID), "invoice_id": "INV-2026-0001",
        "quote_id": None, "deal_id": "DEAL-1", "contact_id": "CUST-1", "status": "issued",
        "issue_date": "2026-09-21", "due_date": "2026-10-21", "currency": "THB",
        "subtotal": "30000.00", "discount_amount": "0", "vat_rate": "0.07",
        "vat_amount": "2100.00", "total": "32100.00", "paid_amount": "0.00",
        "note": None, "generated_document_id": "GD-1", "receipt_document_id": None,
        "created_by": None, "archived_at": None, "payments": [],
        "created_at": "2026-09-21T00:00:00+00:00", "updated_at": "2026-09-21T00:00:00+00:00",
        "data_snapshot": {"line_items": [
            {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
             "unit_price": "15000.00", "line_total": "30000.00", "notes": None},
        ]},
    }]
    return client


class TestCorrectingABill:
    @pytest.mark.asyncio
    async def test_the_quantity_changes_and_the_money_is_recomputed(self):
        client = _seeded()
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว",
            permission_keys=["invoice.update"], language="th")
        assert "48,150.00" in reply.text          # 3 × 15,000 + 7% VAT, on the card
        await _confirm(client)                    # I6: a money change is asked first
        assert client._invoices[0]["total"] == "48150.00"

    @pytest.mark.asyncio
    async def test_a_bill_whose_pdf_was_made_says_to_issue_it_again(self):
        client = _seeded()
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์เป็น 3 ตัว",
            permission_keys=["invoice.update"], language="th")
        reply = await _confirm(client)
        assert "ออกเอกสาร" in reply.text

    @pytest.mark.asyncio
    async def test_the_due_date_the_model_read_is_saved(self):
        client = _seeded()
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "due_date": "2026-09-30"},
            message="ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน",
            permission_keys=["invoice.update"], language="th")
        assert client._invoices[0]["due_date"] == "2026-09-30"
        assert "30 ก.ย." in reply.text or "2026-09-30" in reply.text

    @pytest.mark.asyncio
    async def test_a_sentence_that_changes_both_does_both(self):
        # Two routes, one reply: doing only the lines and still saying
        # "แก้ใบแจ้งหนี้แล้ว" would report a due date that never moved.
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3,
                    "due_date": "2026-09-30"},
            message="เปลี่ยนจำนวนแอร์เป็น 3 ตัว และเลื่อนกำหนดชำระเป็นสิ้นเดือน",
            permission_keys=["invoice.update"], language="th")
        assert client._invoices[0]["due_date"] != "2026-09-30"   # nothing before the tap
        await _confirm(client)
        assert client._invoices[0]["total"] == "48150.00"
        assert client._invoices[0]["due_date"] == "2026-09-30"

    @pytest.mark.asyncio
    async def test_a_bill_code_is_never_mistaken_for_a_due_date(self):
        # The field is read, never the sentence: a date parser loose on
        # "ใบแจ้งหนี้ INV-2026-0001" is how a bill acquires a due date
        # nobody typed.
        assert chat._due_date_field("") is None
        assert chat._due_date_field(None) is None
        assert str(chat._due_date_field("2026-09-30")) == "2026-09-30"

    @pytest.mark.asyncio
    async def test_a_paid_bill_refuses_and_names_the_road_out(self):
        client = _seeded()
        client._invoices[0]["status"] = "paid"
        client._invoices[0]["paid_amount"] = "32100.00"
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "due_date": "2026-09-30"},
            message="เปลี่ยนกำหนดชำระ", permission_keys=["invoice.update"], language="th")
        assert "แก้รายการไม่ได้" in reply.text and "ยกเลิก" in reply.text

    @pytest.mark.asyncio
    async def test_a_void_bill_says_it_was_cancelled_not_that_it_was_paid(self):
        client = _seeded()
        client._invoices[0]["status"] = "void"
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "due_date": "2026-09-30"},
            message="เปลี่ยนกำหนดชำระ", permission_keys=["invoice.update"], language="th")
        assert "ยกเลิกแล้ว" in reply.text and "รับชำระ" not in reply.text

    @pytest.mark.asyncio
    async def test_without_the_permission_it_is_refused(self):
        client = _seeded()
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "qty": 3, "target_name": "แอร์"},
            message="เปลี่ยนจำนวนแอร์เป็น 3", permission_keys=["invoice.read"], language="th")
        assert reply.text == chat._t(chat.SUGGEST_NO_PERMISSION_LEAD, "th")
        assert client._invoices[0]["total"] == "32100.00"


class TestHandingTheDocumentOver:
    @pytest.mark.asyncio
    async def test_a_linked_customer_is_told_the_bill_was_sent(self):
        client = _seeded()
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "invoice"},
            permission_keys=["invoice.update"], language="th")
        assert "สมชาย" in reply.text and "ไลน์" in reply.text
        assert "ส่งซ้ำ" not in reply.text
        assert reply.intent == {"action": "send", "entity": "invoice"}

    @pytest.mark.asyncio
    async def test_a_customer_with_no_line_is_refused_by_name(self):
        client = _seeded()
        client._invoices[0]["contact_id"] = "CUST-2"
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001"},
            permission_keys=["invoice.update"], language="th")
        assert "สมหญิง ร่ำรวย" in reply.text and "ไลน์" in reply.text

    @pytest.mark.asyncio
    async def test_a_bill_with_no_pdf_says_to_issue_it_first(self):
        client = _seeded()
        client._invoices[0]["generated_document_id"] = None
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001"},
            permission_keys=["invoice.update"], language="th")
        assert "ออกเอกสาร" in reply.text

    @pytest.mark.asyncio
    async def test_a_receipt_is_sent_as_the_receipt_not_the_bill(self):
        client = _seeded()
        client._invoices[0]["receipt_document_id"] = "GD-R"
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "receipt"},
            permission_keys=["invoice.update"], language="th")
        assert "ใบเสร็จ" in reply.text

    @pytest.mark.asyncio
    async def test_the_models_kind_wins_over_a_word_in_the_sentence(self):
        """Review finding 4: the word was OR-ed over the field, so a
        sentence that MENTIONS ใบเสร็จ while asking for the invoice was
        refused — and the mirror case sends the customer the wrong
        document, which cannot be taken back."""
        client = _seeded()
        client._invoices[0]["receipt_document_id"] = None      # no receipt exists
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "invoice"},
            permission_keys=["invoice.update"], language="th",
            message="ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า ไม่ใช่ใบเสร็จนะ")
        assert "ยังไม่มีอะไรให้ส่ง" not in reply.text
        assert "ใบแจ้งหนี้" in reply.text and "สมชาย" in reply.text

    @pytest.mark.asyncio
    async def test_the_mirror_case_does_not_send_the_wrong_document(self):
        client = _seeded()
        client._invoices[0]["receipt_document_id"] = "GD-R"
        await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "receipt"},
            permission_keys=["invoice.update"], language="th",
            message="ส่งใบเสร็จ INV-2026-0001 ให้ลูกค้า")
        pushed = [r for r in client.recorded if r[0] == "create_notification"]
        assert pushed and pushed[-1][3] == "receipt_issued"

    @pytest.mark.asyncio
    async def test_the_word_is_read_only_when_the_model_named_no_kind(self):
        client = _seeded()
        client._invoices[0]["receipt_document_id"] = "GD-R"
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001"},          # the model named none
            permission_keys=["invoice.update"], language="th",
            message="ส่งใบเสร็จให้ลูกค้า INV-2026-0001")
        assert "ใบเสร็จ" in reply.text

    @pytest.mark.asyncio
    async def test_a_receipt_that_is_not_made_yet_names_the_right_road(self):
        # Minor finding: "ออกเอกสาร" re-issues the INVOICE PDF and leaves
        # the send failing the same way; the receipt's road is ออกใบเสร็จ.
        client = _seeded()
        client._invoices[0]["receipt_document_id"] = None
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "receipt"},
            permission_keys=["invoice.update"], language="th",
            message="ส่งใบเสร็จ INV-2026-0001 ให้ลูกค้า")
        assert "ออกใบเสร็จ" in reply.text
        assert 'ออกเอกสาร' not in reply.text

    @pytest.mark.asyncio
    async def test_without_the_permission_nothing_is_pushed(self):
        client = _seeded()
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001"},
            permission_keys=["invoice.read"], language="th")
        assert reply.text == chat._t(chat.SUGGEST_NO_PERMISSION_LEAD, "th")
        assert not [r for r in client.recorded if r[0] == "create_notification"]


class TestHandingTheQuotationOver:
    """Review finding 5: `("send","quote")` was registered, parity-accepted
    and reachable, and no test or scenario executed one line of it —
    `_handle_quote_intent`'s send branch, `_quote_by_code`, `_quote_parties`
    and `KIND_WORDS["quote"]` were all untravelled. This repo has paid for
    that before: a helper on an untravelled branch is a NameError in
    production with a green suite."""

    def _with_quote(self, *, document="GD-Q1", contact="CUST-1"):
        client = _seeded()
        client._deals = [{"id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": contact,
                          "stage": "quoted", "amount": "32100.00", "products": []}]
        client._quotes = [{"id": "QUOTE-1", "license_id": str(LICENSE_ID),
                           "quote_id": "Q-2026-0001", "deal_id": "DEAL-1", "status": "sent",
                           "generated_document_id": document, "owner_member_id": None,
                           "total": "32100.00"}]
        return client

    @pytest.mark.asyncio
    async def test_an_issued_quotation_goes_to_a_linked_customer(self):
        client = self._with_quote()
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read", "quote.update"],
            message="ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้าทางไลน์")
        assert "ใบเสนอราคา" in reply.text
        assert "Q-2026-0001" in reply.text
        assert "สมชาย" in reply.text and "ไลน์" in reply.text
        assert reply.intent == {"action": "send", "entity": "quote"}
        pushed = [r for r in client.recorded if r[0] == "create_notification"]
        assert pushed and pushed[-1][3] == "document_sent"

    @pytest.mark.asyncio
    async def test_a_customer_with_no_line_is_refused_by_name(self):
        client = self._with_quote(contact="CUST-2")
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read", "quote.update"],
            message="ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า")
        assert "สมหญิง ร่ำรวย" in reply.text and "ยังไม่ได้ผูกไลน์" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_notification"]

    @pytest.mark.asyncio
    async def test_a_quotation_with_no_pdf_says_to_issue_it_first(self):
        client = self._with_quote(document=None)
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read", "quote.update"],
            message="ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า")
        assert "ออกเอกสาร" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_notification"]

    @pytest.mark.asyncio
    async def test_a_quotation_nobody_has_is_not_found_not_an_error(self):
        client = self._with_quote()
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-9999"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read", "quote.update"],
            message="ส่งใบเสนอราคา Q-2026-9999 ให้ลูกค้า")
        assert "Q-2026-9999" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_notification"]

    @pytest.mark.asyncio
    async def test_quote_update_is_the_permission_that_is_checked(self):
        client = self._with_quote()
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read"],          # can look, cannot hand over
            message="ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า")
        assert reply.text == chat._t(chat.SUGGEST_NO_PERMISSION_LEAD, "th")
        assert not [r for r in client.recorded if r[0] == "create_notification"]

    @pytest.mark.asyncio
    async def test_the_quotes_own_parties_are_reached_through_its_deal(self):
        # _quote_parties has no contact of its own to read; if the walk
        # through the deal breaks, the refusal would name nobody.
        client = self._with_quote()
        customer, company = await chat._quote_parties(
            client, LICENSE_ID, client._quotes[0])
        assert customer.get("first_name") == "สมชาย"
        assert company.get("company_name")


class TestALineOfARaisedBillIsNotTheBill:
    """Re-review, ruling 19 — the guard must key on WHAT THE MODEL NAMED,
    not on the verb.

    Measured 23 ก.ย. 2569 (scripts/dev/ask-model.py), verbatim:

      ยกเลิกแอร์ในใบแจ้งหนี้ INV-2026-0003
         {"action": "update", "entity": "invoice",
          "fields": {"code": …, "target_name": "แอร์", "qty": 0}}
      เอาแอร์ออกจากใบแจ้งหนี้ INV-2026-0003
         {"action": "update", "entity": "invoice",
          "fields": {"code": …, "target_name": "แอร์"}}
      ยกเลิกรายการแอร์ใน INV-2026-0003
         {"action": "delete", "entity": "line_item",
          "fields": {"code": …, "target_name": "แอร์"}}
      ยกเลิกใบแจ้งหนี้ INV-2026-0003
         {"action": "void", "entity": "invoice", "fields": {"code": …}}

    Round 1 guarded `create/delete/add/remove`, so the first two — the ones
    a person actually types — fell through to "ยังไม่รู้ว่าจะแก้อะไร …
    เช่น เปลี่ยนจำนวนแอร์เป็น 3 ตัว": a question about setting a quantity
    in answer to a request to take the line off.
    """

    async def _guard(self, client, intent, message="", language="th"):
        return await chat._invoice_line_sentence_reply(
            client, LICENSE_ID, intent, language, message=message)

    @pytest.mark.asyncio
    async def test_the_measured_remove_reading_is_answered_honestly(self):
        client = _seeded()
        reply = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}})
        assert reply is not None
        assert "แอร์" in reply.text and "INV-2026-0001" in reply.text
        assert "ยกเลิกใบนี้แล้วออกใบใหม่" in reply.text
        assert "ยังไม่รู้ว่าจะแก้อะไร" not in reply.text

    @pytest.mark.asyncio
    async def test_the_measured_cancel_a_line_reading_is_answered_honestly(self):
        # "ยกเลิกแอร์…" -> update with qty 0. Round 1 answered it by
        # suggesting a quantity.
        client = _seeded()
        reply = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty": 0}})
        assert reply is not None and "แอร์" in reply.text

    @pytest.mark.asyncio
    async def test_a_product_the_bill_does_not_carry_cannot_be_added(self):
        client = _seeded()
        reply = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "พัดลม", "qty": 2}})
        assert reply is not None and "พัดลม" in reply.text

    @pytest.mark.asyncio
    async def test_the_measured_delete_line_item_reading_is_answered_honestly(self):
        client = _seeded()
        reply = await self._guard(client, {
            "action": "delete", "entity": "invoice",      # after _entity_by_code
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}})
        assert reply is not None and "แอร์" in reply.text

    @pytest.mark.asyncio
    async def test_a_void_that_names_a_product_never_reaches_the_cancel_card(self):
        """Not a reading the deployed model was observed to give — the
        measured "ยกเลิกแอร์…" is `update` — but the ruling is to guard on
        what was NAMED, for any verb. `("void","invoice")` is registered
        and passes the gate, so a verb-keyed guard leaves this open."""
        client = _seeded()
        reply = await self._guard(client, {
            "action": "void", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}})
        assert reply is not None
        assert "ยืนยันยกเลิก" not in reply.text

    @pytest.mark.asyncio
    async def test_the_real_void_of_the_whole_bill_is_untouched(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "void", "entity": "invoice",
            "fields": {"code": "INV-2026-0001"}}) is None

    @pytest.mark.asyncio
    async def test_a_quantity_change_on_a_line_that_is_there_still_runs(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3}}) is None

    @pytest.mark.asyncio
    async def test_a_delta_on_a_line_that_is_there_still_runs(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty_change": 2}}) is None

    @pytest.mark.asyncio
    async def test_a_price_change_on_a_line_that_is_there_still_runs(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "unit_price": "16000"}}) is None

    @pytest.mark.asyncio
    async def test_a_draft_is_told_the_truth_about_itself(self):
        """A draft has not been raised, so "ออกไปแล้ว … ยกเลิกแล้วออกใหม่"
        would be false advice. Its lines come from the deal."""
        client = _seeded()
        client._invoices[0]["status"] = "draft"
        reply = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}})
        assert reply is not None
        assert "ออกไปแล้ว" not in reply.text
        assert "ดีล" in reply.text

    @pytest.mark.asyncio
    async def test_the_guard_reads_the_model_never_the_sentence(self):
        # The same reading with a sentence that says the opposite must give
        # the same answer: words may decline, they may not decide.
        client = _seeded()
        a = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}},
            message="เปลี่ยนจำนวนแอร์เป็น 5 ตัว เพิ่มพัดลมด้วย")
        b = await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}}, message="")
        assert a is not None and b is not None and a.text == b.text

    @pytest.mark.asyncio
    async def test_a_sentence_with_no_invoice_code_is_not_this_guards_business(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "create", "entity": "invoice",
            "fields": {"target_name": "สมชาย"}}) is None
        assert await self._guard(client, {
            "action": "create", "entity": "line_item",
            "fields": {"code": "D-2026-0001", "target_name": "พัดลม", "qty": 2}}) is None

    @pytest.mark.asyncio
    async def test_an_invoice_nobody_has_is_left_to_the_not_found_road(self):
        client = _seeded()
        assert await self._guard(client, {
            "action": "update", "entity": "invoice",
            "fields": {"code": "INV-2026-9999", "target_name": "แอร์"}}) is None


class TestTheWholeRoadForALineSentence:
    """Through `_execute_intent` — the real entry point, so the permission
    gate, `_entity_by_code` and the dispatcher are all in the picture.

    The guard lives before the gate and nowhere else (ruling 19.3), so a
    test that called `_handle_invoice_intent` directly would prove nothing
    about what a person actually gets: the round-1 scenario caught exactly
    that, when a unit test passed while the gate refused the sentence first.
    """

    KEYS = ["invoice.read", "invoice.create", "invoice.update", "invoice.void",
            "deal.read", "deal.update", "quote.read", "quote.update"]

    async def _say(self, client, intent, message):
        return await chat._execute_intent(
            client, intent=intent, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            message=message, permission_keys=list(self.KEYS), language="th")

    @pytest.mark.asyncio
    async def test_the_measured_remove_reading_never_writes_and_never_voids(self):
        client = _seeded()
        reply = await self._say(
            client, {"action": "update", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}},
            "เอาแอร์ออกจากใบแจ้งหนี้ INV-2026-0001")
        assert "ยกเลิกใบนี้แล้วออกใบใหม่" in reply.text
        assert not [r for r in client.recorded if r[0].startswith("update_invoice")]
        assert not [r for r in client.recorded if r[0] == "void_invoice"]

    @pytest.mark.asyncio
    async def test_the_measured_delete_line_item_reading_stops_at_the_guard(self):
        """"ยกเลิกรายการแอร์ใน INV-…" is read as delete/line_item;
        `_entity_by_code` normalises the entity and the guard catches it
        before `("delete","invoice")` can reach the void road."""
        client = _seeded()
        reply = await self._say(
            client, {"action": "delete", "entity": "line_item",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}},
            "ยกเลิกรายการแอร์ใน INV-2026-0001")
        assert "แอร์" in reply.text and "ยกเลิกใบนี้แล้วออกใบใหม่" in reply.text
        assert not [r for r in client.recorded if r[0] == "void_invoice"]

    @pytest.mark.asyncio
    async def test_a_void_naming_a_product_never_shows_the_cancel_card(self):
        client = _seeded()
        reply = await self._say(
            client, {"action": "void", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์"}},
            "ยกเลิกแอร์ในใบแจ้งหนี้ INV-2026-0001")
        assert "ยืนยันยกเลิก" not in [q[0] for q in (reply.quick_replies or [])]
        assert not [r for r in client.recorded if r[0] == "void_invoice"]

    @pytest.mark.asyncio
    async def test_the_measured_void_of_the_whole_bill_still_reaches_the_card(self):
        client = _seeded()
        reply = await self._say(
            client, {"action": "void", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001"}},
            "ยกเลิกใบแจ้งหนี้ INV-2026-0001")
        assert "ยืนยันยกเลิก" in [q[0] for q in (reply.quick_replies or [])]

    @pytest.mark.asyncio
    async def test_adding_a_line_never_raises_a_second_invoice(self):
        client = _seeded()
        before = len(client._invoices)
        reply = await self._say(
            client, {"action": "update", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "พัดลม", "qty": 2}},
            "ใส่พัดลม 2 ตัวในใบแจ้งหนี้ INV-2026-0001")
        assert "พัดลม" in reply.text
        assert len(client._invoices) == before
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    @pytest.mark.asyncio
    async def test_a_quantity_change_still_edits_through_the_whole_road(self):
        client = _seeded()
        await self._say(
            client, {"action": "update", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3}},
            "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว")
        await _confirm(client)
        assert client._invoices[0]["total"] == "48150.00"

    @pytest.mark.asyncio
    async def test_the_bill_is_looked_up_once_for_the_guard_and_the_edit(self, monkeypatch):
        """The guard before the gate found the bill, and the edit handler
        used to find it again: two round trips for one sentence. The
        answer and the write must not move — only the second lookup goes."""
        from chann_app.services import invoices as invoice_service

        real, asked = invoice_service.find_by_code, []

        async def counted(client, license_id, code):
            asked.append(code)
            return await real(client, license_id, code)

        monkeypatch.setattr(invoice_service, "find_by_code", counted)
        client = _seeded()
        reply = await self._say(
            client, {"action": "update", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3}},
            "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว")
        assert "48,150.00" in reply.text
        assert asked == ["INV-2026-0001"]
        await _confirm(client)
        assert client._invoices[0]["total"] == "48150.00"

    @pytest.mark.asyncio
    async def test_a_bill_the_guard_did_not_find_is_still_asked_for_by_the_edit(self, monkeypatch):
        """Only a FOUND bill is carried: a lookup that failed is asked again,
        so a passing blip never becomes a "not found" the tier did not say."""
        from chann_app.services import invoices as invoice_service

        real, asked = invoice_service.find_by_code, []

        async def first_one_fails(client, license_id, code):
            asked.append(code)
            if len(asked) == 1:
                return None
            return await real(client, license_id, code)

        monkeypatch.setattr(invoice_service, "find_by_code", first_one_fails)
        client = _seeded()
        await self._say(
            client, {"action": "update", "entity": "invoice",
                     "fields": {"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3}},
            "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว")
        assert asked == ["INV-2026-0001", "INV-2026-0001"]
        await _confirm(client)
        assert client._invoices[0]["total"] == "48150.00"

    @pytest.mark.asyncio
    async def test_a_deal_line_is_not_handed_the_invoice_refusal(self):
        # The guard reads the code the MODEL filed; a message-wide search
        # would catch a deal's own line edit.
        client = _seeded()
        client._deals = [{"id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
                          "stage": "new", "amount": None, "products": [
                              {"id": "dp-1", "product_name": "พัดลม", "qty": 1,
                               "quoted_unit_price": "1000.00"}]}]
        reply = await self._say(
            client, {"action": "update", "entity": "line_item",
                     "fields": {"code": "D-2026-0001", "target_name": "พัดลม", "qty": 2}},
            "เปลี่ยนจำนวนพัดลมในดีล D-2026-0001 เป็น 2 เหมือนใน INV-2026-0001")
        assert "ยกเลิกใบนี้แล้วออกใบใหม่" not in reply.text

    @pytest.mark.asyncio
    async def test_the_plain_create_road_is_untouched(self):
        client = _seeded()
        reply = await self._say(
            client, {"action": "create", "entity": "invoice",
                     "fields": {"target_name": "สมชาย"}},
            "ออกใบแจ้งหนี้ให้ สมชาย")
        assert "ยกเลิกใบนี้แล้วออกใบใหม่" not in reply.text


class TestTheSentenceWhenTheBillSettles:
    @pytest.mark.asyncio
    async def test_paying_in_full_says_the_deal_closed_and_what_it_is_worth(self):
        client = _seeded()
        client._deals = [{
            "id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
            "stage": "quoted", "amount": "30000.00", "products": [],
        }]
        reply = await chat._handle_invoice_payment(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "full": True}, message="รับชำระ INV-2026-0001 ครบ",
            permission_keys=["invoice.update"], language="th")
        assert "ปิดสำเร็จ" in reply.text
        assert "D-2026-0001" in reply.text and "32,100.00" in reply.text

    @pytest.mark.asyncio
    async def test_a_part_payment_says_nothing_about_the_deal(self):
        client = _seeded()
        client._deals = [{
            "id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
            "stage": "quoted", "amount": "30000.00", "products": [],
        }]
        reply = await chat._handle_invoice_payment(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "amount": 2000}, message="มัดจำ INV-2026-0001 2000",
            permission_keys=["invoice.update"], language="th")
        assert "ปิดสำเร็จ" not in reply.text

    @pytest.mark.asyncio
    async def test_a_second_bill_on_an_already_won_deal_claims_nothing(self):
        """Review finding 1, reproduced: the deal was ALREADY won and worth
        9,999.00; this second bill of 32,100.00 is paid in full and closes
        nothing. The old read-back saw "won + closed_at" and announced a
        close that never happened, with a value from the wrong bill."""
        client = _seeded()
        client._deals = [{
            "id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
            "stage": "won", "closed_at": "2026-09-01T00:00:00+00:00",
            "amount": "9999.00", "products": [],
        }]
        reply = await chat._handle_invoice_payment(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "full": True}, message="รับชำระ INV-2026-0001 ครบ",
            permission_keys=["invoice.update"], language="th")
        assert "ชำระครบ" in reply.text
        assert "ปิดสำเร็จ" not in reply.text
        assert "9,999.00" not in reply.text
        assert client._deals[0]["amount"] == "9999.00"   # nothing was written

    @pytest.mark.asyncio
    async def test_the_sentence_comes_only_from_the_payload(self):
        """No `closed_deal` on the reply means no sentence — chat never
        infers a close from the invoice's status, and never reads the deal
        back to guess. The Data tier is the only thing that knows."""
        client = _seeded()
        client._deals = [{
            "id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
            "stage": "won", "closed_at": "2026-09-01T00:00:00+00:00",
            "amount": "9999.00", "products": [],
        }]
        client._suppress_closed_deal = True      # the tier stays silent
        reply = await chat._handle_invoice_payment(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "full": True}, message="รับชำระ INV-2026-0001 ครบ",
            permission_keys=["invoice.update"], language="th")
        assert "ปิดสำเร็จ" not in reply.text
        # and it did not go looking: no read-back of the deal
        assert not [r for r in client.recorded if r[0] == "get_deal"]

    @pytest.mark.asyncio
    async def test_a_deal_that_did_not_close_is_not_claimed_to_have(self):
        # `close_won_from_invoice` refuses a lost deal, so the Data tier
        # reports no `closed_deal` and the reply cannot say something that
        # did not happen.
        client = _seeded()
        client._deals = [{
            "id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
            "stage": "lost", "amount": "30000.00", "products": [],
        }]
        reply = await chat._handle_invoice_payment(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "full": True}, message="รับชำระ INV-2026-0001 ครบ",
            permission_keys=["invoice.update"], language="th")
        assert "ชำระครบ" in reply.text and "ปิดสำเร็จ" not in reply.text


class TestTheModelsReadingReachesTheRightHandler:
    @pytest.mark.asyncio
    async def test_an_inv_code_on_a_line_item_is_the_invoices_line(self):
        # Measured 23 ก.ย. 2569: "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0003
        # เป็น 3 ตัว" comes back entity="line_item" with an INV- code. The
        # line-item road knows only D- and Q-, so without this the sentence
        # fell off the end.
        renamed = chat._entity_by_code({
            "action": "update", "entity": "line_item",
            "fields": {"code": "INV-2026-0003", "target_name": "แอร์", "qty": 3},
        })
        assert renamed["entity"] == "invoice" and renamed["action"] == "update"
        assert renamed["fields"]["target_name"] == "แอร์"

    def test_the_code_renames_the_entity_and_leaves_the_action_alone(self):
        """Review finding 3: the prefix normalisation is sound — a
        system-generated code cannot be misread — but rewriting the model's
        ACTION to "update" turned an add into a set and a delete into
        something the edit road answers as "what should change?"."""
        for action in ("create", "update", "delete"):
            renamed = chat._entity_by_code({
                "action": action, "entity": "line_item",
                "fields": {"code": "INV-2026-0003", "target_name": "แอร์", "qty": 3},
            })
            assert renamed["entity"] == "invoice"
            assert renamed["action"] == action, f"{action} was relabelled {renamed['action']}"
            assert renamed["fields"]["target_name"] == "แอร์"

    def test_a_deal_or_quote_code_on_a_line_is_left_where_it_is(self):
        for code in ("D-2026-0001", "Q-2026-0001"):
            kept = chat._entity_by_code({
                "action": "create", "entity": "line_item", "fields": {"code": code}})
            assert kept["entity"] == "line_item" and kept["action"] == "create"

    @pytest.mark.asyncio
    async def test_a_plain_new_invoice_is_still_created(self):
        # The guard must not cost the ordinary road: no line fields, no guard.
        client = _seeded()
        client._deals = [{"id": "DEAL-9", "deal_id": "D-2026-0009", "contact_id": "CUST-1",
                          "stage": "new", "amount": None, "products": [
                              {"product_name": "แอร์", "qty": 1, "quoted_unit_price": "1000.00"}]}]
        await chat._handle_invoice_intent(
            client, intent={"action": "create", "entity": "invoice",
                            "fields": {"deal_code": "D-2026-0009"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.create", "invoice.update"],
            message="ออกใบแจ้งหนี้ให้ดีล D-2026-0009")
        assert [r for r in client.recorded if r[0] == "create_invoice"]

    @pytest.mark.asyncio
    async def test_voiding_the_bill_itself_still_works(self):
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "void", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update", "invoice.void"],
            message="ยกเลิกใบแจ้งหนี้ INV-2026-0001")
        assert "ยกเลิก" in reply.text

    @pytest.mark.asyncio
    async def test_an_update_that_carries_an_amount_is_still_a_payment(self):
        # "มัดจำ INV-… 2000" came back as update with deposit_amount
        # (21 ก.ย. 2569) and must keep reaching the payment road.
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "update", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001", "deposit_amount": 2000}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"], message="มัดจำ INV-2026-0001 2000")
        assert "2,000.00" in reply.text

    @pytest.mark.asyncio
    async def test_an_update_that_carries_a_due_date_is_an_edit(self):
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "update", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001", "due_date": "2026-09-30"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"],
            message="ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน")
        assert client._invoices[0]["due_date"] == "2026-09-30"

    @pytest.mark.asyncio
    async def test_an_update_with_only_a_code_asks_what_to_change(self):
        # Measured 23 ก.ย. 2569: "แก้รายการในใบแจ้งหนี้ INV-…" comes back
        # as update with nothing but the code. Asking beats guessing, and
        # beats the payment road's "เท่าไหร่ครับ" — nobody said money.
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "update", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"],
            message="แก้รายการในใบแจ้งหนี้ INV-2026-0001")
        assert "เท่าไหร่" not in reply.text
        assert "INV-2026-0001" in reply.text
        assert not [r for r in client.recorded if r[0].startswith("update_invoice")]

    @pytest.mark.asyncio
    async def test_the_reissue_the_edit_advises_reaches_the_issue_handler(self):
        # A reply that tells the shop to type something has to be a reply
        # whose advice works. The render itself needs SmartBrowz, so what
        # is proved here is the routing: "ออกเอกสาร INV-… ใหม่" reaches
        # the invoice's own issue road, allowing a re-issue.
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "issue", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"],
            message="ออกเอกสาร INV-2026-0001 ใหม่")
        # Not "that is not something I can do", and not the receipt road.
        assert "ยังทำรายการนี้ไม่ได้" not in reply.text
        assert not [r for r in client.recorded if r[0] == "set_invoice_receipt_document"]

    @pytest.mark.asyncio
    async def test_send_on_an_invoice_reaches_the_send_handler(self):
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "send", "entity": "invoice",
                            "fields": {"code": "INV-2026-0001", "kind": "invoice"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"],
            message="ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า")
        assert "สมชาย" in reply.text and "ไลน์" in reply.text


# ------------------------------------------ final review C1 (23 ก.ย. 2569)


class TestAStaleOrCancelledDocumentIsNotHandedOver:
    """Chat answers the same refusals as the dashboard: a void bill, a bill
    corrected after its PDF was made, a rejected or expired quotation."""

    def _pushed(self, client):
        return [r for r in client.recorded if r[0] == "create_notification"]

    @pytest.mark.asyncio
    async def test_a_void_bill_is_not_sent(self):
        client = _seeded()
        client._invoices[0]["status"] = "void"
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "invoice"},
            permission_keys=["invoice.update"], language="th")
        assert "ใบนี้ยกเลิกแล้ว" in reply.text
        assert not self._pushed(client)

    @pytest.mark.asyncio
    async def test_an_edited_bill_says_to_issue_it_again_first(self):
        client = _seeded()
        client._invoices[0]["data_snapshot"]["needs_reissue_at"] = "2026-09-23T03:00:00+00:00"
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, entity="invoice",
            fields={"code": "INV-2026-0001", "kind": "invoice"},
            permission_keys=["invoice.update"], language="th")
        assert "แก้ไขแล้ว ต้องออกเอกสารใหม่ก่อน" in reply.text
        assert "ออกเอกสาร INV-2026-0001" in reply.text
        assert reply.quick_replies and reply.quick_replies[0][1] == "ออกเอกสาร INV-2026-0001"
        assert not self._pushed(client)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["rejected", "expired"])
    async def test_a_closed_quotation_is_not_sent(self, status):
        client = TestHandingTheQuotationOver()._with_quote()
        client._quotes[0]["status"] = status
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote",
                            "fields": {"code": "Q-2026-0001"}},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["quote.read", "quote.update"],
            message="ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า")
        assert "Q-2026-0001" in reply.text and "ส่งให้ลูกค้าไม่ได้" in reply.text
        assert not self._pushed(client)


# ------------------------------------------ final review I6 (24 ก.ย. 2569)
#
# Readings, the deployed model's own (ask-model.py, google/gemini-3.1-flash-lite,
# 24 ก.ย. 2569):
#   "ลดราคา 1000 ในใบแจ้งหนี้ INV-2026-0003"
#       → update invoice {code, unit_price_change: -1000}
#   "ลดราคาแอร์ในใบแจ้งหนี้ INV-2026-0003 เหลือ 14000"
#       → update invoice {code, target_name: แอร์, unit_price: 14000}
#   "ลดราคาให้ 1000 บาท INV-2026-0003"
#       → update invoice {code, discount_amount: 1000}


async def _confirm(client, word="ยืนยันแก้", permission_keys=("invoice.update",)):
    pending = client._pending
    assert pending and pending["entity"] == "invoice_edit_confirm", pending
    return await chat._resolve_invoice_edit_confirm(
        client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID, message=word, pending=pending,
        permission_keys=list(permission_keys), language="th")


class TestAMoneyCorrectionIsAskedFirst:
    @pytest.mark.asyncio
    async def test_the_card_shows_the_new_total_and_writes_nothing(self):
        client = _seeded()
        reply = await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว",
            permission_keys=["invoice.update"], language="th")
        assert "48,150.00" in reply.text and "32,100.00" in reply.text
        assert ("ยืนยันแก้", "ยืนยันแก้") in reply.quick_replies
        assert client._invoices[0]["total"] == "32100.00"
        assert not [r for r in client.recorded if r[0] == "update_invoice_lines"]

    @pytest.mark.asyncio
    async def test_confirming_saves_exactly_what_the_card_showed(self):
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "unit_price": 14000},
            message="ลดราคาแอร์ในใบแจ้งหนี้ INV-2026-0001 เหลือ 14000",
            permission_keys=["invoice.update"], language="th")
        reply = await _confirm(client)
        assert client._invoices[0]["total"] == "29960.00"        # 2 × 14,000 + 7%
        assert "29,960.00" in reply.text and "ออกเอกสาร" in reply.text

    @pytest.mark.asyncio
    async def test_declining_keeps_the_bill(self):
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์เป็น 3 ตัว", permission_keys=["invoice.update"], language="th")
        reply = await _confirm(client, word="ไม่แก้")
        assert "เหมือนเดิม" in reply.text
        assert client._invoices[0]["total"] == "32100.00"

    @pytest.mark.asyncio
    async def test_a_bill_that_moved_in_between_is_not_overwritten(self):
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์เป็น 3 ตัว", permission_keys=["invoice.update"], language="th")
        client._invoices[0]["total"] = "99999.00"
        reply = await _confirm(client)
        assert "เปลี่ยนไปแล้ว" in reply.text
        assert client._invoices[0]["total"] == "99999.00"

    @pytest.mark.asyncio
    async def test_the_word_reaches_the_resolver_not_the_model(self):
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "target_name": "แอร์", "qty": 3},
            message="เปลี่ยนจำนวนแอร์เป็น 3 ตัว", permission_keys=["invoice.update"], language="th")
        reply = await chat.handle_chat_message(
            client, message="ยืนยันแก้", ctx=_ctx(oa="sales"), language="th")
        assert client._invoices[0]["total"] == "48150.00", reply.text

    @pytest.mark.asyncio
    async def test_a_due_date_alone_is_not_money_and_is_saved_at_once(self):
        client = _seeded()
        await chat._handle_invoice_edit(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"code": "INV-2026-0001", "due_date": "2026-09-30"},
            message="เปลี่ยนกำหนดชำระเป็นสิ้นเดือน",
            permission_keys=["invoice.update"], language="th")
        assert client._invoices[0]["due_date"] == "2026-09-30"


class TestAnAmountOffIsAskedNeverGuessed:
    """"ลดราคา 1000" on a ONE-line bill must not become that line's unit
    price — the single-line fallback makes any price reading land there."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fields,message", [
        ({"code": "INV-2026-0001", "unit_price_change": -1000}, "ลดราคา 1000 ในใบแจ้งหนี้ INV-2026-0001"),
        ({"code": "INV-2026-0001", "discount_amount": 1000}, "ลดราคาให้ 1000 บาท INV-2026-0001"),
    ])
    async def test_the_measured_readings_ask_and_write_nothing(self, fields, message):
        client = _seeded()
        reply = await chat._handle_invoice_intent(
            client, intent={"action": "update", "entity": "invoice", "fields": fields},
            ctx=_ctx(oa="sales"), license_id=LICENSE_ID, language="th",
            permission_keys=["invoice.read", "invoice.update"], message=message,
        )
        assert "แบบไหน" in reply.text and "เปลี่ยนราคาแอร์" in reply.text, reply.text
        assert client._invoices[0]["total"] == "32100.00"
        assert client._invoices[0]["data_snapshot"]["line_items"][0]["unit_price"] == "15000.00"
        assert not [r for r in client.recorded if r[0] in ("update_invoice_lines", "add_invoice_payment")]
        assert (client._pending or {}).get("entity") != "invoice_edit_confirm"
