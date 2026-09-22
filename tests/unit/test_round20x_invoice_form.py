"""Round 20X — the invoice belongs to a deal; the customer's name finds it.

Owner, 22 ก.ย. 2569, two complaints and one correction:

* "ทำไมใน ใบแจ้งหนี้ไม่อยู่ใน section งานขาย" — the nav entry moves into the
  selling group, right after the quotation;
* "invoice ต้องมีตัวเลือกให้ผูกกับ deal หรือลูกค้าได้" — the invoices page
  gets a create form (customer → deal → quote) and the deal and customer
  pages get a button into it;
* "ใบแจ้งหนี้จะไม่ผูกกับลูกค้าโดยตรง อย่างน้อยจะมีดีลเกิดขึ้น" — so there is
  no invoice without a deal, and "ออกใบแจ้งหนี้ให้ สมชาย" in chat means
  "from สมชาย's deal": one deal with lines → billed (through its sent
  quotation when it has one), several → a choice, none → "สร้างดีลก่อน".

Every model reading below is the deployed model's own
(scripts/dev/ask-model.py, 22 ก.ย. 2569, before and after the prompt hint).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase6_chat import LICENSE_ID  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402
from test_round20v_invoices import (  # noqa: E402,F401 — the fixtures are used by name
    SALES_KEYS,
    _ai_configured,
    _harness,
    _sales,
    _say,
    _shop,
    engine,
)

from chann_app.data_client import DataClient  # noqa: E402
from chann_app.services import invoices as inv  # noqa: E402
from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT  # noqa: E402

BY_NAME = {"action": "create", "entity": "invoice", "fields": {"target_name": "สมชาย"}, "missing": []}


def _by_name(name: str) -> dict:
    return {"action": "create", "entity": "invoice", "fields": {"target_name": name}, "missing": []}


def _by_deal(code: str) -> dict:
    return {"action": "create", "entity": "invoice", "fields": {"deal_code": code}, "missing": []}


async def _second_deal(client, customer, *lines):
    deal = await client.create_deal("L1", {"contact_id": customer["id"]})
    for name, qty, price in lines:
        await client.add_deal_product("L1", deal["id"], {"product_name": name, "qty": qty, "quoted_unit_price": price})
    return deal


# ------------------------------------------------------------- service


class TestBillableDeals:
    async def test_only_deals_with_lines_that_were_not_lost(self, engine):
        client, customer, deal, quote = await _shop()
        empty = await _second_deal(client, customer)
        lost = await _second_deal(client, customer, ("พัดลม", 1, "1500.00"))
        await client.update_deal("L1", lost["id"], {"stage": "lost"})
        won = await _second_deal(client, customer, ("แอร์", 1, "9000.00"))
        await client.update_deal("L1", won["id"], {"stage": "won"})
        rows = await inv.billable_deals(client, "L1", customer["id"])
        assert [r["id"] for r in rows] == [deal["id"], won["id"]], "no lines and lost are out; won stays in"
        assert empty["id"] not in {r["id"] for r in rows}

    async def test_another_customers_deal_is_not_offered(self, engine):
        client, customer, deal, quote = await _shop()
        other = await client.create_customer("L1", {"first_name": "สมหญิง", "phone": "0899999999"})
        await _second_deal(client, other, ("พัดลม", 1, "1500.00"))
        assert [r["id"] for r in await inv.billable_deals(client, "L1", customer["id"])] == [deal["id"]]


# ---------------------------------------------------------------- chat


class TestByCustomerName:
    async def test_one_deal_with_a_sent_quote_is_billed_through_the_quote(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมชาย", BY_NAME)
        assert "ออกใบแจ้งหนี้ INV-2026-0001 ให้ สมชาย ใจดี แล้ว" in reply.text
        assert "34,240.00" in reply.text, "the quote's arithmetic, VAT included"
        assert client._invoices[0]["quote_id"] == quote["id"] and client._invoices[0]["deal_id"] == deal["id"]
        assert reply.entity_type == "invoice"

    async def test_one_deal_without_a_quote_is_billed_from_its_lines(self, engine):
        client, customer, deal, quote = await _shop(quote_status="draft")
        reply = await _say(client, "ออกใบแจ้งหนี้ให้คุณสมชายหน่อยครับ", _by_name("คุณสมชาย"))
        assert "INV-2026-0001" in reply.text
        assert client._invoices[0]["quote_id"] is None, "a draft quote is not billable; the deal's lines are"
        assert client._invoices[0]["deal_id"] == deal["id"]

    async def test_no_deal_says_create_one_first(self, engine):
        client, customer, deal, quote = await _shop()
        other = await client.create_customer("L1", {"first_name": "สมหญิง", "last_name": "รักดี", "phone": "0899999999"})
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมหญิง", _by_name("สมหญิง"))
        assert reply.text.startswith("สมหญิง รักดี ยังไม่มีดีล — สร้างดีลก่อน")
        assert ("สร้างดีล", "สร้างดีลให้ สมหญิง รักดี") in reply.quick_replies
        assert reply.entity_type == "customer" and reply.entity_id == other["id"]
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    async def test_a_deal_without_lines_says_add_the_products(self, engine):
        client, customer, deal, quote = await _shop()
        other = await client.create_customer("L1", {"first_name": "สมหญิง", "phone": "0899999999"})
        empty = await _second_deal(client, other)
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมหญิง", _by_name("สมหญิง"))
        assert f"สมหญิง มีดีล {empty['deal_id']} แต่ยังไม่มีรายการสินค้า" in reply.text
        assert ("เพิ่มสินค้า", f"เพิ่มสินค้า เข้าดีล {empty['deal_id']}") in reply.quick_replies
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    async def test_several_deals_are_a_choice_and_the_button_bills_that_deal(self, engine):
        client, customer, deal, quote = await _shop()
        second = await _second_deal(client, customer, ("พัดลม", 1, "1500.00"))
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมชาย", BY_NAME)
        assert reply.text.startswith("สมชาย ใจดี มีหลายดีล ออกใบแจ้งหนี้จากดีลไหนครับ")
        assert deal["deal_id"] in reply.text and second["deal_id"] in reply.text
        assert (second["deal_id"], f"ออกใบแจ้งหนี้ให้ดีล {second['deal_id']}") in reply.quick_replies
        assert not [r for r in client.recorded if r[0] == "create_invoice"], "a choice, never a guess"
        # The button's sentence, as the model reads it (ask-model, 22 ก.ย.).
        done = await _say(client, f"ออกใบแจ้งหนี้ให้ดีล {second['deal_id']}", _by_deal(second["deal_id"]))
        assert "INV-2026-0001" in done.text and "1,605.00" in done.text
        assert client._invoices[0]["deal_id"] == second["id"] and client._invoices[0]["quote_id"] is None

    async def test_a_lost_deal_is_not_the_one_chosen(self, engine):
        client, customer, deal, quote = await _shop()
        lost = await _second_deal(client, customer, ("พัดลม", 1, "1500.00"))
        await client.update_deal("L1", lost["id"], {"stage": "lost"})
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมชาย", BY_NAME)
        assert "INV-2026-0001" in reply.text and client._invoices[0]["deal_id"] == deal["id"]

    async def test_a_duplicate_name_is_a_choice_and_the_number_finishes_it(self, engine):
        client, customer, deal, quote = await _shop()
        twin = await client.create_customer("L1", {"first_name": "สมชาย", "last_name": "ขยัน", "phone": "0877777777"})
        await _second_deal(client, twin, ("พัดลม", 2, "1500.00"))
        ask = await _say(client, "ออกใบแจ้งหนี้ให้ สมชาย", BY_NAME)
        assert "พบลูกค้าชื่อ สมชาย หลายคน" in ask.text
        assert client._pending["entity"] == "customer_disambiguation"
        assert not [r for r in client.recorded if r[0] == "create_invoice"]
        # "2" is read by hand and re-runs the order with the chosen code.
        done = await _say(client, "2", {"action": "suggest", "suggestions": []})
        assert "INV-2026-0001" in done.text and "3,210.00" in done.text
        assert client._invoices[0]["contact_id"] == twin["id"]

    async def test_an_unknown_name_is_said_and_writes_nothing(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมปอง", _by_name("สมปอง"))
        assert reply.text == "ไม่พบลูกค้าชื่อ สมปอง ในบริษัทนี้"
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    async def test_without_the_key_nothing_is_written(self, engine):
        client, customer, deal, quote = await _shop(keys=["customer.read", "deal.read", "quote.read", "invoice.read"])
        reply = await _say(client, "ออกใบแจ้งหนี้ให้ สมชาย", BY_NAME)
        assert "INV-" not in reply.text
        assert not [r for r in client.recorded if r[0] == "create_invoice"]

    async def test_the_question_names_the_customer_form_too(self, engine):
        client, customer, deal, quote = await _shop()
        reply = await _say(client, "ออกใบแจ้งหนี้", {"action": "create", "entity": "invoice", "fields": {}, "missing": []})
        assert "ออกใบแจ้งหนี้ให้ สมชาย" in reply.text and not client._invoices if hasattr(client, "_invoices") else True

    def test_the_prompt_teaches_the_name_form_inside_the_invoice_block(self):
        block = INTENT_SYSTEM_PROMPT.split('- entity="invoice"', 1)[1].split("\n- entity=", 1)[0]
        assert '"target_name"' in block and "ออกใบแจ้งหนี้ให้ สมชาย" in block
        assert "never list deal_code as missing" in block


# -------------------------------------------------------------- routes


class TestRoutes:
    async def _bill(self):
        client, customer, deal, quote = await _shop(fake=ChatFake)

        async def company_profile(license_id, *, _c=client):
            return _c._company_out()

        client.get_company_profile = company_profile
        return client, customer, deal, quote

    async def test_the_list_narrows_by_deal_and_by_customer(self, engine):
        client, customer, deal, quote = await self._bill()
        second = await _second_deal(client, customer, ("พัดลม", 1, "1500.00"))
        http = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        first = http.post(f"{base}/invoices", json={"deal_id": deal["id"]}).json()
        other = http.post(f"{base}/invoices", json={"deal_id": second["id"]}).json()
        by_deal = http.get(f"{base}/invoices", params={"deal_id": second["id"]})
        assert by_deal.status_code == 200 and by_deal.headers["X-Total-Count"] == "1"
        assert [r["invoice_id"] for r in by_deal.json()] == [other["invoice_id"]]
        by_customer = http.get(f"{base}/invoices", params={"contact_id": customer["id"]})
        assert by_customer.headers["X-Total-Count"] == "2"
        assert {r["invoice_id"] for r in by_customer.json()} == {first["invoice_id"], other["invoice_id"]}

    async def test_the_quote_list_narrows_by_deal_and_still_searches(self, engine):
        """GET /quotes has passed `q` and `offset` since round 20N to a client
        that did not take them — a 500 behind every quote list. The fake
        now implements the same method, so the route is walked here."""
        client, customer, deal, quote = await self._bill()
        second = await _second_deal(client, customer, ("พัดลม", 1, "1500.00"))
        other = await client.create_quote("L1", {"deal_id": second["id"]})
        http = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        rows = http.get(f"{base}/quotes", params={"deal_id": second["id"]})
        assert rows.status_code == 200 and [r["id"] for r in rows.json()] == [other["id"]]
        assert rows.headers["X-Total-Count"] == "1"
        searched = http.get(f"{base}/quotes", params={"q": quote["quote_id"], "offset": 0})
        assert searched.status_code == 200 and [r["id"] for r in searched.json()] == [quote["id"]]

    async def test_the_real_client_sends_what_the_routes_pass(self):
        """The seam itself, over a mock transport: every keyword the two
        routes pass is accepted and becomes a query parameter."""
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json=[], headers={"X-Total-Count": "0"})

        client = DataClient(base_url="http://data.test", secret="s", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await client.list_quotes_with_total("L1", "sent", limit=50, q="Q-2026", offset=50, deal_id="D1")
        await client.list_invoices_with_total("L1", status="issued", q="INV", contact_id="C1", deal_id="D1", overdue=True, limit=10, offset=0)
        quotes, invoices = seen
        assert dict(quotes.params) == {"status_": "sent", "q": "Q-2026", "deal_id": "D1", "limit": "50", "offset": "50"}
        assert dict(invoices.params) == {
            "status_": "issued", "contact_id": "C1", "deal_id": "D1", "q": "INV", "overdue": "true", "limit": "10",
        }
        route_kwargs = {"limit", "q", "offset", "deal_id"}
        assert route_kwargs <= set(inspect.signature(DataClient.list_quotes_with_total).parameters)

    async def test_the_create_body_still_takes_a_quote_or_a_deal_only(self, engine):
        client, customer, deal, quote = await self._bill()
        http = _harness(_sales(), client)
        base = f"/api/v1/licenses/{LICENSE_ID}"
        # A customer alone is not a bill (owner, 22 ก.ย. 2569).
        assert http.post(f"{base}/invoices", json={"contact_id": customer["id"]}).status_code == 422
        assert http.post(f"{base}/invoices", json={}).status_code == 422
        assert http.post(f"{base}/invoices", json={"deal_id": deal["id"], "note": "งวดแรก"}).status_code == 201


# ----------------------------------------------------------------- nav


class TestTheNavAndThePages:
    def test_invoices_sit_in_the_selling_group_after_quotes(self):
        source = (ROOT / "presentation/app/liff/_nav-model.tsx").read_text(encoding="utf-8")
        selling = source.split('key: "selling"', 1)[1].split('key: "service"', 1)[0]
        assert 'key: "invoices"' in selling, "owner, 22 ก.ย. 2569: ทำไมใน ใบแจ้งหนี้ไม่อยู่ใน section งานขาย"
        assert selling.index('key: "quotes"') < selling.index('key: "invoices"') < selling.index('key: "appointments"')
        paperwork = source.split('key: "paperwork"', 1)[1].split('key: "shop"', 1)[0]
        assert 'key: "invoices"' not in paperwork

    def test_the_deal_and_customer_pages_open_the_form_prefilled(self):
        deal_page = (ROOT / "presentation/app/liff/sales/deals/[id]/DealDetail.tsx").read_text(encoding="utf-8")
        assert "/liff/sales/invoices?deal_id=${deal.id}&create=1" in deal_page
        assert 'can("invoice.create")' in deal_page
        customer_page = (ROOT / "presentation/app/liff/sales/customers/[id]/CustomerDetail.tsx").read_text(encoding="utf-8")
        assert "/liff/sales/invoices?contact_id=${customerId}&create=1" in customer_page
        assert "invoices?${search}" in customer_page and 'permissions.has("invoice.read")' in customer_page

    def test_the_form_has_no_line_editor_and_asks_before_issuing(self):
        form = (ROOT / "presentation/app/liff/sales/invoices/_create-sheet.tsx").read_text(encoding="utf-8")
        assert "quotes/${quoteId}/products" in form and "deals?${search}" in form
        assert "customers?${search}" in form, "the customer is found by the server's search, not a select of everyone"
        assert "await ask({" in form and "copy.createAndIssue" in form
        assert "unit_price" not in form.replace("quoted_unit_price", ""), "no manual lines: the deal's or the quote's, read-only"
        for key in ("create", "noDealsForCustomer", "quoteNone", "createDraft", "createAndIssue", "filteredByDeal"):
            for lang in ("th", "en"):
                assert f"      {key}:" in (ROOT / f"presentation/lib/i18n/{lang}.ts").read_text(encoding="utf-8"), (lang, key)
