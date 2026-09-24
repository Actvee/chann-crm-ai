"""Phase 17 "ตาราง/กราฟ" — the chart renderer (services/charts.py).

The renderer is a pure function on purpose, so everything that can go
wrong with a picture can be asserted here: the bytes are a PNG of the
right size, the same numbers always draw the same picture, an empty result
draws a picture that says so instead of raising, and a Thai product name
long enough to cross the whole canvas stays inside its own box.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import charts  # noqa: E402

BY_STAGE = [("ใหม่", 12), ("เสนอราคาแล้ว", 7), ("ปิดสำเร็จ", 3), ("ไม่สำเร็จ", 1)]
BY_MONTH = [("เม.ย. 69", 120000), ("พ.ค. 69", 245000), ("มิ.ย. 69", 0),
            ("ก.ค. 69", 1250000), ("ส.ค. 69", 640000), ("ก.ย. 69", 318000)]
LONG_THAI = "แอร์ติดผนังระบบอินเวอร์เตอร์ ขนาด 12000 บีทียู รุ่นประหยัดไฟเบอร์ 5 พร้อมติดตั้งและรับประกันคอมเพรสเซอร์ 10 ปี"


def _image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


class TestBytes:
    @pytest.mark.parametrize("kind,points", [
        ("bar", BY_STAGE), ("hbar", BY_STAGE), ("line", BY_MONTH),
    ])
    def test_every_kind_is_a_png_of_the_declared_width(self, kind, points):
        png = charts.render(charts.Chart(
            title="ยอดขาย", subtitle="เดือนนี้", points=points, kind=kind, footer="รวม 23"))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = _image(png).size
        # The width is fixed — LINE shows an image at the bubble's width.
        # The HEIGHT follows the content since 18 ก.ย. 2569: five rows
        # centred inside a 780 px card left a third of it blank above and
        # below them, which reads as a chart that failed to load.
        assert width == charts.W
        assert charts.VALUE_H // 2 <= height <= charts.H

    def test_a_chart_stays_small_enough_to_send_over_line(self):
        png = charts.render(charts.Chart(title="ยอดขายรายเดือน", points=BY_MONTH, kind="line", money=True))
        # LINE accepts up to 10 MB; anything near that is a slow message on
        # a phone. A flat-colour chart has no business exceeding 200 KB.
        assert len(png) < 200_000

    def test_the_same_numbers_always_draw_the_same_picture(self):
        chart = charts.Chart(title="ยอดขายตามสถานะดีล", subtitle="ดีลทั้งหมด · บาท",
                             points=BY_STAGE, footer="รวม 23 ดีล", money=True)
        assert charts.render(chart) == charts.render(chart)

    def test_an_unknown_kind_is_a_programming_error_not_a_blank_picture(self):
        with pytest.raises(ValueError):
            charts.render(charts.Chart(title="x", points=BY_STAGE, kind="pie"))


class TestEmptyState:
    @pytest.mark.parametrize("kind", ["bar", "hbar", "line"])
    def test_no_data_draws_a_picture_that_says_so(self, kind):
        png = charts.render(charts.Chart(title="ยอดขายรายเดือน", points=[], kind=kind))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert _image(png).size == (charts.W, charts.H)

    def test_all_zero_values_still_draw_bars_not_an_empty_frame(self):
        # A shop with four stages and nothing in any of them asked a real
        # question and must see the four stages, not "ไม่มีข้อมูล".
        png = charts.render(charts.Chart(title="ยอดขาย", points=[(s, 0) for s, _ in BY_STAGE]))
        assert _image(png).size == (charts.W, charts.H)


class TestLabels:
    def test_a_very_long_thai_label_never_leaves_the_card(self):
        png = charts.render(charts.Chart(
            title=LONG_THAI, subtitle="จากดีลที่ปิดสำเร็จ · บาท",
            points=[(LONG_THAI, 318000), ("พัดลม", 4000)], kind="hbar", money=True))
        im = _image(png).convert("RGB")
        paper = im.getpixel((4, 400))
        # The 24 px margin outside the card is untouched paper on every
        # side: ink there means a label ran off the picture.
        for y in range(40, _image(png).size[1] - 40, 7):
            assert im.getpixel((6, y)) == paper
            assert im.getpixel((charts.W - 7, y)) == paper

    def test_a_long_vertical_axis_label_wraps_to_two_lines_at_most(self):
        png = charts.render(charts.Chart(
            title="ยอดขายรายคน", points=[(LONG_THAI, 5), ("สมหญิง", 3)], kind="bar"))
        im = _image(png).convert("RGB")
        paper = im.getpixel((4, 400))
        # Nothing is drawn in the bottom margin, where a third line would land.
        for x in range(40, charts.W - 40, 9):
            assert im.getpixel((x, charts.H - 6)) == paper

    def test_more_bars_than_fit_are_folded_into_one_labelled_rest(self):
        rows = [(f"คนที่ {i}", 10 - i) for i in range(10)]
        kept = charts._condense(rows, charts.MAX_BARS, "th")
        assert len(kept) == charts.MAX_BARS
        assert kept[-1][0].startswith("อื่น ๆ")
        # The picture's total still equals the data's total: a chart that
        # quietly dropped the tail would disagree with the text beside it.
        assert sum(v for _, v in kept) == sum(v for _, v in rows)
        assert charts._condense(rows, 20, "en") == [(k, float(v)) for k, v in rows]


class TestAxis:
    def test_counts_get_whole_number_gridlines(self):
        # 12 deals used to label the axis 0 / 3.12 / 6.25 / 9.38 / 12.5.
        top = charts._axis_top(12, integer=True)
        assert top == 12 and all(float(top * i / 4).is_integer() for i in range(5))

    def test_money_gets_round_gridlines_above_the_peak(self):
        top = charts._axis_top(1_250_000, integer=False)
        assert top >= 1_250_000 and top % 100_000 == 0

    def test_an_all_zero_series_still_has_an_axis(self):
        assert charts._axis_top(0, integer=True) > 0
        assert charts._axis_top(0, integer=False) > 0


class TestNumbers:
    @pytest.mark.parametrize("value,expected", [
        (0, "0"), (7, "7"), (1250000, "1,250,000"), (7.5, "7.5"), (31800.0, "31,800"),
    ])
    def test_thai_number_formatting(self, value, expected):
        assert charts._fmt(value) == expected

    def test_axis_ticks_shorten_millions_in_each_language(self):
        assert charts._short(1_250_000, language="th").endswith("ล้าน")
        assert charts._short(1_250_000, language="en").endswith("M")
        assert charts._short(31_800, language="th") == "31,800"


class TestShippedFonts:
    def test_the_runtime_fonts_are_in_the_application_package(self):
        assert charts.REGULAR.exists() and charts.BOLD.exists()

    def test_they_are_the_same_files_the_guide_renderer_uses(self):
        guide = ROOT / "scripts" / "dev" / "guide-fonts"
        assert charts.REGULAR.read_bytes() == (guide / "Sarabun-Regular.ttf").read_bytes()
        assert charts.BOLD.read_bytes() == (guide / "Sarabun-Bold.ttf").read_bytes()


class TestDegradation:
    def test_a_missing_font_is_reported_not_crashed_through(self, monkeypatch):
        monkeypatch.setattr(charts, "REGULAR", Path("/nowhere/Sarabun-Regular.ttf"))
        with pytest.raises(charts.ChartUnavailable):
            charts.render(charts.Chart(title="x", points=BY_STAGE))
        # The caller's form of the same call answers None, so a report with
        # no picture still answers with its numbers.
        assert charts.render_or_none(charts.Chart(title="x", points=BY_STAGE)) is None


class TestANegativeNeverVanishes:
    """Final fix, item 11: `max(value, 0)` drew a negative as a 3-pixel
    sliver on the baseline — the same mark as a value of one. A refund
    month, a loss, a negative balance must be seen on the picture, not
    only in the words above it."""

    WITH_A_LOSS = [("ก.ค. 69", 120000), ("ส.ค. 69", -45000), ("ก.ย. 69", 80000)]

    @staticmethod
    def _negative_pixels(png: bytes) -> list[tuple[int, int]]:
        im = _image(png).convert("RGB")
        want = Image.new("RGB", (1, 1), charts.NEGATIVE).getpixel((0, 0))
        width, height = im.size
        return [(x, y) for y in range(0, height, 2) for x in range(0, width, 2)
                if im.getpixel((x, y)) == want]

    def test_a_negative_bar_hangs_below_the_zero_line(self):
        png = charts.render(charts.Chart(title="กำไรรายเดือน", points=self.WITH_A_LOSS,
                                         kind="bar", money=True))
        drawn = self._negative_pixels(png)
        low, high, _step = charts._signed_axis(-45000, 120000, integer=True)
        top, base = 168, 620
        zero_y = base - (base - top) * (0 - low) / (high - low)
        below = [y for _, y in drawn if y > zero_y]
        above = [y for _, y in drawn if y < zero_y]
        # A real bar, not a sliver: 45,000 on this scale is ~90 px tall,
        # hanging DOWN from zero …
        assert len(below) > 200, len(below)
        assert max(below) > zero_y + 40
        # … and the only NEGATIVE ink above zero is its own number.
        assert above and min(above) > zero_y - 40

    def test_the_axis_covers_both_ends_and_crosses_zero_on_a_gridline(self):
        low, high, step = charts._signed_axis(-45000, 120000, integer=True)
        assert low <= -45000 and high >= 120000
        assert (0 - low) % step == 0 and (high - low) % step == 0
        assert 3 <= (high - low) / step <= 6

    @pytest.mark.parametrize("kind", ["hbar", "line"])
    def test_the_other_kinds_mark_it_where_they_cannot_draw_it(self, kind):
        png = charts.render(charts.Chart(title="กำไร", points=self.WITH_A_LOSS,
                                         kind=kind, money=True))
        assert len(self._negative_pixels(png)) > 20

    @pytest.mark.parametrize("kind,points", [("bar", BY_STAGE), ("hbar", BY_STAGE),
                                             ("line", BY_MONTH)])
    def test_a_chart_with_no_negative_is_unchanged(self, kind, points):
        png = charts.render(charts.Chart(title="ยอดขาย", points=points, kind=kind))
        assert self._negative_pixels(png) == []
