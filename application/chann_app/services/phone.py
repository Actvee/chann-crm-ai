"""One rule for phone numbers, used by chat, CSV import and the API bodies.

User review (4 Sep 2026): letters were being saved as phone numbers. A
phone may contain digits, spaces, dashes, dots, parentheses and a leading
"+", and must carry 9–15 digits. Anything else is refused with a reason
the person can act on, at every door the number can come in through.
"""
from __future__ import annotations

import re

_ALLOWED = re.compile(r"^\+?[\d\s\-().]+$")


def phone_problem(value: str | None) -> str | None:
    """None when the value is an acceptable phone number, otherwise one of
    "letters" (non-numeric characters present) or "length" (not 9–15 digits)."""
    text = (value or "").strip()
    if not text:
        return None
    if not _ALLOWED.match(text):
        return "letters"
    digits = re.sub(r"\D", "", text)
    if not 9 <= len(digits) <= 15:
        return "length"
    return None


def normalise_phone(value: str | None) -> str:
    """Digits only; a Thai international prefix becomes the local zero."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("66") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits
