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
    _filter_by_oa,
    ask_for_missing,
    greet,
    handle_chat_message,
    handle_reply,
    required_permission,
    suggest_what_you_can_do,
)
from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402


class ProfileConflictForTest(Exception):
    status_code = 409
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

    def __init__(self, *, role="sales", permission_keys=None, mapping=None,
                 pending_intent=None):
        self._role = role
        self._pending = pending_intent
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

    async def update_profile(self, chann_uid, fields, actor_id=None):
        self.recorded.append(("update_profile", chann_uid, fields, actor_id))
        if getattr(self, "_profile_conflict", False):
            raise ProfileConflictForTest("invalid value")
        return {"chann_uid": chann_uid, **fields}

    async def get_pending_intent(self, chann_uid, oa):
        self.recorded.append(("get_pending_intent", chann_uid, oa))
        return self._pending

    async def set_pending_intent(self, chann_uid, oa, *, action, entity,
                                 fields, missing, ttl_seconds=600):
        self._pending = {"action": action, "entity": entity,
                         "fields": fields, "missing": missing}
        self.recorded.append(("set_pending_intent", chann_uid, oa, self._pending))

    async def clear_pending_intent(self, chann_uid, oa):
        self._pending = None
        self.recorded.append(("clear_pending_intent", chann_uid, oa))


def _ctx(resolution=TenantResolution.SINGLE, display_name="LINE Name",
         primary_role="sales", oa=None):
    # oa defaults to primary_role: in the ordinary case a message really does
    # arrive on the OA matching the identity's role. Tests that specifically
    # prove ctx.oa is used INSTEAD of a possibly-stale ctx.primary_role pass
    # the two explicitly and differently.
    if oa is None:
        oa = primary_role
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
        chann_uid="CHN-S-000001", primary_role=primary_role,
        display_name=display_name, resolution=resolution, memberships=memberships,
        oa=oa,
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

    def test_long_permission_sets_are_grouped_and_capped(self):
        """Regression for the live failure on 25 Aug 2026: a flat 49-item
        alphabetical list ("อนุมัติ", "ไม่อนุมัติ", ... billing) with no
        relation to what was asked. Groups now cap the number of categories
        shown up front rather than truncating one long flat list."""
        text = suggest_what_you_can_do(sorted(PERMISSION_KEYS), _catalog(), "th")
        assert "และอีก" in text and "หมวดหมู่" in text
        # no more than (no priority group + the "other groups" allowance)
        # category headers appear
        assert text.count(":\n") <= 1 + 2  # SUGGEST_OTHER_GROUPS = 2

    def test_suggest_is_localised(self):
        text = suggest_what_you_can_do(["customer.read"], _catalog(), "en")
        assert "View customers" in text
        assert "Customers" in text  # group header, also localised

    def test_unknown_entity_gets_a_short_honest_reply_not_random_groups(self):
        """The exact live failure: asked about a report, the model returned
        an entity ("financial_report") the system has never heard of, and
        the reply dumped unrelated categories (approvals, assignment rules)
        with no connection to reports at all."""
        text = suggest_what_you_can_do(
            sorted(PERMISSION_KEYS), _catalog(), "th",
            requested_action="read", requested_entity="financial_report",
        )
        assert "ระบบยังไม่มีฟังก์ชันนี้" in text
        assert "ทำอะไรได้บ้าง" in text
        # must NOT dump unrelated categories
        assert "อนุมัติ" not in text
        assert "กฎการมอบหมายงาน" not in text

    def test_known_feature_denied_leads_with_no_permission_message(self):
        text = suggest_what_you_can_do(
            ["customer.read"], _catalog(), "th",
            requested_action="create", requested_entity="product",
        )
        assert "คุณยังไม่มีสิทธิ์ทำสิ่งนี้" in text
        assert "ระบบยังไม่มีฟังก์ชันนี้" not in text

    def test_requested_group_is_shown_first(self):
        """A member who can do lots of things, asked about tickets, should
        see tickets first — not wherever "ticket" happens to sort."""
        text = suggest_what_you_can_do(
            ["customer.read", "deal.create", "ticket.read", "ticket.assign"],
            _catalog(), "th",
            requested_action="read", requested_entity="ticket",
        )
        # group headers are the short, un-indented lines ending in ":" —
        # the lead sentence also ends in ":" but is a full sentence, not one
        headers = [
            l for l in text.splitlines()
            if l.endswith(":") and not l.startswith(" ") and len(l) < 20
        ]
        assert headers, "no group headers found"
        assert headers[0] == "ใบงาน:"
        assert text.index("ใบงาน:") < text.index("ลูกค้า:")

    def test_plain_query_with_no_entity_is_unaffected(self):
        """A bare "what can I do" must never trip the unknown-feature path —
        that path requires an actual requested_entity."""
        text = suggest_what_you_can_do(
            ["customer.read", "deal.create"], _catalog(), "th"
        )
        assert "ระบบยังไม่มีฟังก์ชันนี้" not in text
        assert describe("customer.read") in text

    def test_unknown_entity_reply_is_localised(self):
        text = suggest_what_you_can_do(
            sorted(PERMISSION_KEYS), _catalog(), "en",
            requested_action="read", requested_entity="financial_report",
        )
        assert "not a feature yet" in text

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
        """Regression for the live failure: the model invented an entity
        ("financial_report") the system has never heard of. The gate must
        not treat it as understood — and the reply must be the short,
        honest "not a feature yet" message, not a dump of unrelated groups
        (that dump was itself what made the earlier bug hard to notice)."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "view", "entity": "financial_report", "fields": {}, "missing": []})))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.read"]),
            message="ขอดูรายงานทางการเงิน", ctx=_ctx(), ai_client=ai,
        )
        assert "เข้าใจแล้ว" not in reply.text
        assert "ระบบยังไม่มีฟังก์ชันนี้" in reply.text
        assert describe("customer.read") not in reply.text

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


class TestPhase8ProfileChat:
    """Phase 8 — Master Spec 8.5 chat-side tests.

    The Data-tier authorization/validation logic (may_edit_on_behalf,
    phone/email format) is covered by the Postgres integration suite; these
    cover the chat dispatch — that a profile intent bypasses the generic
    gate, self-edit always succeeds regardless of permission_keys, and
    invalid values surface as a friendly reply rather than an exception.
    """

    async def test_self_edit_bypasses_the_generic_gate(self):
        """A member with ZERO permission keys must still be able to edit
        their own phone number — self-edit is not a permission-gated action."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])   # holds nothing at all
        reply = await handle_chat_message(
            client, message="แก้เบอร์เป็น 0812345678", ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert "แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว" in reply.text
        assert ("update_profile", "CHN-S-000001", {"phone": "0812345678"}, "CHN-S-000001") in client.recorded

    async def test_invalid_value_gets_a_friendly_reply_not_a_crash(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"email": "not-an-email"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        client._profile_conflict = True
        reply = await handle_chat_message(
            client, message="เปลี่ยนอีเมล", ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert "ไม่ถูกต้อง" in reply.text

    async def test_no_fields_asks_what_to_change(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile", "fields": {}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        reply = await handle_chat_message(
            client, message="แก้โปรไฟล์", ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert "กรุณาระบุ" in reply.text
        assert not any(r[0] == "update_profile" for r in client.recorded)

    async def test_unknown_field_from_model_is_dropped_not_forwarded(self):
        """If the model puts something outside PROFILE_EDITABLE_FIELDS in
        fields (e.g. it hallucinates a "role" key), it must never reach the
        Data tier update call — only the real editable fields are sent."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678", "role": "owner"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        await handle_chat_message(
            client, message="แก้เบอร์และสิทธิ์", ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        sent_fields = next(r[2] for r in client.recorded if r[0] == "update_profile")
        assert sent_fields == {"phone": "0812345678"}

    async def test_profile_reply_is_localised(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        reply = await handle_chat_message(
            client, message="update my phone", ctx=_ctx(primary_role="technician"), ai_client=ai, language="en",
        )
        assert "updated" in reply.text.lower()


class TestProfileEligibilityFollowsTheChannel:
    """Master Spec 8.1 lists self-profile editing under the Customer and
    Technician OA tables only. The check must follow the CURRENT message's
    OA, not the identity's stored primary_role — primary_role is fixed at
    first contact and goes stale the moment the same LINE account later
    messages a different OA under the same provider.
    """

    async def test_sales_oa_cannot_self_edit_through_chat(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        reply = await handle_chat_message(
            client, message="แก้เบอร์เป็น 0812345678",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "ช่างและลูกค้า" in reply.text
        assert not any(r[0] == "update_profile" for r in client.recorded)

    async def test_stale_primary_role_does_not_deny_incorrectly(self):
        """Stored primary_role is "sales" from first contact, but this
        message genuinely arrived on the Customer OA — ctx.oa is the ground
        truth, so the edit must go through."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        reply = await handle_chat_message(
            client, message="แก้เบอร์เป็น 0812345678",
            ctx=_ctx(primary_role="sales", oa="customer"), ai_client=ai,
        )
        assert "แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว" in reply.text

    async def test_stale_primary_role_does_not_bypass_the_restriction_either(self):
        """The reverse: stored primary_role is "customer", but the message
        arrived on Sales OA — the restriction follows the channel, not the
        label."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "profile",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(permission_keys=[])
        reply = await handle_chat_message(
            client, message="แก้เบอร์เป็น 0812345678",
            ctx=_ctx(primary_role="customer", oa="sales"), ai_client=ai,
        )
        assert "แก้ไขข้อมูลส่วนตัวเรียบร้อยแล้ว" not in reply.text
        assert "ช่างและลูกค้า" in reply.text


class TestConversationContinuity:
    """Spec 6.4 describes parsing one message in isolation, which quietly
    assumes every message is self-contained. The bot itself produces messages
    that are not: it asks "what is the phone number?", and the honest human
    answer is a bare "0812345678" — no verb, no entity, unparseable alone.
    """

    async def test_an_unanswered_question_is_remembered(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"name": "สมชาย"}, "missing": ["phone"]}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "กรุณาระบุ" in reply.text
        stored = next(r for r in client.recorded if r[0] == "set_pending_intent")
        _, chann_uid, oa, saved = stored
        assert chann_uid == "CHN-S-000001"
        assert oa == "sales"
        assert saved["action"] == "create"
        assert saved["entity"] == "customer"
        assert saved["missing"] == ["phone"]

    async def test_a_bare_answer_completes_the_previous_action(self):
        """The whole point: the model returns only the field, with no entity
        and no action of its own, and it must still land in the action that
        was already under way rather than being parsed from nothing."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": None,
             "fields": {"phone": "0812345678"}, "missing": []})))
        client = FakeDataClient(
            permission_keys=["customer.create"],
            pending_intent={"action": "create", "entity": "customer",
                            "fields": {"name": "สมชาย"}, "missing": ["phone"]},
        )
        reply = await handle_chat_message(
            client, message="0812345678", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.intent["entity"] == "customer"
        assert reply.intent["fields"] == {"name": "สมชาย", "phone": "0812345678"}
        assert reply.intent["missing"] == []
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_a_clearly_new_request_abandons_the_old_one(self):
        """Conservative on purpose: filing a genuinely new request into an
        unrelated half-built record is much worse than re-asking."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"title": "ดีลใหม่"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["deal.create"],
            pending_intent={"action": "create", "entity": "customer",
                            "fields": {"name": "สมชาย"}, "missing": ["phone"]},
        )
        reply = await handle_chat_message(
            client, message="สร้างดีลใหม่", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.intent["entity"] == "deal"
        assert "สมชาย" not in json.dumps(reply.intent, ensure_ascii=False)
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_pending_state_is_read_and_written_per_oa_not_globally(self):
        """LINE issues the SAME user ID to one physical account across every
        OA under one provider, so an in-progress Sales-OA conversation must
        never be visible to a message on a different OA for the same
        chann_uid."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"name": "สมชาย"}, "missing": ["phone"]}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        read_oa = next(r[2] for r in client.recorded if r[0] == "get_pending_intent")
        write_oa = next(r[2] for r in client.recorded if r[0] == "set_pending_intent")
        assert read_oa == "sales" and write_oa == "sales"


class TestOAChannelScoping:
    """Regression for a live bug: an account holding tenant-wide permissions
    (an Owner) texting through the Technician or Customer OA saw a "what can
    I do" list including "อนุมัติ" and "จัดการการเรียกเก็บเงิน" —
    capabilities Master Spec §6's OA activity tables place exclusively under
    Sales OA. Holding a tenant permission and a channel actually offering it
    through chat are two different questions; the gate only ever asked the
    first one.
    """

    async def test_technician_oa_never_offers_sales_only_capabilities(self):
        filtered = _filter_by_oa(list(PERMISSION_KEYS), "technician")
        for key in ("approval.approve", "billing.manage", "role.manage",
                    "member.manage", "deal.create", "quote.create"):
            assert key not in filtered
        assert "ticket.read" in filtered
        assert "service_report.create" in filtered

    async def test_customer_oa_never_offers_sales_only_capabilities(self):
        filtered = _filter_by_oa(list(PERMISSION_KEYS), "customer")
        for key in ("approval.approve", "billing.manage", "deal.create",
                    "role.manage", "audit_log.view"):
            assert key not in filtered
        assert "customer.read" in filtered
        assert "warranty.read" in filtered

    async def test_sales_oa_is_not_additionally_restricted(self):
        """Sales OA's own spec table covers nearly everything a tenant does,
        so there is nothing left to narrow beyond the tenant permission gate
        itself."""
        held = ["approval.approve", "billing.manage", "deal.create"]
        assert _filter_by_oa(held, "sales") == held

    async def test_owner_via_technician_channel_gets_the_narrow_list(self):
        """End-to-end, the exact reported scenario: an account holding every
        permission key asks "what can I do" on the Technician OA."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "suggestions": []})))
        client = FakeDataClient(permission_keys=list(PERMISSION_KEYS))
        reply = await handle_chat_message(
            client, message="ทำอะไรได้บ้าง",
            ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert describe("approval.approve") not in reply.text
        assert describe("billing.manage") not in reply.text
        assert describe("role.manage") not in reply.text

    async def test_a_sales_only_action_via_technician_oa_is_refused(self):
        """Holding the tenant permission is not enough if the channel does
        not offer that capability at all — an Owner cannot create a deal by
        texting the Technician OA, even holding deal.create tenant-wide."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"title": "x"}, "missing": []})))
        client = FakeDataClient(permission_keys=list(PERMISSION_KEYS))
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert "เข้าใจแล้ว" not in reply.text

    async def test_the_same_action_on_sales_oa_still_works(self):
        """The counterpart, so the fix cannot pass by refusing everything."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"title": "x"}, "missing": []})))
        client = FakeDataClient(permission_keys=list(PERMISSION_KEYS))
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "เข้าใจแล้ว" in reply.text
