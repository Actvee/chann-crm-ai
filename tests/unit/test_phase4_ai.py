"""Phase 4 mandatory tests — Master Spec 4.7.

Uses httpx.MockTransport rather than monkeypatching our own client, so the
request our code actually builds is inspected: if the thinking-off flag or the
provider block stopped being sent, these tests fail. Monkeypatching complete()
would assert only that we call ourselves the way we expect to.

No test here reaches the network. Runtime acceptance ("ส่งข้อความไทย → ได้ JSON
ภายใน 3 วิ", 4.8) is a separate live check against DEV, not a unit test.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402
from chann_app.services.ai import intent as intent_mod  # noqa: E402,F401
from chann_app.services.ai.client import (  # noqa: E402
    AINotConfigured,
    AIUnavailable,
    complete,
)
from chann_app.services.ai.intent import (  # noqa: E402
    UNAVAILABLE_REPLY,
    build_prompt,
    parse_intent,
    parse_intent_json,
    unavailable_reply,
)
from chann_app.services.ai.metrics import metrics  # noqa: E402
from chann_app.services.ai.providers import (  # noqa: E402
    PROVIDER_PREFERENCE,
    provider_block,
)


def _openrouter_reply(content: str, provider: str = "fireworks") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            "provider": provider,
        },
    )


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "openrouter_model_reasoning", "deepseek/deepseek-v4-pro")
    metrics.reset()
    yield
    metrics.reset()


class TestAIIntentParsing:
    """4.7 test_ai_intent_parsing"""

    async def test_thai_create_customer_parses_to_action(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply(json.dumps({
                "action": "create", "entity": "customer",
                "fields": {"name": "สมชาย"}, "missing": [],
            }, ensure_ascii=False))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            result = await parse_intent(
                message="เพิ่มลูกค้าชื่อสมชาย",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.create"],
                client=c,
            )

        assert result["action"] == "create"
        assert result["entity"] == "customer"
        assert result["fields"]["name"] == "สมชาย"
        assert result["missing"] == []
        # the Thai text must survive intact all the way into the request body
        assert captured["body"]["messages"][1]["content"] == "เพิ่มลูกค้าชื่อสมชาย"

    async def test_incomplete_message_reports_missing_fields(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _openrouter_reply(json.dumps({
                "action": "create", "entity": "customer",
                "fields": {}, "missing": ["name"],
            }))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            result = await parse_intent(
                message="เพิ่มลูกค้า",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.create"],
                client=c,
            )
        assert result["missing"] == ["name"]

    async def test_action_without_permission_returns_suggest(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply(json.dumps({
                "action": "suggest",
                "suggestions": ["ดูรายชื่อลูกค้า"],
            }, ensure_ascii=False))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            result = await parse_intent(
                message="ลบลูกค้าทั้งหมด",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.read"],
                client=c,
            )

        assert result["action"] == "suggest"
        assert result["suggestions"]
        # the model must be TOLD the permission set — that is what makes a
        # useful suggest possible rather than a blind refusal
        assert "customer.read" in captured["body"]["messages"][0]["content"]
        assert "customer.delete" not in captured["body"]["messages"][0]["content"]

    async def test_timeout_then_success_falls_through_to_second_attempt(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("simulated 10s timeout", request=request)
            return _openrouter_reply(json.dumps({"action": "read", "entity": "customer"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            result = await parse_intent(
                message="ดูลูกค้า",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.read"],
                client=c,
            )
        assert calls["n"] == 2
        assert result["action"] == "read"

    async def test_all_providers_fail_raises_rather_than_hanging(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("all providers down", request=request)

        started = time.monotonic()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(AIUnavailable):
                await parse_intent(
                    message="เพิ่มลูกค้าชื่อสมชาย",
                    chann_uid="CHN-S-000001", role="sales",
                    license_id="lic-1", permission_keys=["customer.create"],
                    client=c,
                )
        # must fail fast, not hang
        assert time.monotonic() - started < 10
        # and there must be a plain user-facing reply available for this case
        assert UNAVAILABLE_REPLY and "ขออภัย" in UNAVAILABLE_REPLY

    async def test_4xx_is_not_retried(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, json={"error": "bad key"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(AIUnavailable):
                await complete(system_prompt="s", user_message="u", client=c)
        assert calls["n"] == 1, "a 401 will be a 401 next time too — retrying wastes the budget"

    async def test_429_is_retried(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"error": "slow down"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(AIUnavailable):
                await complete(system_prompt="s", user_message="u", client=c)
        assert calls["n"] == 2

    async def test_missing_api_key_is_a_config_error_not_an_outage(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        with pytest.raises(AINotConfigured):
            await complete(system_prompt="s", user_message="u")


class TestThinkingModeOff:
    """4.7 test_thinking_mode_off"""

    async def test_chat_tier_sends_reasoning_disabled(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply(json.dumps({"action": "read", "entity": "customer"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await parse_intent(
                message="ดูลูกค้า",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.read"],
                client=c,
            )

        # Qwen3.6 ships with thinking ON, so omitting the field is not the same
        # as disabling it — assert it is sent, and sent as false.
        assert captured["body"]["reasoning"] == {"enabled": False}
        assert captured["body"]["model"] == "qwen/qwen3.6-35b-a3b"

    async def test_reasoning_tier_keeps_thinking_on(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply("ok")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await complete(
                system_prompt="s", user_message="u", thinking=True, client=c
            )
        assert captured["body"]["reasoning"] == {"enabled": True}
        assert captured["body"]["model"] == "deepseek/deepseek-v4-pro"

    async def test_no_thinking_trace_leaks_into_parsed_output(self):
        """A reply wrapped in prose/fences must still yield clean JSON."""
        def handler(request: httpx.Request) -> httpx.Response:
            return _openrouter_reply(
                "Let me think about this...\n```json\n"
                + json.dumps({"action": "create", "entity": "customer",
                              "fields": {"name": "สมชาย"}}, ensure_ascii=False)
                + "\n```\n"
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            result = await parse_intent(
                message="เพิ่มลูกค้าชื่อสมชาย",
                chann_uid="CHN-S-000001", role="sales",
                license_id="lic-1", permission_keys=["customer.create"],
                client=c,
            )
        assert result == {
            "action": "create", "entity": "customer",
            "fields": {"name": "สมชาย"}, "missing": [],
        }
        assert "think" not in json.dumps(result).lower()


class TestIntentJsonParsing:
    def test_bare_json(self):
        assert parse_intent_json('{"action":"read"}')["action"] == "read"

    def test_fenced_json(self):
        assert parse_intent_json('```json\n{"action":"read"}\n```')["action"] == "read"

    def test_json_with_leading_prose(self):
        assert parse_intent_json('Sure!\n{"action":"read"}')["action"] == "read"

    def test_defaults_are_filled_in(self):
        out = parse_intent_json('{"action":"read"}')
        assert out["fields"] == {} and out["missing"] == [] and out["entity"] is None

    def test_no_json_raises(self):
        with pytest.raises(AIUnavailable):
            parse_intent_json("I could not do that.")

    def test_json_without_action_raises(self):
        with pytest.raises(AIUnavailable):
            parse_intent_json('{"entity":"customer"}')

    def test_non_object_json_raises(self):
        with pytest.raises(AIUnavailable):
            parse_intent_json('["read"]')


class TestProviderPreference:
    """PROVIDER_PREFERENCE is intentionally empty — see providers.py.

    The original slugs were copied from the spec and never verified; a live
    call was served by DeepInfra, meaning neither matched. These tests now
    pin the honest behaviour: no preference is expressed unless someone adds
    a slug they have actually observed serving the model.
    """

    def test_no_preference_is_expressed_by_default(self):
        assert PROVIDER_PREFERENCE == {}
        assert provider_block("qwen/qwen3.6-35b-a3b") is None
        assert provider_block("deepseek/deepseek-v4-pro") is None

    def test_unknown_family_is_left_to_openrouter(self):
        assert provider_block("someone/else-9b") is None
        assert provider_block("no-slash-model") is None

    def test_a_configured_family_still_produces_a_valid_block(self, monkeypatch):
        """The mechanism must still work once real slugs are filled in."""
        monkeypatch.setitem(PROVIDER_PREFERENCE, "qwen", ["deepinfra"])
        block = provider_block("qwen/qwen3.6-35b-a3b")
        assert block["order"] == ["deepinfra"]
        assert block["allow_fallbacks"] is True


class TestMetricsHook:
    """4.8: monitoring hook วัด latency + error rate"""

    async def test_success_and_failure_are_both_recorded(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 2:
                return _openrouter_reply(json.dumps({"action": "read"}))
            raise httpx.ConnectError("down", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await complete(system_prompt="s", user_message="u", client=c)
            await complete(system_prompt="s", user_message="u", client=c)
            with pytest.raises(AIUnavailable):
                await complete(system_prompt="s", user_message="u", client=c)

        snap = metrics.snapshot("qwen/qwen3.6-35b-a3b")
        assert snap["samples"] == 4          # 2 ok + 2 failed attempts
        assert snap["error_rate"] == 0.5
        assert snap["p95_latency_s"] is not None
        assert snap["prompt_tokens"] == 240

    def test_empty_window_reports_no_samples(self):
        metrics.reset()
        snap = metrics.snapshot()
        assert snap["samples"] == 0
        assert snap["error_rate"] is None
        assert snap["p95_latency_s"] is None


class TestPromptConstruction:
    def test_permission_keys_are_sorted_and_present(self):
        p = build_prompt(
            chann_uid="CHN-S-1", role="sales", license_id="lic-1",
            permission_keys={"customer.read", "deal.create"},
        )
        assert "customer.read, deal.create" in p
        assert "CHN-S-1" in p and "lic-1" in p

    def test_no_permissions_says_none_rather_than_blank(self):
        p = build_prompt(
            chann_uid="CHN-S-1", role="sales", license_id="lic-1", permission_keys=[]
        )
        assert "(none)" in p

    def test_language_defaults_to_thai(self):
        p = build_prompt(
            chann_uid="CHN-S-1", role="sales", license_id="lic-1", permission_keys=[]
        )
        assert "language: th" in p


class TestPhase5BotLanguage:
    """Phase 5 — Master Spec 5.4/5.5 test_i18n_bot_language.

    The UI half of Phase 5 (dictionary completeness, the switcher, localStorage)
    is enforced by `npm run typecheck` in the presentation tier, which the
    source-verification script already runs — see lib/i18n/th.ts.
    """

    async def test_language_en_is_instructed_in_the_prompt(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply(json.dumps({"action": "read", "entity": "customer"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await parse_intent(
                message="show me customers",
                chann_uid="CHN-S-1", role="sales", license_id="lic-1",
                permission_keys=["customer.read"], language="en", client=c,
            )
        system = captured["body"]["messages"][0]["content"]
        assert "language: en" in system
        assert "in English" in system
        assert "in Thai" not in system

    async def test_language_th_is_instructed_in_the_prompt(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _openrouter_reply(json.dumps({"action": "read", "entity": "customer"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await parse_intent(
                message="ดูลูกค้า",
                chann_uid="CHN-S-1", role="sales", license_id="lic-1",
                permission_keys=["customer.read"], language="th", client=c,
            )
        system = captured["body"]["messages"][0]["content"]
        assert "language: th" in system
        assert "in Thai" in system

    def test_machine_facing_values_stay_english_by_instruction(self):
        p = build_prompt(
            chann_uid="CHN-S-1", role="sales", license_id="lic-1",
            permission_keys=["customer.read"], language="th",
        )
        # action/entity/field keys must not be translated or downstream
        # matching breaks — the prompt has to say so explicitly
        assert "stay in English" in p

    def test_unknown_locale_falls_back_to_thai(self):
        p = build_prompt(
            chann_uid="CHN-S-1", role="sales", license_id="lic-1",
            permission_keys=[], language="fr",
        )
        assert "in Thai" in p

    def test_unavailable_reply_is_localised(self):
        assert "ขออภัย" in unavailable_reply("th")
        assert "Sorry" in unavailable_reply("en")

    def test_unavailable_reply_defaults_to_thai(self):
        assert unavailable_reply() == unavailable_reply("th")
        assert unavailable_reply("de") == unavailable_reply("th")
        assert unavailable_reply("") == unavailable_reply("th")

    def test_legacy_constant_still_points_at_thai(self):
        assert UNAVAILABLE_REPLY == unavailable_reply("th")
