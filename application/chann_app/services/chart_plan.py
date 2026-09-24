"""The model designs the picture; the server computes it (round 21C).

Owner, 23 ก.ย. 2569: the picture may be designed by AI, never calculated
by it. So the model is handed the numbers we already worked out and asked
for a *plan* — kind, labels, unit, which bar to highlight — and the plan
has NO field for a value. It cannot send a wrong number because it cannot
send a number.

The plan is rendered as one self-contained HTML page with inline SVG and
NO JavaScript, screenshotted through SmartBrowz `preview_image`
(services/pdf/smartbrowz.py:238 — built, paid for and until now unused),
and stored by the existing `reports_ai.publish_chart`. Nothing downstream
of that changes: same path, same signed link, same LINE image, same quota.

What the probe measured (Task 15, four pages through the real endpoint):
SmartBrowz's screenshot DOES execute and wait for JavaScript, but only for
about two seconds past load — a canvas painted during parse came back with
its four bars (canvas.png, 12,849 bytes), a paint deferred 1.5 s was
captured (delayed.png reads "LATE PAINT ARRIVED"), the same paint deferred
5 s was not (delayed5.png still reads "NOT YET", returned after 2.49 s),
and `take_screenshot(html)` takes no options with which to extend that
window. Warm latency 2.0–3.4 s, ~7.4 s on the first call of a cold
process; byte counts identical across two runs, so it is deterministic.
Every frame comes back a fixed **1920×1080** whatever the page asks for.

Two things follow, and both are in this file. First, canvas.png proves a
JS chart library *would* work, and that is exactly why we do not use one:
it would stake the picture on a CDN script finishing inside an
undocumented ~2 s window we cannot configure, where inline SVG has already
painted when the HTML arrives (Ruling 7). Second, the frame is 1920×1080
and the probe's own chart sat in the top-left ~30% of it — so this page is
authored to FILL 1920×1080: html/body at 100% with no margin, one
`<svg width="100%" height="100%" viewBox="0 0 1920 1080">`, and a 16:9
composition sized to be read on a phone. Anything less reaches the
customer as mostly blank white.

When anything at all goes wrong — a refused plan, a model that is not
configured, a screenshot that times out — the picture falls back, in this
order: the deterministic plan the code builds, then `charts.py` (Pillow),
then words. A picture is never worth losing the answer over.
"""
from __future__ import annotations

import asyncio
import base64
import html as html_escape
import json
import logging
import math
import unicodedata
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

from . import charts
from .reports_ai import publish_chart

log = logging.getLogger(__name__)

ALLOWED_KINDS = ("bar", "hbar", "line", "value", "donut")
#: What Pillow can actually draw (`charts._KINDS`), for the fallback.
_KINDS_FALLBACK = {"bar": "bar", "hbar": "hbar", "line": "line", "value": "value", "donut": "bar"}
ALLOWED_UNITS = ("money", "count", "score", "percent")
ALLOWED_FIELDS = frozenset(
    {"kind", "title", "subtitle", "unit", "series_label", "highlight", "labels", "note"})
#: The caps are charts.py's, not a second copy of them: a plan may not ask
#: for a picture the Pillow floor could not draw if it had to (Ruling 6).
#: `donut` is the one kind Pillow has no drawing for, so its cap is set
#: here — six slices is where a ring stops being readable on a phone.
MAX_LABELS = {"bar": charts.MAX_BARS, "hbar": charts.MAX_HBARS,
              "line": charts.MAX_POINTS, "donut": 6, "value": 1}
MAX_TEXT = 80
#: Final review I1: a ring prints the sum of its slices ("รวม") and each
#: slice's share. That sum means something only when the rows ARE parts of
#: one whole. Of the five, these three are (money by stage, jobs by
#: technician, overdue vs not yet due); `won_this_month` is this month
#: against last and `satisfaction_avg` is an average — summing either
#: prints a number no report computed.
SHARE_OF_WHOLE_REPORTS = frozenset({"pipeline_value", "open_jobs_by_tech", "outstanding_invoices"})


def spec_is_share_of_whole(spec: dict) -> bool:
    """An ad-hoc result is parts of one whole only when it is a count or a
    sum split by a category. Every ALLOWED_GROUP_BY is a category (none is
    a period), so the group being set is the test; avg/min/max never add
    up to anything."""
    return str(spec.get("metric") or "count") in ("count", "sum") and bool(spec.get("group_by"))


def spec_unit(spec: dict) -> str:
    """What kind of number the ad-hoc result is — from what was measured,
    never "money unless it is a count": a satisfaction average designed as
    money invited the model to label a score "บาท"."""
    if str(spec.get("metric") or "count") == "count":
        return "count"
    if spec.get("field") == "score" or spec.get("entity") == "surveys":
        return "score"
    return "money"
#: The OA theme (CLAUDE.md rule 5), read from the one place it is defined.
THEME = dict(charts.ACCENT)

#: One screenshot, bounded. The LINE webhook is synchronous all the way
#: down (chat._handle_ai_report → reports_ai.handle_report_request →
#: publish_files → SmartBrowz PDF, then publish_chart_for → here), so this
#: is the SECOND SmartBrowz call in one request and the person is watching
#: a typing indicator while it runs. `preview_image` has no retry and no
#: wrapper of its own, and Task 15 measured a misconfigured accounts URL
#: parking in the SDK's own backoff for minutes — so the budget is taken
#: here, where the fallback is cheap: on timeout the Pillow chart is drawn
#: in ~0.2 s and the reply still carries a picture. Twelve seconds is
#: measured, not guessed: warm calls came back in 3.3-3.8 s over fourteen
#: real screenshots, and the first call of a cold process — the OAuth token
#: refresh — cost 5.0 s here and 7.4 s in Task 15. An 8 s budget threw that
#: cold call away; this one keeps it.
SHOT_TIMEOUT_S = 12.0


class ChartPlanInvalid(ValueError):
    """The model's design was not usable. Never shown to a person — the
    caller falls back to the plan the code would have made."""


@dataclass(frozen=True)
class ChartPlan:
    kind: str
    title: str
    subtitle: str
    unit: str
    series_label: str
    highlight: str | None
    labels: tuple[str, ...]
    note: str


def _text(value, field: str, *, numerals: bool = True) -> str:
    """One of the plan's four pieces of prose.

    Ruling 1: arithmetic is never the model's. The schema already makes a
    value impossible — there is no field to put one in — but prose is the
    back door, so it is shut here too. A per cent sign is an arithmetic
    result wherever it appears. A numeral is refused in `series_label` and
    `note`, the two fields where the model would be stating a finding
    ("เลยกำหนด 10,000 บาท") rather than naming the picture; `title` and
    `subtitle` keep their digits because a window and a date — "3 เดือน
    ล่าสุด", "23 ก.ย. 2569" — are neither a sum nor a claim. A finding said
    in words ("คิดเป็นสองในสาม") is the model's to make: it is a reading of
    the picture, not a number nobody computed.
    """
    said = str(value or "").strip()
    if len(said) > MAX_TEXT:
        raise ChartPlanInvalid(f"{field} is too long")
    if "<" in said or ">" in said:
        raise ChartPlanInvalid(f"{field} may not contain markup")
    if "%" in said or "％" in said:
        raise ChartPlanInvalid(f"{field} may not state a percentage")
    if not numerals and any(character.isdigit() for character in said):
        raise ChartPlanInvalid(f"{field} may not state a number")
    return said


def validate_chart_plan(plan: dict, *, labels: list[str], unit: str, rows: int,
                        additive: bool = False) -> ChartPlan:
    """Refuse anything that is not exactly a design of THIS result.

    `additive` says the rows are parts of one whole (see
    SHARE_OF_WHOLE_REPORTS / spec_is_share_of_whole). It defaults to False
    so a caller that does not know can never get a ring."""
    if not isinstance(plan, dict):
        raise ChartPlanInvalid("plan must be an object")
    unknown = set(plan) - ALLOWED_FIELDS
    if unknown:
        raise ChartPlanInvalid(f"unknown field '{sorted(unknown)[0]}'")
    kind = str(plan.get("kind") or "bar")
    if kind not in ALLOWED_KINDS:
        raise ChartPlanInvalid(f"kind '{kind}' is not allowed")
    if kind == "donut" and not additive:
        raise ChartPlanInvalid("a donut is only for parts of one whole")
    said_unit = str(plan.get("unit") or unit)
    if said_unit not in ALLOWED_UNITS or said_unit != unit:
        raise ChartPlanInvalid("unit does not match the report")
    given = [str(one) for one in (plan.get("labels") or [])]
    if sorted(given) != sorted(labels):
        raise ChartPlanInvalid("labels do not match the result")
    if len(given) > MAX_LABELS[kind]:
        raise ChartPlanInvalid(f"too many labels for {kind}")
    if kind == "value" and rows != 1:
        raise ChartPlanInvalid("a value card needs exactly one number")
    if kind != "value" and rows < 2:
        raise ChartPlanInvalid("nothing to plot: one number is not a chart")
    highlight = plan.get("highlight")
    if highlight not in (None, "") and str(highlight) not in given:
        raise ChartPlanInvalid("highlight is not one of the labels")
    return ChartPlan(
        kind=kind,
        title=_text(plan.get("title"), "title"),
        subtitle=_text(plan.get("subtitle"), "subtitle"),
        unit=said_unit,
        series_label=_text(plan.get("series_label"), "series_label", numerals=False),
        highlight=str(highlight) if highlight else None,
        labels=tuple(given),
        note=_text(plan.get("note"), "note", numerals=False),
    )


DESIGN_PROMPT = (
    "You are designing ONE chart for numbers that are already final. "
    "You will be given the question, the labels and their values.\n"
    f"Reply with JSON only, using exactly these fields: {sorted(ALLOWED_FIELDS)}.\n"
    f"kind: one of {list(ALLOWED_KINDS)} — bar for a few categories, hbar for names, "
    "line for months in order, donut for parts of one whole, value for a single number.\n"
    "donut ONLY when you are told shares_of_whole is true; otherwise never donut.\n"
    "labels: the SAME labels you were given, in the order you want them drawn. "
    "Do not rename them, do not add one, do not drop one.\n"
    # Measured, 23 ก.ย. 2569: without these two lines the model read `unit`
    # as "the word for the unit" and answered "บาท" or "งาน" — a fair
    # reading of a prompt that never said otherwise — and four designs out
    # of six were thrown away by the validator for a defect in the PROMPT.
    # The rule (docs/MODEL_FIRST.md) is to fix the one definition, not to
    # widen the rule that refused it.
    f"unit: copy back the unit you were GIVEN, character for character. It is "
    f"one of {list(ALLOWED_UNITS)} — a kind of number, not a word for money. "
    "If you want to name the currency or what is being counted, that is "
    "series_label (\"บาท\", \"งาน\", \"คะแนน\").\n"
    "highlight: one of those labels, or null.\n"
    "title/subtitle/series_label/note: short Thai, at most 80 characters each, no HTML.\n"
    "NEVER include a number, a total, a percentage or any other arithmetic "
    "result — not in a field of its own, which does not exist, and not inside "
    "the title, the note or any other words. They are not yours to give: the "
    "server already worked them out and will print them on the picture itself. "
    "A note may say what the shape means in words (\"the overdue part is the "
    "larger one\"), never what it adds up to.\n"
    "You are choosing how this is drawn, not what it says."
)


async def design(question: str, *, labels: list[str], values: list[float], unit: str,
                 language: str = "th", client=None, additive: bool = False) -> ChartPlan:
    """Ask the model for a design. Raises ChartPlanInvalid; never returns
    a plan that has not been through the validator."""
    from .ai.client import complete
    from .reports_ai import extract_json

    told = json.dumps(
        {"question": question, "unit": unit, "language": language,
         "shares_of_whole": bool(additive),
         "data": [{"label": label, "value": value} for label, value in zip(labels, values)]},
        ensure_ascii=False)
    raw = await complete(system_prompt=DESIGN_PROMPT, user_message=told,
                         max_tokens=400, client=client)
    data = raw if isinstance(raw, dict) else extract_json(str(raw))
    return validate_chart_plan(data, labels=labels, unit=unit, rows=len(values),
                               additive=additive)


def code_plan(*, title: str, subtitle: str, labels: list[str], unit: str,
              group_by: str | None = None) -> ChartPlan:
    """The design the code would have chosen. Used when the model is not
    configured, is unavailable, or answers with something we refuse."""
    from .reports_ai import CHART_KIND

    kind = "value" if len(labels) < 2 else CHART_KIND.get(str(group_by or ""), "bar")
    if len(labels) > MAX_LABELS[kind]:
        kind = "hbar"
    # `_short`, not a plain slice: `title[:80]` can cut between a Thai base
    # character and its tone mark and strand the mark (the hazard `_cells`
    # exists to prevent).
    return ChartPlan(kind=kind, title=_short(title, MAX_TEXT),
                     subtitle=_short(subtitle, MAX_TEXT),
                     unit=unit, series_label="", highlight=None,
                     labels=tuple(labels[:MAX_LABELS[kind]]), note="")


# ------------------------------------------------------------- the renderer
#
# One page, one <svg>, 1920x1080 — the frame SmartBrowz returns whatever we
# ask for (Task 15). Everything is laid out in those coordinates, so a
# number is where the arithmetic put it and nothing depends on how the
# browser wraps a box.

W, H = 1920, 1080
CARD = (40, 40, 1840, 1000)
PAD = 96
PLOT_TOP, PLOT_BOTTOM = 300.0, 800.0
PLOT_LEFT, PLOT_RIGHT = 150.0, 1770.0
NOTE_Y, FOOT_Y = 936, 996
#: hbar's three columns: the name, the track, the number.
HBAR_LABEL_R, HBAR_TRACK_L, HBAR_TRACK_R = 470.0, 500.0, 1500.0


def _values_in_plan_order(plan: ChartPlan, rows: list[tuple[str, float]]) -> list[float]:
    """The values of `rows`, rearranged to stand beside `plan.labels`.

    The model may choose the ORDER — DESIGN_PROMPT invites it ("in the order
    you want them drawn") and the validator allows any permutation, because
    how the picture is arranged is a design and designs are the model's. The
    PAIRING is never the model's: a number belongs to the row the Data tier
    returned it in. Zipping the plan's order against the Data tier's order
    drew สมชาย's 12 under ประวิทย์'s name on BOTH roads — every number right,
    every one of them under the wrong name (round 1 review). The plan cannot
    send a number; this is the other half of the same rule, which is that it
    cannot move one either.

    Repeated labels are paired in the order their rows arrived, so a result
    with two "อื่น ๆ" keeps both of them. A plan naming something the result
    does not contain is refused, and the caller falls back to `code_plan`.
    """
    waiting: dict[str, deque[float]] = {}
    for label, value in rows:
        waiting.setdefault(str(label), deque()).append(float(value))
    ordered: list[float] = []
    for label in plan.labels:
        queue = waiting.get(str(label))
        if not queue:
            raise ChartPlanInvalid(f"the plan names '{label}', which is not in the result")
        ordered.append(queue.popleft())
    return ordered


def _fmt(value: float, unit: str) -> str:
    """One formatter for both roads.

    `charts._fmt` is the one the Pillow floor has always used, and its rule
    is the owner-facing one: 1,250,000 baht reads "1,250,000", never
    "1250000.00", "which nobody scans correctly on a phone" (charts.py:103).
    Printing money two ways depending on whether SmartBrowz answered would
    make the same report read differently on two days (round 1 review), so
    the designed road asks the drawn road how a number looks."""
    return charts._fmt(value, money=unit == "money")


def _cells(label: str) -> list[str]:
    """Thai written down is not Thai as `len()` counts it: สระบน, สระล่าง
    and วรรณยุกต์ are combining marks that take no width of their own, so
    "เสนอราคาแล้ว" is twelve code points but eleven columns wide. Each cell
    here is one base character with its marks still attached, which makes a
    cut both honest about the width and safe — slicing a plain string can
    leave a tone mark stranded on the next line (round 20L)."""
    cells: list[str] = []
    for character in str(label or ""):
        if cells and unicodedata.category(character) in ("Mn", "Me"):
            cells[-1] += character
        else:
            cells.append(character)
    return cells


def _short(label: str, limit: int = 22) -> str:
    """A label that would leave the card is cut with an ellipsis HERE,
    where it can be tested — not by the SVG, which just overflows
    silently (round 20L: "เสนอราคาแล้ว" became "เสนอราคาแล้" + "ว")."""
    cells = _cells(label)
    limit = max(2, int(limit))
    return str(label or "") if len(cells) <= limit else "".join(cells[: limit - 1]) + "…"


def _wrap(label: str, per_line: int, lines: int = 2) -> list[str]:
    """The same cut, but allowed a second line first — a bar with room
    under it should show the whole name rather than an ellipsis.

    A second line is only ever taken at a SPACE. Breaking a Thai word in
    the middle is how "เสนอราคาแล้ว" became "เสนอราคาแล้" over a lonely
    "ว" under the bar next to it: two lines, and the second one a single
    letter that reads as a typo. A word with nowhere to break gets the
    ellipsis instead, which at least looks deliberate."""
    said = str(label or "").strip()
    per_line = max(4, int(per_line))
    cells = _cells(said)
    if len(cells) <= per_line:
        return [said]
    head = "".join(cells[:per_line + 1])
    cut = head.rfind(" ")
    if cut <= 0 or lines < 2:
        return [_short(said, per_line)]
    first, rest = said[:cut].strip(), said[cut:].strip()
    return [line for line in (first, _short(rest, per_line)) if line]


def _scale(values: list[float]) -> tuple[float, float]:
    """(lo, span) with zero always inside it. A bar chart that does not
    start at zero lies about the ratio between its bars, and a negative
    number drawn as a positive one lies twice."""
    numbers = [float(v) for v in values] or [0.0]
    lo = min([0.0] + numbers)
    hi = max([0.0] + numbers)
    span = hi - lo
    return lo, (span if span > 0 else 1.0)


def _y(value: float, lo: float, span: float, bottom: float = PLOT_BOTTOM) -> float:
    return bottom - (float(value) - lo) / span * (bottom - PLOT_TOP)


def _floor(values: list[float]) -> float:
    """Where the deepest bar is allowed to reach. A bar below the axis
    carries its number UNDER it, and with the plot running all the way to
    PLOT_BOTTOM that number lands on top of the category labels — which is
    exactly what the first render of `bar-negative` did ("-5,000.00" over
    "ก"). Negative numbers therefore buy themselves a strip of air."""
    return PLOT_BOTTOM - (54.0 if any(float(value) < 0 for value in values) else 0.0)


def _grid(lo: float, span: float, bottom: float = PLOT_BOTTOM) -> list[str]:
    out = []
    for step in range(5):
        y = PLOT_TOP + step * (bottom - PLOT_TOP) / 4
        out.append(f'<line class="grid" x1="{PLOT_LEFT - 40:.0f}" y1="{y:.1f}" '
                   f'x2="{PLOT_RIGHT:.0f}" y2="{y:.1f}"/>')
    zero = _y(0.0, lo, span, bottom)
    out.append(f'<line class="axis" x1="{PLOT_LEFT - 40:.0f}" y1="{zero:.1f}" '
               f'x2="{PLOT_RIGHT:.0f}" y2="{zero:.1f}"/>')
    return out


def _klass(label: str, plan: ChartPlan, base: str = "bar") -> str:
    return f"{base} highlight" if plan.highlight and label == plan.highlight else base


def _bars(plan: ChartPlan, rows: list[tuple[str, float]], e) -> list[str]:
    values = [value for _, value in rows]
    lo, span = _scale(values)
    bottom = _floor(values)
    zero = _y(0.0, lo, span, bottom)
    slot = (PLOT_RIGHT - PLOT_LEFT) / max(len(rows), 1)
    bar_w = min(210.0, slot * 0.56)
    per_line = max(6, min(20, int(slot / 17)))
    out = _grid(lo, span, bottom)
    for index, (label, value) in enumerate(rows):
        y = _y(value, lo, span, bottom)
        top, height = min(y, zero), abs(y - zero)
        if height < 6.0:  # a zero is still a fact, and needs a mark to sit on
            top, height = (zero - 6.0, 6.0) if value >= 0 else (zero, 6.0)
        x = PLOT_LEFT + index * slot + (slot - bar_w) / 2
        out.append(f'<rect class="{_klass(label, plan)}" x="{x:.1f}" y="{top:.1f}" '
                   f'width="{bar_w:.1f}" height="{height:.1f}" rx="10"/>')
        value_y = top - 20 if value >= 0 else top + height + 44
        out.append(f'<text class="v" x="{x + bar_w / 2:.1f}" y="{value_y:.1f}" '
                   f'text-anchor="middle">{e(_fmt(value, plan.unit))}</text>')
        for line_no, line in enumerate(_wrap(label, per_line)):
            out.append(f'<text class="l" x="{x + bar_w / 2:.1f}" '
                       f'y="{PLOT_BOTTOM + 52 + line_no * 42:.0f}" '
                       f'text-anchor="middle">{e(line)}</text>')
    return out


def _hbars(plan: ChartPlan, rows: list[tuple[str, float]], e) -> list[str]:
    values = [value for _, value in rows]
    lo, span = _scale(values)
    # Rows are held to a readable band and the whole group is centred, so
    # two technicians sit together in the middle of the card instead of one
    # at the top and one 500 px below it with nothing in between.
    row_h = min((PLOT_BOTTOM - PLOT_TOP) / max(len(rows), 1), 116.0)
    top = (PLOT_TOP + PLOT_BOTTOM) / 2 - row_h * len(rows) / 2
    bar_h = min(56.0, row_h * 0.56)
    size = int(max(22, min(34, row_h * 0.52)))
    # The name column is as wide as the longest name needs, up to a third
    # of the card; short names give their room back to the bars.
    longest = max((len(_cells(label)) for label, _ in rows), default=0)
    label_r = min(max(HBAR_LABEL_R, PAD + longest * size * 0.58 + 16), 760.0)
    track_l = label_r + 30
    track_r = HBAR_TRACK_R
    width = track_r - track_l
    zero = track_l + (0.0 - lo) / span * width
    limit = int((label_r - PAD) / (size * 0.58))
    out: list[str] = []
    for index, (label, value) in enumerate(rows):
        mid = top + (index + 0.5) * row_h
        out.append(f'<text class="hl" x="{label_r:.0f}" y="{mid + size * 0.36:.1f}" '
                   f'text-anchor="end" font-size="{size}">{e(_short(label, limit))}</text>')
        out.append(f'<rect class="track" x="{track_l:.0f}" y="{mid - bar_h / 2:.1f}" '
                   f'width="{width:.0f}" height="{bar_h:.1f}" rx="10"/>')
        end = track_l + (float(value) - lo) / span * width
        left, length = min(zero, end), abs(end - zero)
        if length < 6.0:
            left, length = (zero, 6.0) if value >= 0 else (zero - 6.0, 6.0)
        out.append(f'<rect class="{_klass(label, plan)}" x="{left:.1f}" '
                   f'y="{mid - bar_h / 2:.1f}" width="{length:.1f}" height="{bar_h:.1f}" rx="10"/>')
        out.append(f'<text class="hv" x="{track_r + 24:.0f}" y="{mid + size * 0.36:.1f}" '
                   f'font-size="{size}">{e(_fmt(value, plan.unit))}</text>')
    return out


def _line(plan: ChartPlan, rows: list[tuple[str, float]], e) -> list[str]:
    values = [value for _, value in rows]
    lo, span = _scale(values)
    bottom = _floor(values)
    count = len(rows)
    step = (PLOT_RIGHT - PLOT_LEFT) / max(count - 1, 1)
    xs = [PLOT_LEFT + index * step if count > 1 else (PLOT_LEFT + PLOT_RIGHT) / 2
          for index in range(count)]
    ys = [_y(value, lo, span, bottom) for _, value in rows]
    out = _grid(lo, span, bottom)
    out.append('<polyline class="series" points="'
               + " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + '"/>')
    # Past seven points the value labels collide with each other, so only
    # the three a reader actually looks for are drawn: where it started,
    # where it ended, and the peak.
    highest = max(range(count), key=lambda index: rows[index][1])
    shown = set(range(count)) if count <= 7 else {0, count - 1, highest}
    per_label = max(5, int(step / 16))
    for index, (label, value) in enumerate(rows):
        marked = plan.highlight and label == plan.highlight
        out.append(f'<circle class="{"dot highlight" if marked else "dot"}" cx="{xs[index]:.1f}" '
                   f'cy="{ys[index]:.1f}" r="{14 if marked else 9}"/>')
        if index in shown or marked:
            out.append(f'<text class="v" x="{xs[index]:.1f}" y="{ys[index] - 26:.1f}" '
                       f'text-anchor="middle">{e(_fmt(value, plan.unit))}</text>')
        out.append(f'<text class="l" x="{xs[index]:.1f}" y="{PLOT_BOTTOM + 52:.0f}" '
                   f'text-anchor="middle">{e(_short(label, per_label))}</text>')
    return out


def _ring_point(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees - 90.0)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _donut(plan: ChartPlan, rows: list[tuple[str, float]], e) -> list[str]:
    cx, cy, outer, inner = 620.0, 548.0, 228.0, 134.0
    sizes = [abs(float(value)) for _, value in rows]
    total = sum(sizes)
    out: list[str] = []
    if total <= 0:
        # Nothing to divide. An empty ring says "none of it happened"
        # where a blank middle of the page says only "something broke".
        out.append(f'<circle class="track" cx="{cx}" cy="{cy}" r="{(outer + inner) / 2:.0f}" '
                   f'fill="none" stroke-width="{outer - inner:.0f}"/>')
    start = 0.0
    for index, ((label, value), size) in enumerate(zip(rows, sizes)):
        if total <= 0:
            break
        sweep = 360.0 * size / total
        klass = _klass(label, plan, "slice") + f" tone{index % 6}"
        if sweep >= 359.9:
            out.append(f'<circle class="{klass}" cx="{cx}" cy="{cy}" '
                       f'r="{(outer + inner) / 2:.0f}" fill="none" '
                       f'stroke-width="{outer - inner:.0f}"/>')
        elif sweep > 0.01:
            x0, y0 = _ring_point(cx, cy, outer, start)
            x1, y1 = _ring_point(cx, cy, outer, start + sweep)
            x2, y2 = _ring_point(cx, cy, inner, start + sweep)
            x3, y3 = _ring_point(cx, cy, inner, start)
            big = 1 if sweep > 180 else 0
            out.append(
                f'<path class="{klass}" d="M{x0:.1f},{y0:.1f} A{outer},{outer} 0 {big} 1 '
                f'{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f} A{inner},{inner} 0 {big} 0 '
                f'{x3:.1f},{y3:.1f} Z"/>')
        start += sweep
    said = _fmt(total, plan.unit)
    # Sized to the hole, not to a hope: "22,000.00" at 62 px is wider than
    # the ring's opening and printed itself over the slices.
    middle = int(min(62, max(30, inner * 1.7 / max(len(said) * 0.55, 1))))
    out.append(f'<text class="ringtotal" x="{cx}" y="{cy + middle * 0.12:.0f}" '
               f'text-anchor="middle" font-size="{middle}">{e(said)}</text>')
    out.append(f'<text class="ringfoot" x="{cx}" y="{cy + 58:.0f}" text-anchor="middle">'
               f'{e(plan.series_label or "รวม")}</text>')
    legend_top = cy - (len(rows) - 1) * 33
    for index, ((label, value), size) in enumerate(zip(rows, sizes)):
        y = legend_top + index * 66
        share = (100.0 * size / total) if total > 0 else 0.0
        out.append(f'<rect class="{_klass(label, plan, "slice")} tone{index % 6}" '
                   f'x="1000" y="{y - 26:.0f}" width="34" height="34" rx="8"/>')
        out.append(f'<text class="lg" x="1054" y="{y:.0f}">{e(_short(label, 22))}</text>')
        out.append(f'<text class="lgv" x="{PLOT_RIGHT:.0f}" y="{y:.0f}" text-anchor="end">'
                   f'{e(_fmt(value, plan.unit))} · {share:.0f}%</text>')
    return out


def _value_card(plan: ChartPlan, rows: list[tuple[str, float]], e) -> list[str]:
    label, value = rows[0] if rows else ("", 0.0)
    said = _fmt(value, plan.unit)
    size = int(min(200, max(80, 1560 / max(len(said) * 0.58, 1))))
    return [
        f'<text class="big" x="{W / 2:.0f}" y="600" text-anchor="middle" '
        f'font-size="{size}">{e(said)}</text>',
        f'<text class="biglabel" x="{W / 2:.0f}" y="712" text-anchor="middle">'
        f'{e(_short(label, 44))}</text>',
    ]


@lru_cache(maxsize=1)
def _font_face() -> str:
    """Sarabun, inlined as a data: URI — the same face the drawn charts and
    the guide pictures use (charts.py), and the only way a self-contained
    page can be sure it has a Thai font at all. The page fetches nothing at
    render time; a headless browser without a Thai face would otherwise
    hand the customer a picture of tofu boxes, which no test can see."""
    faces = []
    for path, weight in ((charts.REGULAR, 400), (charts.BOLD, 700)):
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            log.warning("the chart fonts are missing from %s", charts.FONT_DIR)
            return ""
        faces.append(
            "@font-face{font-family:'Sarabun';font-style:normal;font-weight:%d;"
            "src:url(data:font/ttf;base64,%s) format('truetype')}" % (weight, encoded))
    return "".join(faces)


def _css(colour: str) -> str:
    return (
        _font_face()
        + "*{box-sizing:border-box}"
        + f"html,body{{margin:0;padding:0;width:100%;height:100%;background:{charts.PAPER}}}"
        + "svg{display:block;width:100%;height:100%}"
        + f"text{{font-family:'Sarabun','Noto Sans Thai',sans-serif;fill:{charts.INK}}}"
        + f".card{{fill:{charts.WHITE};stroke:{charts.LINE};stroke-width:2}}"
        + ".h1{font-size:62px;font-weight:700}"
        + f".sub{{font-size:34px;fill:{charts.SOFT}}}"
        + f".rule{{stroke:{charts.LINE};stroke-width:2}}"
        + f".grid{{stroke:{charts.GRID};stroke-width:2}}"
        + f".axis{{stroke:{charts.LINE};stroke-width:3}}"
        + f".bar{{fill:{colour};opacity:.55}}"
        + ".bar.highlight{opacity:1}"
        + f".track{{fill:{charts.GRID};stroke:{charts.GRID}}}"
        + ".v{font-size:34px;font-weight:700}"
        + f".l{{font-size:30px;fill:{charts.SOFT}}}"
        + f".hl{{fill:{charts.INK}}}.hv{{font-weight:700}}"
        + f".series{{fill:none;stroke:{colour};stroke-width:6;stroke-linejoin:round;"
          "stroke-linecap:round}"
        + f".dot{{fill:{colour}}}.dot.highlight{{stroke:{charts.WHITE};stroke-width:6}}"
        + f".slice{{fill:{colour};opacity:.9}}.slice.highlight{{opacity:1}}"
        + f".slice.tone1{{opacity:.74}}.slice.tone2{{opacity:.58}}.slice.tone3{{opacity:.44}}"
        + f".slice.tone4{{opacity:.32}}.slice.tone5{{fill:{charts.SOFT};opacity:.45}}"
        + f".slice.highlight{{opacity:1;fill:{colour}}}"
        + f"circle.slice{{fill:none;stroke:{colour}}}"
        # No font-size here: the ring's total carries its own, sized to the
        # hole, and a CSS rule would quietly beat the attribute (it did).
        + ".ringtotal{font-weight:700}"
        + f".ringfoot{{font-size:30px;fill:{charts.SOFT}}}"
        + ".lg{font-size:32px}"
        + f".lgv{{font-size:32px;font-weight:700}}"
        + f".big{{font-weight:700;fill:{colour}}}"
        + f".biglabel{{font-size:46px;fill:{charts.SOFT}}}"
        + f".note{{font-size:32px;fill:{charts.SOFT}}}"
        + f".foot{{font-size:28px;fill:{charts.FAINT}}}"
    )


def render_chart_html(plan: ChartPlan, *, values: list[float], company_name: str = "",
                      oa: str = "sales", language: str = "th") -> str:
    """One self-contained page: inline SVG, inline CSS, no script, no
    network — and 1920x1080 of it, because that is the frame SmartBrowz
    hands back whatever the page asks for (Task 15). The OA's colour, the
    shop's name, and nothing else.

    `values[i]` belongs to `plan.labels[i]`, and the caller owes that:
    `plan.labels` may be the MODEL's ordering, so `values` must have been
    put in the same order by `_values_in_plan_order` first. Handing this
    the Data tier's row order against a reordered plan draws every number
    under the wrong name (round 1 review)."""
    e = html_escape.escape
    colour = THEME.get(oa, THEME["sales"])
    rows = [(label, float(value)) for label, value in zip(plan.labels, values)]
    if not rows:
        said = charts.NO_DATA[language if language in charts.NO_DATA else "th"]
        body = [f'<text class="biglabel" x="{W / 2:.0f}" y="560" text-anchor="middle">'
                f'{e(said)}</text>']
    elif plan.kind == "value" or len(rows) == 1:
        body = _value_card(plan, rows, e)
    elif plan.kind == "hbar":
        body = _hbars(plan, rows, e)
    elif plan.kind == "line":
        body = _line(plan, rows, e)
    elif plan.kind == "donut":
        body = _donut(plan, rows, e)
    else:
        body = _bars(plan, rows, e)
    x, y, width, height = CARD
    head = [
        f'<rect class="card" x="{x}" y="{y}" width="{width}" height="{height}" rx="32"/>',
        f'<text class="h1" x="{PAD}" y="152">{e(_short(plan.title, 46))}</text>',
    ]
    if plan.subtitle:
        head.append(f'<text class="sub" x="{PAD}" y="212">{e(_short(plan.subtitle, 70))}</text>')
    head.append(f'<line class="rule" x1="{PAD}" y1="248" x2="{W - PAD}" y2="248"/>')
    foot = []
    if plan.note:
        foot.append(f'<text class="note" x="{PAD}" y="{NOTE_Y}">{e(_short(plan.note, 90))}</text>')
    stamp = " · ".join(part for part in (company_name, plan.series_label) if part)
    if stamp:
        foot.append(f'<text class="foot" x="{PAD}" y="{FOOT_Y}">{e(stamp)}</text>')
    return (
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        f"<title>{e(plan.title)}</title><style>{_css(colour)}</style></head><body>"
        f'<svg width="100%" height="100%" viewBox="0 0 {W} {H}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{e(plan.title)}">'
        + "".join(head) + "".join(body) + "".join(foot)
        + "</svg></body></html>"
    )


# ------------------------------------------------------------- the publishers

async def _screenshot(page: str) -> bytes | None:
    """HTML → PNG through SmartBrowz. None on any failure — the caller
    falls back to Pillow, and the answer is never lost.

    Bounded, because this runs inside the LINE webhook: config errors,
    provider errors and `RendererUnavailable` are all RuntimeError by
    design in pdf/smartbrowz.py, and a slow or hung call is cut at
    SHOT_TIMEOUT_S. An ImportError or an AttributeError is a bug of ours
    and is left to surface — swallowing one is exactly how the report PDF
    shipped broken and silent (Task 15)."""
    from .pdf import PdfOptions, get_renderer

    try:
        result = await asyncio.wait_for(
            get_renderer("smartbrowz").preview_image(page, PdfOptions()), SHOT_TIMEOUT_S)
    except (asyncio.TimeoutError, RuntimeError, OSError) as exc:
        log.info("chart screenshot unavailable (%s); falling back to the drawn chart",
                 type(exc).__name__)
        return None
    content = result.content or b""
    return content if len(content) > 512 else None


async def _plan_and_values(question: str, *, rows: list[tuple[str, float]], unit: str,
                           language: str, ai_client, subtitle: str = "",
                           group_by: str | None = None,
                           additive: bool = False) -> tuple[ChartPlan, list[float]]:
    """The design, and the numbers rearranged to stand beside it.

    The two are produced together on purpose: a plan is only usable with the
    values that belong to ITS labels, and the one place that could get that
    wrong is the one place that must not (round 1 review). A plan that is
    refused — by the validator, or because it names a label the result does
    not contain — falls back here to the design the code would have made,
    and the person never sees an error."""
    labels = [label for label, _ in rows]
    values = [value for _, value in rows]
    try:
        plan = await design(question, labels=labels, values=values, unit=unit,
                            language=language, client=ai_client, additive=additive)
        return plan, _values_in_plan_order(plan, rows)
    except Exception:  # noqa: BLE001
        log.info("chart plan refused or unavailable; drawing the code's own design")
    plan = code_plan(title=question, subtitle=subtitle, labels=labels, unit=unit,
                     group_by=group_by)
    return plan, _values_in_plan_order(plan, rows)


async def _publish(plan: ChartPlan, *, values: list[float], license_id: str,
                   company_name: str, oa: str, footer: str, language: str,
                   store=None) -> str | None:
    """`values` is already in the plan's own order — see `_plan_and_values`.
    Both roads read it the same way, so they cannot disagree about which
    number belongs to which label."""
    png = await _screenshot(render_chart_html(
        plan, values=values, company_name=company_name, oa=oa, language=language))
    if png is None:
        png = charts.render_or_none(charts.Chart(
            title=plan.title, subtitle=plan.subtitle,
            points=[(label, float(value)) for label, value in zip(plan.labels, values)],
            kind=_KINDS_FALLBACK[plan.kind], oa=oa, language=language,
            money=plan.unit == "money", footer=footer or plan.note,
        ))
    if png is None:
        return None
    return await publish_chart(png, license_id=str(license_id), store=store)


async def publish_for_basic_report(client, *, report: dict, license_id: str,
                                   language: str = "th", ai_client=None,
                                   company_name: str = "", store=None,
                                   ) -> tuple[str | None, bool]:
    """A picture of one of the five. THIS is what costs a credit — the
    numbers were free (spec §5).

    `client` is the Data tier client, accepted so this reads like every
    other publisher on this road and so a caller need not special-case it.
    Nothing here asks the Data tier anything: the five reports arrive with
    their numbers already computed, which is the whole point of them being
    free."""
    source = [(str(row.get("label_th") if language != "en" else row.get("label_en")),
               float(row.get("value") or 0)) for row in (report.get("rows") or [])]
    if not source:
        return None, False
    title = str(report.get("title_th") if language != "en" else report.get("title_en"))
    subtitle = (report.get("notes_th" if language != "en" else "notes_en") or [""])[0]
    plan, values = await _plan_and_values(
        title, rows=source, unit=str(report.get("unit") or "count"),
        language=language, ai_client=ai_client, subtitle=subtitle,
        additive=str(report.get("key") or "") in SHARE_OF_WHOLE_REPORTS)
    url = await _publish(plan, values=values, license_id=license_id,
                         company_name=company_name, oa="sales", footer=subtitle,
                         language=language, store=store)
    return url, True


async def publish_for_spec(spec: dict, result: dict, language: str, *, license_id: str,
                           ai_client=None, company_name: str = "", store=None,
                           ) -> tuple[str | None, bool]:
    """The ad-hoc road's picture, designed rather than drawn."""
    from .reports_ai import describe

    source = [(str(row.get("label") or ""), float(row.get("value") or 0))
              for row in (result.get("rows") or [])]
    if not source:
        return None, False
    plan, values = await _plan_and_values(
        describe(spec, language), rows=source, unit=spec_unit(spec),
        language=language, ai_client=ai_client, group_by=spec.get("group_by"),
        additive=spec_is_share_of_whole(spec))
    url = await _publish(plan, values=values, license_id=license_id,
                         company_name=company_name, oa="sales", footer="",
                         language=language, store=store)
    return url, True
