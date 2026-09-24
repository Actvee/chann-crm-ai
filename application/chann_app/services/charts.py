"""Phase 17 — the "ตาราง/กราฟ" output, drawn as a picture.

The master spec asks the ad-hoc report engine for three output shapes:
text, table/chart, and a file. Text and files existed; a chart did not, so
"อยากดู report ยอดขายเป็นกราฟ" came back as a paragraph of numbers (owner,
8 Sep 2026). LINE has no table and no chart component — the only way to
show one in a chat is to send a picture — so this module draws one.

Everything here is a pure function: data in, PNG bytes out. No network, no
storage, no clock. That is deliberate:

  * a chart is easy to unit-test only while it has no I/O, and the thing
    most likely to break it (a long Thai label, an empty result, a value
    of zero) is a drawing problem, not a plumbing problem;
  * the same bytes must come out for the same numbers, so a test can
    assert on them and a reviewer can diff a picture. Nothing here reads
    the time — a caption naming "as of" is passed in by the caller.

House style is the guide pictures' (scripts/dev/render-guide-images.py):
the same paper, ink and OA accent colours, the same Sarabun face, so a
chart in the chat looks like it came from the same product as the
illustrated guide. Sized 1040x780 — LINE shows an image at the bubble's
width and lets the reader tap to zoom; this is legible on a phone without
tapping, and small enough to send in a second or two.
"""
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

W, H = 1040, 780
#: One figure needs a shorter card than a plot does.
VALUE_H = 520
#: Bars are thin marks. Capped, so a wide slot becomes air, not colour.
BAR_MAX_W = 56.0
#: A column band wider than this is paper, not information.
MAX_SLOT_W = 176.0

# The guide pictures' palette, unchanged (render-guide-images.py).
INK, SOFT, FAINT, LINE, PAPER, WHITE = "#1a2030", "#5a6478", "#8b93a3", "#e5e0d8", "#faf7f2", "#ffffff"
ACCENT = {"customer": "#e8731a", "technician": "#1f6fd6", "sales": "#178a50"}
ACCENT_SOFT = {"customer": "#fdeee2", "technician": "#e6f0fc", "sales": "#e7f6ee"}
GRID = "#eceae5"
#: A value below zero. Not the OA accent: a loss drawn in the same green as
#: a sale reads as a sale at a glance, and a negative is the one number on
#: a chart the reader must not miss.
NEGATIVE = "#c0392b"

FONT_DIR = Path(__file__).resolve().parents[1] / "static" / "fonts"
REGULAR = FONT_DIR / "Sarabun-Regular.ttf"
BOLD = FONT_DIR / "Sarabun-Bold.ttf"

NO_DATA = {"th": "ไม่มีข้อมูลในช่วงที่ขอ", "en": "No data in that range"}
OTHERS = {"th": "อื่น ๆ", "en": "others"}

# How many bars fit before the tail is folded into "อื่น ๆ". Past this the
# bars are thinner than their own labels and the picture stops answering
# the question it was asked.
MAX_BARS = 8
MAX_HBARS = 10
MAX_POINTS = 14


class ChartUnavailable(RuntimeError):
    """Pillow or the bundled font is missing — the caller falls back to text."""


@dataclass
class Chart:
    """One picture's worth of numbers, in the caller's language."""

    title: str
    points: list[tuple[str, float]] = field(default_factory=list)
    subtitle: str = ""
    footer: str = ""
    kind: str = "bar"  # "bar" | "hbar" | "line" | "value"
    oa: str = "sales"
    language: str = "th"
    money: bool = False


# ------------------------------------------------------------------ helpers

def _fonts():
    try:
        from PIL import ImageFont
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
        raise ChartUnavailable("Pillow is not installed") from exc
    if not REGULAR.exists() or not BOLD.exists():
        raise ChartUnavailable(f"the chart fonts are missing from {FONT_DIR}")

    cache: dict[tuple[int, bool], object] = {}

    def font(size: int, bold: bool = False):
        key = (size, bold)
        if key not in cache:
            cache[key] = ImageFont.truetype(str(BOLD if bold else REGULAR), size)
        return cache[key]

    return font


def _fmt(value: float, money: bool = False) -> str:
    """Thai/English number formatting: thousands separators, and decimals
    only when the number actually has them. A count of 7 reads "7", an
    average of 7.5 reads "7.5", and 1250000 baht reads "1,250,000" — never
    "1250000.00", which nobody scans correctly on a phone."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".") if not money else f"{number:,.2f}"


def _short(value: float, money: bool = False, language: str = "th") -> str:
    """The same number, shortened for an axis tick, where a seven-digit
    figure would collide with the gridline above it. Thai reads ล้าน
    (million) natively, so that is the unit used rather than M."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 1_000_000:
        head = f"{number / 1_000_000:,.2f}".rstrip("0").rstrip(".")
        return head + ("M" if language == "en" else " ล้าน")
    return _fmt(number, money)


def _axis_ticks(top_value: float, count: int, money: bool, language: str) -> list[str]:
    """Every tick on one axis in ONE unit.

    `_short` decided per value, so an axis topping out at 2.4 million read
    "2.4 ล้าน · 1.8 ล้าน · 1.2 ล้าน · 600,000 · 0" — two units on one
    scale, which makes the reader do arithmetic to compare two ticks
    (18 ก.ย. 2569). The unit is chosen once, from the top of the axis.
    """
    millions = abs(top_value) >= 1_000_000
    out = []
    for i in range(count):
        value = top_value * i / (count - 1)
        if not millions:
            out.append(_fmt(value, money))
            continue
        head = f"{value / 1_000_000:,.2f}".rstrip("0").rstrip(".")
        out.append(head + ("M" if language == "en" else " ล้าน") if value else "0")
    return out


def _ellipsis(draw, text: str, fnt, max_w: float) -> str:
    """A label cut to fit, with a real ellipsis. Thai has no spaces, so the
    cut is by character; the head is kept because that is where the name
    is ("แอร์ 12000 BTU ติดผนัง" → "แอร์ 12000 BT…")."""
    text = (text or "").strip()
    if not text or draw.textlength(text, font=fnt) <= max_w:
        return text
    cut = len(text)
    while cut > 1 and draw.textlength(text[:cut] + "…", font=fnt) > max_w:
        cut -= 1
    return text[:cut] + "…"


def _wrap_two(draw, text: str, fnt, max_w: float) -> list[str]:
    """An axis label on at most two lines. Breaks on a space when there is
    one, otherwise mid-run — Thai names have no spaces and a one-line
    truncation would lose the half that tells two members apart."""
    text = (text or "").strip()
    if not text or draw.textlength(text, font=fnt) <= max_w:
        return [text]
    space = -1
    for i, ch in enumerate(text):
        if ch == " " and draw.textlength(text[:i], font=fnt) <= max_w:
            space = i
    if space > 0:
        return [text[:space], _ellipsis(draw, text[space + 1:], fnt, max_w)]
    cut = len(text)
    while cut > 1 and draw.textlength(text[:cut], font=fnt) > max_w:
        cut -= 1
    tail = text[cut:]
    # A second line holding one or two characters is an orphan — it reads
    # as a mistake ("เสนอราคาแล้" / "ว") rather than as a wrapped label.
    # One line with an ellipsis is the tidier truth (18 ก.ย. 2569).
    if len(tail) <= 2:
        return [_ellipsis(draw, text, fnt, max_w)]
    return [text[:cut], _ellipsis(draw, tail, fnt, max_w)]


def _fit(draw, text: str, max_w: float, font, sizes, bold: bool = False):
    """The largest of `sizes` at which `text` fits, so a value label
    shrinks instead of running into its neighbour."""
    chosen = font(sizes[-1], bold)
    for size in sizes:
        fnt = font(size, bold)
        if draw.textlength(text, font=fnt) <= max_w:
            return fnt
        chosen = fnt
    return chosen


def _condense(points: list[tuple[str, float]], limit: int, language: str) -> list[tuple[str, float]]:
    """The top `limit` bars, with everything after them summed into one.
    Dropping the tail silently would make the total on the picture
    disagree with the total in the text answer beside it."""
    rows = [(str(label), float(value or 0)) for label, value in points]
    if len(rows) <= limit:
        return rows
    head = rows[: limit - 1]
    tail = sum(v for _, v in rows[limit - 1:])
    label = OTHERS.get(language) or OTHERS["th"]
    return head + [(f"{label} ({len(rows) - limit + 1})", tail)]


_STEPS = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)
_INT_STEPS = (1, 2, 3, 4, 5, 6, 8, 10)


def _axis_top(peak: float, integer: bool) -> float:
    """The top gridline: four equal steps of a round size, at or above the
    tallest bar. Picking the step first (rather than rounding the peak up)
    is what keeps the four labels readable — 0/3/6/9/12 for twelve deals,
    never 0/3.12/6.25/9.38/12.5, which is what a phone screen showed the
    owner the first time (a count is a whole number all the way up)."""
    if peak <= 0:
        return 4.0 if integer else 1.0
    quarter = peak / 4.0
    magnitude = 10.0 ** max(0, len(str(int(quarter))) - 1) if quarter >= 1 else (
        10.0 ** -len(str(int(1 / quarter)))
    )
    steps = _INT_STEPS if integer else _STEPS
    if integer:
        magnitude = max(magnitude, 1.0)
    for step in steps:
        if step * magnitude >= quarter:
            return float(step * magnitude * 4)
    return float(10 * magnitude * 4)


def _signed_axis(low: float, high: float, integer: bool) -> tuple[float, float, float]:
    """(bottom, top, step) for an axis that has to show a value below zero.

    One round step, chosen from the whole span the way `_axis_top` chooses
    it for a positive one, then as many whole steps below zero and above it
    as the data needs — so zero always sits on a gridline and every tick is
    a round number. Only used when a value IS negative: a chart without one
    keeps `_axis_top` and draws exactly what it drew before.
    """
    low, high = min(float(low), 0.0), max(float(high), 0.0)
    step = _axis_top(high - low, integer) / 4.0
    below = math.ceil(round(-low / step, 9)) if low < 0 else 0
    above = math.ceil(round(high / step, 9)) if high > 0 else 0
    if below + above < 2:
        above += 1
    return -below * step, above * step, step


def _tick_text(values: list[float], money: bool, language: str) -> list[str]:
    """`_axis_ticks`' one-unit rule, for ticks that may be negative."""
    millions = max(abs(v) for v in values) >= 1_000_000
    out = []
    for value in values:
        if not millions:
            out.append(_fmt(value, money))
            continue
        head = f"{value / 1_000_000:,.2f}".rstrip("0").rstrip(".")
        out.append(head + ("M" if language == "en" else " ล้าน") if value else "0")
    return out


def _integral(points: list[tuple[str, float]]) -> bool:
    return all(float(v) == int(float(v)) for _, v in points)


# ------------------------------------------------------------------ the frame

class _Canvas:
    def __init__(self, chart: Chart, height: int = H):
        from PIL import Image, ImageDraw

        self.chart = chart
        # A value card holds one figure and needs none of a bar chart's
        # plotting room; at the full 780 it is mostly empty paper, which
        # reads as a mistake rather than as restraint.
        self.h = height
        self.font = _fonts()
        self.accent = ACCENT.get(chart.oa, ACCENT["sales"])
        self.accent_soft = ACCENT_SOFT.get(chart.oa, ACCENT_SOFT["sales"])
        self.im = Image.new("RGB", (W, self.h), PAPER)
        self.d = ImageDraw.Draw(self.im)

    def resize(self, height: int) -> None:
        """Start again on a card of the right height.

        The row count is only known after the points are condensed, and a
        chart's height should follow its content — so the canvas is made
        once the caller knows how much it has to draw.
        """
        from PIL import Image, ImageDraw

        self.h = height
        self.im = Image.new("RGB", (W, self.h), PAPER)
        self.d = ImageDraw.Draw(self.im)

    def frame(self) -> None:
        d, f = self.d, self.font
        d.rounded_rectangle((24, 24, W - 24, self.h - 24), radius=26, fill=WHITE, outline=LINE, width=2)
        # The accent header, drawn the way the guide's dashboard mock-up is:
        # a rounded rectangle with its lower corners squared off.
        d.rounded_rectangle((24, 24, W - 24, 118), radius=26, fill=self.accent)
        d.rectangle((24, 80, W - 24, 118), fill=self.accent)
        title = _ellipsis(d, self.chart.title, f(34, True), W - 140 - (
            d.textlength(self.chart.subtitle, font=f(23)) + 40 if self.chart.subtitle else 0))
        d.text((56, 72), title, fill=WHITE, font=f(34, True), anchor="lm")
        if self.chart.subtitle:
            d.text((W - 56, 73), self.chart.subtitle, fill="#f2fbf6", font=f(23), anchor="rm")

    def footer(self) -> None:
        if self.chart.footer:
            self.d.text((56, self.h - 52), _ellipsis(self.d, self.chart.footer, self.font(22), W - 112),
                        fill=SOFT, font=self.font(22), anchor="lm")

    def empty(self) -> None:
        d, f = self.d, self.font
        text = NO_DATA.get(self.chart.language) or NO_DATA["th"]
        d.rounded_rectangle((150, 300, W - 150, 470), radius=18, fill=PAPER, outline=LINE, width=2)
        d.text((W // 2, 385), text, fill=FAINT, font=f(34), anchor="mm")

    def png(self) -> bytes:
        buf = io.BytesIO()
        # optimize=True keeps the picture under ~60 KB; PNG carries no
        # timestamp, so the same numbers always produce the same bytes.
        self.im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# ------------------------------------------------------------------ the charts

def _bar(chart: Chart) -> bytes:
    c = _Canvas(chart)
    d, f = c.d, c.font
    c.frame()
    points = _condense(chart.points, MAX_BARS, chart.language)
    if not points:
        c.empty()
        c.footer()
        return c.png()

    left, right, top, base = 150, W - 60, 168, 620
    if any(v < 0 for _, v in points):
        return _signed_bar(c, points, left, right, top, base)
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    # Hairline, solid, one step off the surface — the grid is chrome, and
    # chrome that competes with the bars is ink that is not data.
    # Four categories spread over 890px put 200px of paper between each
    # pair of bars, which reads as a chart with most of its columns
    # missing. The plot takes the width it needs and sits in the middle of
    # the card instead (18 ก.ย. 2569).
    slot = min((right - left) / len(points), MAX_SLOT_W)
    plot_w = slot * len(points)
    left += ((right - left) - plot_w) / 2
    # Capped, never filling the slot: a bar that fills its band reads as a
    # block of colour rather than a measurement, and the leftover IS the
    # design (dataviz: bars are thin marks, the band's remainder is air).
    width = min(BAR_MAX_W, slot * 0.46)
    ticks = _axis_ticks(top_value, 5, chart.money, chart.language)
    for i in range(5):
        y = base - (base - top) * i / 4
        d.line((left, y, right, y), fill=LINE if i == 0 else GRID, width=1)
        d.text((left - 16, y), ticks[i], fill=FAINT, font=f(20), anchor="rm")
    # One direct label, on the tallest — a number on every bar is chaos
    # and goes unread; the axis carries the rest, and the reply above the
    # picture lists every value in words anyway.
    tallest = max(range(len(points)), key=lambda i: points[i][1])
    for i, (label, value) in enumerate(points):
        centre = left + slot * (i + 0.5)
        height = (base - top) * (max(value, 0) / top_value) if top_value else 0
        x0, x1 = centre - width / 2, centre + width / 2
        if height >= 8:
            # Rounded at the data end, square on the baseline.
            d.rounded_rectangle((x0, base - height, x1, base), radius=6, fill=c.accent)
            d.rectangle((x0, base - 6, x1, base), fill=c.accent)
        else:
            # A tiny (or zero) value still gets a mark: an empty column
            # reads as "no answer", and the number beside it says otherwise.
            d.rectangle((x0, base - 3, x1, base), fill=c.accent if value else LINE)
        if i == tallest:
            d.text((centre, base - height - 22), _fmt(value, chart.money),
                   fill=INK, font=f(24, True), anchor="mm")
        lf = f(21)
        for n, line in enumerate(_wrap_two(d, label, lf, slot - 10)):
            d.text((centre, base + 26 + n * 27), line, fill=SOFT, font=lf, anchor="mm")
    c.footer()
    return c.png()


def _signed_bar(c: "_Canvas", points: list[tuple[str, float]],
                left: float, right: float, top: float, base: float) -> bytes:
    """`_bar` when a value is below zero (final fix, item 11).

    `max(value, 0)` drew a loss as the same 3-pixel sliver as a value of
    one. Here the axis runs from a round step below the lowest value to
    one above the highest, zero is a gridline of its own weight, and a
    negative bar hangs DOWN from it in NEGATIVE with its value beside it.
    Kept apart from `_bar` so a chart with no negative draws exactly the
    bytes it drew before.
    """
    chart, d, f = c.chart, c.d, c.font
    low, high, step = _signed_axis(min(v for _, v in points), max(v for _, v in points),
                                   _integral(points))
    steps = int(round((high - low) / step))

    def y_of(value: float) -> float:
        return base - (base - top) * (value - low) / (high - low)

    zero = y_of(0.0)
    slot = min((right - left) / len(points), MAX_SLOT_W)
    plot_w = slot * len(points)
    left += ((right - left) - plot_w) / 2
    width = min(BAR_MAX_W, slot * 0.46)
    values = [low + step * i for i in range(steps + 1)]
    for value, text in zip(values, _tick_text(values, chart.money, chart.language)):
        y = y_of(value)
        d.line((left, y, right, y), fill=GRID, width=1)
        d.text((left - 16, y), text, fill=FAINT, font=f(20), anchor="rm")
    d.line((left, zero, right, zero), fill=LINE, width=2)
    tallest = max(range(len(points)), key=lambda i: points[i][1])
    for i, (label, value) in enumerate(points):
        centre = left + slot * (i + 0.5)
        x0, x1 = centre - width / 2, centre + width / 2
        end = y_of(value)
        if value < 0:
            # Rounded at the data end (the bottom), square on zero; never
            # thinner than a visible mark, however small the loss.
            end = max(end, zero + 8)
            d.rounded_rectangle((x0, zero, x1, end), radius=6, fill=NEGATIVE)
            d.rectangle((x0, zero, x1, zero + 6), fill=NEGATIVE)
            # Every negative carries its number, above zero where nothing
            # else is drawn in its column.
            d.text((centre, zero - 22), _fmt(value, chart.money),
                   fill=NEGATIVE, font=f(24, True), anchor="mm")
        elif zero - end >= 8:
            d.rounded_rectangle((x0, end, x1, zero), radius=6, fill=c.accent)
            d.rectangle((x0, zero - 6, x1, zero), fill=c.accent)
        else:
            d.rectangle((x0, zero - 3, x1, zero), fill=c.accent if value else LINE)
        if i == tallest and value >= 0:
            d.text((centre, end - 22), _fmt(value, chart.money),
                   fill=INK, font=f(24, True), anchor="mm")
        lf = f(21)
        for n, line in enumerate(_wrap_two(d, label, lf, slot - 10)):
            d.text((centre, base + 26 + n * 27), line, fill=SOFT, font=lf, anchor="mm")
    c.footer()
    return c.png()


def _hbar(chart: Chart) -> bytes:
    c = _Canvas(chart)
    d, f = c.d, c.font
    points = _condense(chart.points, MAX_HBARS, chart.language)
    if not points:
        c.frame()
        c.empty()
        c.footer()
        return c.png()

    label_w = 300
    x0, x1 = 56 + label_w + 20, W - 150
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    # The card is as tall as the rows need. Centring five rows inside a
    # 780px card left a third of it empty above and below them, which
    # reads as a chart that failed to load rather than as breathing room.
    top, foot = 168.0, 110.0
    slot = 76.0
    needed = top + slot * len(points) + foot
    if needed > H:
        # Eight rows at the comfortable pitch do not fit: tighten the pitch
        # rather than let the last rows run off the card and land on the
        # footer, which is what happened the first time this card learned
        # to resize (18 ก.ย. 2569).
        slot = (H - foot - top) / len(points)
        needed = H
    c.resize(int(max(400, needed)))
    d = c.d
    c.frame()
    height = min(BAR_MAX_W, slot * 0.42)
    lf = f(23)
    for i, (label, value) in enumerate(points):
        centre = top + slot * (i + 0.5)
        length = (x1 - x0) * (max(value, 0) / top_value) if top_value else 0
        d.text((56 + label_w, centre), _ellipsis(d, label, lf, label_w), fill=SOFT, font=lf, anchor="rm")
        # No track behind the bar: a full-width block of tinted paper is
        # ink that carries no data, and it made every row read as full.
        if length >= 10:
            d.rounded_rectangle((x0, centre - height / 2, x0 + length, centre + height / 2),
                                radius=6, fill=c.accent)
            d.rectangle((x0, centre - height / 2, x0 + 6, centre + height / 2), fill=c.accent)
        elif value < 0:
            # This form has no room left of zero, so a loss is clamped —
            # and marked, in NEGATIVE with its number in the same colour,
            # rather than drawn as the sliver a value of one would get.
            d.rectangle((x0, centre - height / 2, x0 + 8, centre + height / 2), fill=NEGATIVE)
        elif value:
            d.rectangle((x0, centre - height / 2, x0 + 4, centre + height / 2), fill=c.accent)
        else:
            d.rectangle((x0, centre - 1.5, x0 + 4, centre + 1.5), fill=LINE)
        # The value at the tip: with no axis on this form, the tip labels
        # ARE the scale, so every row keeps one.
        d.text((x0 + max(length, 8 if value < 0 else 0) + 14, centre), _fmt(value, chart.money),
               fill=NEGATIVE if value < 0 else INK, font=f(23), anchor="lm")
    c.footer()
    return c.png()


def _line(chart: Chart) -> bytes:
    c = _Canvas(chart)
    d, f = c.d, c.font
    c.frame()
    points = [(str(k), float(v or 0)) for k, v in chart.points][-MAX_POINTS:]
    if not points:
        c.empty()
        c.footer()
        return c.png()

    left, right, top, base = 150, W - 90, 176, 608
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    ticks = _axis_ticks(top_value, 5, chart.money, chart.language)
    for i in range(5):
        y = base - (base - top) * i / 4
        d.line((left, y, right, y), fill=LINE if i == 0 else GRID, width=1)
        d.text((left - 16, y), ticks[i], fill=FAINT, font=f(20), anchor="rm")

    step = (right - left) / max(len(points) - 1, 1)
    xs = [left + step * i for i in range(len(points))] if len(points) > 1 else [(left + right) / 2]
    ys = [base - (base - top) * (max(v, 0) / top_value) for _, v in points]
    if len(points) > 1:
        # The area under the line, so the shape reads at a glance on a
        # phone; the line alone is a hairline at this size.
        d.polygon([(xs[0], base)] + list(zip(xs, ys)) + [(xs[-1], base)], fill=c.accent_soft)
        d.line(list(zip(xs, ys)), fill=c.accent, width=5, joint="curve")
    lf = f(21)
    # One direct label, at the end of the line — a number on every point is
    # the anti-pattern the whole form is built to avoid, and on this chart
    # the last one ran off the right edge of the card entirely
    # (18 ก.ย. 2569). The axis carries the rest.
    last = len(points) - 1
    for i, (label, value) in enumerate(points):
        x, y = xs[i], ys[i]
        if value < 0:
            # Clamped to the floor of this axis, so it is marked: a solid
            # NEGATIVE point with its own number, never a dot that reads
            # as zero (final fix, item 11).
            d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=NEGATIVE)
            text = _fmt(value, chart.money)
            vf = f(22, True)
            half = d.textlength(text, font=vf) / 2 + 8
            cx = min(max(x, 56 + half), W - 56 - half)
            d.rounded_rectangle((cx - half, y - 44, cx + half, y - 14), radius=8, fill=WHITE)
            d.text((cx, y - 29), text, fill=NEGATIVE, font=vf, anchor="mm")
        else:
            d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=WHITE, outline=c.accent, width=5)
        if i == last and value >= 0:
            text = _fmt(value, chart.money)
            vf = f(24, True)
            half = d.textlength(text, font=vf) / 2 + 8
            # Kept inside the card: a label pushed past the edge is worse
            # than no label, and the value is in the reply text as well.
            cx = min(max(x, 56 + half), W - 56 - half)
            # The label sits on a white pill: on a falling line the next
            # segment runs straight through the number otherwise, and a
            # struck-through figure is the one thing a chart must not show.
            d.rounded_rectangle((cx - half, y - 44, cx + half, y - 12), radius=8, fill=WHITE)
            d.text((cx, y - 28), text, fill=INK, font=vf, anchor="mm")
        for n, line in enumerate(_wrap_two(d, label, lf, step - 8 if len(points) > 1 else 200)):
            d.text((x, base + 26 + n * 27), line, fill=SOFT, font=lf, anchor="mm")
    c.footer()
    return c.png()


def _value(chart: Chart) -> bytes:
    """One number, drawn as a picture.

    Owner, 18 ก.ย. 2569: "ปัจจุบันถูกสร้างออกมาเป็นหน้าเว็บ ไม่ได้เป็นรูป
    และมีแต่ตัวอักษร ไม่ได้เป็นกราฟหรือรูปที่สร้างจาก AI เลย".

    A report that comes back as a single figure had no picture at all —
    `chart_for` returned None and the person was handed the HTML page,
    which for an ungrouped report is a line of text. They asked for an
    image and got a document.

    The rule that produced that is still right for its own case: a chart
    of ONE BAR says less than the sentence does. A number set large on the
    same card is a different thing — it is the tile every dashboard opens
    with, it reads at a glance on a phone, and it forwards into a chat as
    an image. So: bars when there is something to compare, a value card
    when there is one number, and never a page pretending to be a picture.
    """
    c = _Canvas(chart, height=VALUE_H)
    d, f = c.d, c.font
    c.frame()
    value = float(chart.points[0][1]) if chart.points else 0.0
    caption = chart.points[0][0] if chart.points else ""
    text = _fmt(value, chart.money)
    # Label above, figure below: the reading order of every KPI tile, and
    # it puts the large mass in the optical centre rather than the exact
    # one, where a single number always looks like it has slipped.
    top, bottom = 118, c.h - 24
    if caption:
        d.text((W / 2, top + (bottom - top) * 0.34), _ellipsis(d, caption, f(28), W - 200),
               fill=SOFT, font=f(28), anchor="mm")
    # The figure takes the room it needs: 140 for "7", smaller for
    # "1,250,000", so a seven-digit number never runs off the card.
    fnt = _fit(d, text, W - 200, f, (140, 120, 100, 84, 68, 54), bold=True)
    d.text((W / 2, top + (bottom - top) * (0.60 if caption else 0.5)), text,
           fill=c.accent, font=fnt, anchor="mm")
    c.footer()
    return c.png()


_KINDS = {"bar": _bar, "hbar": _hbar, "line": _line, "value": _value}


def render(chart: Chart) -> bytes:
    """PNG bytes for one chart. Raises ChartUnavailable when the drawing
    stack is not there; every other failure is the caller's data being
    wrong and should not be swallowed here."""
    draw = _KINDS.get(chart.kind)
    if draw is None:
        raise ValueError(f"unknown chart kind: {chart.kind!r}")
    return draw(chart)


def render_or_none(chart: Chart) -> bytes | None:
    """The same, for a caller whose answer stands without the picture: the
    chart is an extra output of a report that already has a text form, so
    a drawing failure must never cost the person their numbers."""
    try:
        return render(chart)
    except ChartUnavailable as exc:
        log.warning("chart not drawn: %s", exc)
        return None
    except Exception:  # noqa: BLE001
        log.exception("chart rendering failed")
        return None
