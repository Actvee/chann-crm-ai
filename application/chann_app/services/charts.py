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
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

W, H = 1040, 780

# The guide pictures' palette, unchanged (render-guide-images.py).
INK, SOFT, FAINT, LINE, PAPER, WHITE = "#1a2030", "#5a6478", "#8b93a3", "#e5e0d8", "#faf7f2", "#ffffff"
ACCENT = {"customer": "#e8731a", "technician": "#1f6fd6", "sales": "#178a50"}
ACCENT_SOFT = {"customer": "#fdeee2", "technician": "#e6f0fc", "sales": "#e7f6ee"}
GRID = "#eceae5"

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
    kind: str = "bar"  # "bar" | "hbar" | "line"
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
    return [text[:cut], _ellipsis(draw, text[cut:], fnt, max_w)]


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


def _integral(points: list[tuple[str, float]]) -> bool:
    return all(float(v) == int(float(v)) for _, v in points)


# ------------------------------------------------------------------ the frame

class _Canvas:
    def __init__(self, chart: Chart):
        from PIL import Image, ImageDraw

        self.chart = chart
        self.font = _fonts()
        self.accent = ACCENT.get(chart.oa, ACCENT["sales"])
        self.accent_soft = ACCENT_SOFT.get(chart.oa, ACCENT_SOFT["sales"])
        self.im = Image.new("RGB", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.im)

    def frame(self) -> None:
        d, f = self.d, self.font
        d.rounded_rectangle((24, 24, W - 24, H - 24), radius=26, fill=WHITE, outline=LINE, width=2)
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
            self.d.text((56, H - 52), _ellipsis(self.d, self.chart.footer, self.font(22), W - 112),
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

    left, right, top, base = 150, W - 60, 176, 608
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    for i in range(5):
        y = base - (base - top) * i / 4
        d.line((left, y, right, y), fill=GRID if i else LINE, width=2 if i == 0 else 1)
        d.text((left - 16, y), _short(top_value * i / 4, chart.money, chart.language), fill=FAINT, font=f(20), anchor="rm")

    slot = (right - left) / len(points)
    width = min(96.0, slot * 0.6)
    for i, (label, value) in enumerate(points):
        centre = left + slot * (i + 0.5)
        height = (base - top) * (max(value, 0) / top_value) if top_value else 0
        x0, x1 = centre - width / 2, centre + width / 2
        if height >= 8:
            d.rounded_rectangle((x0, base - height, x1, base), radius=8, fill=c.accent)
            d.rectangle((x0, base - 8, x1, base), fill=c.accent)
        else:
            # A tiny (or zero) value still gets a mark: an empty column
            # reads as "no answer", and the number beside it says otherwise.
            d.rectangle((x0, base - 3, x1, base), fill=c.accent if value else LINE)
        vf = _fit(d, _fmt(value, chart.money), slot - 8, f, (24, 21, 18, 16), bold=True)
        d.text((centre, base - height - 20), _fmt(value, chart.money), fill=INK, font=vf, anchor="mm")
        lf = f(21)
        for n, line in enumerate(_wrap_two(d, label, lf, slot - 10)):
            d.text((centre, base + 26 + n * 27), line, fill=SOFT, font=lf, anchor="mm")
    c.footer()
    return c.png()


def _hbar(chart: Chart) -> bytes:
    c = _Canvas(chart)
    d, f = c.d, c.font
    c.frame()
    points = _condense(chart.points, MAX_HBARS, chart.language)
    if not points:
        c.empty()
        c.footer()
        return c.png()

    label_w, top, bottom = 300, 168, 612
    x0, x1 = 56 + label_w + 20, W - 150
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    # Few rows are centred rather than stretched: five bars spread over the
    # full height read as a chart with half its rows missing.
    slot = min((bottom - top) / len(points), 76.0)
    top += ((bottom - top) - slot * len(points)) / 2
    height = min(46.0, slot * 0.62)
    lf = f(23)
    for i, (label, value) in enumerate(points):
        centre = top + slot * (i + 0.5)
        length = (x1 - x0) * (max(value, 0) / top_value) if top_value else 0
        d.text((56 + label_w, centre), _ellipsis(d, label, lf, label_w), fill=INK, font=lf, anchor="rm")
        d.rounded_rectangle((x0, centre - height / 2, x1, centre + height / 2), radius=8, fill=c.accent_soft)
        if length >= 10:
            d.rounded_rectangle((x0, centre - height / 2, x0 + length, centre + height / 2), radius=8, fill=c.accent)
        elif value:
            d.rectangle((x0, centre - height / 2, x0 + 4, centre + height / 2), fill=c.accent)
        vf = _fit(d, _fmt(value, chart.money), W - 66 - (x0 + length), f, (24, 21, 18), bold=True)
        d.text((x0 + length + 14, centre), _fmt(value, chart.money), fill=INK, font=vf, anchor="lm")
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

    left, right, top, base = 150, W - 60, 176, 608
    top_value = _axis_top(max(v for _, v in points), _integral(points))
    for i in range(5):
        y = base - (base - top) * i / 4
        d.line((left, y, right, y), fill=GRID if i else LINE, width=2 if i == 0 else 1)
        d.text((left - 16, y), _short(top_value * i / 4, chart.money, chart.language), fill=FAINT, font=f(20), anchor="rm")

    step = (right - left) / max(len(points) - 1, 1)
    xs = [left + step * i for i in range(len(points))] if len(points) > 1 else [(left + right) / 2]
    ys = [base - (base - top) * (max(v, 0) / top_value) for _, v in points]
    if len(points) > 1:
        # The area under the line, so the shape reads at a glance on a
        # phone; the line alone is a hairline at this size.
        d.polygon([(xs[0], base)] + list(zip(xs, ys)) + [(xs[-1], base)], fill=c.accent_soft)
        d.line(list(zip(xs, ys)), fill=c.accent, width=5, joint="curve")
    lf = f(21)
    for i, (label, value) in enumerate(points):
        x, y = xs[i], ys[i]
        d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=WHITE, outline=c.accent, width=5)
        text = _fmt(value, chart.money)
        vf = _fit(d, text, step - 6 if len(points) > 1 else 200, f, (23, 20, 17), bold=True)
        # The label sits on a white pill: on a falling line the next
        # segment runs straight through the number otherwise, and a
        # struck-through figure is the one thing a chart must not show.
        half = d.textlength(text, font=vf) / 2 + 8
        d.rounded_rectangle((x - half, y - 42, x + half, y - 12), radius=8, fill=WHITE)
        d.text((x, y - 27), text, fill=INK, font=vf, anchor="mm")
        for n, line in enumerate(_wrap_two(d, label, lf, step - 8 if len(points) > 1 else 200)):
            d.text((x, base + 26 + n * 27), line, fill=SOFT, font=lf, anchor="mm")
    c.footer()
    return c.png()


_KINDS = {"bar": _bar, "hbar": _hbar, "line": _line}


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
