"""Thai message -> JSON action — Master Spec 4.6/4.7.

The permission gate is NOT here. The model is told which permission keys the
caller holds so it can answer usefully ("you can't do that, but here's what you
can"), but a model that hallucinates an action it was told it lacks must still
be stopped by the real check in the Data Tier. Prompt text is guidance; it is
not an authorization boundary, and Principle #10's gate stays where it is.
"""
from __future__ import annotations

import json
import logging

from .client import AIUnavailable, complete

log = logging.getLogger(__name__)

# Shown to the user when every provider failed (4.7). Deliberately plain: no
# blame, no jargon, no invented detail about what went wrong.
#
# Phase 5: this string is produced before any model call succeeds, so it cannot
# come from the model — it has to exist per-locale in our own code.
UNAVAILABLE_REPLY_BY_LOCALE = {
    "th": "ขออภัย ระบบไม่พร้อมใช้งานชั่วคราว กรุณาลองใหม่อีกครั้ง",
    "en": "Sorry — the service is temporarily unavailable. Please try again.",
}
DEFAULT_LOCALE = "th"

# Kept for callers that predate Phase 5's language plumbing.
UNAVAILABLE_REPLY = UNAVAILABLE_REPLY_BY_LOCALE[DEFAULT_LOCALE]


def unavailable_reply(language: str = DEFAULT_LOCALE) -> str:
    """Fall back to Thai for an unknown locale — the product is Thai-first."""
    return UNAVAILABLE_REPLY_BY_LOCALE.get(
        (language or "").lower(), UNAVAILABLE_REPLY_BY_LOCALE[DEFAULT_LOCALE]
    )

INTENT_SYSTEM_PROMPT = """You are an intent parser for a CRM system.
Convert the user's message into a JSON action.

User context:
- chann_uid: {chann_uid}
- role: {role}
- license_id: {license_id}
- language: {language}

Permission keys this user holds: {permission_keys}

Return ONLY a JSON object, no prose and no markdown fences.

Shape:
{{"action": "...", "entity": "...", "fields": {{}}, "missing": []}}

Rules:
- If required information is missing, list the missing field names in "missing".
- If the user's permission keys do not cover the requested action, return
  {{"action": "suggest", "suggestions": [...]}} listing things they may do instead.
- Never invent field values the user did not provide.
- Any human-readable text you produce (suggestions, and the names of missing
  fields as shown to the user) MUST be written in {language_name}.
  Machine-facing values — action, entity, and field keys — stay in English
  regardless of language, because downstream code matches on them.
"""

# Spelled out for the model rather than passing a bare code: "th" is far more
# ambiguous in a prompt than "Thai".
LANGUAGE_NAMES = {"th": "Thai", "en": "English"}


def build_prompt(
    *,
    chann_uid: str,
    role: str,
    license_id: str,
    permission_keys: list[str] | frozenset[str],
    language: str = "th",
) -> str:
    keys = sorted(permission_keys)
    lang = (language or DEFAULT_LOCALE).lower()
    return INTENT_SYSTEM_PROMPT.format(
        chann_uid=chann_uid,
        role=role,
        license_id=license_id,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang, LANGUAGE_NAMES[DEFAULT_LOCALE]),
        permission_keys=", ".join(keys) if keys else "(none)",
    )


def parse_intent_json(raw: str) -> dict:
    """Extract the JSON object from a model reply.

    Tolerates markdown fences and leading prose because models emit them even
    when told not to, but does not tolerate a shape the caller can't rely on:
    a reply without a usable "action" raises rather than returning a dict that
    looks fine until something downstream reads .get("action") and gets None.
    """
    text = (raw or "").strip()

    if text.startswith("```"):
        # ```json ... ``` — drop the fence line and everything after the close.
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise AIUnavailable("model reply contained no JSON object")
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIUnavailable(f"model reply was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise AIUnavailable("model reply was not a JSON object")

    action = parsed.get("action")
    if not isinstance(action, str) or not action.strip():
        raise AIUnavailable("model reply had no usable 'action'")

    parsed.setdefault("entity", None)
    parsed.setdefault("fields", {})
    parsed.setdefault("missing", [])
    if not isinstance(parsed["fields"], dict):
        parsed["fields"] = {}
    if not isinstance(parsed["missing"], list):
        parsed["missing"] = []
    return parsed


async def parse_intent(
    *,
    message: str,
    chann_uid: str,
    role: str,
    license_id: str,
    permission_keys: list[str] | frozenset[str],
    language: str = "th",
    client=None,
) -> dict:
    """Parse one user message. Raises AIUnavailable; never returns a half-result."""
    system_prompt = build_prompt(
        chann_uid=chann_uid,
        role=role,
        license_id=license_id,
        permission_keys=permission_keys,
        language=language,
    )
    raw = await complete(
        system_prompt=system_prompt,
        user_message=message,
        thinking=False,          # 4.3: chat tier runs with thinking OFF
        client=client,
    )
    return parse_intent_json(raw)
