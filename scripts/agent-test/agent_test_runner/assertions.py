"""Checking a reply against what a scenario expected.

Every failure has to be actionable by something that cannot open a debugger:
the reason says what was wanted, what came back, and — for the text
assertions — the surrounding characters, so the caller can see whether the
answer was wrong or merely worded differently.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .scenario import BAD_CLASSES, _as_list


@dataclass
class Outcome:
    """What a step produced, in the one shape every assertion reads.

    A chat step fills text/quick_replies/images/intent; an http step fills
    status/body. Sharing the object keeps the assertion vocabulary flat
    rather than splitting it per step type, which is what a scenario author
    sees when they read the README.
    """

    text: str = ""
    quick_replies: list[tuple[str, str]] = field(default_factory=list)
    list_card: dict | None = None
    images: list[str] = field(default_factory=list)
    intent: dict | None = None
    used_ai: bool = False
    status: int | None = None
    body: Any = None


def classify(text: str) -> str:
    """The bad-reply class this text falls into, or "ok".

    Same order and same needles as simulate-phrasings.py, so `is_not` in a
    scenario and a FINDING in a simulator mean the same thing.
    """
    for label, needles in BAD_CLASSES.items():
        if any(needle in text for needle in needles):
            return label
    return "ok"


def _excerpt(text: str, limit: int = 160) -> str:
    flat = " / ".join(line for line in text.splitlines() if line.strip())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _near(text: str, needle: str) -> str:
    """The part of the reply that came closest to the needle.

    A missing substring is nearly always a wording change, so showing the
    line that shares the most characters turns "not found" into a diff a
    reader can act on.
    """
    best, best_score = "", 0
    for line in text.splitlines():
        score = sum(1 for ch in set(needle) if ch in line)
        if score > best_score:
            best, best_score = line, score
    return best.strip()[:120]


def _json_at(body: Any, path: str) -> tuple[bool, Any]:
    """Walk a dotted path; digits index into lists. Returns (found, value)."""
    current = body
    for part in path.split("."):
        if isinstance(current, list):
            if not part.lstrip("-").isdigit():
                return False, None
            index = int(part)
            if not -len(current) <= index < len(current):
                return False, None
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        else:
            return False, None
    return True, current


def check(expect: dict, outcome: Outcome) -> list[str]:
    """Every assertion in `expect`, all of them; returns the failures.

    Deliberately not short-circuiting: one run should report every way the
    step disagreed, so the caller fixes the scenario (or the code) once
    instead of discovering the next problem on the next run.
    """
    problems: list[str] = []
    text = outcome.text or ""

    for needle in _as_list(expect.get("contains")):
        if str(needle) not in text:
            problems.append(
                f"expected the reply to contain {needle!r}\n"
                f"closest line: {_near(text, str(needle)) or '(reply is empty)'}"
            )
    for needle in _as_list(expect.get("not_contains")):
        if str(needle) in text:
            problems.append(f"the reply contains {needle!r} and should not")
    for needle in _as_list(expect.get("actions_include")):
        # List-card actions are message buttons too. Ignoring them falsely
        # reported that a search result offered no customer choices.
        choices = [payload for _, payload in outcome.quick_replies]
        choices += [str(row.get("action_text") or "")
                    for row in (outcome.list_card or {}).get("rows", [])]
        if not any(str(needle) in choice for choice in choices):
            problems.append(f"no message action matching {needle!r}; got {choices}")
    for pattern in _as_list(expect.get("regex")):
        if not re.search(pattern, text, re.MULTILINE):
            problems.append(f"no match for regex {pattern!r} in the reply")

    for wanted in _as_list(expect.get("quick_replies_include")):
        labels = [label for label, _ in outcome.quick_replies]
        payloads = [payload for _, payload in outcome.quick_replies]
        if not any(str(wanted) in candidate for candidate in labels + payloads):
            problems.append(
                f"no quick reply matching {wanted!r}; got {labels or '(none)'}"
            )

    if "has_image" in expect:
        has = bool(outcome.images)
        if has is not bool(expect["has_image"]):
            problems.append(
                f"expected has_image={expect['has_image']}, reply carried "
                f"{len(outcome.images)} image(s)"
            )

    if "intent" in expect:
        got = outcome.intent or {}
        for key, wanted in expect["intent"].items():
            if str(got.get(key)) != str(wanted):
                problems.append(
                    f"intent.{key}: expected {wanted!r}, got {got.get(key)!r}"
                    + ("" if outcome.intent else " (the reply carried no intent — "
                                                 "it was answered before the model)")
                )

    if "max_lines" in expect:
        lines = len(text.splitlines()) or (1 if text else 0)
        if lines > expect["max_lines"]:
            problems.append(
                f"{lines} lines, more than the {expect['max_lines']} a LINE "
                f"bubble should carry"
            )
    if "max_chars" in expect and len(text) > expect["max_chars"]:
        problems.append(f"{len(text)} characters, more than {expect['max_chars']}")

    forbidden = _as_list(expect.get("is_not"))
    if forbidden:
        kind = classify(text)
        if kind in forbidden:
            problems.append(
                f"the reply is a {kind} answer, which this step forbids\n"
                f"reply: {_excerpt(text)}"
            )

    if "used_ai" in expect and outcome.used_ai is not bool(expect["used_ai"]):
        problems.append(
            f"expected used_ai={expect['used_ai']}, the model was "
            f"{'called' if outcome.used_ai else 'not called'}"
        )

    if "status" in expect and outcome.status != expect["status"]:
        problems.append(
            f"HTTP {outcome.status}, expected {expect['status']}"
            f" — body: {_excerpt(json.dumps(outcome.body, ensure_ascii=False, default=str))}"
        )
    for path, wanted in (expect.get("json_path") or {}).items():
        found, value = _json_at(outcome.body, str(path))
        if not found:
            problems.append(f"json path {path!r} is not in the response body")
        elif str(value) != str(wanted):
            problems.append(f"json {path}: expected {wanted!r}, got {value!r}")
    for needle in _as_list(expect.get("json_contains")):
        dumped = json.dumps(outcome.body, ensure_ascii=False, default=str)
        if str(needle) not in dumped:
            problems.append(f"the response body does not contain {needle!r}")

    return problems
