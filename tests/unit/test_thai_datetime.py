"""Thai date and time parsing for reminders.

Tested exhaustively because a reminder on the wrong day is silently wrong:
the person believes they are covered and finds out when the customer has
already gone quiet. Everything here is pure, so there is no excuse for not
covering the phrasings people actually type.
"""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.thai_datetime import (  # noqa: E402
    format_thai_date,
    format_thai_time,
    parse_thai_date,
    parse_thai_time,
    to_gregorian_year,
)

# A Friday, so weekday arithmetic has a known reference.
FRIDAY = date(2026, 8, 28)


class TestBuddhistYears:
    def test_buddhist_years_convert(self):
        """2569 is 2026. Thai calendars and Thai people use the Buddhist era,
        so a date typed from one has to be read as one."""
        assert to_gregorian_year(2569) == 2026
        assert to_gregorian_year(2570) == 2027

    def test_gregorian_years_pass_through(self):
        assert to_gregorian_year(2026) == 2026

    def test_two_digit_years_are_buddhist(self):
        """"69" is 2569 in Thai usage far more often than 2069."""
        assert to_gregorian_year(69) == 2026


class TestRelativeDates:
    def test_today_and_tomorrow(self):
        assert parse_thai_date("วันนี้", FRIDAY) == FRIDAY
        assert parse_thai_date("พรุ่งนี้", FRIDAY) == date(2026, 8, 29)
        assert parse_thai_date("มะรืนนี้", FRIDAY) == date(2026, 8, 30)

    def test_in_n_days_and_weeks(self):
        assert parse_thai_date("อีก 3 วัน", FRIDAY) == date(2026, 8, 31)
        assert parse_thai_date("อีก 2 สัปดาห์", FRIDAY) == date(2026, 9, 11)

    def test_a_weekday_means_the_next_one_never_today(self):
        """Someone saying "on Friday" on a Friday means the one coming, not
        the day that is already half over."""
        assert parse_thai_date("วันศุกร์", FRIDAY) == date(2026, 9, 4)

    def test_a_later_weekday_this_week(self):
        # Friday -> next Monday is 3 days away.
        assert parse_thai_date("วันจันทร์", FRIDAY) == date(2026, 8, 31)

    def test_next_pushes_a_further_week(self):
        # Friday + "จันทร์หน้า" skips past this coming Monday.
        assert parse_thai_date("จันทร์หน้า", FRIDAY) == date(2026, 9, 7)


class TestAbsoluteDates:
    def test_named_month_with_buddhist_year(self):
        assert parse_thai_date("15 มี.ค. 2569", FRIDAY) == date(2026, 3, 15)

    def test_named_month_without_a_year_looks_forward(self):
        """A bare "15 มีนาคม" already past this year means next year: a
        reminder is always about something ahead."""
        assert parse_thai_date("15 มีนาคม", FRIDAY) == date(2027, 3, 15)

    def test_numeric_date(self):
        assert parse_thai_date("15/03/2569", FRIDAY) == date(2026, 3, 15)

    def test_an_iso_date_is_read_as_written_not_as_d_m_y(self):
        """The production misread of 1 Sep 2026, pinned exactly.

        "เตือน C-2026-0011 2026-09-06" was parsed as 26 September 1963: the
        d-m-y pattern found "26-09-06" inside the ISO date, took "06" as a
        two-digit Buddhist year, and the confirmation dutifully echoed
        "26 ก.ย. 2506". ISO is the format the system writes onto its own
        quick-reply buttons, so it must win — and the record code sitting
        next to it must not confuse the read.
        """
        got = parse_thai_date("เตือน C-2026-0011 2026-09-06", date(2026, 9, 2))
        assert got == date(2026, 9, 6)

    def test_a_buddhist_year_in_iso_shape_converts_like_every_other(self):
        """"2569-09-06" is 2026, not a literal year five centuries out that
        would be stored, never announced, and silently wrong."""
        assert parse_thai_date("2569-09-06", date(2026, 9, 2)) == date(2026, 9, 6)

    def test_an_impossible_date_is_refused_not_clamped(self):
        """31 February must not quietly become 28 February on a document
        someone is relying on."""
        assert parse_thai_date("31/02/2569", FRIDAY) is None

    def test_unrecognised_text_returns_none(self):
        """None becomes "say it like this" for the user. Guessing would put a
        reminder on a day nobody chose."""
        assert parse_thai_date("เดี๋ยวค่อยว่ากัน", FRIDAY) is None
        assert parse_thai_date("", FRIDAY) is None


class TestTimes:
    def test_explicit_clock_times(self):
        assert parse_thai_time("14:00") == time(14, 0)
        assert parse_thai_time("9.30") == time(9, 30)

    def test_thai_afternoon_counting(self):
        """บ่าย 2 is 14:00, not 2:00 — the number restarts after noon."""
        assert parse_thai_time("บ่าย 2") == time(14, 0)
        assert parse_thai_time("บ่าย 3") == time(15, 0)

    def test_a_bare_vague_word_still_resolves(self):
        assert parse_thai_time("เช้า") == time(9, 0)
        assert parse_thai_time("เย็น") == time(17, 0)

    def test_specific_beats_vague(self):
        """"บ่าย 3" must not fall through to the generic บ่าย = 13:00."""
        assert parse_thai_time("นัดบ่าย 3 โมง") == time(15, 0)

    def test_evening_counting(self):
        assert parse_thai_time("2 ทุ่ม") == time(20, 0)

    def test_morning_counting(self):
        assert parse_thai_time("9 โมงเช้า") == time(9, 0)

    def test_no_time_means_a_whole_day_reminder(self):
        """None is a real answer, not a failure: most reminders are for a day,
        not a moment."""
        assert parse_thai_time("พรุ่งนี้") is None
        assert parse_thai_time("") is None

    def test_an_impossible_clock_time_is_refused(self):
        assert parse_thai_time("25:00") is None
        assert parse_thai_time("10:75") is None


class TestFormatting:
    def test_dates_are_shown_in_the_buddhist_era(self):
        """The reply echoes the parsed date back so a misread is caught before
        it matters — which only works if it is shown the way the reader
        thinks about dates."""
        assert format_thai_date(date(2026, 3, 15)) == "15 มี.ค. 2569"

    def test_times_are_shown_with_the_thai_hour_marker(self):
        assert format_thai_time(time(14, 0)) == "14:00 น."
        assert format_thai_time(None) == ""


class TestReminderSubject:
    """What a reminder is ABOUT, pulled out of the message that set it.

    "นัดดูสินค้าวันนี้ตอน 3 โมง" used to be reduced to a date and a time,
    with the actual instruction thrown away — so the reminder that arrived
    could only name the record type. The date is stored structurally, so it
    is stripped here rather than repeated in the text.
    """

    def _subject(self, message):
        from chann_app.services.chat import _reminder_subject

        return _reminder_subject(message, "")

    def test_the_instruction_survives_the_date_and_time(self):
        assert self._subject("นัดดูสินค้าวันนี้ตอน 3 โมง") == "ดูสินค้า"
        assert self._subject("นัดโทรหาวันนี้") == "โทรหา"

    def test_a_longer_subject_after_the_date_is_kept(self):
        assert self._subject(
            "เตือน D-2026-0001 พรุ่งนี้ บ่าย 2 ประชุมสรุปราคา"
        ) == "ประชุมสรุปราคา"

    def test_a_bare_reminder_has_no_subject(self):
        """Empty is a correct answer: the caller then names the record
        instead, rather than inventing a subject nobody typed."""
        assert self._subject("เตือน C-2026-0005 วันนี้") == ""

    def test_a_weekday_does_not_leave_the_word_day_behind(self):
        """Stripping "วัน" before "ศุกร์" left "วัน" as the subject — caught
        while writing these, and the reason weekday names are removed
        first."""
        assert self._subject("เตือนวันศุกร์") == ""

    def test_a_named_month_is_not_mistaken_for_a_subject(self):
        assert self._subject("นัดประชุมวันจันทร์ 15 มีนาคม") == "ประชุม"

    def test_a_single_stray_character_is_not_a_subject(self):
        assert self._subject("เตือน ก วันนี้") == ""
