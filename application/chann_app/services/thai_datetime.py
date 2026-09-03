"""Parsing the dates and times a Thai salesperson actually types.

Deliberately deterministic and small, not an AI parse. A reminder that
lands on the wrong day is worse than one the system says it did not
understand: the person believes they are covered and finds out when the
customer has already gone quiet. So this recognises a closed set of
phrasings exactly, and returns None for everything else, which the caller
turns into "say it like this" rather than a guess.

Buddhist-era years are accepted because that is what Thai calendars and
Thai people use: 2569 is 2026. The cutoff at 2400 is safe — no Gregorian
year the system will ever see is above it, and no Buddhist year below it.
"""
from __future__ import annotations

from contextvars import ContextVar
from zoneinfo import ZoneInfo

import re
from datetime import datetime, date, time, timedelta

# Weekday names, including the common short forms. Monday is 0, matching
# date.weekday().
_THAI_WEEKDAYS = {
    "จันทร์": 0, "อังคาร": 1, "พุธ": 2, "พฤหัส": 3, "พฤหัสบดี": 3,
    "ศุกร์": 4, "เสาร์": 5, "อาทิตย์": 6,
}

_THAI_MONTHS = {
    "มกราคม": 1, "ม.ค.": 1, "มค": 1,
    "กุมภาพันธ์": 2, "ก.พ.": 2, "กพ": 2,
    "มีนาคม": 3, "มี.ค.": 3, "มีค": 3,
    "เมษายน": 4, "เม.ย.": 4, "เมย": 4,
    "พฤษภาคม": 5, "พ.ค.": 5, "พค": 5,
    "มิถุนายน": 6, "มิ.ย.": 6, "มิย": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "กค": 7,
    "สิงหาคม": 8, "ส.ค.": 8, "สค": 8,
    "กันยายน": 9, "ก.ย.": 9, "กย": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "ตค": 10,
    "พฤศจิกายน": 11, "พ.ย.": 11, "พย": 11,
    "ธันวาคม": 12, "ธ.ค.": 12, "ธค": 12,
}

# Times of day that carry no clock reading. Mapped to conventional hours a
# Thai office would assume, which is a judgement call — but a stated one,
# and the reply always echoes the resolved time so the person can correct it.
_VAGUE_TIMES = {
    "เช้า": time(9, 0),
    "สาย": time(10, 0),
    "เที่ยง": time(12, 0),
    "บ่าย": time(13, 0),
    "เย็น": time(17, 0),
    "ค่ำ": time(19, 0),
}


def to_gregorian_year(year: int) -> int:
    """2569 -> 2026, 69 -> 2026, 26 -> 2026, 2026 -> 2026.

    Two digits are read as a Buddhist short year when that lands in this
    century ("69" is 2569 = 2026) and as a Gregorian short year otherwise
    ("26" is 2026). The old rule treated every two-digit year as Buddhist,
    so "15/03/26" became 2526 = 1983 and a customer could move their
    appointment into the past without anyone noticing (3 Sep audit).
    """
    if year >= 2400:
        return year - 543
    if year < 100:
        # BE 2543–2599 → 2000–2056: "43".."99" are Buddhist short years.
        if year >= 43:
            return 2500 + year - 543
        # "00".."42" as Buddhist would be 1957–1999; nobody books a visit
        # then. Read them as 2000–2042.
        return 2000 + year
    return year


def parse_thai_date(text: str, today: date) -> date | None:
    """A date from Thai text, or None when nothing is recognised."""
    # ISO first, before anything that could mistake it. "2026-09-06" was
    # read by the d-m-y pattern as day 26, month 09, and the trailing 06
    # as a two-digit year — landing on 26 September 1963. The system
    # writes ISO dates onto its own quick-reply buttons, so this is the
    # format that failed most reliably.
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text or "")
    if iso:
        try:
            # A Buddhist year typed in ISO shape ("2569-09-06") converts the
            # same way every other year here does. Without this it became a
            # literal year-2569 date — five centuries ahead, never announced,
            # and silently wrong in exactly the way this module exists to
            # prevent.
            return date(
                to_gregorian_year(int(iso.group(1))),
                int(iso.group(2)),
                int(iso.group(3)),
            )
        except ValueError:
            pass

    # "วันที่ 6" — a bare day of the month, which is how someone says a
    # date within the next few weeks without naming the month. Taken as
    # the NEXT such day: "มาดูสินค้าวันที่ 6" said on the 20th means next
    # month's 6th, not one that has already passed.
    bare_day = re.search(r"วันที่\s*(\d{1,2})(?!\s*[/\-.\d])", text or "")
    if bare_day:
        day = int(bare_day.group(1))
        if 1 <= day <= 31:
            for month_offset in (0, 1, 2):
                month = today.month + month_offset
                year = today.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    continue
                if candidate >= today:
                    return candidate

    if not text:
        return None
    cleaned = text.strip().lower()

    if any(word in cleaned for word in ("วันนี้", "today")):
        return today
    if any(word in cleaned for word in ("พรุ่งนี้", "tomorrow")):
        return today + timedelta(days=1)
    if "มะรืน" in cleaned:
        return today + timedelta(days=2)

    # "อีก 3 วัน" / "in 3 days"
    relative = re.search(r"อีก\s*(\d+)\s*วัน", cleaned) or re.search(r"in\s+(\d+)\s+days?", cleaned)
    if relative:
        return today + timedelta(days=int(relative.group(1)))

    relative_weeks = re.search(r"อีก\s*(\d+)\s*(?:สัปดาห์|อาทิตย์)", cleaned)
    if relative_weeks:
        return today + timedelta(weeks=int(relative_weeks.group(1)))

    # "วันศุกร์" / "ศุกร์หน้า" — the NEXT such weekday. Never today, because
    # someone saying "on Friday" on a Friday means the one coming, not the
    # day that is already half over.
    for name, index in _THAI_WEEKDAYS.items():
        if name in cleaned:
            ahead = (index - today.weekday()) % 7
            if ahead == 0:
                ahead = 7
            if "หน้า" in cleaned and ahead < 7:
                ahead += 7
            return today + timedelta(days=ahead)

    # "15 มีนาคม" / "15 มี.ค. 2569"
    named = re.search(r"(\d{1,2})\s*([ก-๙.]+)\s*(\d{2,4})?", cleaned)
    if named:
        month = _THAI_MONTHS.get(named.group(2).strip().rstrip("."))
        if month is None:
            month = _THAI_MONTHS.get(named.group(2).strip())
        if month:
            day = int(named.group(1))
            year = to_gregorian_year(int(named.group(3))) if named.group(3) else today.year
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            # A bare "15 มีนาคม" already past this year means next year: a
            # reminder is always about something ahead.
            if named.group(3) is None and candidate < today:
                candidate = date(year + 1, month, day)
            return candidate

    # 15/03/2569 or 15-03-26
    numeric = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", cleaned)
    if numeric:
        try:
            return date(
                to_gregorian_year(int(numeric.group(3))),
                int(numeric.group(2)),
                int(numeric.group(1)),
            )
        except ValueError:
            return None

    return None


def parse_thai_time(text: str) -> time | None:
    """A clock time from Thai text, or None for a whole-day reminder."""
    if not text:
        return None
    cleaned = text.strip().lower()

    # "14:00" / "14.00" / "9:30 น."
    explicit = re.search(r"(\d{1,2})[:.](\d{2})", cleaned)
    if explicit:
        hour, minute = int(explicit.group(1)), int(explicit.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None

    # "บ่าย 2" / "บ่ายสองโมง" style: the vague word plus a number. Handled
    # before the bare vague words so "บ่าย 3" is 15:00, not 13:00.
    afternoon = re.search(r"บ่าย\s*(\d{1,2})", cleaned)
    if afternoon:
        hour = int(afternoon.group(1))
        if 1 <= hour <= 6:
            return time(hour + 12, 0)

    morning = re.search(r"(\d{1,2})\s*โมงเช้า", cleaned)
    if morning:
        hour = int(morning.group(1))
        if 6 <= hour <= 11:
            return time(hour, 0)

    evening = re.search(r"(?:ทุ่ม)", cleaned)
    if evening:
        count = re.search(r"(\d{1,2})\s*ทุ่ม", cleaned)
        if count:
            hour = int(count.group(1))
            if 1 <= hour <= 5:
                return time(hour + 18, 0)

    # "14 น." — a bare hour with the Thai hour marker.
    bare = re.search(r"(\d{1,2})\s*น\.?(?!\d)", cleaned)
    if bare:
        hour = int(bare.group(1))
        if 0 <= hour <= 23:
            return time(hour, 0)

    for word, value in _VAGUE_TIMES.items():
        if word in cleaned:
            return value

    return None


# Phase 16.3 — the reader's display preferences, set once per request by
# the webhook (and the LIFF routes) and read wherever a date is printed.
# A context variable rather than a parameter threaded through ~30
# handlers: the preference belongs to the person, not to the call.
_DISPLAY: ContextVar[dict] = ContextVar("display_prefs", default={})
DATE_FORMATS = ("dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd")
DEFAULT_TIMEZONE = "Asia/Bangkok"


def set_display_prefs(prefs: dict | None) -> None:
    _DISPLAY.set(dict(prefs or {}))


def display_prefs() -> dict:
    return _DISPLAY.get()


def local_tz():
    """The reader's zone; Bangkok when unset or unknown."""
    name = str(display_prefs().get("timezone") or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_today() -> date:
    return datetime.now(local_tz()).date()


_EN_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_thai_date(value: date) -> str:
    """The date the way this reader asked for it: Thai text with the
    Buddhist year by default; a numeric format from the preference; the
    Gregorian year and English months for an English reader."""
    prefs = display_prefs()
    english = str(prefs.get("language") or "th") == "en"
    year = value.year if english else value.year + 543
    fmt = str(prefs.get("date_format") or "")
    if fmt == "dd/mm/yyyy":
        return f"{value.day:02d}/{value.month:02d}/{year}"
    if fmt == "mm/dd/yyyy":
        return f"{value.month:02d}/{value.day:02d}/{year}"
    if fmt == "yyyy-mm-dd":
        return f"{year}-{value.month:02d}-{value.day:02d}"
    if english:
        return f"{value.day} {_EN_MONTHS[value.month]} {year}"
    months = [
        "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
        "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
    ]
    return f"{value.day} {months[value.month]} {year}"


def format_thai_time(value: time | None) -> str:
    return f"{value.hour:02d}:{value.minute:02d} น." if value else ""
