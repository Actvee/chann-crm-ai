"""Deal amount and closing date, read out of ordinary Thai/English chat.

User review (4 Sep 2026): "มูลค่า 500,000 บาท คาดว่าจะปิดวันที่ 30/09/2026"
came back with the amount and the date swapped, or the amount taken as
a phone number. The model is not asked to do arithmetic on "1.2 ล้าน"
any more; it may name the fields, and this module decides what the
numbers actually are — deterministically, so a test can pin it.

Rules, in words:
- phone numbers, ISO/numeric dates, record codes (D-2026-0001) and
  quantities ("3 ตัว") are never amounts;
- an amount needs a money cue (มูลค่า/ราคา/ยอด/บาท/฿, a unit like ล้าน/K,
  or comma grouping) or, failing that, a bare number of at least 1,000;
- two different candidate amounts in one message is ambiguous — the
  caller asks, it does not pick;
- the closing date reuses parse_thai_date (the project's one date
  parser) on the text with amounts and phones removed, plus the month
  phrasings a salesperson uses ("สิ้นเดือนนี้", "ปลายเดือนหน้า").
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .thai_datetime import parse_thai_date

UNIT_MULTIPLIERS = {
    "ล้าน": Decimal(1_000_000), "m": Decimal(1_000_000), "mb": Decimal(1_000_000),
    "แสน": Decimal(100_000), "หมื่น": Decimal(10_000), "พัน": Decimal(1_000),
    "k": Decimal(1_000),
}
MONEY_CUES = (
    "มูลค่า", "ราคา", "ยอด", "บาท", "฿", "thb", "งบ", "วงเงิน", "amount", "worth", "value", "budget", "usd", "$",
)
QUANTITY_WORDS = ("ตัว", "ชิ้น", "อัน", "เครื่อง", "ชุด", "คน", "วัน", "นาที", "ชั่วโมง", "เดือน", "ปี", "%", "เปอร์เซ็นต์", "ราย")
CURRENCY_CUES = {"usd": "USD", "$": "USD", "ดอลลาร์": "USD", "eur": "EUR", "ยูโร": "EUR", "jpy": "JPY", "เยน": "JPY"}

_PHONE_RE = re.compile(r"(?<!\d)(?:\+66|0)\d{1,2}[- ]?\d{3}[- ]?\d{3,4}(?!\d)")
_CODE_RE = re.compile(r"\b[A-Z]{1,3}-\d{4}-\d{4}\b")
# d/m, d-m, d/m/y, d-m-y and d.m.y — but never "1.2" (a decimal): a dotted
# date needs all three parts.
_NUMERIC_DATE_RE = re.compile(r"(?<!\d)\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?(?!\d)|(?<!\d)\d{1,2}\.\d{1,2}\.\d{2,4}(?!\d)|\b\d{4}-\d{2}-\d{2}\b")
_AMOUNT_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(ล้านบาท|ล้าน|แสน|หมื่น|พัน|mb|m|k|บาท|฿|thb|usd)?",
    re.IGNORECASE,
)
_THAI_MONTH_WORDS = ("ม.ค", "ก.พ", "มี.ค", "เม.ย", "พ.ค", "มิ.ย", "ก.ค", "ส.ค", "ก.ย", "ต.ค", "พ.ย", "ธ.ค",
                     "มกรา", "กุมภา", "มีนา", "เมษา", "พฤษภา", "มิถุนา", "กรกฎา", "สิงหา", "กันยา", "ตุลา", "พฤศจิกา", "ธันวา")
DATE_CUES = ("ปิด", "close", "closing", "คาดว่า", "กำหนด", "ภายใน", "ส่งมอบ", "สิ้นเดือน", "ปลายเดือน", "ต้นเดือน", "วันที่", "expect")


def _strip_non_amounts(text: str) -> str:
    text = _PHONE_RE.sub(" ", text or "")
    text = _CODE_RE.sub(" ", text)
    text = _NUMERIC_DATE_RE.sub(" ", text)
    return text


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def parse_amount(text: str) -> tuple[Decimal | None, str, list[Decimal]]:
    """(amount, currency, candidates). amount is None when nothing money-like
    was said, or when more than one distinct candidate makes it ambiguous
    (candidates then carries them all)."""
    cleaned = _strip_non_amounts(text)
    lowered = cleaned.lower()
    currency = "THB"
    for cue, code in CURRENCY_CUES.items():
        if cue in lowered:
            currency = code
            break
    candidates: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(cleaned):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        value = _to_decimal(raw)
        if value is None:
            continue
        before = lowered[max(0, match.start() - 14):match.start()]
        after = lowered[match.end():match.end() + 12].lstrip()
        # "3 ตัว", "2 วัน", "วันที่ 30", "รอบ 2": a count or a day, not money
        if unit in ("", "m") and any(after.startswith(w) for w in QUANTITY_WORDS):
            continue
        if any(before.rstrip().endswith(w) for w in ("วันที่", "รอบ", "ครั้งที่", "ข้อ", "เลข", "ที่")):
            continue
        # a date the numeric strip missed: "30 กันยายน", "15 ต.ค."
        if unit == "" and any(after.startswith(w) for w in _THAI_MONTH_WORDS):
            continue
        if unit in UNIT_MULTIPLIERS or unit == "ล้านบาท":
            value = value * (UNIT_MULTIPLIERS["ล้าน"] if unit == "ล้านบาท" else UNIT_MULTIPLIERS[unit])
        elif unit in ("บาท", "฿", "thb", "usd"):
            pass
        else:
            cued = any(c in before for c in MONEY_CUES) or "," in raw
            if not cued and value < 1000:
                continue
        value = value.quantize(Decimal("0.01"))
        if value not in candidates:
            candidates.append(value)
    if len(candidates) == 1:
        return candidates[0], currency, candidates
    return None, currency, candidates


def _end_of_month(today: date, months_ahead: int = 0) -> date:
    month = today.month - 1 + months_ahead
    year = today.year + month // 12
    month = month % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_close_date(text: str, today: date) -> date | None:
    """The expected closing date named in the message, or None."""
    raw = text or ""
    lowered = raw.lower()
    if "สิ้นเดือนหน้า" in lowered or "ปลายเดือนหน้า" in lowered:
        return _end_of_month(today, 1)
    if "สิ้นเดือน" in lowered or "ปลายเดือน" in lowered or "end of the month" in lowered or "end of month" in lowered:
        return _end_of_month(today)
    if "ต้นเดือนหน้า" in lowered:
        return _end_of_month(today) + timedelta(days=1)
    if "สิ้นปี" in lowered or "ปลายปี" in lowered:
        return date(today.year, 12, 31)
    # Amounts and phones out of the way, so "500,000 บาท" cannot be read as
    # a day-of-month by the named-month pattern.
    stripped = _PHONE_RE.sub(" ", raw)
    stripped = re.sub(r"\d[\d,]*(?:\.\d+)?\s*(?:ล้านบาท|ล้าน|แสน|หมื่น|พัน|mb|k|บาท|฿|thb|usd)\b", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\d{1,3}(?:,\d{3})+", " ", stripped)
    return parse_thai_date(stripped, today)


_DATE_CUE_RE = re.compile(r"(?<!เ)ปิด|close|closing|คาดว่า|กำหนด|ภายใน|ส่งมอบ|สิ้นเดือน|ปลายเดือน|ต้นเดือน|วันที่|expect")


def has_date_cue(text: str) -> bool:
    # "เปิดดีล" (open a deal) contains ปิด — the lookbehind keeps it out.
    return bool(_DATE_CUE_RE.search((text or "").lower()))


def _phone_like(value) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    return len(digits) in (9, 10) and digits.startswith("0")


def extract_deal_fields(message: str, ai_fields: dict | None, today: date) -> dict:
    """Merge what the model named with what the message actually says.

    Returns {"amount": Decimal|None, "currency": str, "expected_close_date":
    date|None, "ambiguous": [field, ...]}. The model's values are checked,
    never trusted: an amount that looks like a phone number, or a date
    that is not a date, is dropped and re-read from the message.
    """
    ai_fields = ai_fields or {}
    amount, currency, candidates = parse_amount(message)
    ambiguous: list[str] = []
    ai_amount = ai_fields.get("amount")
    if ai_amount not in (None, ""):
        parsed = None
        if isinstance(ai_amount, (int, float, Decimal)):
            parsed = Decimal(str(ai_amount))
        else:
            parsed, _, _ = parse_amount(str(ai_amount))
        if parsed is not None and not _phone_like(ai_amount) and parsed > 0:
            # Accept the model's value only when it agrees with something in
            # the message — the message is the ground truth.
            if not candidates or parsed in candidates:
                amount = parsed
    if amount is None and len(candidates) > 1:
        ambiguous.append("amount")
    ai_currency = str(ai_fields.get("currency") or "").strip().upper()
    if len(ai_currency) == 3 and ai_currency.isalpha():
        currency = ai_currency

    close = None
    ai_date = ai_fields.get("expected_close_date") or ai_fields.get("closing_date") or ai_fields.get("close_date")
    if ai_date:
        close = parse_thai_date(str(ai_date), today)
    if close is None:
        close = parse_close_date(message, today)
    if close is None and has_date_cue(message) and re.search(r"\d|สิ้น|ปลาย|ต้น|หน้า", message or ""):
        # A date was clearly meant but could not be read — ask, don't guess.
        if not any(w in (message or "") for w in ("สิ้นเดือน", "ปลายเดือน", "ต้นเดือน", "สิ้นปี")):
            ambiguous.append("expected_close_date")
    return {"amount": amount, "currency": currency, "expected_close_date": close, "ambiguous": ambiguous}


def format_amount(amount, currency: str = "THB") -> str:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return str(amount)
    text = f"{value:,.2f}".rstrip("0").rstrip(".") if value != value.to_integral() else f"{int(value):,}"
    return f"{text} บาท" if currency == "THB" else f"{text} {currency}"
