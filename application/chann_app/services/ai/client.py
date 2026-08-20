"""OpenRouter client — Master Spec 4.1-4.5.

Two things here are load-bearing and easy to get wrong:

1. Thinking mode. Qwen3.6-35B-A3B ships with integrated thinking ON. Master
   Spec 4.3 requires it OFF for the chat tier, and 4.7's test asserts a reply
   inside 3s rather than 80+. So chat calls send reasoning.enabled=false
   explicitly — omitting the field is not the same as disabling it.

2. Failing closed, quickly. 4.7 requires that every provider failing produces
   a plain apology rather than a hang. Every path out of complete() is either
   a parsed result or AIUnavailable within the timeout budget; nothing here
   retries forever or swallows an exception into a None the caller must guess
   about.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from ...config import settings
from .metrics import Timer
from .providers import provider_block

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# 4.7: "OpenRouter ไม่ตอบใน 10 วิ → fallback provider"
REQUEST_TIMEOUT_S = 10.0
MAX_ATTEMPTS = 2
RETRY_BACKOFF_S = 0.5

# Retrying a 4xx just burns the timeout budget — the request is malformed or
# the key is bad, and it will be equally malformed next time. 429 is the
# exception: it is explicitly "try again".
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class AIUnavailable(RuntimeError):
    """Every attempt failed. Callers must degrade gracefully, never hang."""


class AINotConfigured(RuntimeError):
    """OPENROUTER_API_KEY / OPENROUTER_MODEL missing — a deploy problem."""


def _chat_model() -> str:
    model = (settings.openrouter_model or "").strip()
    if not model:
        raise AINotConfigured("OPENROUTER_MODEL is REQUIRED_NOT_CONFIGURED")
    return model


def _reasoning_model() -> str:
    """Ad-hoc report tier (Phase 17) — thinking stays ON here by design."""
    model = (settings.openrouter_model_reasoning or "").strip()
    if not model:
        raise AINotConfigured("OPENROUTER_MODEL_REASONING is REQUIRED_NOT_CONFIGURED")
    return model


async def complete(
    *,
    system_prompt: str,
    user_message: str,
    thinking: bool = False,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return the assistant's raw text. Raises AIUnavailable if all attempts fail."""
    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise AINotConfigured("OPENROUTER_API_KEY is REQUIRED_NOT_CONFIGURED")

    chosen = model or (_reasoning_model() if thinking else _chat_model())

    body: dict = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Explicit both ways — see module docstring.
        "reasoning": {"enabled": bool(thinking)},
    }
    providers = provider_block(chosen)
    if providers is not None:
        body["provider"] = providers

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    last_error = "no attempt was made"

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with Timer(chosen) as timer:
                try:
                    resp = await http.post(
                        OPENROUTER_URL, headers=headers, json=body,
                        timeout=REQUEST_TIMEOUT_S,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    log.warning("openrouter attempt %s failed: %s", attempt, last_error)
                else:
                    if resp.status_code == 200:
                        text, usage, provider = _parse(resp)
                        timer.ok = True
                        timer.provider = provider
                        timer.prompt_tokens = usage.get("prompt_tokens", 0) or 0
                        timer.completion_tokens = usage.get("completion_tokens", 0) or 0
                        return text
                    last_error = f"HTTP {resp.status_code}"
                    log.warning(
                        "openrouter attempt %s returned %s", attempt, resp.status_code
                    )
                    if resp.status_code not in RETRYABLE_STATUS:
                        break

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_S)

        raise AIUnavailable(f"all OpenRouter attempts failed ({last_error})")
    finally:
        if owns_client:
            await http.aclose()


def _parse(resp: httpx.Response) -> tuple[str, dict, str | None]:
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise AIUnavailable(f"OpenRouter returned non-JSON: {exc}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise AIUnavailable("OpenRouter returned no choices")

    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise AIUnavailable("OpenRouter returned an empty message")

    return content, data.get("usage") or {}, data.get("provider")
