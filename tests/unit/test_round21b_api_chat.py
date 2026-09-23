"""Round 21B — API keys from chat: list, revoke with a confirmation, and
"create" answered with the page (a secret never sits in a LINE thread)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import ACTION_PERMISSIONS, handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

KEYS = ["setting.manage", "customer.read"]
LIST = {"action": "read", "entity": "api_key", "fields": {}, "missing": []}
REVOKE = {"action": "delete", "entity": "api_key", "fields": {"target_name": "ระบบบัญชี"}, "missing": []}
CREATE = {"action": "create", "entity": "api_key", "fields": {"name": "ระบบบัญชี"}, "missing": []}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    # A standalone run of this file collects no other module's autouse
    # fixture, so the model has to be set here too (test_phase6_chat.py's
    # _ai_configured does the same) — otherwise parse_intent raises
    # AINotConfigured before the fake AI transport is ever reached.
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "liff_sales_id", "2010948960-xDfbMrIP")


def _shop(owner=True, keys=KEYS):
    client = FakeDataClient(role="owner" if owner else "sales", permission_keys=list(keys))
    client._api_keys = [
        {"id": "k1", "license_id": LICENSE_ID, "name": "ระบบบัญชี", "key_prefix": "chann_live_ab12",
         "last_used_at": "2026-09-22T10:00:00+00:00", "revoked_at": None, "created_at": "2026-09-20T00:00:00+00:00"},
        {"id": "k2", "license_id": LICENSE_ID, "name": "ร้านค้าออนไลน์", "key_prefix": "chann_live_cd34",
         "last_used_at": None, "revoked_at": None, "created_at": "2026-09-21T00:00:00+00:00"},
    ]
    return client


def _shop_with_duplicate_names(owner=True, keys=KEYS):
    """Two live keys sharing a name — `ApiKey.name` has no uniqueness
    constraint, so this is a real shape the Data tier can return."""
    client = FakeDataClient(role="owner" if owner else "sales", permission_keys=list(keys))
    client._api_keys = [
        {"id": "k1", "license_id": LICENSE_ID, "name": "ระบบบัญชี", "key_prefix": "chann_live_ab12",
         "last_used_at": "2026-09-22T10:00:00+00:00", "revoked_at": None, "created_at": "2026-09-20T00:00:00+00:00"},
        {"id": "k2", "license_id": LICENSE_ID, "name": "ระบบบัญชี", "key_prefix": "chann_live_cd34",
         "last_used_at": None, "revoked_at": None, "created_at": "2026-09-21T00:00:00+00:00"},
    ]
    return client


async def _say(client, message, reading, *, owner=True):
    ctx = _ctx(primary_role="sales", oa="sales")
    ctx.memberships[0]["is_owner"] = owner
    async with httpx.AsyncClient(transport=_ai(json.dumps(reading, ensure_ascii=False))) as ai:
        return await handle_chat_message(client, message=message, ctx=ctx, ai_client=ai)


def _writes(client, name):
    return [r for r in client.recorded if r[0] == name]


class TestRegistry:
    def test_the_three_roads_are_behind_setting_manage(self):
        assert ACTION_PERMISSIONS[("read", "api_key")] == "setting.manage"
        assert ACTION_PERMISSIONS[("delete", "api_key")] == "setting.manage"
        assert ACTION_PERMISSIONS[("create", "api_key")] == "setting.manage"
        assert chat.ENTITY_DASHBOARD_PAGE["api_key"][0] == "api-keys"

    def test_the_prompt_teaches_it_to_sales_only(self):
        from chann_app.services.ai.intent import build_prompt
        sales = build_prompt(chann_uid="u", role="sales", license_id="l", permission_keys=KEYS, oa="sales")
        assert 'entity="api_key"' in sales
        tech = build_prompt(chann_uid="u", role="technician", license_id="l", permission_keys=["ticket.read"], oa="technician")
        assert 'entity="api_key"' not in tech


class TestList:
    async def test_the_owner_sees_names_prefixes_and_last_use_never_the_key(self):
        client = _shop()
        reply = await _say(client, "รายการ API key", LIST)
        assert "ระบบบัญชี" in reply.text and "chann_live_ab12" in reply.text and "ร้านค้าออนไลน์" in reply.text
        assert "ยังไม่เคยใช้" in reply.text
        assert "x" * 10 not in reply.text and len([l for l in reply.text.splitlines() if "chann_live_" in l]) == 2
        assert reply.quick_reply_url and "api-keys" in reply.quick_reply_url[1]

    async def test_no_keys_points_at_the_page(self):
        client = _shop()
        client._api_keys = []
        reply = await _say(client, "รายการ API key", LIST)
        assert "ยังไม่มี" in reply.text and reply.quick_reply_url

    async def test_an_admin_who_is_not_the_owner_is_told_so(self):
        client = _shop(owner=False)
        reply = await _say(client, "รายการ API key", LIST, owner=False)
        assert "เจ้าของร้าน" in reply.text and "chann_live_" not in reply.text

    async def test_without_setting_manage_it_is_the_permission_sentence(self):
        client = _shop(keys=["customer.read"])
        reply = await _say(client, "รายการ API key", LIST)
        assert "สิทธิ์" in reply.text and _writes(client, "list_api_keys") == []


class TestRevoke:
    async def test_revoke_asks_first_then_acts_on_the_button(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key ระบบบัญชี", REVOKE)
        assert "ระบบบัญชี" in reply.text and "ยืนยัน" in reply.text
        assert _writes(client, "revoke_api_key") == []
        sends = [send for _, send in reply.quick_replies]
        assert sends[0] == "ยืนยันเพิกถอน API key ระบบบัญชี"
        reply = await _say(client, sends[0], {**REVOKE, "fields": {"target_name": "ระบบบัญชี", "confirm": True}})
        assert "เพิกถอน" in reply.text and "แล้ว" in reply.text
        assert [w[2] for w in _writes(client, "revoke_api_key")] == ["k1"]

    async def test_cancelling_revokes_nothing(self):
        client = _shop()
        await _say(client, "เพิกถอน API key ระบบบัญชี", REVOKE)
        reply = await _say(client, "ยกเลิก", {"action": "cancel", "entity": "api_key", "fields": {}, "missing": []})
        assert _writes(client, "revoke_api_key") == []

    async def test_an_unknown_name_is_named_back_and_nothing_happens(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key ไม่มีอันนี้", {**REVOKE, "fields": {"target_name": "ไม่มีอันนี้"}})
        assert "ไม่พบ" in reply.text and "ไม่มีอันนี้" in reply.text
        assert _writes(client, "revoke_api_key") == []

    async def test_no_name_asks_which(self):
        client = _shop()
        reply = await _say(client, "เพิกถอน API key", {**REVOKE, "fields": {}})
        assert "key ไหน" in reply.text and "ระบบบัญชี" in reply.text
        assert _writes(client, "revoke_api_key") == []


class TestRevokeDuplicateNames:
    """Round 21B fix round 1 — two live keys can share a name; the name
    alone must never pick one silently."""

    async def test_a_shared_name_lists_both_and_revokes_neither(self):
        client = _shop_with_duplicate_names()
        reply = await _say(client, "เพิกถอน API key ระบบบัญชี", REVOKE)
        assert "ระบบบัญชี" in reply.text
        assert "chann_live_ab12" in reply.text and "chann_live_cd34" in reply.text
        sends = [send for _, send in reply.quick_replies]
        assert "ยืนยันเพิกถอน API key chann_live_cd34" in sends
        assert _writes(client, "revoke_api_key") == []

    async def test_tapping_the_prefix_button_revokes_exactly_that_key(self):
        client = _shop_with_duplicate_names()
        reply = await _say(
            client, "ยืนยันเพิกถอน API key chann_live_cd34",
            {"action": "delete", "entity": "api_key",
             "fields": {"target_name": "chann_live_cd34", "confirm": True}, "missing": []},
        )
        assert "เพิกถอน" in reply.text and "แล้ว" in reply.text
        assert [w[2] for w in _writes(client, "revoke_api_key")] == ["k2"]

    async def test_the_prefix_typed_as_the_name_asks_for_that_key_only(self):
        client = _shop_with_duplicate_names()
        reply = await _say(
            client, "เพิกถอน API key chann_live_ab12",
            {"action": "delete", "entity": "api_key",
             "fields": {"target_name": "chann_live_ab12"}, "missing": []},
        )
        assert "ยืนยัน" in reply.text
        # Only the ab12 key is asked about — not both duplicates.
        assert reply.text.count("chann_live_") == 1
        assert "chann_live_ab12" in reply.text
        assert _writes(client, "revoke_api_key") == []


class TestCreate:
    async def test_create_never_makes_a_key_in_chat_and_opens_the_page(self):
        client = _shop()
        reply = await _say(client, "สร้าง API key ระบบบัญชี", CREATE)
        assert "หน้าจอ" in reply.text or "แดชบอร์ด" in reply.text
        assert reply.quick_reply_url and "api-keys" in reply.quick_reply_url[1]
        assert _writes(client, "create_api_key") == []
