"""Phase 10 — the arithmetic and identity freezing behind a quote document.

Tested exhaustively and without a database or a render, because this is
where a bug is both most likely and most expensive: the output goes to a
customer as a priced offer, and `docs/SMARTBROWZ_DOCUMENT_ENGINE.md`
requires every money and tax value to come from deterministic logic rather
than anything AI-assisted.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.documents.html import render_quote_html  # noqa: E402
from chann_app.services.documents.snapshot import (  # noqa: E402
    QuoteNotRenderable,
    build_line_items,
    build_quote_snapshot,
    compute_totals,
)


def _company(**overrides):
    base = {
        "legal_name": "บริษัท ทดสอบ จำกัด",
        "company_name": "ร้านทดสอบ",
        "tax_id": "0105558123456",
        "company_address": "99/1 ถนนสุขุมวิท กรุงเทพฯ 10110",
        "company_phone": "021234567",
        "company_email": "billing@example.com",
        "vat_rate": "0.07",
        "missing_for_documents": [],
    }
    base.update(overrides)
    return base


def _deal(products=None):
    return {
        "deal_id": "D-2026-0001",
        "contact_id": "c-1",
        "products": products if products is not None else [
            {"product_name": "พัดลมตั้งพื้น", "qty": 3, "quoted_unit_price": "1250.00"},
        ],
    }


def _customer(**overrides):
    base = {
        "first_name": "สมชาย", "last_name": "ใจดี",
        "phone": "0812345678", "email": "somchai@example.com",
        "address": "1 หมู่ 2 ต.บางรัก",
    }
    base.update(overrides)
    return base


def _quote():
    return {"quote_id": "Q-2026-0001", "status": "draft", "deal_id": "d-1"}


class TestLineItems:
    def test_line_total_is_unit_price_times_quantity(self):
        items = build_line_items([
            {"product_name": "พัดลม", "qty": 3, "quoted_unit_price": "1250.00"},
        ])
        assert items[0]["line_total"] == "3750.00"
        assert items[0]["line_no"] == 1

    def test_prices_are_decimal_not_float(self):
        """0.1 + 0.2 != 0.3 in binary floating point. On a priced offer that
        eventually shows up as a total that is one satang off and cannot be
        explained to the customer."""
        items = build_line_items([
            {"product_name": "A", "qty": 3, "quoted_unit_price": "0.10"},
        ])
        assert items[0]["line_total"] == "0.30"

    def test_rounding_is_half_up_not_bankers(self):
        """Python's default rounding is half-even, which disagrees with a
        hand calculator exactly on .005 — the worst place to differ."""
        items = build_line_items([
            {"product_name": "A", "qty": 1, "quoted_unit_price": "10.125"},
        ])
        assert items[0]["line_total"] == "10.13"

    def test_off_catalogue_items_keep_the_name_quoted_on_the_deal(self):
        """Phase 9 allows products outside the catalogue (product_id is
        nullable), so the name and price must come from the deal row."""
        items = build_line_items([
            {"product_name": "งานติดตั้งพิเศษ", "qty": 1,
             "quoted_unit_price": "5000", "product_id": None},
        ])
        assert items[0]["product_name"] == "งานติดตั้งพิเศษ"
        assert items[0]["unit_price"] == "5000.00"

    def test_no_products_yields_no_lines(self):
        assert build_line_items([]) == []
        assert build_line_items(None) == []


class TestTotals:
    def test_vat_is_applied_to_the_subtotal(self):
        items = build_line_items([
            {"product_name": "A", "qty": 2, "quoted_unit_price": "100.00"},
        ])
        totals = compute_totals(items, "0.07")
        assert totals["subtotal"] == "200.00"
        assert totals["vat_amount"] == "14.00"
        assert totals["grand_total"] == "214.00"
        assert totals["vat_applicable"] is True
        assert totals["vat_rate_percent"] == "7"

    def test_not_vat_registered_produces_no_vat_line_at_all(self):
        """None is not 0. A company with no VAT registration must not have a
        'VAT 0.00' row, which would misstate its tax status."""
        items = build_line_items([
            {"product_name": "A", "qty": 1, "quoted_unit_price": "100.00"},
        ])
        totals = compute_totals(items, None)
        assert totals["vat_applicable"] is False
        assert totals["vat_amount"] is None
        assert totals["grand_total"] == "100.00"

    def test_zero_percent_vat_is_a_real_line_distinct_from_unregistered(self):
        items = build_line_items([
            {"product_name": "A", "qty": 1, "quoted_unit_price": "100.00"},
        ])
        totals = compute_totals(items, "0")
        assert totals["vat_applicable"] is True
        assert totals["vat_amount"] == "0.00"

    def test_subtotal_sums_every_line(self):
        items = build_line_items([
            {"product_name": "A", "qty": 2, "quoted_unit_price": "1250.50"},
            {"product_name": "B", "qty": 1, "quoted_unit_price": "99.49"},
        ])
        totals = compute_totals(items, None)
        assert totals["subtotal"] == "2600.49"

    def test_empty_quote_totals_to_zero_rather_than_failing(self):
        totals = compute_totals([], "0.07")
        assert totals["subtotal"] == "0.00"
        assert totals["grand_total"] == "0.00"


class TestSnapshot:
    def test_refuses_when_the_company_is_not_document_ready(self):
        """Never render a tax document with a blank tax ID. The same rule
        10.6 states for provider outages — no fabricated documents — applies
        to incomplete tenant data."""
        with pytest.raises(QuoteNotRenderable) as exc:
            build_quote_snapshot(
                quote=_quote(), deal=_deal(), customer=_customer(),
                company=_company(tax_id=None, missing_for_documents=["tax_id"]),
            )
        assert "tax_id" in str(exc.value)

    def test_legal_name_is_used_on_the_document_not_the_shop_name(self):
        snap = build_quote_snapshot(
            quote=_quote(), deal=_deal(), customer=_customer(), company=_company(),
        )
        assert snap["company"]["name"] == "บริษัท ทดสอบ จำกัด"
        assert snap["company"]["trading_name"] == "ร้านทดสอบ"

    def test_legal_name_falls_back_to_the_shop_name_when_unset(self):
        """A sole trader may trade under their registered name. An empty
        heading is worse than a duplicated one."""
        snap = build_quote_snapshot(
            quote=_quote(), deal=_deal(), customer=_customer(),
            company=_company(legal_name=None),
        )
        assert snap["company"]["name"] == "ร้านทดสอบ"

    def test_vat_rate_is_frozen_into_the_snapshot(self):
        """The whole reason data_snapshot exists: if the rate changes next
        year, an already-issued document must still reproduce with the rate
        that was actually applied."""
        snap = build_quote_snapshot(
            quote=_quote(), deal=_deal(), customer=_customer(), company=_company(),
        )
        assert snap["totals"]["vat_rate"] == "0.07"
        assert Decimal(snap["totals"]["vat_amount"]) == Decimal("262.50")

    def test_customer_name_joins_the_parts_it_has(self):
        snap = build_quote_snapshot(
            quote=_quote(), deal=_deal(),
            customer=_customer(last_name=None), company=_company(),
        )
        assert snap["customer"]["name"] == "สมชาย"


class TestQuoteHtml:
    def _snap(self, **kw):
        return build_quote_snapshot(
            quote=_quote(), deal=_deal(), customer=_customer(),
            company=_company(**kw),
        )

    def test_renders_the_identifying_details(self):
        html = render_quote_html(self._snap())
        for expected in (
            "Q-2026-0001", "D-2026-0001", "0105558123456",
            "บริษัท ทดสอบ จำกัด", "สมชาย ใจดี", "พัดลมตั้งพื้น",
        ):
            assert expected in html

    def test_money_is_formatted_with_separators_only_at_render_time(self):
        html = render_quote_html(self._snap())
        assert "3,750.00" in html
        assert "4,012.50" in html  # 3750 + 7% VAT

    def test_no_vat_row_when_the_company_is_not_registered(self):
        html = render_quote_html(self._snap(vat_rate=None))
        assert "ภาษีมูลค่าเพิ่ม" not in html
        assert "จำนวนเงินรวมทั้งสิ้น" in html

    def test_product_names_are_escaped(self):
        """Product names are tenant-supplied free text and end up inside
        markup handed to a browser engine."""
        snap = build_quote_snapshot(
            quote=_quote(),
            deal=_deal([{
                "product_name": "<script>alert(1)</script>",
                "qty": 1, "quoted_unit_price": "1.00",
            }]),
            customer=_customer(), company=_company(),
        )
        html = render_quote_html(snap)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_an_empty_quote_still_renders_a_readable_document(self):
        snap = build_quote_snapshot(
            quote=_quote(), deal=_deal([]), customer=_customer(), company=_company(),
        )
        html = render_quote_html(snap)
        assert "ไม่มีรายการสินค้า" in html
        assert "<!DOCTYPE html>" in html
