"""Phase 6 round 2 — Master Spec 6.9 mandatory tests.

The chat engine is exercised against a fake DataClient and a mocked OpenRouter
transport, so these are deterministic and never spend money. The live proof
that OpenRouter actually answers Thai in under 3s is runtime acceptance
(6.10), not a unit test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402
from chann_app.services.chat import (  # noqa: E402
    ACTION_PERMISSIONS,
    SUGGEST_LIMIT,
    ChatReply,
    ask_for_missing,
    greet,
    handle_chat_message,
    handle_reply,
    required_permission,
    suggest_what_you_can_do,
)
from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402
from chann_data.permissions import (  # noqa: E402
    PERMISSION_DESCRIPTIONS,
    PERMISSION_KEYS,
    describe,
)

LICENSE_ID = "11111111-1111-1111-1111-111111111111"


def _catalog() -> list[dict]:
    return [
        {
            "key": key,
            "group": key.split(".", 1)[0] if "." in key else "general",
            "label": PERMISSION_DESCRIPTIONS.get(key, {}),
        }
        for key in sorted(PERMISSION_KEYS)
    ]


class FakeDataClient:
    """Stands in for the Data tier. Records what the engine asked it for."""

    def __init__(self, *, role="sales", permission_keys=None, mapping=None):
        self._role = role
        self._permission_keys = list(
            permission_keys if permission_keys is not None else ["customer.read"]
        )
        self._mapping = mapping
        self.recorded: list[tuple] = []

    async def authorization_context(self, license_id, chann_uid):
        return {
            "role": self._role,
            "is_owner": False,
            "permission_keys": self._permission_keys,
        }

    async def permission_catalog(self):
        return _catalog()

    async def get_message_entity(self, license_id, message_id):
        return self._mapping

    async def record_message_entity(self, license_id, message_id, entity_type, entity_id):
        self.recorded.append((license_id, message_id, entity_type, entity_id))
        return {"id": "rec"}


def _ctx(resolution=TenantResolution.SINGLE, display_name="LINE Name"):
    memberships = []
    if resolution is TenantResolution.SINGLE:
        # Mirrors MembershipOut exactly — no display_name, because the real
        # payload has none. A fake with extra keys hides dead code.
        memberships = [{
            "license_id": LICENSE_ID, "license_code": "TESTCO",
            "company_name": "บริษัททดสอบ", "chann_uid": "CHN-S-000001",
            "role": "sales", "status": "active",
        }]
    elif resolution is TenantResolution.MULTIPLE:
        memberships = [
            {"license_id": LICENSE_ID, "license_code": "COA",
             "company_name": "บริษัท ก", "chann_uid": "CHN-S-000001",
             "role": "sales", "status": "active"},
            {"license_id": "22222222-2222-2222-2222-222222222222",
             "license_code": "COB", "company_name": "บริษัท ข",
             "chann_uid": "CHN-S-000001", "role": "sales", "status": "active"},
        ]
    return ResolvedContext(
        chann_uid="CHN-S-000001", primary_role="sales",
        display_name=display_name, resolution=resolution, memberships=memberships,
    )


def _ai(content: str):
    """A MockTransport that answers every OpenRouter call with `content`."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "provider": "fireworks",
        })
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestSlotFilling:
    """6.9 test_slot_filling"""

    async def test_missing_name_asks_for_it(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "create", "entity": "customer",
                 "fields": {}, "missing": ["ชื่อลูกค้า"]}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.create"]),
            message="เพิ่มลูกค้า", ctx=_ctx(), ai_client=ai,
        )
        assert "กรุณาระบุ" in reply.text
        assert "ชื่อลูกค้า" in reply.text

    async def test_complete_message_proceeds_past_slot_filling(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "create", "entity": "customer",
                 "fields": {"name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.create"]),
            message="เพิ่มลูกค้าชื่อสมชาย", ctx=_ctx(), ai_client=ai,
        )
        assert "กรุณาระบุ" not in reply.text
        assert reply.intent["fields"]["name"] == "สมชาย"
        assert reply.entity_type == "customer"

    async def test_missing_is_asked_before_permission_is_considered(self):
        """An unparsed request must never be refused for permissions.

        Telling someone "you can't do that" about a request we did not even
        understand is both wrong and confusing.
        """
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "create", "entity": "ticket",
                 "fields": {}, "missing": ["รายละเอียด"]}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),   # no ticket.create
            message="เปิดใบงาน", ctx=_ctx(), ai_client=ai,
        )
        assert "กรุณาระบุ" in reply.text

    def test_ask_for_missing_is_localised(self):
        assert "กรุณาระบุ" in ask_for_missing(["ชื่อ"], "th")
        assert "Please provide" in ask_for_missing(["name"], "en")


class TestReplyToEntity:
    """6.9 test_reply_to_entity"""

    async def test_reply_acts_on_the_mapped_entity(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "update", "entity": "customer",
                 "fields": {"name": "สมหญิง"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            mapping={"entity_type": "customer", "entity_id": "abc-123"},
        )
        reply = await handle_reply(
            client, message_id="msg-1", reply_text="แก้ชื่อเป็นสมหญิง",
            ctx=_ctx(), ai_client=ai,
        )
        # entity comes from what was replied to, not from the reply text
        assert reply.entity_type == "customer"
        assert reply.entity_id == "abc-123"

    async def test_reply_to_unmapped_message_says_so(self):
        client = FakeDataClient(mapping=None)
        reply = await handle_reply(
            client, message_id="unknown", reply_text="แก้ชื่อ", ctx=_ctx()
        )
        assert "ไม่พบข้อความต้นฉบับ" in reply.text

    async def test_reply_in_english_is_localised(self):
        client = FakeDataClient(mapping=None)
        reply = await handle_reply(
            client, message_id="unknown", reply_text="rename",
            ctx=_ctx(), language="en",
        )
        assert "original message" in reply.text


class TestSuggestWhatYouCanDo:
    """6.9 test_suggest_what_you_can_do"""

    def test_sales_sees_only_sales_permissions(self):
        text = suggest_what_you_can_do(
            ["customer.read", "customer.create", "deal.create"], _catalog(), "th"
        )
        assert describe("customer.create") in text
        # never offer something they cannot do
        assert describe("ticket.assign") not in text
        assert describe("billing.manage") not in text

    def test_cs_sees_only_cs_permissions(self):
        text = suggest_what_you_can_do(
            ["ticket.read", "ticket.assign", "chat_session.reply"], _catalog(), "th"
        )
        assert describe("ticket.assign") in text
        assert describe("deal.create") not in text

    def test_no_permissions_says_so_rather_than_an_empty_list(self):
        text = suggest_what_you_can_do([], _catalog(), "th")
        assert "ยังไม่มีสิทธิ์" in text

    def test_platform_admin_keys_are_never_suggested(self):
        text = suggest_what_you_can_do(
            ["customer.read", "platform.admin.break_glass"], _catalog(), "th"
        )
        assert describe("platform.admin.break_glass") not in text

    def test_long_permission_sets_are_truncated(self):
        text = suggest_what_you_can_do(sorted(PERMISSION_KEYS), _catalog(), "th")
        assert text.count("•") == SUGGEST_LIMIT
        assert "และอีก" in text

    def test_suggest_is_localised(self):
        text = suggest_what_you_can_do(["customer.read"], _catalog(), "en")
        assert "View customers" in text

    async def test_no_permission_intent_routes_to_suggest(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": "suggest", "suggestions": []})))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),
            message="ลบลูกค้าทั้งหมด", ctx=_ctx(), ai_client=ai,
        )
        assert describe("customer.read") in reply.text


class TestGreeting:
    """6.9 test_greeting"""

    def test_before_registration_uses_line_display_name(self):
        text = greet(_ctx(TenantResolution.NONE, display_name="สมชาย LINE"))
        assert "สมชาย LINE" in text
        assert "ยังไม่พบบริษัท" in text

    def test_after_registration_uses_the_line_name_and_company(self):
        """There is no per-tenant display name to prefer.

        This previously asserted that a name on the membership row won over
        the LINE profile name. It passed only because the fake membership
        dict carried a display_name key that MembershipOut does not have —
        the real payload never contains one, so that branch could never run.
        """
        text = greet(_ctx(display_name="LINE Nickname"))
        assert "LINE Nickname" in text
        assert "บริษัททดสอบ" in text

    def test_falls_back_to_chann_uid_when_line_has_no_name(self):
        text = greet(_ctx(display_name=None))
        assert "CHN-S-000001" in text

    def test_multiple_tenants_asks_which(self):
        text = greet(_ctx(TenantResolution.MULTIPLE))
        assert "บริษัท ก" in text and "บริษัท ข" in text

    def test_greeting_is_localised(self):
        text = greet(_ctx(TenantResolution.NONE, display_name="Somchai"), "en")
        assert "Hello Somchai" in text


class TestAIFailureDegradesGracefully:
    async def test_all_providers_down_gives_plain_apology(self):
        def handler(request):
            raise httpx.ConnectError("down", request=request)
        ai = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reply = await handle_chat_message(
            FakeDataClient(), message="เพิ่มลูกค้าชื่อสมชาย", ctx=_ctx(),
            ai_client=ai,
        )
        assert "ขออภัย" in reply.text

    async def test_missing_api_key_does_not_leak_config_detail(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        reply = await handle_chat_message(
            FakeDataClient(), message="เพิ่มลูกค้า", ctx=_ctx()
        )
        assert "ขออภัย" in reply.text
        assert "OPENROUTER" not in reply.text

    async def test_unregistered_user_is_greeted_not_parsed(self):
        client = FakeDataClient()
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย", ctx=_ctx(TenantResolution.NONE)
        )
        assert "ยังไม่พบบริษัท" in reply.text


class TestPermissionCatalogue:
    def test_every_permission_key_has_a_description(self):
        assert set(PERMISSION_DESCRIPTIONS) == set(PERMISSION_KEYS)

    def test_every_description_has_both_languages(self):
        for key, entry in PERMISSION_DESCRIPTIONS.items():
            assert entry.get("th"), f"{key} missing Thai"
            assert entry.get("en"), f"{key} missing English"

    def test_describe_falls_back_to_the_key_itself(self):
        assert describe("not.a.real.key") == "not.a.real.key"

    def test_describe_falls_back_to_thai_for_unknown_language(self):
        assert describe("customer.read", "fr") == describe("customer.read", "th")

class TestPermissionGateIsEnforcedInCode:
    """Regression for the live failure on 24 Aug 2026.

    Asked "ขอดูรายงานทางการเงิน", the model returned
    {"action":"view","entity":"financial_report"} — an entity the system has
    never heard of — and the engine replied "coming soon" instead of listing
    what the user can actually do. The prompt was the only thing deciding
    permission, which it must never be.
    """

    async def test_unknown_entity_falls_back_to_suggest(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "view", "entity": "financial_report", "fields": {}, "missing": []})))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),
            message="ขอดูรายงานทางการเงิน", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" not in reply.text
        assert describe("customer.read") in reply.text

    async def test_known_entity_without_permission_suggests(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "ticket", "fields": {"x": 1}, "missing": []})))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),   # no ticket.create
            message="เปิดใบงานให้หน่อย", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" not in reply.text
        assert describe("customer.read") in reply.text

    async def test_known_entity_with_permission_proceeds(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.create"]),
            message="เพิ่มลูกค้าชื่อสมชาย", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" in reply.text
        assert reply.entity_type == "customer"

    async def test_action_aliases_are_normalised(self):
        """The model uses view/list/read interchangeably; all must resolve."""
        for action in ("view", "list", "read", "show", "get"):
            ai = httpx.AsyncClient(transport=_ai(json.dumps(
                {"action": action, "entity": "customer", "fields": {}, "missing": []})))
            reply = await handle_chat_message(
                FakeDataClient(permission_keys=["customer.read"]),
                message="ดูลูกค้า", ctx=_ctx(), ai_client=ai,
            )
            assert "เข้าใจแล้ว" in reply.text, f"action={action} was not normalised"

    def test_required_permission_mapping(self):
        assert required_permission("create", "customer") == "customer.create"
        assert required_permission("view", "customer") == "customer.read"     # alias
        assert required_permission("read", "financial_report") is None        # unknown
        assert required_permission("create", None) is None
        assert required_permission("explode", "customer") is None             # bad action

    def test_every_mapped_permission_key_actually_exists(self):
        """A typo here would silently deny an action forever."""
        unknown = {v for v in ACTION_PERMISSIONS.values() if v not in PERMISSION_KEYS}
        assert unknown == set(), f"unknown permission keys in mapping: {unknown}"


class TestPhase7EntitiesAreGated:
    """Phase 7 master data must go through the same code-level gate."""

    def test_product_and_team_map_to_real_permission_keys(self):
        assert required_permission("create", "product") == "product.manage"
        assert required_permission("add", "product") == "product.manage"     # alias
        assert required_permission("create", "team") == "team.manage"
        assert required_permission("create", "sales_group") == "team.manage"

    async def test_product_without_permission_suggests(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "product",
             "fields": {"name": "แอร์"}, "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),   # no product.manage
            message="เพิ่มสินค้าแอร์", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" not in reply.text

    async def test_product_with_permission_proceeds(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "product",
             "fields": {"name": "แอร์"}, "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["product.manage"]),
            message="เพิ่มสินค้าแอร์", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" in reply.text
