"""Round 21E fix round 3 — re-review 2's I-1, I-2 and the LINE-reason copy.

I-1. The strict road pushes first and writes the row after. If the row
     write fails AFTER LINE accepted, the customer HAS the document: the
     send is reported sent (logged at ERROR with the code), never "ส่งไม่
     สำเร็จ — ลองอีกครั้ง", which would push it a second time.
I-2. A Data-tier error while asking whether the customer has a LINE user
     is answered in ONE place — logged, and `customer_has_line: None`
     ("unknown") on both GETs. The invoice GET used to say "not linked";
     the quote GET used to fail the whole page. The screens treat None as
     "try sending" (enabled, no reason line); the send route asks again.
N-1. "ลูกค้าอาจบล็อก OA" only for LINE 400 "Failed to send messages"; 403
     is the shop's channel, not the customer; anything else is neutral.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import chat, document_send  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from conftest import line_got  # noqa: E402
from test_phase6_chat import LICENSE_ID, _ctx  # noqa: E402
from test_round21c_document_send import COMPANY, INVOICE, LINKED, FakeRouteClient  # noqa: E402
from test_round21e_quote_send import KEYS, _shop  # noqa: E402

READER = TenantPrincipal(
    license_id="L1", chann_uid="CHN-S-000001", role="sales", is_owner=False,
    permission_keys=frozenset({"quote.read", "invoice.read"}), audience="staff")


# ------------------------------------------------ I-1: LINE took it, the row did not


class _RowFails:
    async def line_target_of(self, chann_uid):
        return "U-somchai"

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kwargs):
        raise DataTierError(503, "data tier unavailable")

    async def record_message_entity(self, *args, **kwargs):
        return None

    async def list_notifications(self, license_id, **kwargs):
        return []


class TestLineAcceptedButTheRowDidNot:
    @pytest.mark.asyncio
    async def test_the_send_is_sent_and_the_row_failure_is_logged(self, line_accepts, caplog, monkeypatch):
        monkeypatch.setattr("chann_app.services.chat.document_download_url", lambda l, d: f"https://x/d/{d}")
        with caplog.at_level(logging.ERROR):
            out = await document_send.send_document_to_customer(
                _RowFails(), license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True
        assert len(line_accepts) == 1
        line_got(line_accepts, "U-somchai", "INV-2026-0003")
        assert any(r.levelno >= logging.ERROR and "INV-2026-0003" in r.getMessage()
                   and "row" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_chat_says_sent_not_try_again(self, line_accepts):
        client = _shop()

        async def row_fails(*a, **k):
            raise DataTierError(503, "data tier unavailable")
        client.create_notification = row_fails
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote", "fields": {"code": "Q-2026-0002"}},
            ctx=_ctx(oa="sales", license_id=LICENSE_ID), license_id=LICENSE_ID, language="th",
            permission_keys=KEYS, message="ส่งใบเสนอราคา Q-2026-0002 ให้ลูกค้า")
        assert "ทางไลน์แล้ว" in reply.text and "ไม่สำเร็จ" not in reply.text, reply.text
        assert len(line_accepts) == 1


# -------------------------------------------- I-2: unknown is not "not linked"


class _LookupFails(FakeRouteClient):
    async def line_target_of(self, chann_uid):
        raise DataTierError(503, "identities unavailable")


class TestALookupErrorIsUnknown:
    @pytest.mark.asyncio
    async def test_the_one_place_answers_none_and_logs(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert await document_send.customer_line_status(_LookupFails(), LINKED) is None
        assert any("LINE" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_the_invoice_get_says_unknown(self):
        out = await routers_phase2.get_invoice(
            license_id="L1", invoice_id="i1", principal=READER, client=_LookupFails())
        assert out["customer_has_line"] is None

    @pytest.mark.asyncio
    async def test_the_quote_page_still_opens_and_says_unknown(self):
        out = await routers_phase2.get_quote_detail(
            license_id="L1", quote_id="q1", principal=READER, client=_LookupFails())
        assert out["quote"]["quote_id"] == "Q-2026-0009"
        assert out["customer_has_line"] is None

    @pytest.mark.asyncio
    async def test_the_push_stays_fail_closed(self, line_accepts):
        with pytest.raises(DataTierError):
            await document_send.send_document_to_customer(
                _LookupFails(), license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert not line_accepts

    def test_the_screens_enable_the_button_on_unknown(self):
        invoices = (ROOT / "presentation/app/liff/sales/invoices/InvoiceList.tsx").read_text(encoding="utf-8")
        quote = (ROOT / "presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx").read_text(encoding="utf-8")
        # Only a known "no" disables it; undefined (still loading) on the
        # invoice sheet stays disabled without a reason, as before.
        assert "!open.customer_has_line ||" not in invoices
        assert "open.customer_has_line === undefined" in invoices
        assert "customer_has_line?: boolean | null" in quote
        assert "detail?.customer_has_line !== false" in quote


# ------------------------------------------------- N-1: what LINE's answer means


class TestWhatLinesAnswerMeans:
    @pytest.mark.parametrize("raw, reason", [
        ('LINE push failed: 400 {"message":"Failed to send messages"}', "blocked"),
        ('LINE push failed: 400 {"message":"The request body has 1 error(s)"}', "line_refused"),
        ('LINE push failed: 403 {"message":"Access to this API is not available for your account"}', "channel_refused"),
        ("LINE push failed: 500 Internal Server Error", "line_refused"),
        ("LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED", "not_configured"),
    ])
    def test_the_mapping(self, raw, reason):
        assert document_send.failure_reason(raw) == reason

    def test_the_words(self):
        words = document_send.SEND_FAILURE_WORDS
        assert "บล็อก" in words["blocked"]["th"]
        assert "บล็อก" not in words["channel_refused"]["th"]
        assert "ตั้งค่า LINE" in words["channel_refused"]["th"]
        assert words["line_refused"]["th"] == "LINE ไม่รับข้อความ"

    def test_the_screens_say_the_channel_reason(self):
        for page in ("invoices/InvoiceList.tsx", "quotes/[id]/QuoteDetail.tsx"):
            src = (ROOT / "presentation/app/liff/sales" / page).read_text(encoding="utf-8")
            assert "sendPushChannel" in src, page
        for lang in ("th", "en"):
            assert re.search(r"sendPushChannel:", (ROOT / f"presentation/lib/i18n/{lang}.ts").read_text(encoding="utf-8"))
