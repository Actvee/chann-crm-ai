"""Round 21E fix round 4 — "คำสั่งชำระเงินแล้วของ invoice ไม่มีความยืดหยุ่น".

Owner, DEV, 24 ก.ย. 2569. Measured with the deployed model (report, Fix
round 4): every sentence that names the bill already reads as pay; with the
card in the recent turns (be055c8) the code-less "ชำระแล้ว" / "จ่ายแล้ว"
read as pay on that bill. What stayed rigid was the handler:

- the "เท่าไหร่ครับ" answer was read by hand only — "จ่ายแล้ว",
  "ลูกค้าโอนมาหมดแล้ว", "เต็ม" answered "อ่านจำนวนเงินไม่ออก";
- update {status:"paid"} asked for an amount;
- a paid statement with no invoice anywhere offered no invoice to pick;
- a second "จ่ายแล้ว" answered "รับชำระไม่ได้ครับ".

Controller rulings: the pending answer checks cancel words FIRST (they only
decline — the model reads "ยกเลิก" as voiding the bill), then a plain number
(and the "ครบ" button the question itself offers), and gives anything else
to the MODEL with the pending context; the code from the sentence or the
recent turns records at once, the code known only from the hour-long
last-viewed memory asks once; an unstated method stays โอนเงิน.
The readings below are the model's own (paymeasure, 24 ก.ย. 2569).
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

KEYS = ["invoice.read", "invoice.update", "invoice.create", "customer.read"]


def _bill(n: int, total: str) -> dict:
    return {
        "id": f"INV-{n}", "license_id": str(LICENSE_ID), "invoice_id": f"INV-2026-000{n}",
        "quote_id": None, "deal_id": None, "contact_id": "CUST-1", "status": "issued",
        "issue_date": f"2026-09-2{n}", "due_date": "2026-10-20", "currency": "THB",
        "subtotal": total, "discount_amount": "0", "vat_rate": "0", "vat_amount": "0",
        "total": total, "paid_amount": "0.00", "note": None,
        "data_snapshot": {"customer": {"name": "สมชาย ใจดี"},
                          "line_items": [{"product_name": "แอร์", "qty": 1, "unit_price": total}]},
        "generated_document_id": f"DOC-{n}", "receipt_document_id": None,
        "created_by": "CHN-S-000001", "archived_at": None,
        "created_at": f"2026-09-2{n}T00:00:00+00:00", "updated_at": f"2026-09-2{n}T00:00:00+00:00",
        "payments": [],
    }


def _shop(pending=None):
    client = FakeDataClient(role="sales", permission_keys=list(KEYS), pending_intent=pending)
    client._customers = [{"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
                          "last_name": "ใจดี", "stage": "customer"}]
    client._invoices = [_bill(1, "5350.00"), _bill(2, "32100.00"), _bill(3, "12000.00")]
    return client


def _paid(client):
    return [r for r in client.recorded if r[0] == "add_invoice_payment"]


def _ctx_():
    return _ctx(oa="sales", license_id=LICENSE_ID)


async def _pay(client, fields, message="ชำระแล้ว", action="pay"):
    return await chat._handle_invoice_intent(
        client, intent={"action": action, "entity": "invoice", "fields": fields},
        ctx=_ctx_(), license_id=LICENSE_ID, language="th", permission_keys=KEYS, message=message)


# ----------------------------------------------------------- the model's field


class TestWhatTheModelSaidIsRecorded:
    @pytest.mark.asyncio
    async def test_paid_in_full_records_the_balance_with_todays_reply(self):
        # Measured: "ชำระแล้ว" after viewing INV-0002 -> pay {code, full:true}.
        client = _shop()
        reply = await _pay(client, {"code": "INV-2026-0002", "full": True})
        assert _paid(client) and _paid(client)[0][3]["amount"] in ("32100.00", "32100")
        assert "บันทึกรับชำระ INV-2026-0002 32,100.00" in reply.text and "ชำระครบ" in reply.text
        assert ("ออกใบเสร็จ", "ออกใบเสร็จ INV-2026-0002") in reply.quick_replies

    @pytest.mark.asyncio
    async def test_status_paid_is_paid_in_full_not_a_question(self):
        # Measured (no context): "เปลี่ยนสถานะเป็นชำระแล้ว" -> update {status:"paid"}.
        client = _shop()
        reply = await _pay(client, {"code": "INV-2026-0002", "status": "paid"},
                           "เปลี่ยนสถานะ INV-2026-0002 เป็นชำระแล้ว", action="update")
        assert "เท่าไหร่" not in reply.text
        assert len(_paid(client)) == 1 and "ชำระครบ" in reply.text

    @pytest.mark.asyncio
    async def test_a_stated_amount_beats_full(self):
        # Measured (no context): "ลูกค้าจ่ายเงินมาแล้ว 32,100 บาท" -> {amount:32100, full:true}.
        # The amount is what the person said; "full" is the model's gloss.
        client = _shop()
        await _pay(client, {"code": "INV-2026-0002", "amount": 10000, "full": True}, "โอนมาแล้ว 10,000")
        assert _paid(client)[0][3]["amount"] in ("10000.00", "10000")

    @pytest.mark.asyncio
    async def test_a_bare_receive_still_asks_how_much(self):
        # The shipped round20v contract: "รับชำระ INV-…" (the verb, no amount).
        client = _shop()
        reply = await _pay(client, {"code": "INV-2026-0002"}, "รับชำระ INV-2026-0002")
        assert "เท่าไหร่ครับ" in reply.text and not _paid(client)


# ---------------------------------------------------------- which invoice


class TestWhichInvoice:
    @pytest.mark.asyncio
    async def test_no_invoice_anywhere_asks_which_with_the_unpaid_ones(self):
        client = _shop()
        reply = await _pay(client, {"full": True}, "จ่ายแล้ว")
        assert not _paid(client)
        codes = [b[1] for b in reply.quick_replies]
        assert any("INV-2026-0002" in c for c in codes) and len([c for c in codes if "INV-" in c]) == 3

    @pytest.mark.asyncio
    async def test_a_stated_amount_puts_the_matching_bill_first(self):
        client = _shop()
        reply = await _pay(client, {"amount": 32100}, "ลูกค้าจ่ายเงินมาแล้ว 32,100 บาท")
        assert not _paid(client)
        first = [b for b in reply.quick_replies if "INV-" in b[1]][0]
        assert "INV-2026-0002" in first[1] and "INV-2026-0002" in first[0]
        assert first[1].startswith("ลูกค้าจ่ายเงินมาแล้ว 32,100 บาท")

    @pytest.mark.asyncio
    async def test_known_only_from_the_last_viewed_memory_asks_once(self):
        client = _shop()
        client._last_entity_ref = {"entity_type": "invoice", "entity_id": "INV-2", "code": "INV-2026-0002"}
        reply = await _pay(client, {"full": True}, "จ่ายแล้ว")
        assert not _paid(client)
        assert "รับชำระ INV-2026-0002 เต็ม 32,100.00 บาท ใช่ไหม" in reply.text, reply.text
        assert ("ใช่", "รับชำระ INV-2026-0002 ครบ") in reply.quick_replies

    @pytest.mark.asyncio
    async def test_the_code_the_model_gave_records_at_once(self):
        client = _shop()
        client._last_entity_ref = {"entity_type": "invoice", "entity_id": "INV-2", "code": "INV-2026-0002"}
        await _pay(client, {"code": "INV-2026-0002", "full": True}, "จ่ายแล้ว")
        assert len(_paid(client)) == 1


# ------------------------------------------------ the answer to "เท่าไหร่ครับ"


def _asked():
    return {"action": "pay", "entity": "invoice_pay_amount",
            "fields": {"code": "INV-2026-0002", "invoice_id": "INV-2", "method": ""},
            "missing": ["payment_amount"]}


def _model(monkeypatch, reading, calls):
    async def fake_parse(**kw):
        calls.append(kw)
        if reading is None:
            raise AssertionError("the model must not be asked for this answer")
        return reading
    monkeypatch.setattr(chat, "parse_intent", fake_parse)


async def _answer(client, text):
    return await chat.handle_chat_message(client, message=text, ctx=_ctx_(), ai_client=None, language="th")


class TestThePendingAnswerIsReadByTheModel:
    @pytest.mark.parametrize("said", ["จ่ายแล้ว", "ลูกค้าโอนมาหมดแล้ว", "เต็ม", "หมดเลย"])
    @pytest.mark.asyncio
    async def test_a_paid_in_full_answer_records_the_balance(self, said, monkeypatch):
        calls: list = []
        _model(monkeypatch, {"action": "pay", "entity": "invoice",
                             "fields": {"code": "INV-2026-0002", "full": True}, "missing": []}, calls)
        client = _shop(_asked())
        reply = await _answer(client, said)
        assert len(_paid(client)) == 1, reply.text
        assert "ชำระครบ" in reply.text and "อ่านจำนวนเงินไม่ออก" not in reply.text
        assert calls and calls[0].get("pending", {}).get("fields", {}).get("code") == "INV-2026-0002"

    @pytest.mark.asyncio
    async def test_cancel_declines_before_the_model_is_asked(self, monkeypatch):
        # Measured: "ยกเลิก" with the pending context -> void INV-2026-0002.
        _model(monkeypatch, None, [])
        client = _shop(_asked())
        reply = await _answer(client, "ยกเลิก")
        assert "ยังไม่ได้บันทึกรับชำระ" in reply.text
        assert not _paid(client) and client._invoices[1]["status"] == "issued"

    @pytest.mark.parametrize("said, amount", [("5000", "5000"), ("5,000", "5000"), ("5000 บาท", "5000")])
    @pytest.mark.asyncio
    async def test_a_plain_number_needs_no_model(self, said, amount, monkeypatch):
        _model(monkeypatch, None, [])
        client = _shop(_asked())
        await _answer(client, said)
        assert _paid(client)[0][3]["amount"].startswith(amount)

    @pytest.mark.asyncio
    async def test_the_questions_own_button_needs_no_model(self, monkeypatch):
        _model(monkeypatch, None, [])
        client = _shop(_asked())
        await _answer(client, "ครบ")
        assert len(_paid(client)) == 1

    @pytest.mark.asyncio
    async def test_an_amount_with_a_method_is_the_models(self, monkeypatch):
        # Measured: "5,000 เงินสด" -> pay {code, amount:5000, method:cash}.
        _model(monkeypatch, {"action": "pay", "entity": "invoice",
                             "fields": {"code": "INV-2026-0002", "amount": 5000, "method": "cash"}}, [])
        client = _shop(_asked())
        reply = await _answer(client, "5,000 เงินสด")
        assert _paid(client)[0][3]["amount"].startswith("5000")
        assert _paid(client)[0][3]["method"] == "cash" and "เงินสด" in reply.text

    @pytest.mark.asyncio
    async def test_not_paid_yet_writes_nothing_and_asks_again(self, monkeypatch):
        # Measured: "ยังไม่ได้จ่าย" -> read {scope:"outstanding"}.
        _model(monkeypatch, {"action": "read", "entity": "invoice", "fields": {"scope": "outstanding"}}, [])
        client = _shop(_asked())
        reply = await _answer(client, "ยังไม่ได้จ่าย")
        assert not _paid(client)
        assert "เท่าไหร่" in reply.text or "อ่านจำนวนเงินไม่ออก" in reply.text

    @pytest.mark.asyncio
    async def test_a_question_is_answered_and_the_question_stays_open(self, monkeypatch):
        # Measured: "ค้างเท่าไหร่นะ" -> read {code}.
        _model(monkeypatch, {"action": "read", "entity": "invoice", "fields": {"code": "INV-2026-0002"}}, [])
        client = _shop(_asked())
        reply = await _answer(client, "ค้างเท่าไหร่นะ")
        assert not _paid(client) and "32,100.00" in reply.text
        assert client._pending and client._pending["entity"] == "invoice_pay_amount"

    @pytest.mark.asyncio
    async def test_another_bill_in_the_answer_is_not_this_one(self, monkeypatch):
        _model(monkeypatch, {"action": "pay", "entity": "invoice",
                             "fields": {"code": "INV-2026-0003", "full": True}}, [])
        client = _shop(_asked())
        await _answer(client, "อีกใบจ่ายแล้ว")
        assert not _paid(client)


# ------------------------------------------------------ paid twice, questions


class TestPaidTwiceAndQuestions:
    @pytest.mark.asyncio
    async def test_a_bill_already_paid_says_so_and_records_nothing(self):
        client = _shop()
        await _pay(client, {"code": "INV-2026-0002", "full": True})
        reply = await _pay(client, {"code": "INV-2026-0002", "full": True}, "จ่ายแล้ว")
        assert len(_paid(client)) == 1
        assert "INV-2026-0002 ชำระครบแล้วครับ (ไม่ได้บันทึกซ้ำ)" in reply.text
        assert ("ออกใบเสร็จ", "ออกใบเสร็จ INV-2026-0002") in reply.quick_replies

    @pytest.mark.asyncio
    async def test_a_question_stays_a_read(self, monkeypatch):
        # Measured: "ชำระแล้วยัง" with the card in view -> read {code}.
        _model(monkeypatch, {"action": "read", "entity": "invoice",
                             "fields": {"code": "INV-2026-0002"}, "missing": []}, [])
        client = _shop()
        reply = await _answer(client, "ชำระแล้วยัง")
        assert not _paid(client) and "INV-2026-0002" in reply.text
