"""Round 21C — the invoice sheet can correct a bill and hand it over.

Source assertions, like tests/unit/test_round20x_invoice_form.py: these
pin that the screen exists and calls the right routes. What they cannot
see is whether it looks right — that is the ui-ux pass and a human
looking at it.

Ruling 4 (round 21C controller resolutions): the paid-amount assertion
below reads the real expression the sheet computes with — `open` is
`Invoice | null`, so the guard is `Number(open?.paid_amount ?? 0)`, not
the unguarded `open.paid_amount` the brief's draft used.

Review round 1 (task-10-review.md) added the classes below: the last-line
guard (critical finding 1), reading `customer_has_line` straight off the
invoice rather than a client-side lookup (ruling 18, findings 2 and 3),
and matching the send route's real failure codes (finding 4). The last
class calls `routers_phase2.get_invoice` directly, the way
`tests/unit/test_round21c_invoice_edit.py` fakes the client — this is a
backend behaviour test, not a source assertion, because "the field is on
the response" cannot be checked by reading TSX text.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INVOICES = (ROOT / "presentation/app/liff/sales/invoices/InvoiceList.tsx").read_text(encoding="utf-8")
QUOTE = (ROOT / "presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx").read_text(encoding="utf-8")
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "application"))
from chann_app import routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402


class TestTheLineEditor:
    def test_the_sheet_patches_lines_and_details(self):
        assert "/lines" in INVOICES and '"PATCH"' in INVOICES
        assert "ProductLineForm" in INVOICES

    def test_it_is_closed_once_money_has_been_received(self):
        assert "Number(open?.paid_amount" in INVOICES

    def test_a_reissue_is_offered_when_the_document_no_longer_matches(self):
        assert "needs_reissue" in INVOICES


class TestTheLastLineCannotBeDeleted:
    """Review round 1, critical finding 1: `removeLine` could call
    `saveLines([])` — a bill with no lines. Both the visible control and
    `saveLines` itself (regardless of caller) must refuse."""

    def test_the_delete_control_is_disabled_on_the_last_line(self):
        assert "lines.length <= 1" in INVOICES

    def test_savelines_refuses_an_empty_list_regardless_of_the_caller(self):
        assert "next.length === 0" in INVOICES

    def test_the_reason_is_visible_text_not_only_a_disabled_button(self):
        assert "oneLineRequired" in INVOICES
        assert "oneLineRequired" in TH


class TestCustomerHasLineComesFromTheServer:
    """Ruling 18: the live client-side lookup (`loadCustomerHasLine`, a
    `customerHasLine` state, two fetches per invoice open) is deleted
    outright rather than patched — the field now arrives atomically with
    the rest of the invoice, so there is no window for a stale answer and
    no duplicate request to make in the first place."""

    def test_the_sheet_reads_the_field_straight_off_the_invoice(self):
        assert "open.customer_has_line" in INVOICES

    def test_no_client_side_lookup_of_its_own_remains(self):
        assert "loadCustomerHasLine" not in INVOICES
        assert "customerHasLine" not in INVOICES


class TestSending:
    def test_both_screens_can_send_to_the_customer(self):
        assert "/send" in INVOICES
        assert "/send" in QUOTE

    def test_a_disabled_send_says_why_in_visible_text(self):
        # not only title=: the reason is rendered
        assert "sendNoLine" in INVOICES and "sendNoLine" in QUOTE
        assert "sendNoLine" in TH

    def test_a_send_failure_matches_the_routes_real_not_issued_code(self):
        # Review round 1, finding 4: a blanket "anything but
        # customer_not_linked means sendNotIssued" masked real errors
        # (500s, rate limits, ...). Both screens must match the exact
        # code `_document_send_error` returns for "no document yet".
        assert '"not_issued"' in INVOICES
        assert '"not_issued"' in QUOTE


class TestGetInvoiceCarriesCustomerHasLine:
    """Ruling 18, restored: `GET .../invoices/{id}` answers
    `customer_has_line` itself, computed the same way
    `services/document_send.py`'s `CustomerNotLinked` decides — read
    once, live, from the customer behind the invoice's `contact_id`."""

    INVOICE_ROW = {
        "id": "33333333-3333-3333-3333-333333333333",
        "invoice_id": "INV-2026-0099", "status": "issued", "paid_amount": "0",
        "contact_id": "44444444-4444-4444-4444-444444444444", "deal_id": None,
        "total": "1000.00",
    }
    STAFF = TenantPrincipal(
        license_id="L1", chann_uid="CHN-OWNER", role="member", is_owner=False,
        permission_keys=frozenset({"invoice.read"}), audience="sales",
    )

    class _FakeClient:
        def __init__(self, invoice, customer):
            self._invoice = invoice
            self._customer = customer

        async def get_invoice(self, license_id, invoice_id):
            return self._invoice

        async def get_customer(self, license_id, customer_id):
            return self._customer

        async def line_target_of(self, chann_uid):
            # A linked customer has a LINE user behind the uid (round 21E).
            return {"CHN-C-1": "U-C-1"}.get(chann_uid)

    @pytest.mark.asyncio
    async def test_true_when_the_customer_has_linked_line(self):
        client = self._FakeClient(self.INVOICE_ROW, {"id": "c1", "customer_chann_uid": "CHN-C-1"})
        result = await routers_phase2.get_invoice(
            license_id="L1", invoice_id=self.INVOICE_ROW["id"], principal=self.STAFF, client=client,
        )
        assert result["customer_has_line"] is True

    @pytest.mark.asyncio
    async def test_false_when_the_customer_has_no_line(self):
        client = self._FakeClient(self.INVOICE_ROW, {"id": "c1", "customer_chann_uid": None})
        result = await routers_phase2.get_invoice(
            license_id="L1", invoice_id=self.INVOICE_ROW["id"], principal=self.STAFF, client=client,
        )
        assert result["customer_has_line"] is False

    @pytest.mark.asyncio
    async def test_false_when_the_invoice_has_no_contact(self):
        client = self._FakeClient({**self.INVOICE_ROW, "contact_id": None}, {"customer_chann_uid": "CHN-C-1"})
        result = await routers_phase2.get_invoice(
            license_id="L1", invoice_id=self.INVOICE_ROW["id"], principal=self.STAFF, client=client,
        )
        assert result["customer_has_line"] is False


class TestAStaleOrCancelledDocumentCannotBeSent:
    """Final review C1: the send button is disabled on a void bill, on a
    bill that needs re-issuing, and on a rejected/expired quotation — and
    each says why in visible text (ui-ux-pro-max: disabled-states; the
    repo's disabled-needs-a-reason)."""

    def test_the_invoice_button_reads_void_and_needs_reissue(self):
        assert "sendBlocked" in INVOICES
        assert 'row.status === "void"' in INVOICES
        assert "row.needs_reissue" in INVOICES
        assert "sendBlocked(open) !== null" in INVOICES

    def test_the_invoice_sheet_says_each_reason(self):
        for key in ("sendVoid", "sendNeedsReissue"):
            assert key in INVOICES and key in TH

    def test_the_quote_button_reads_a_closed_quote(self):
        assert "quoteClosed" in QUOTE
        assert '"rejected"' in QUOTE and '"expired"' in QUOTE
        assert "sendQuoteClosed" in QUOTE and "sendQuoteClosed" in TH

    def test_the_routes_reason_codes_are_matched(self):
        for code in ('"void"', '"needs_reissue"'):
            assert code in INVOICES
        assert '"quote_closed"' in QUOTE
