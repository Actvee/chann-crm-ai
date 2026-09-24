"""Round 21C — a deal dict is worth what the Data tier says it is worth."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.deal_value import deal_value  # noqa: E402


class TestPrecedence:
    def test_the_typed_amount_wins_over_the_lines(self):
        deal = {"amount": "99000.00",
                "products": [{"qty": 1, "quoted_unit_price": "1000.00"}]}
        assert deal_value(deal) == Decimal("99000.00")

    def test_lines_are_used_when_no_amount_was_typed(self):
        deal = {"amount": None,
                "products": [{"qty": 2, "quoted_unit_price": "15000.00"}]}
        assert deal_value(deal) == Decimal("30000.00")

    def test_a_typed_zero_is_a_typed_value_not_a_missing_one(self):
        deal = {"amount": "0",
                "products": [{"qty": 1, "quoted_unit_price": "500.00"}]}
        assert deal_value(deal) == Decimal("0")

    def test_neither_is_zero(self):
        assert deal_value({"amount": None, "products": []}) == Decimal("0")

    def test_rubbish_does_not_raise(self):
        assert deal_value({"amount": "หมื่นห้า", "products": []}) == Decimal("0")


class TestTheOtherTwoRoadsDelegate:
    def test_sales_charts_and_chat_use_the_same_helper(self):
        from chann_app.services import sales_charts
        from chann_app.services import chat

        deal = {"amount": "99000.00", "products": [{"qty": 1, "quoted_unit_price": "1000.00"}]}
        assert sales_charts.deal_value(deal) == Decimal("99000.00")
        assert chat._deal_value(deal) == Decimal("99000.00")
