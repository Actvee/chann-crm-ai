"""Round 20V — invoices, payments and receipts, from the quotation onward.

Owner, 21 ก.ย. 2569: "ทำข้อ 2 … รวมเอาเรื่อง invoice" — ใบแจ้งหนี้/ใบเสร็จหลัง
ใบเสนอราคา + สถานะชำระ (มัดจำ/จ่ายแล้ว/ค้าง).

Three layers, each with the fakes the rest of the suite uses:

* the service (`services/invoices.py`) — which quotes may be billed, the
  frozen snapshot, the ledger rules said before the Data tier is asked,
  the receipt only when paid in full;
* the chat roads — every sentence the owner listed, played through the
  real router with the model's real reading (measured with
  scripts/dev/ask-model.py, 21 ก.ย. 2569) and the FakeDataClient's
  ledger; the customer OA's read-only side; the refusals;
* the HTTP routes — the quote button, the sheet's four actions, and a
  customer principal that reads only its own bills and writes nothing.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402

from chann_app import routers_phase2  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import invoices as inv  # noqa: E402
from chann_app.services.authorization import CUSTOMER_PERMISSION_KEYS, TenantPrincipal  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.documents.html import render_invoice_html, render_receipt_html  # noqa: E402
from chann_app.services.documents.snapshot import (  # noqa: E402
    QuoteNotRenderable,
    build_invoice_snapshot,
    build_receipt_snapshot,
)

COMPANY = {
    "legal_name": "บริษัท ทดสอบ จำกัด", "company_name": "ร้านทดสอบ", "tax_id": "0105558123456",
    "company_address": "99/1", "company_phone": "021234567", "company_email": "a@b.com",
    "vat_rate": "0.07", "open_hours": None,
}
SALES_KEYS = [
    "customer.read", "deal.read", "quote.read", "quote.create", "quote.update",
    "invoice.read", "invoice.create", "invoice.update", "invoice.void",
]


class _Store:
    def __init__(self):
        self.puts: list[str] = []

    async def put(self, *, key, content, content_type):
        self.puts.append(key)

        class R:
            pass

        r = R()
        r.path = f"gs://bucket/{key}"
        r.sha256 = "ab" * 32
        return r

    async def get(self, *, path):
        return b"%PDF-fake"


class _Renderer:
    name = "fake"

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.html: list[str] = []

    async def render(self, html, options, idempotency_key=None):
        if self.fail:
            raise self.fail
        self.html.append(html)

        class R:
            pass

        r = R()
        r.content = html.encode("utf-8")
        r.url = None
        r.renderer = "fake"
        return r


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    """The model call is answered by a MockTransport; the settings only have
    to say a model exists (test_phase6_chat's autouse fixture, which does not
    reach this module)."""
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


@pytest.fixture
def engine(monkeypatch):
    """The document engine as fakes, and a public base URL so links exist."""
    store, renderer = _Store(), _Renderer()
    monkeypatch.setattr(inv, "get_document_store", lambda *a, **k: store)
    monkeypatch.setattr(inv, "get_renderer", lambda *a, **k: renderer)
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    return store, renderer


async def _shop(*, quote_status="sent", keys=SALES_KEYS, fake=FakeDataClient):
    """A shop with one customer (on LINE), one deal with two lines, one quote."""
    client = fake(permission_keys=list(keys), company_profile=dict(COMPANY))
    # ChatFake resets the profile to a bare tenant; the bill needs a
    # document-ready, VAT-registered shop.
    client._company_profile = dict(COMPANY)
    customer = await client.create_customer(
        "L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
    )
    customer["customer_chann_uid"] = "CHN-S-000001"
    client._line_targets = {**getattr(client, "_line_targets", {}), "CHN-S-000001": "Uline1"}
    deal = await client.create_deal("L1", {"contact_id": customer["id"]})
    await client.add_deal_product(
        "L1", deal["id"], {"product_name": "แอร์ 12000 BTU", "qty": 2, "quoted_unit_price": "15000.00"},
    )
    await client.add_deal_product(
        "L1", deal["id"], {"product_name": "ค่าติดตั้ง", "qty": 1, "quoted_unit_price": "2000.00"},
    )
    quote = await client.create_quote("L1", {"deal_id": deal["id"]})
    quote["status"] = quote_status
    client.recorded.clear()
    return client, customer, deal, quote


def _reading(obj: dict):
    return httpx.AsyncClient(transport=_ai(json.dumps(obj, ensure_ascii=False)))


async def _say(client, message, reading, *, oa="sales", chann_uid=None):
    ctx = _ctx(primary_role=oa, oa=oa)
    if chann_uid:
        ctx.chann_uid = chann_uid
    async with _reading(reading) as ai:
        return await handle_chat_message(client, message=message, ctx=ctx, ai_client=ai)


# ------------------------------------------------------------------ service


class TestWhichQuotesMayBeBilled:
    @pytest.mark.parametrize("status", ["draft", "rejected", "expired"])
    async def test_a_quote_that_is_not_an_accepted_offer_is_refused(self, status):
        client, customer, deal, quote = await _shop(quote_status=status)
        with pytest.raises(inv.QuoteNotBillable):
            await inv.create_from_quote(
                client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
            )
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    @pytest.mark.parametrize("status", ["sent", "accepted"])
    async def test_a_sent_or_accepted_quote_becomes_a_draft_with_the_quote_arithmetic(self, status):
        client, customer, deal, quote = await _shop(quote_status=status)
        row = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        assert row["status"] == "draft" and row["invoice_id"] == "INV-2026-0001"
        # 32,000 + 7% VAT — the same compute_totals the quotation prints.
        assert row["total"] == "34240.00" and row["vat_amount"] == "2240.00"
        snapshot = row["data_snapshot"]
        assert [i["product_name"] for i in snapshot["line_items"]] == ["แอร์ 12000 BTU", "ค่าติดตั้ง"]
        assert snapshot["quote"]["quote_id"] == "Q-2026-0001" and snapshot["deal"]["deal_id"] == "D-2026-0001"

    async def test_the_quotes_own_discount_travels_onto_the_bill(self):
        client, customer, deal, quote = await _shop()
        quote["discount_amount"] = "2000.00"
        row = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        assert row["discount_amount"] == "2000.00"
        assert row["total"] == "32100.00"

    async def test_a_deal_without_lines_cannot_be_billed(self):
        client, customer, deal, quote = await _shop()
        with pytest.raises(inv.QuoteNotBillable):
            await inv.create_from_deal(
                client, license_id="L1", deal={**deal, "products": []}, customer=customer, company=COMPANY,
            )

    async def test_an_incomplete_company_profile_refuses_before_any_row_is_written(self):
        client, customer, deal, quote = await _shop()
        with pytest.raises(QuoteNotRenderable):
            await inv.create_from_quote(
                client, license_id="L1", quote=quote, deal=deal, customer=customer,
                company={**COMPANY, "tax_id": None, "missing_for_documents": ["tax_id"]},
            )
        assert not [r for r in client.recorded if r[0] == "create_invoice"]


class TestIssueAndTheLedger:
    async def test_issue_prints_the_number_and_dates_the_row_keeps(self, engine):
        store, renderer = engine
        client, customer, deal, quote = await _shop()
        draft = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        issued, document = await inv.issue_invoice_document(
            client, license_id="L1", invoice=draft, company=COMPANY, actor_id="CHN-1",
        )
        assert issued["status"] == "issued" and issued["generated_document_id"] == document["id"]
        assert date.fromisoformat(issued["due_date"]) - date.fromisoformat(issued["issue_date"]) == \
            __import__("datetime").timedelta(days=30)
        html = renderer.html[-1]
        assert "INV-2026-0001" in html and "ใบแจ้งหนี้" in html and issued["due_date"] in html
        assert store.puts[-1].startswith("documents/L1/invoices/")
        assert document["document_type"] == "invoice" and document["source_entity_type"] == "invoice"
        # Store first, record second: the key is known before the row is.
        with pytest.raises(inv.InvoiceAlreadyIssued):
            await inv.issue_invoice_document(client, license_id="L1", invoice=issued, company=COMPANY)

    async def test_payments_are_checked_here_and_settled_by_the_ledger(self, engine):
        client, customer, deal, quote = await _shop()
        draft = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        with pytest.raises(inv.InvoiceNotOpen):
            await inv.record_payment(client, license_id="L1", invoice=draft, amount="10")
        issued, _ = await inv.issue_invoice_document(client, license_id="L1", invoice=draft, company=COMPANY)
        with pytest.raises(inv.PaymentInvalid, match="exceeds"):
            await inv.record_payment(client, license_id="L1", invoice=issued, amount="99999")
        with pytest.raises(inv.PaymentInvalid):
            await inv.record_payment(client, license_id="L1", invoice=issued, amount="0")
        with pytest.raises(inv.PaymentInvalid, match="method"):
            await inv.record_payment(client, license_id="L1", invoice=issued, amount="10", method="bitcoin")
        with pytest.raises(inv.InvoiceNotPaid):
            await inv.issue_receipt_document(client, license_id="L1", invoice=issued, company=COMPANY)
        part = await inv.record_payment(client, license_id="L1", invoice=issued, amount="4,240", method="cash")
        assert part["status"] == "partially_paid" and part["outstanding"] == "30000.00"
        full = await inv.record_payment(client, license_id="L1", invoice=part, amount=None, full=True)
        assert full["status"] == "paid" and full["paid_amount"] == "34240.00"

    async def test_the_receipt_prints_the_ledger_and_tells_the_customer(self, engine):
        store, renderer = engine
        client, customer, deal, quote = await _shop()
        draft = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        issued, _ = await inv.issue_invoice_document(client, license_id="L1", invoice=draft, company=COMPANY)
        part = await inv.record_payment(client, license_id="L1", invoice=issued, amount="10000", method="promptpay", reference="KB123")
        paid = await inv.record_payment(client, license_id="L1", invoice=part, amount=None, full=True)
        done, document = await inv.issue_receipt_document(client, license_id="L1", invoice=paid, company=COMPANY)
        assert done["receipt_document_id"] == document["id"] and document["document_type"] == "receipt"
        html = renderer.html[-1]
        assert "ใบเสร็จรับเงิน" in html and "พร้อมเพย์" in html and "KB123" in html and "ชำระครบถ้วนแล้ว" in html
        assert document["data_snapshot"]["receipt"]["paid_total"] == "34240.00"
        assert store.puts[-1].startswith("documents/L1/receipts/")
        pushed = await inv.notify_customer_receipt(client, license_id="L1", invoice=done, document=document)
        assert pushed is True
        note = next(r for r in client.recorded if r[0] == "create_notification")
        assert note[3] == "receipt_issued" and "INV-2026-0001" in note[4] and "https://app.example/api/v1/documents/" in note[4]
        assert note[6] is False, "no dashboard row for a customer without a dashboard"

    async def test_a_walk_in_customer_gets_no_push(self, engine):
        client, customer, deal, quote = await _shop()
        customer["customer_chann_uid"] = None
        draft = await inv.create_from_quote(
            client, license_id="L1", quote=quote, deal=deal, customer=customer, company=COMPANY,
        )
        assert await inv.notify_customer_receipt(client, license_id="L1", invoice=draft, document={"id": "GD-9"}) is False
        assert not [r for r in client.recorded if r[0] == "create_notification"]


class TestSnapshots:
    def _lines(self):
        return [{"product_name": "แอร์", "qty": 2, "quoted_unit_price": "15000.00", "notes": None}]

    def test_the_invoice_snapshot_is_the_quotes_arithmetic_with_a_due_date(self):
        snapshot = build_invoice_snapshot(
            lines=self._lines(), customer={"first_name": "สมชาย"}, company=COMPANY,
            invoice={"invoice_id": "INV-2026-0007", "issue_date": "2026-09-21"},
            quote={"quote_id": "Q-2026-0001"}, due_date=date(2026, 10, 21),
        )
        assert snapshot["totals"]["grand_total"] == "32100.00" and snapshot["totals"]["vat_applicable"] is True
        assert snapshot["invoice"]["due_date"] == "2026-10-21" and snapshot["issued_on"] == "2026-09-21"
        html = render_invoice_html(snapshot)
        assert "INV-2026-0007" in html and "อ้างอิงใบเสนอราคา Q-2026-0001" in html and "2026-10-21" in html

    def test_a_shop_without_vat_prints_no_vat_line(self):
        snapshot = build_invoice_snapshot(
            lines=self._lines(), customer={}, company={**COMPANY, "vat_rate": None},
        )
        assert snapshot["totals"]["vat_applicable"] is False and snapshot["totals"]["grand_total"] == "30000.00"
        assert "ภาษีมูลค่าเพิ่ม" not in render_invoice_html(snapshot)

    def test_the_receipt_snapshot_sums_the_ledger_in_words(self):
        base = build_invoice_snapshot(lines=self._lines(), customer={}, company=COMPANY, invoice={"invoice_id": "INV-2026-0001"})
        snapshot = build_receipt_snapshot(
            invoice={"invoice_id": "INV-2026-0001", "status": "paid", "issue_date": "2026-09-01", "data_snapshot": base},
            payments=[
                {"amount": "100.00", "method": "cash", "paid_at": "2026-09-02T03:00:00+00:00"},
                {"amount": "32000.00", "method": "transfer", "paid_at": "2026-09-03T03:00:00+00:00", "reference": "X"},
            ],
            company=COMPANY,
        )
        assert snapshot["receipt"]["paid_total"] == "32100.00" and snapshot["receipt"]["payment_count"] == 2
        assert snapshot["payments"][0]["method_label"] == "เงินสด"
        html = render_receipt_html(snapshot)
        assert "RCPT-INV-2026-0001" in html and "โอนเงิน" in html and "32,100.00" in html

    def test_the_report_whitelist_can_sum_what_is_owed(self):
        from chann_app.services.reports_ai import validate_query_spec

        spec = validate_query_spec({
            "entity": "invoices", "metric": "sum", "field": "outstanding",
            "filter": {"status": "issued"}, "date_range": "this_month",
        })
        assert spec["date_field"] == "issue_date"


# ------------------------------------------------------------------ chat roads


class TestSalesChat:
    async def test_issue_from_a_quote_creates_and_issues_in_one_reply(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                           {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        assert "ออกใบแจ้งหนี้ INV-2026-0001 ให้ สมชาย ใจดี แล้ว" in reply.text
        assert "34,240.00" in reply.text and "https://app.example/api/v1/documents/" in reply.text
        assert "ครบกำหนดชำระ" in reply.text
        assert reply.entity_type == "invoice"
        assert [r[0] for r in client.recorded if r[0] in ("create_invoice", "issue_invoice")] == ["create_invoice", "issue_invoice"]
        assert ("รับชำระ", "รับชำระ INV-2026-0001") in reply.quick_replies

    async def test_a_second_bill_for_the_same_quote_is_named_not_made(self, engine):
        client, customer, deal, quote = await _shop()
        reading = {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []}
        await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001", reading)
        client.recorded.clear()
        reply = await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001", reading)
        assert "มีใบแจ้งหนี้ INV-2026-0001 อยู่แล้ว" in reply.text
        assert len(client._invoices) == 1

    async def test_a_draft_quote_is_refused_with_the_way_forward(self, engine):
        client, customer, deal, quote = await _shop(quote_status="draft")
        reply = await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                           {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        assert "ยังออกใบแจ้งหนี้ไม่ได้" in reply.text and not client._invoices if hasattr(client, "_invoices") else True
        assert ("ออกเอกสาร", "ออกเอกสาร Q-2026-0001") in reply.quick_replies

    async def test_from_a_deal_by_its_code(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "สร้างใบแจ้งหนี้ให้ดีล D-2026-0001",
                           {"action": "create", "entity": "invoice", "fields": {"deal_code": "D-2026-0001"}, "missing": []})
        assert "INV-2026-0001" in reply.text and client._invoices[0]["quote_id"] is None

    async def test_a_refusal_writes_nothing(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ยังไม่ต้องออกใบแจ้งหนี้ Q-2026-0001 นะ",
                           {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        assert "ยังไม่ได้ออกใบแจ้งหนี้" in reply.text
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    async def test_the_slow_renderer_is_said_in_words_with_a_retry_button(self, engine, monkeypatch):
        from chann_app.services.pdf.base import RendererUnavailable

        monkeypatch.setattr(inv, "get_renderer", lambda *a, **k: _Renderer(fail=RendererUnavailable("slow")))
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                           {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        assert "ตอบช้าผิดปกติ" in reply.text and "slow" not in reply.text
        assert ("ลองอีกครั้ง", "ออกใบแจ้งหนี้ INV-2026-0001") in reply.quick_replies
        # The draft exists; the retry issues it.
        monkeypatch.setattr(inv, "get_renderer", lambda *a, **k: _Renderer())
        reply = await _say(client, "ออกใบแจ้งหนี้ INV-2026-0001",
                           {"action": "issue", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert "ออกใบแจ้งหนี้ INV-2026-0001 ให้" in reply.text and client._invoices[0]["status"] == "issued"

    async def _issued(self, engine):
        client, customer, deal, quote = await _shop()
        await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                   {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        client.recorded.clear()
        return client

    async def test_the_card_the_list_and_the_outstanding_view(self, engine):
        client = await self._issued(engine)
        card = await _say(client, "ใบแจ้งหนี้ INV-2026-0001",
                          {"action": "read", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert card.text.startswith("ใบแจ้งหนี้ INV-2026-0001 · รอชำระ") and "ค้าง 34,240.00" in card.text
        assert ("รับชำระ", "รับชำระ INV-2026-0001") in card.quick_replies
        listing = await _say(client, "รายการใบแจ้งหนี้", {"action": "read", "entity": "invoice", "fields": {}, "missing": []})
        assert listing.text.startswith("ใบแจ้งหนี้\nINV-2026-0001 · รอชำระ")
        assert listing.list_card and listing.list_card["rows"][0]["action_text"] == "ใบแจ้งหนี้ INV-2026-0001"
        assert "ใบแจ้งหนี้ INV-2026-0001 · รอชำระ\nลูกค้า" not in listing.text, "the list must not become the card because ใบแจ้งหนี้ contains นี้"
        owed = await _say(client, "ยอดค้างชำระ", {"action": "read", "entity": "invoice", "fields": {"scope": "outstanding"}, "missing": []})
        assert owed.text.startswith("ยอดค้างชำระรวม 34,240.00 บาท (1 ใบ)")

    async def test_a_deposit_then_the_balance_then_the_receipt(self, engine):
        client = await self._issued(engine)
        deposit = await _say(client, "มัดจำ INV-2026-0001 2000 เงินสด",
                             {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001", "amount": 2000, "method": "cash"}, "missing": []})
        assert "บันทึกรับชำระ INV-2026-0001 2,000.00 บาท (เงินสด)" in deposit.text and "ชำระบางส่วน" in deposit.text
        over = await _say(client, "รับชำระ INV-2026-0001 99999 โอน",
                          {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001", "amount": 99999, "method": "transfer"}, "missing": []})
        assert "เกินยอดค้าง 32,240.00" in over.text and client._invoices[0]["paid_amount"] == "2000.00"
        rest = await _say(client, "รับชำระ INV-2026-0001 ครบ",
                          {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001", "full": True}, "missing": []})
        assert "32,240.00 บาท (โอนเงิน)" in rest.text and "สถานะ ชำระครบ" in rest.text
        assert ("ออกใบเสร็จ", "ออกใบเสร็จ INV-2026-0001") in rest.quick_replies
        receipt = await _say(client, "ออกใบเสร็จ INV-2026-0001",
                             {"action": "receipt", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert "ออกใบเสร็จของ INV-2026-0001 แล้ว และส่งให้ลูกค้าทาง LINE แล้ว" in receipt.text
        assert "https://app.example/api/v1/documents/" in receipt.text
        assert next(r for r in client.recorded if r[0] == "create_notification")[3] == "receipt_issued"
        voided = await _say(client, "ยกเลิกใบแจ้งหนี้ INV-2026-0001",
                            {"action": "void", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert "ยกเลิก INV-2026-0001 ไม่ได้" in voided.text and client._invoices[0]["status"] == "paid"

    async def test_a_receipt_before_full_payment_is_refused(self, engine):
        client = await self._issued(engine)
        reply = await _say(client, "ออกใบเสร็จ INV-2026-0001",
                           {"action": "receipt", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert "ยังชำระไม่ครบ (ค้าง 34,240.00 บาท)" in reply.text
        assert not [r for r in client.recorded if r[0] == "set_invoice_receipt_document"]

    async def test_a_missing_amount_is_asked_and_answered_by_hand(self, engine):
        client = await self._issued(engine)
        ask = await _say(client, "รับชำระ INV-2026-0001",
                         {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert ask.text.startswith("รับชำระ INV-2026-0001 เท่าไหร่ครับ (ค้างอยู่ 34,240.00 บาท)")
        assert client._pending["entity"] == "invoice_pay_amount"
        nonsense = await _say(client, "เยอะ", {"action": "suggest", "suggestions": []})
        assert "อ่านจำนวนเงินไม่ออก" in nonsense.text and client._pending is not None
        paid = await _say(client, "5000 โอน", {"action": "suggest", "suggestions": []})
        assert "บันทึกรับชำระ INV-2026-0001 5,000.00 บาท (โอนเงิน)" in paid.text
        assert client._pending is None and client._invoices[0]["paid_amount"] == "5000.00"

    async def test_the_question_can_be_dropped(self, engine):
        client = await self._issued(engine)
        await _say(client, "รับชำระ INV-2026-0001",
                   {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        reply = await _say(client, "ยกเลิก", {"action": "suggest", "suggestions": []})
        assert "ยังไม่ได้บันทึกรับชำระของ INV-2026-0001" in reply.text
        assert not [r for r in client.recorded if r[0] == "add_invoice_payment"]

    async def test_void_asks_first_and_then_does_it(self, engine):
        client = await self._issued(engine)
        ask = await _say(client, "ยกเลิกใบแจ้งหนี้ INV-2026-0001",
                         {"action": "void", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        assert ask.text.startswith("ยกเลิกใบแจ้งหนี้ INV-2026-0001 (สมชาย ใจดี ยอด 34,240.00 บาท) ใช่ไหม")
        assert client._invoices[0]["status"] == "issued"
        kept = await _say(client, "ไม่ยกเลิก", {"action": "suggest", "suggestions": []})
        assert "ยังเก็บใบแจ้งหนี้ INV-2026-0001 ไว้" in kept.text
        await _say(client, "ยกเลิกใบแจ้งหนี้ INV-2026-0001",
                   {"action": "void", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        done = await _say(client, "ยืนยันยกเลิก", {"action": "suggest", "suggestions": []})
        assert done.text == "ยกเลิกใบแจ้งหนี้ INV-2026-0001 แล้ว" and client._invoices[0]["status"] == "void"

    async def test_without_the_key_nothing_is_written(self, engine):
        client, customer, deal, quote = await _shop(keys=["quote.read", "invoice.read"])
        reply = await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                           {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        assert not [r for r in client.recorded if r[0] == "create_invoice"]
        assert "INV-" not in reply.text


class TestCustomerChat:
    async def _with_bill(self, engine, *, pay=False, receipt=False):
        client, customer, deal, quote = await _shop()
        await _say(client, "ออกใบแจ้งหนี้ Q-2026-0001",
                   {"action": "create", "entity": "invoice", "fields": {"quote_code": "Q-2026-0001"}, "missing": []})
        if pay:
            await _say(client, "รับชำระ INV-2026-0001 ครบ",
                       {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001", "full": True}, "missing": []})
        if receipt:
            await _say(client, "ออกใบเสร็จ INV-2026-0001",
                       {"action": "receipt", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []})
        client.recorded.clear()
        return client

    async def test_my_invoices_shows_what_is_owed_and_that_the_shop_will_call(self, engine):
        client = await self._with_bill(engine)
        reply = await _say(client, "ใบแจ้งหนี้ของฉัน", {"action": "read", "entity": "invoice", "fields": {}, "missing": []},
                           oa="customer", chann_uid="CHN-S-000001")
        assert reply.text.startswith("ใบแจ้งหนี้ของคุณ\nINV-2026-0001 · รอชำระ · ยอด 34,240.00 บาท · ค้าง 34,240.00 · ครบกำหนด")
        assert "ยอดค้างรวม 34,240.00 บาท — ทางร้านจะติดต่อเรื่องช่องทางชำระ" in reply.text

    async def test_the_typed_words_answer_when_the_model_shrugs(self, engine):
        client = await self._with_bill(engine)
        for words in ("ยอดค้าง", "ใบแจ้งหนี้ของฉัน"):
            reply = await _say(client, words, {"action": "suggest", "suggestions": []}, oa="customer", chann_uid="CHN-S-000001")
            assert "INV-2026-0001" in reply.text, words

    async def test_asking_for_a_receipt_on_an_unpaid_bill(self, engine):
        client = await self._with_bill(engine)
        reply = await _say(client, "ขอใบเสร็จ", {"action": "read", "entity": "invoice", "fields": {"document": "receipt"}, "missing": []},
                           oa="customer", chann_uid="CHN-S-000001")
        assert reply.text.startswith("ใบแจ้งหนี้ INV-2026-0001 ยังค้างชำระ 34,240.00 บาท จึงยังไม่มีใบเสร็จ")

    async def test_the_receipt_link_once_paid_and_issued(self, engine):
        client = await self._with_bill(engine, pay=True, receipt=True)
        reply = await _say(client, "ขอใบเสร็จ", {"action": "suggest", "suggestions": []}, oa="customer", chann_uid="CHN-S-000001")
        assert reply.text.startswith("ใบเสร็จของ INV-2026-0001 (ยอด 34,240.00 บาท):\nhttps://app.example/api/v1/documents/")

    async def test_paid_but_not_yet_issued_points_at_the_shop(self, engine):
        client = await self._with_bill(engine, pay=True)
        reply = await _say(client, "ขอใบเสร็จ", {"action": "suggest", "suggestions": []}, oa="customer", chann_uid="CHN-S-000001")
        assert "ทางร้านยังไม่ได้ออกใบเสร็จ" in reply.text
        assert ("คุยกับร้าน", "คุยกับร้าน ขอใบเสร็จ INV-2026-0001") in reply.quick_replies

    async def test_another_customers_bills_are_invisible(self, engine):
        client = await self._with_bill(engine)
        reply = await _say(client, "ใบแจ้งหนี้ของฉัน", {"action": "read", "entity": "invoice", "fields": {}, "missing": []},
                           oa="customer", chann_uid="CHN-S-000099")
        assert "ยังไม่มีใบแจ้งหนี้ของคุณ" in reply.text and "INV-" not in reply.text

    async def test_a_customer_cannot_pay_or_void_from_chat(self, engine):
        client = await self._with_bill(engine)
        for message, reading in (
            ("รับชำระ INV-2026-0001 5000", {"action": "pay", "entity": "invoice", "fields": {"code": "INV-2026-0001", "amount": 5000}, "missing": []}),
            ("ยกเลิกใบแจ้งหนี้ INV-2026-0001", {"action": "void", "entity": "invoice", "fields": {"code": "INV-2026-0001"}, "missing": []}),
        ):
            await _say(client, message, reading, oa="customer", chann_uid="CHN-S-000001")
            assert not [r for r in client.recorded if r[0] in ("add_invoice_payment", "void_invoice")], message
        assert client._invoices[0]["status"] == "issued"


# ------------------------------------------------------------------ routes


def _harness(principal: TenantPrincipal, client):
    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


def _sales(keys=SALES_KEYS):
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-OWNER", role="member", is_owner=False,
        permission_keys=frozenset(keys), audience="sales",
    )


def _customer():
    return TenantPrincipal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="customer", is_owner=False,
        permission_keys=CUSTOMER_PERMISSION_KEYS, audience="customer",
    )


class TestRoutes:
    async def _bill(self, engine):
        client, customer, deal, quote = await _shop(fake=ChatFake)

        async def company_profile(license_id, *, _c=client):
            return _c._company_out()

        # ChatFake answers the live-chat shop card only; the bill needs the
        # document-ready, VAT-registered profile the fake was built with.
        client.get_company_profile = company_profile
        return client, quote

    async def test_the_quote_button_then_the_sheets_four_actions(self, engine):
        client, quote = await self._bill(engine)
        http = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        created = http.post(f"{base}/quotes/{quote['id']}/invoice", json={})
        assert created.status_code == 201, created.text
        invoice = created.json()
        assert invoice["invoice_id"] == "INV-2026-0001" and invoice["status"] == "draft"
        again = http.post(f"{base}/quotes/{quote['id']}/invoice", json={})
        assert again.status_code == 409

        listed = http.get(f"{base}/invoices")
        assert listed.status_code == 200 and listed.headers["X-Total-Count"] == "1"
        assert listed.json()[0]["outstanding"] == "34240.00" and listed.json()[0]["is_overdue"] is False

        issued = http.post(f"{base}/invoices/{invoice['id']}/issue")
        assert issued.status_code == 200, issued.text
        assert issued.json()["invoice"]["status"] == "issued" and issued.json()["generated_document_id"]
        assert http.post(f"{base}/invoices/{invoice['id']}/issue").status_code == 409

        bad = http.post(f"{base}/invoices/{invoice['id']}/payments", json={"amount": "99999", "method": "cash"})
        assert bad.status_code == 422 and bad.json()["detail"]["reason_code"] == "payment_invalid"
        part = http.post(f"{base}/invoices/{invoice['id']}/payments", json={"amount": "4240", "method": "cash"})
        assert part.status_code == 201 and part.json()["status"] == "partially_paid"
        early = http.post(f"{base}/invoices/{invoice['id']}/receipt")
        assert early.status_code == 409 and early.json()["detail"]["reason_code"] == "invoice_state"
        rest = http.post(f"{base}/invoices/{invoice['id']}/payments", json={"full": True, "method": "transfer"})
        assert rest.status_code == 201 and rest.json()["status"] == "paid" and len(rest.json()["payments"]) == 2

        receipt = http.post(f"{base}/invoices/{invoice['id']}/receipt")
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["receipt_document_id"] and receipt.json()["customer_notified"] is True
        detail = http.get(f"{base}/invoices/{invoice['id']}")
        assert detail.json()["receipt_document_id"] == receipt.json()["receipt_document_id"]
        # Paid: void is refused by the ledger, said as a 409 the page translates.
        assert http.post(f"{base}/invoices/{invoice['id']}/void").status_code == 409
        summary = http.get(f"{base}/invoices/summary")
        assert summary.status_code == 200 and summary.json()["open_count"] == 0

    async def test_void_needs_its_own_key(self, engine):
        client, quote = await self._bill(engine)
        http = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        invoice = http.post(f"{base}/invoices", json={"quote_id": quote["id"]}).json()
        without = _harness(_sales([k for k in SALES_KEYS if k != "invoice.void"]), client)
        assert without.post(f"{base}/invoices/{invoice['id']}/void").status_code == 403
        assert http.post(f"{base}/invoices/{invoice['id']}/void").status_code == 200
        assert http.get(f"{base}/invoices/{invoice['id']}").json()["status"] == "void"

    async def test_a_customer_reads_only_its_own_bills_and_writes_nothing(self, engine):
        client, quote = await self._bill(engine)
        staff = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        invoice = staff.post(f"{base}/quotes/{quote['id']}/invoice", json={}).json()
        staff.post(f"{base}/invoices/{invoice['id']}/issue")
        staff.post(f"{base}/invoices/{invoice['id']}/payments", json={"full": True})
        receipt_id = staff.post(f"{base}/invoices/{invoice['id']}/receipt").json()["receipt_document_id"]
        # A second customer's bill, which the first must never see.
        other = await client.create_customer("L1", {"first_name": "สมหญิง", "phone": "0899999999"})
        other["customer_chann_uid"] = "CHN-S-000002"
        other_deal = await client.create_deal("L1", {"contact_id": other["id"]})
        await client.add_deal_product("L1", other_deal["id"], {"product_name": "พัดลม", "qty": 1, "quoted_unit_price": "1000.00"})
        theirs = staff.post(f"{base}/invoices", json={"deal_id": other_deal["id"]}).json()

        me = _harness(_customer(), client)
        mine = me.get(f"{base}/invoices")
        assert mine.status_code == 200 and [r["invoice_id"] for r in mine.json()] == ["INV-2026-0001"]
        assert me.get(f"{base}/invoices/{invoice['id']}").status_code == 200
        assert me.get(f"{base}/invoices/{theirs['id']}").status_code == 404
        assert me.get(f"{base}/invoices/summary").status_code == 403
        for path in ("issue", "payments", "receipt", "void"):
            assert me.post(f"{base}/invoices/{invoice['id']}/{path}", json={"amount": "1"}).status_code == 403, path
        assert me.post(f"{base}/quotes/{quote['id']}/invoice", json={}).status_code == 403
        # Their own receipt opens; a quote's document (or anyone else's) does not.
        assert me.get(f"{base}/documents/{receipt_id}/link").status_code == 200
        quote_doc = await client.record_generated_document("L1", {
            "document_type": "quote", "source_entity_type": "quote", "source_entity_id": quote["id"],
            "template_version_id": "v1", "data_snapshot": {}, "output_path": "x", "sha256": "y",
        })
        assert me.get(f"{base}/documents/{quote_doc['id']}/link").status_code == 404
