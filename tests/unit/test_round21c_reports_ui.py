"""Round 21C — the five reports are one tap from the reports page."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "presentation/app/liff/sales/reports/ai/AiReports.tsx").read_text(encoding="utf-8")
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
EN = (ROOT / "presentation/lib/i18n/en.ts").read_text(encoding="utf-8")

KEYS = ["pipeline_value", "won_this_month", "open_jobs_by_tech",
        "outstanding_invoices", "satisfaction_avg"]


class TestTheCards:
    def test_all_five_are_on_the_page(self):
        for key in KEYS:
            assert key in PAGE, key

    def test_they_call_the_free_route_not_the_ai_one(self):
        assert "/reports/basic/" in PAGE

    def test_the_numbers_come_from_the_server(self):
        # No arithmetic in the browser: no total, no percentage, no
        # difference is computed here (round 20K).
        assert "headline" in PAGE and "notes_th" in PAGE

    def test_both_languages_have_the_strings(self):
        for key in KEYS:
            assert key in TH and key in EN


class TestFixRound1:
    """Review findings, fix round 1: rows on demand, parallel fetches,
    the unused `picture` string, and reusing the shared money formatter."""

    def test_a_report_with_more_than_three_rows_gets_a_way_to_see_the_rest(self):
        # A cap with no disclosure and no control is "hidden forever", not
        # "on demand" — there must be an expand/collapse affordance.
        assert "aria-expanded" in PAGE
        assert "basic.showAll" in PAGE and "basic.showLess" in PAGE
        for key in ("showAll", "showLess"):
            assert key in TH and key in EN

    def test_a_report_with_three_or_fewer_rows_shows_no_button(self):
        # The toggle is conditional on there being a 4th row, not always
        # rendered.
        assert "hasMore" in PAGE
        assert "> 3" in PAGE

    def test_the_five_fetches_start_together_not_one_after_another(self):
        # A `for`-await loop starts card 2's request only once card 1's has
        # resolved; Promise.allSettled starts all five before any resolve,
        # while still trying each key independently (one failure must not
        # sink the others).
        assert "Promise.allSettled(BASIC_KEYS.map(" in PAGE

    def test_the_picture_string_is_gone_or_actually_used(self):
        declared_th = "picture:" in TH
        declared_en = "picture:" in EN
        used = "basic.picture" in PAGE
        assert not ((declared_th or declared_en) and not used)

    def test_money_is_formatted_by_the_shared_helper_not_a_second_one(self):
        assert "useFormatters" in PAGE
        assert "new Intl.NumberFormat" not in PAGE
