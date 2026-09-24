"""Round 21C — sending a document to the customer on LINE.

Owner, 23 ก.ย. 2569: "ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้ว … สามารถออกคำสั่ง
หรือกดปุ่มส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มีผูกก็แจ้งว่าไม่ได้ หรือทำปุ่มเป็นไม่พร้อมใช้งาน".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from conftest import line_got

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import document_send  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

LINKED = {"id": "c1", "first_name": "สมชาย", "last_name": "ใจดี", "customer_chann_uid": "CHN-CUST-1"}
WALK_IN = {"id": "c2", "first_name": "สมหญิง", "last_name": "ร่ำรวย", "customer_chann_uid": None}
COMPANY = {"company_name": "ร้านทดสอบ"}
INVOICE = {"id": "i1", "invoice_id": "INV-2026-0003", "total": "32100.00",
           "generated_document_id": "d1"}


class FakeClient:
    def __init__(self):
        self.pushed = []
        self.notifications = []

    async def line_target_of(self, chann_uid):
        return f"U-{chann_uid}"

    async def list_notifications(self, license_id, **kwargs):
        return self.notifications

    # The real notification road runs (round 21E fix round 3): these are
    # what it asks of the client around the push.
    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kwargs):
        return {"id": "n1", **kwargs}

    async def record_message_entity(self, *args, **kwargs):
        return None


@pytest.fixture
def sent(monkeypatch, line_accepts):
    """What `send_document_to_customer` asked the notification road for —
    recorded, then passed THROUGH to the real road and the stand-in LINE.
    It used to stub the road whole, so these tests stayed green with LINE
    refusing every push (21E re-review 2, finding 4). A test that claims a
    send also asserts on `line_accepts` (what LINE received)."""
    from chann_app.services import notify

    calls = []
    real = notify.send_notification

    async def recording_send_notification(client, **kwargs):
        calls.append(kwargs)
        return await real(client, **kwargs)

    monkeypatch.setattr("chann_app.services.notify.send_notification", recording_send_notification)
    monkeypatch.setattr("chann_app.services.chat.document_download_url",
                        lambda license_id, document_id: f"https://x/d/{document_id}")
    return calls


class TestSending:
    @pytest.mark.asyncio
    async def test_a_linked_customer_gets_the_document(self, sent, line_accepts):
        out = await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind="invoice", record=INVOICE,
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True and out["resent"] is False
        assert out["customer_name"] == "สมชาย ใจดี"
        assert sent and sent[0]["oa"] == "customer"
        assert "INV-2026-0003" in sent[0]["message"]
        assert "https://x/d/d1" in sent[0]["message"]
        line_got(line_accepts, "U-CHN-CUST-1", "INV-2026-0003", "https://x/d/d1")

    @pytest.mark.asyncio
    async def test_a_customer_without_line_is_refused_by_name(self, sent):
        with pytest.raises(document_send.CustomerNotLinked) as exc:
            await document_send.send_document_to_customer(
                FakeClient(), license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=WALK_IN, company=COMPANY)
        assert exc.value.customer_name == "สมหญิง ร่ำรวย"
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_document_that_was_never_issued_cannot_be_sent(self, sent):
        with pytest.raises(document_send.DocumentNotIssued):
            await document_send.send_document_to_customer(
                FakeClient(), license_id="L1", kind="invoice",
                record={**INVOICE, "generated_document_id": None},
                document_id=None, customer=LINKED, company=COMPANY)
        assert sent == []

    @pytest.mark.asyncio
    async def test_sending_the_same_version_twice_says_so(self, sent, line_accepts):
        client = FakeClient()
        client.notifications = [{"type": "document_sent", "entity_type": "invoice",
                                 "entity_id": "i1", "message": "…d1…",
                                 "created_at": "2026-09-23T03:00:00+00:00"}]
        out = await document_send.send_document_to_customer(
            client, license_id="L1", kind="invoice", record=INVOICE,
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True and out["resent"] is True
        line_got(line_accepts, "U-CHN-CUST-1", "INV-2026-0003")


class FakeRouteClient(FakeClient):
    """The shop as the two send routes ask about it: one invoice, its quote,
    and the parties on them."""

    def __init__(self, *, invoice=None, customer=None):
        super().__init__()
        self.invoice = invoice or {**INVOICE, "contact_id": "c1", "receipt_document_id": "r1"}
        self.quote = {"id": "q1", "quote_id": "Q-2026-0009", "deal_id": "d1",
                      "generated_document_id": "gd1"}
        self.customer = customer or LINKED

    async def get_invoice(self, license_id, invoice_id):
        return self.invoice

    async def get_quote(self, license_id, quote_id):
        return self.quote

    async def get_deal(self, license_id, deal_id):
        return {"id": "d1", "contact_id": "c1"}

    async def get_customer(self, license_id, customer_id):
        return self.customer

    async def get_company_profile(self, license_id):
        return COMPANY


STAFF = TenantPrincipal(
    license_id="L1", chann_uid="CHN-S-000001", role="sales", is_owner=False,
    permission_keys=frozenset({"invoice.update", "quote.update"}), audience="staff",
)
CUSTOMER = TenantPrincipal(
    license_id="L1", chann_uid="CHN-CUST-1", role="customer", is_owner=False,
    permission_keys=frozenset({"invoice.update", "quote.update"}), audience="customer",
)


class TestTheSendRoutes:
    @pytest.mark.asyncio
    async def test_the_invoice_route_hands_over_the_invoice(self, sent, line_accepts):
        out = await routers_phase2.send_invoice_to_customer(
            license_id="L1", invoice_id="i1", payload=None, principal=STAFF,
            client=FakeRouteClient())
        assert out["sent"] is True and out["customer_name"] == "สมชาย ใจดี"
        assert "https://x/d/d1" in sent[0]["message"] and "INV-2026-0003" in sent[0]["message"]
        line_got(line_accepts, "U-CHN-CUST-1", "INV-2026-0003", "https://x/d/d1")

    @pytest.mark.asyncio
    async def test_the_invoice_route_can_hand_over_the_receipt_instead(self, sent, line_accepts):
        await routers_phase2.send_invoice_to_customer(
            license_id="L1", invoice_id="i1",
            payload=routers_phase2.DocumentSendBody(kind="receipt"),
            principal=STAFF, client=FakeRouteClient())
        assert "https://x/d/r1" in sent[0]["message"] and sent[0]["type"] == "receipt_issued"
        line_got(line_accepts, "U-CHN-CUST-1", "https://x/d/r1", "ชำระครบแล้ว")

    @pytest.mark.asyncio
    async def test_a_customer_without_line_is_a_409_that_names_them(self, sent):
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_invoice_to_customer(
                license_id="L1", invoice_id="i1", payload=None, principal=STAFF,
                client=FakeRouteClient(customer=WALK_IN))
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "customer_not_linked"
        assert exc.value.detail["customer"] == "สมหญิง ร่ำรวย"
        assert sent == []

    @pytest.mark.asyncio
    async def test_an_invoice_with_no_pdf_yet_is_a_422(self, sent):
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_invoice_to_customer(
                license_id="L1", invoice_id="i1", payload=None, principal=STAFF,
                client=FakeRouteClient(invoice={**INVOICE, "generated_document_id": None,
                                                "contact_id": "c1"}))
        assert exc.value.status_code == 422 and exc.value.detail["error"] == "not_issued"
        assert sent == []

    @pytest.mark.asyncio
    async def test_the_quote_route_hands_over_the_quote(self, sent, line_accepts):
        out = await routers_phase2.send_quote_to_customer(
            license_id="L1", quote_id="q1", principal=STAFF, client=FakeRouteClient())
        assert out["sent"] is True and out["resent"] is False
        assert "Q-2026-0009" in sent[0]["message"] and "https://x/d/gd1" in sent[0]["message"]
        assert sent[0]["entity_type"] == "quote" and sent[0]["type"] == "document_sent"
        line_got(line_accepts, "U-CHN-CUST-1", "Q-2026-0009", "https://x/d/gd1")

    @pytest.mark.asyncio
    async def test_a_quote_that_is_gone_is_a_404(self, sent):
        client = FakeRouteClient()
        client.quote = None
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_quote_to_customer(
                license_id="L1", quote_id="q1", principal=STAFF, client=client)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_both_routes_refuse_a_customer_before_the_client_is_asked(self, sent):
        with pytest.raises(HTTPException) as invoice_exc:
            await routers_phase2.send_invoice_to_customer(
                license_id="L1", invoice_id="i1", payload=None, principal=CUSTOMER,
                client=FakeRouteClient())
        with pytest.raises(HTTPException) as quote_exc:
            await routers_phase2.send_quote_to_customer(
                license_id="L1", quote_id="q1", principal=CUSTOMER, client=FakeRouteClient())
        assert invoice_exc.value.status_code == quote_exc.value.status_code == 403
        assert sent == []


# ------------------------------------------------ final fix round (items 4, 5)


class TestOnlyTheReceiptSaysTheAmount:
    """The quote and invoice tables have no `{total}`; the amount was being
    formatted for them anyway. A formatter that fails must not stop a
    document that never shows the number."""

    @pytest.fixture
    def baht_that_breaks(self, monkeypatch):
        calls = []

        def _baht(value):
            calls.append(value)
            raise AssertionError("the amount was formatted for a message that never shows it")

        monkeypatch.setattr("chann_app.services.invoices.baht", _baht)
        return calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["invoice", "quote"])
    async def test_a_quote_or_invoice_never_formats_the_amount(self, sent, line_accepts, baht_that_breaks, kind):
        out = await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind=kind, record=INVOICE,
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True
        assert line_accepts and line_accepts[-1][1] == "U-CHN-CUST-1"
        assert baht_that_breaks == []

    @pytest.mark.asyncio
    async def test_the_receipt_still_says_the_amount_in_both_languages(self, sent, line_accepts):
        await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind="receipt", record=INVOICE,
            document_id="r1", customer=LINKED, company=COMPANY)
        assert "32,100" in sent[0]["message"]
        assert "32,100" in sent[0]["message_en"]
        line_got(line_accepts, "U-CHN-CUST-1", "32,100")


class TestTheSendErrorOnlyTranslatesItsTwoRefusals:
    def test_the_two_refusals_become_their_codes(self):
        linked = routers_phase2._document_send_error(document_send.CustomerNotLinked("สมหญิง"))
        issued = routers_phase2._document_send_error(document_send.DocumentNotIssued("no pdf"))
        assert (linked.status_code, linked.detail["error"]) == (409, "customer_not_linked")
        assert (issued.status_code, issued.detail["error"]) == (422, "not_issued")

    def test_anything_else_is_a_bug_and_is_raised_not_dressed_as_a_500(self):
        with pytest.raises(KeyError):
            routers_phase2._document_send_error(KeyError("a caller passed the wrong thing"))


class TestOnePredicateForLinked:
    """The send button (GET invoice → `customer_has_line`) and the push
    (`CustomerNotLinked`) must answer from the same function."""

    @pytest.mark.asyncio
    async def test_the_predicate(self):
        # Round 21E review, Important 1: a uid AND a LINE user behind it.
        client = FakeClient()
        assert await document_send.customer_has_line(client, LINKED) is True
        assert await document_send.customer_has_line(client, WALK_IN) is False
        assert await document_send.customer_has_line(client, {}) is False
        assert await document_send.customer_has_line(client, None) is False

    @pytest.mark.asyncio
    async def test_the_button_and_the_push_both_follow_it(self, sent, monkeypatch):
        # A customer who LOOKS linked, and a lookup that finds no LINE user:
        # if either road still reads `customer_chann_uid` itself, it disagrees.
        async def nobody(client, customer):
            return None
        monkeypatch.setattr(document_send, "line_target_for", nobody)
        client = FakeRouteClient()
        shown = await routers_phase2.get_invoice(
            license_id="L1", invoice_id="i1", principal=TenantPrincipal(
                license_id="L1", chann_uid="CHN-S-000001", role="sales", is_owner=False,
                permission_keys=frozenset({"invoice.read"}), audience="staff"),
            client=client)
        assert shown["customer_has_line"] is False
        with pytest.raises(document_send.CustomerNotLinked):
            await document_send.send_document_to_customer(
                client, license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert sent == []


# ------------------------------------------ final review C1 (23 ก.ย. 2569)


class TestAStaleOrCancelledDocumentIsNeverSent:
    """A void bill, a bill corrected after its PDF was made, and a
    rejected/expired quote all still carry a document id. None of them may
    reach the customer: the link cannot be recalled."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,record,reason", [
        ("invoice", {**INVOICE, "status": "void"}, "void"),
        ("receipt", {**INVOICE, "status": "void"}, "void"),
        ("invoice", {**INVOICE, "status": "issued", "needs_reissue": True}, "needs_reissue"),
        ("invoice", {**INVOICE, "status": "issued",
                     "data_snapshot": {"needs_reissue_at": "2026-09-23T03:00:00+00:00"}},
         "needs_reissue"),
        ("quote", {**INVOICE, "status": "rejected"}, "quote_closed"),
        ("quote", {**INVOICE, "status": "expired"}, "quote_closed"),
    ])
    async def test_the_push_refuses_with_the_reason(self, sent, kind, record, reason):
        with pytest.raises(document_send.DocumentNotSendable) as exc:
            await document_send.send_document_to_customer(
                FakeClient(), license_id="L1", kind=kind, record=record,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert exc.value.reason == reason
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_paid_bills_receipt_is_not_held_back_by_an_old_reissue_flag(self, sent, line_accepts):
        # The receipt is its own document; the invoice PDF's staleness is
        # not the receipt's.
        out = await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind="receipt",
            record={**INVOICE, "status": "paid", "needs_reissue": True},
            document_id="r1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True
        assert line_accepts and line_accepts[-1][1] == "U-CHN-CUST-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["draft", "sent", "accepted"])
    async def test_an_open_quote_still_goes(self, sent, line_accepts, status):
        out = await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind="quote", record={**INVOICE, "status": status},
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True
        assert line_accepts and line_accepts[-1][1] == "U-CHN-CUST-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invoice,code", [
        ({**INVOICE, "status": "void", "contact_id": "c1"}, "void"),
        ({**INVOICE, "status": "issued", "needs_reissue": True, "contact_id": "c1"}, "needs_reissue"),
    ])
    async def test_the_invoice_route_answers_the_not_issued_shape_with_the_reason(
            self, sent, invoice, code):
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_invoice_to_customer(
                license_id="L1", invoice_id="i1", payload=None, principal=STAFF,
                client=FakeRouteClient(invoice=invoice))
        assert exc.value.status_code == 422 and exc.value.detail["error"] == code
        assert sent == []

    @pytest.mark.asyncio
    async def test_the_quote_route_refuses_a_rejected_quote(self, sent):
        client = FakeRouteClient()
        client.quote = {**client.quote, "status": "rejected"}
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_quote_to_customer(
                license_id="L1", quote_id="q1", principal=STAFF, client=client)
        assert exc.value.status_code == 422 and exc.value.detail["error"] == "quote_closed"
        assert sent == []
