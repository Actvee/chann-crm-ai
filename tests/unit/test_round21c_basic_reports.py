"""Round 21C — the model picks one of five; the numbers are already made."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import basic_reports  # noqa: E402

REPORT = {
    "key": "outstanding_invoices", "title_th": "ยอดค้างชำระ",
    "title_en": "Outstanding invoices", "unit": "money",
    "headline": {"label_th": "ค้างชำระรวม", "label_en": "Outstanding", "value": 15000.0},
    "rows": [
        {"key": "overdue", "label_th": "เลยกำหนด", "label_en": "Overdue", "value": 10000.0, "count": 1},
        {"key": "not_due", "label_th": "ยังไม่ถึงกำหนด", "label_en": "Not yet due", "value": 5000.0, "count": 1},
    ],
    "notes_th": ["นับใบที่ออกแล้วและชำระบางส่วน"], "notes_en": ["Issued and partially paid."],
    "generated_at": "2026-09-23T04:00:00+00:00",
}


class FakeAi:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        return self.answer


class TestChoosing:
    @pytest.mark.asyncio
    async def test_the_model_returns_a_key_and_only_a_key(self):
        ai = FakeAi('{"report": "outstanding_invoices"}')
        assert await basic_reports.choose_report("ใครยังไม่จ่ายบ้าง", client=ai) == "outstanding_invoices"

    @pytest.mark.asyncio
    async def test_a_key_outside_the_five_is_refused_not_guessed(self):
        ai = FakeAi('{"report": "ยอดขายของคู่แข่ง"}')
        assert await basic_reports.choose_report("อะไรก็ไม่รู้", client=ai) is None

    @pytest.mark.asyncio
    async def test_a_number_in_the_answer_is_ignored_entirely(self):
        """The model may not compute. Even if it volunteers a total, the
        chooser keeps the key and nothing else."""
        ai = FakeAi('{"report": "pipeline_value", "total": 999999}')
        assert await basic_reports.choose_report("ยอดในท่อ", client=ai) == "pipeline_value"


class TestRendering:
    def test_the_text_says_the_headline_the_rows_and_the_note(self):
        said = basic_reports.as_text(REPORT, "th")
        assert "ยอดค้างชำระ" in said
        assert "15,000" in said and "10,000" in said
        assert "เลยกำหนด" in said
        assert "นับใบที่ออกแล้ว" in said

    def test_it_stays_inside_the_reply_ceiling(self):
        assert len(basic_reports.as_text(REPORT, "th").splitlines()) <= 8

    @pytest.mark.parametrize("language", ["th", "en"])
    def test_the_worst_shape_is_exactly_the_eight_lines_the_docstring_says(self, language):
        """Final fix, item 12: the docstring said nine. Every part present —
        more rows than are shown, and a note — is 1 + 1 + ROWS_SHOWN + 1 + 1."""
        many = {**REPORT, "rows": [
            {"key": f"r{i}", "label_th": f"แถว {i}", "label_en": f"Row {i}",
             "value": float(i), "count": i} for i in range(12)]}
        said = basic_reports.as_text(many, language)
        assert len(said.splitlines()) == 4 + basic_reports.ROWS_SHOWN == 8, said
        assert "eight" in basic_reports.as_text.__doc__ and "nine" not in basic_reports.as_text.__doc__

    def test_a_count_report_is_not_printed_as_money(self):
        counted = {**REPORT, "unit": "count",
                   "headline": {"label_th": "งานค้าง", "label_en": "Open", "value": 3.0},
                   "rows": [{"key": "a", "label_th": "สมชาย", "label_en": "Somchai", "value": 3.0, "count": 3}]}
        said = basic_reports.as_text(counted, "th")
        assert "บาท" not in said
        assert "3 งาน" in said

    def test_english_money_carries_a_unit_like_the_rest_of_chat(self):
        """chat.py's own convention always appends a unit word — 'บาท' in
        Thai, 'THB' in English (DEAL_ZERO_TOTAL, chat.py:15205; 'Total:
        {:.2f} THB', chat.py:19300/25915). A bare number in English is not
        that convention."""
        said = basic_reports.as_text(REPORT, "en")
        assert "15,000.00 THB" in said
        assert "10,000.00 THB" in said

    def test_a_count_report_carries_no_currency_in_either_language(self):
        counted = {**REPORT, "unit": "count",
                   "headline": {"label_th": "งานค้าง", "label_en": "Open", "value": 3.0},
                   "rows": [{"key": "a", "label_th": "สมชาย", "label_en": "Somchai", "value": 3.0, "count": 3}]}
        said = basic_reports.as_text(counted, "en")
        assert "บาท" not in said and "THB" not in said


class TestTruncation:
    """`open_jobs_by_tech` (data/chann_data/repositories/basic_reports.py)
    returns one row per technician with an open ticket — unbounded, unlike
    every other report. A shop with more rows than fit must still be told
    the truth: which rows are shown, and that more exist."""

    def _report(self, n_rows: int) -> dict:
        rows = [
            {"key": f"tech{i}", "label_th": f"ช่างสมมติชื่อยาวมากลำดับที่ {i}",
             "label_en": f"A rather long imaginary technician name number {i}",
             "value": float(10 - i), "count": 10 - i}
            for i in range(n_rows)
        ]
        return {
            "key": "open_jobs_by_tech", "title_th": "งานซ่อมค้างแยกตามช่าง",
            "title_en": "Open jobs by technician", "unit": "count",
            "headline": {"label_th": "งานค้างทั้งหมด", "label_en": "Open jobs",
                         "value": float(sum(r["value"] for r in rows))},
            "rows": rows,
            "notes_th": ["นับสถานะ เปิดอยู่ · มอบหมายแล้ว · กำลังทำ"],
            "notes_en": ["Counting open, assigned and in progress."],
            "generated_at": "2026-09-23T04:00:00+00:00",
        }

    def test_the_largest_real_shape_stays_inside_the_wire_limits_and_says_more_exist(self):
        # LONG_LINES, LONG_CHARS = 15, 700 — scripts/dev/simulate-phrasings.py's
        # own real ceiling for a chat reply, not this module's own softer
        # 8-line budget.
        said = basic_reports.as_text(self._report(12), "th")
        lines = said.splitlines()
        assert len(lines) <= 15, said
        assert len(said) <= 700, said
        assert "อีก 8 รายการ" in said, said
        assert "ดูทั้งหมดบนแดชบอร์ด" in said, said

    def test_the_same_shape_in_english(self):
        said = basic_reports.as_text(self._report(12), "en")
        assert len(said.splitlines()) <= 15
        assert len(said) <= 700
        assert "8 more" in said and "dashboard" in said

    def test_a_short_report_gets_no_more_line_at_all(self):
        said = basic_reports.as_text(self._report(3), "th")
        assert "รายการ (ดู" not in said
        assert "และอีก" not in said
