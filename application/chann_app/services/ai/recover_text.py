"""Free text the model retyped, put back the way the person wrote it.

A model asked to copy a long Thai phrase into a JSON field will
occasionally drop a vowel mark or a tone mark. "สนใจอยากซื้อพัดลมขอเข้ามา
ดูสินค้าวันที่ 6" came back as "สนใจอยากซี้พัดลมขอเข้ามาดูสินค้วันที่ 6" —
close enough to look right in a review and wrong in the record a shop
keeps about its customer.

Structured fields are safe: a phone number is digits, a date is a date,
and a name is short enough that damage is obvious. It is the long
free-text fields — notes, descriptions, issue reports — that get quietly
corrupted, and those are exactly the fields where the person's own words
are the whole point.

So the model is used for what it is good at (deciding that a span of the
message IS the note) and not trusted for what it is bad at (reproducing
the characters). The span is located in the original message and the
original characters are kept.
"""
from __future__ import annotations

import re

# Fields whose value is the person's own prose rather than a code, a
# number or a name. Only these are recovered — a corrected spelling in a
# name field may well be the model doing its job.
FREE_TEXT_FIELDS = frozenset({
    "notes", "issue_description", "found_issue", "work_done",
    "parts_changed", "description", "service_address",
})

# Thai combining marks: the characters that go missing. Stripping them
# for comparison is what lets "ซี้" match "ซื้อ" as the same span.
_COMBINING = re.compile(r"[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]")


def _skeleton(text: str) -> str:
    """The consonants and digits, with marks and spacing removed.

    Two strings with the same skeleton are the same words typed with
    different amounts of damage.
    """
    return _COMBINING.sub("", (text or "")).replace(" ", "").lower()


def recover_free_text(fields: dict, message: str) -> dict:
    """`fields` with free text replaced by what the person actually typed.

    Returns a new dict. A field is only replaced when a matching span is
    found in the original message: if the model summarised rather than
    copied, its version is what the caller asked for and is kept.
    """
    if not message:
        return dict(fields)

    out = dict(fields)
    for key, value in fields.items():
        if key not in FREE_TEXT_FIELDS or not isinstance(value, str):
            continue
        if not value.strip():
            continue
        original = _find_span(message, value)
        if original is not None and original != value:
            out[key] = original
    return out


def _find_span(message: str, mangled: str) -> str | None:
    """The stretch of `message` that `mangled` is a damaged copy of.

    Anchored on the ends rather than matched whole: the damage is in the
    middle — dropped vowels, and sometimes a dropped consonant, so
    "ซื้อ" comes back as "ซี้" — and any exact-match approach fails on
    exactly the input this exists for.

    So the first few characters and the last few are located in the
    original, and everything between them is taken verbatim. Those
    anchors are short enough to survive the damage and long enough to be
    unambiguous.
    """
    cleaned = (mangled or "").strip()
    if len(cleaned) < 8:
        # Too short to anchor safely. Replacing the wrong span is worse
        # than keeping the model's version.
        return None

    head = _skeleton(cleaned[:6])
    tail = _skeleton(cleaned[-6:])
    if len(head) < 3 or len(tail) < 3:
        return None

    positions: list[int] = []
    skeleton_chars: list[str] = []
    for index, char in enumerate(message):
        if _COMBINING.match(char) or char == " ":
            continue
        skeleton_chars.append(char.lower())
        positions.append(index)
    haystack = "".join(skeleton_chars)

    start = haystack.find(head)
    if start < 0:
        return None
    end = haystack.rfind(tail)
    if end < start:
        return None

    first = positions[start]
    last = positions[end + len(tail) - 1]
    stop = last + 1
    while stop < len(message) and _COMBINING.match(message[stop]):
        stop += 1

    recovered = message[first:stop].strip()
    # A recovered span wildly longer than what the model returned means
    # the anchors matched in the wrong places.
    if len(recovered) > len(cleaned) * 2:
        return None
    return recovered
