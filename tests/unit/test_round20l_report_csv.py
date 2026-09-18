"""A downloaded report has to say what it is.

Owner, 18 ก.ย. 2569: "CSV ที่เปิดให้ดาวน์โหลด โหลดมาแล้วดูไม่รู้เรื่องเลย
มีแต่ column ผลรวมในบางรายงาน".

A report with no grouping produced a file of exactly two lines — the word
"จำนวน" and a number. Correct, and useless: nothing said what was counted,
over which period, under which filters, or when it was produced. Two such
files in a downloads folder were indistinguishable.

The same pass fixed `describe()`, which printed the raw pair "stage=won"
into the report title, the chart title and the chat reply — a raw key in
front of a person, which rule 4 forbids.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "application") not in sys.path:
    sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import reports_ai  # noqa: E402

AT = datetime(2026, 9, 18, 9, 20, tzinfo=timezone.utc)
GROUPED = {"rows": [{"key": "a", "label": "สมชาย", "value": 5},
                    {"key": "b", "label": "สมหญิง", "value": 2}], "total": 7}
SINGLE = {"rows": [], "total": 128}


def _rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8").lstrip("﻿"))))


def _csv(spec_in: dict, result: dict, language: str = "th", **kw) -> list[list[str]]:
    spec = reports_ai.validate_query_spec(spec_in)
    return _rows(reports_ai.report_csv(spec, result, language, generated_at=AT, **kw))


class TestTheHeaderBlock:
    def test_a_single_number_report_is_no_longer_two_lines(self):
        rows = _csv({"entity": "deals", "metric": "count", "date_range": "last_3_months"}, SINGLE)
        assert len(rows) > 2
        # The number is still there, and still last.
        assert rows[-1] == ["128"]

    def test_it_says_what_was_counted_and_over_what(self):
        rows = _csv(
            {"entity": "deals", "metric": "count", "filter": {"stage": "won"},
             "date_range": "last_3_months"},
            SINGLE, company_name="ร้านชาญแอร์",
        )
        flat = {r[0]: r[1] for r in rows if len(r) == 2}
        assert flat["ร้าน"] == "ร้านชาญแอร์"
        assert flat["ดูข้อมูล"] == "ดีล"
        assert flat["ตัวเลข"] == "จำนวน"
        assert flat["ช่วงเวลา"] == "3 เดือนล่าสุด"
        assert flat["นับจากวันที่"] == "วันที่สร้าง"
        assert flat["เงื่อนไข"] == "สถานะ ปิดสำเร็จ"
        assert flat["ออกรายงานเมื่อ"].startswith("2026-09-18")

    def test_no_filter_says_so_rather_than_leaving_it_blank(self):
        rows = _csv({"entity": "customers", "metric": "count"}, SINGLE)
        flat = {r[0]: r[1] for r in rows if len(r) == 2}
        assert flat["เงื่อนไข"] == "ไม่มี"
        assert flat["ช่วงเวลา"] == "ทุกช่วงเวลา"

    def test_a_numeric_metric_names_the_field_it_summed(self):
        rows = _csv({"entity": "deals", "metric": "sum", "field": "amount"}, SINGLE)
        flat = {r[0]: r[1] for r in rows if len(r) == 2}
        assert flat["คิดจากช่อง"] == "มูลค่า"

    def test_a_blank_line_separates_the_header_from_the_table(self):
        rows = _csv({"entity": "tickets", "metric": "count", "group_by": "assigned_to"}, GROUPED)
        assert [] in rows
        after = rows[rows.index([]) + 1:]
        assert after[0] == ["ช่าง", "จำนวน"]

    def test_the_grouped_table_itself_is_unchanged(self):
        # The data section is what every existing reader already parses.
        rows = _csv({"entity": "tickets", "metric": "count", "group_by": "assigned_to"}, GROUPED)
        after = rows[rows.index([]) + 1:]
        assert after[1:] == [["สมชาย", "5"], ["สมหญิง", "2"], ["รวม", "7"]]

    def test_english_header(self):
        rows = _csv({"entity": "deals", "metric": "count"}, SINGLE, language="en")
        flat = {r[0]: r[1] for r in rows if len(r) == 2}
        assert flat["Data"] == "deals"
        assert flat["Period"] == "All time"

    def test_the_bom_survives_the_header(self):
        spec = reports_ai.validate_query_spec({"entity": "deals", "metric": "count"})
        assert reports_ai.report_csv(spec, SINGLE, "th").startswith("﻿".encode("utf-8"))


class TestNoRawKeysInFrontOfAPerson:
    def test_a_filter_reads_as_words(self):
        spec = reports_ai.validate_query_spec(
            {"entity": "deals", "metric": "count", "filter": {"stage": "won"}}
        )
        text = reports_ai.describe(spec, "th")
        assert "สถานะ ปิดสำเร็จ" in text
        assert "stage=won" not in text and "won" not in text

    def test_every_whitelisted_enum_value_has_words(self):
        # A value the whitelist accepts but this table has never heard of
        # would print raw. Measured against the whitelist, not assumed.
        for entity, table in reports_ai.ALLOWED_ENTITIES.items():
            for key, values in table["enums"].items():
                for value in values:
                    words = reports_ai.VALUE_LABEL.get(key, {}).get(value)
                    assert words, f"{entity}.{key}={value} has no words"
                    assert reports_ai._t(words, "th") != value

    def test_an_id_filter_still_shows_the_id(self):
        # owner_member_id has no label table and should not invent one.
        spec = reports_ai.validate_query_spec({
            "entity": "deals", "metric": "count",
            "filter": {"owner_member_id": "11111111-1111-1111-1111-111111111111"},
        })
        assert "11111111" in reports_ai.describe(spec, "th")
        assert "ผู้ดูแล" in reports_ai.describe(spec, "th")
