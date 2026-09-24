"""A picture has to be a picture, and it has to be paid for once.

Owner, 18 ก.ย. 2569:
  "ปัจจุบันถูกสร้างออกมาเป็นหน้าเว็บ ไม่ได้เป็นรูป และมีแต่ตัวอักษร
   ไม่ได้เป็นกราฟหรือรูปที่สร้างจาก AI เลย ไม่ถูกต้องตามหลักเลย"

Three faults met in one place. A report that came back as a single figure
drew nothing — `chart_for` returned None — so "สร้างรายงานด้วย AI" answered
with the HTML page, a document where an image was asked for. The month's
allowance had already been spent on it. And the dashboard, which reaches
the same engine, never touched the allowance at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for tier in ("application",):
    if str(ROOT / tier) not in sys.path:
        sys.path.insert(0, str(ROOT / tier))

import pytest  # noqa: E402

from chann_app.services import charts, reports_ai  # noqa: E402

SINGLE = {"rows": [], "total": 128}
GROUPED = {"rows": [{"key": "a", "label": "สมชาย", "value": 5}], "total": 5}


def _spec(**over):
    return reports_ai.validate_query_spec({"entity": "deals", "metric": "count", **over})


class TestOneNumberIsStillDrawn:
    def test_a_single_number_report_gets_a_chart_object(self):
        chart = reports_ai.chart_for(_spec(date_range="last_3_months"), SINGLE, "th")
        assert chart is not None
        assert chart.kind == "value"

    def test_and_that_object_renders_to_real_png_bytes(self):
        chart = reports_ai.chart_for(_spec(), SINGLE, "th")
        png = charts.render(chart)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(png) > 5000

    def test_the_number_and_what_it_counts_are_both_on_it(self):
        chart = reports_ai.chart_for(_spec(), SINGLE, "th")
        assert chart.points == [("จำนวนดีล", 128.0)]

    def test_the_title_carries_no_raw_keys(self):
        chart = reports_ai.chart_for(_spec(filter={"stage": "won"}), SINGLE, "th")
        assert "stage=won" not in chart.title
        assert "ปิดสำเร็จ" in chart.title

    def test_a_money_metric_is_drawn_as_money(self):
        chart = reports_ai.chart_for(_spec(metric="sum", field="amount"), SINGLE, "th")
        assert chart.money is True

    def test_grouped_reports_still_get_bars_not_a_value_card(self):
        chart = reports_ai.chart_for(_spec(group_by="stage"), GROUPED, "th")
        assert chart.kind in ("bar", "hbar")

    def test_zero_is_a_number_too(self):
        chart = reports_ai.chart_for(_spec(), {"rows": [], "total": None}, "th")
        assert charts.render(chart).startswith(b"\x89PNG")

    def test_a_seven_digit_figure_still_fits(self):
        chart = reports_ai.chart_for(
            _spec(metric="sum", field="amount"), {"rows": [], "total": 12_500_000.0}, "th"
        )
        assert charts.render(chart).startswith(b"\x89PNG")

    def test_every_kind_the_engine_can_ask_for_is_drawable(self):
        # A kind named by chart_for but missing from _KINDS raises at
        # render time, in production, on someone's report.
        assert set(charts._KINDS) >= {"bar", "hbar", "line", "value"}


class TestTheDashboardPaysToo:
    """`chart_quota.spend_one` had exactly one caller — the chat road."""

    def test_the_application_routes_charge_for_their_picture(self):
        src = (ROOT / "application" / "chann_app" / "routers_phase2.py").read_text()
        assert "_charge_for_the_picture" in src
        # Both doors: the question, and the edited spec re-run.
        assert src.count("await _charge_for_the_picture(") == 2

    def test_it_only_charges_when_there_is_a_picture(self):
        src = (ROOT / "application" / "chann_app" / "routers_phase2.py").read_text()
        body = src[src.index("async def _charge_for_the_picture"):]
        assert 'if not out.get("chart"):' in body
        assert "return out" in body

    def test_over_the_allowance_the_numbers_still_come_back(self):
        src = (ROOT / "application" / "chann_app" / "routers_phase2.py").read_text()
        body = src[src.index("async def _charge_for_the_picture"):]
        # Only the image is withheld — never the result or the files.
        assert 'out["chart"] = None' in body
        assert 'out["result"] = None' not in body


def _body_of(name: str) -> str:
    """One function's source. Round 21C (final fix, item 8) added a second
    picture road — `_handle_basic_report_picture` — earlier in chat.py, so
    a whole-file search found ITS spend first; each road is read on its own."""
    src = (ROOT / "application" / "chann_app" / "services" / "chat.py").read_text()
    start = src.index(f"async def {name}(")
    end = src.find("\nasync def ", start + 1)
    return src[start:end if end != -1 else None]


class TestTheChatChargesAfterItKnows:
    def test_the_spend_happens_once_a_chart_exists(self):
        src = _body_of("_handle_ai_report")
        spend = src.index("quota = await chart_quota.spend_one(")
        guard = src.rindex('if with_chart and out.get("chart"):', 0, spend)
        # Nothing between the guard and the spend but the import.
        assert "handle_report_request" not in src[guard:spend]

    def test_it_is_no_longer_spent_before_the_report_is_made(self):
        src = _body_of("_handle_ai_report")
        spend = src.index("quota = await chart_quota.spend_one(")
        call = src.index("out = await reports_ai.handle_report_request(")
        assert spend > call, "the allowance is being spent before the report exists"

    def test_the_basic_report_picture_is_charged_the_same_way(self):
        src = _body_of("_handle_basic_report_picture")
        spend = src.index("quota = await chart_quota.spend_one(")
        drawn = src.index("await chart_plan.publish_for_basic_report(")
        guard = src.rindex("if url:", 0, spend)
        assert drawn < guard < spend, "the picture credit is spent before a picture exists"


# --------------------------------------------------------------- the redraw

import io  # noqa: E402


def _im(png: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(png)).convert("RGB")


def _margin_is_clean(png: bytes, x: int) -> bool:
    """No ink in the 24px margin outside the card, down the whole height."""
    im = _im(png)
    paper = im.getpixel((4, im.size[1] // 2))
    return all(im.getpixel((x, y)) == paper for y in range(40, im.size[1] - 40, 5))


class TestTheAxisSpeaksOneUnit:
    """"2.4 ล้าน · 1.8 ล้าน · 1.2 ล้าน · 600,000 · 0" — two units on one
    scale, so comparing two ticks needed arithmetic (18 ก.ย. 2569)."""

    def test_a_millions_axis_is_millions_all_the_way_down(self):
        ticks = charts._axis_ticks(2_400_000, 5, True, "th")
        assert ticks[0] == "0"
        assert all(t.endswith(" ล้าน") for t in ticks[1:]), ticks

    def test_a_small_axis_stays_in_plain_numbers(self):
        ticks = charts._axis_ticks(40, 5, False, "th")
        assert ticks == ["0", "10", "20", "30", "40"]

    def test_english_uses_m(self):
        assert all(t.endswith("M") for t in charts._axis_ticks(2_400_000, 5, True, "en")[1:])


class TestNothingRunsOffTheCard:
    def test_the_line_chart_keeps_its_end_label_inside(self):
        # The endpoint label sat at the last point's x, which is the right
        # edge of the plot — "1,340,000" hung off the picture entirely.
        png = charts.render(charts.Chart(
            title="ยอดขายรายเดือน",
            points=[("เม.ย.", 820000), ("พ.ค.", 940000), ("มิ.ย.", 760000),
                    ("ก.ค.", 1180000), ("ส.ค.", 1020000), ("ก.ย.", 1340000)],
            kind="line", money=True, language="th",
        ))
        assert _margin_is_clean(png, charts.W - 7)

    def test_eight_rows_fit_inside_the_card(self):
        # The first content-height version let eight rows overflow the card
        # and land on the footer.
        png = charts.render(charts.Chart(
            title="งานแยกตามช่าง",
            points=[(f"ช่าง {i}", 30 - i * 2) for i in range(1, 9)],
            kind="hbar", language="th", footer="รวม 160 งาน",
        ))
        im = _im(png)
        assert im.size[1] <= charts.H
        assert _margin_is_clean(png, 7)


class TestTheCardIsAsTallAsItsContent:
    def test_three_rows_make_a_shorter_card_than_eight(self):
        def tall(n: int) -> int:
            png = charts.render(charts.Chart(
                title="งานแยกตามช่าง", points=[(f"ช่าง {i}", 10) for i in range(n)],
                kind="hbar", language="th"))
            return _im(png).size[1]

        assert tall(3) < tall(8) <= charts.H

    def test_a_value_card_has_its_own_height(self):
        png = charts.render(charts.Chart(
            title="จำนวนดีล", points=[("จำนวนดีล", 128)], kind="value", language="th"))
        assert _im(png).size == (charts.W, charts.VALUE_H)


class TestLabelsAreNotOrphaned:
    def test_a_two_character_tail_becomes_one_ellipsised_line(self):
        # "เสนอราคาแล้ว" wrapped to "เสนอราคาแล้" + "ว" under the narrower
        # column slot, and the lone "ว" read as a rendering fault.
        from PIL import Image, ImageDraw

        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        fnt = charts._fonts()(21)
        width = d.textlength("เสนอราคาแล้", font=fnt) + 2
        assert len(charts._wrap_two(d, "เสนอราคาแล้ว", fnt, width)) == 1

    def test_a_real_two_line_label_still_wraps(self):
        from PIL import Image, ImageDraw

        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        fnt = charts._fonts()(21)
        text = "แอร์ผนัง Daikin 12000 BTU Inverter"
        assert len(charts._wrap_two(d, text, fnt, d.textlength(text, font=fnt) / 2)) == 2
