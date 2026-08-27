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
    maybe_handle_storefront,
    required_permission,
    suggest_what_you_can_do,
)
from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402


class ProfileConflictForTest(Exception):
    status_code = 409


class ConflictForTest(Exception):
    status_code = 409


class NotFoundForTest(Exception):
    status_code = 404

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
                 pending_intent=None, customers=None, deals=None,
                 storefront_results=None, last_customer_ref=None, raises=None,
                 quotes=None):
        self._role = role
        self._raises = raises
        self._last_customer_ref = last_customer_ref
        self._customers = list(customers) if customers is not None else []
        self._deals = list(deals) if deals is not None else []
        self._quotes = list(quotes) if quotes is not None else []
        self._next_deal_n = 1
        self._storefront_results = storefront_results or []
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

    async def set_last_customer_ref(self, chann_uid, oa, *, customer_id, name, ttl_seconds=600):
        self._last_customer_ref = {"customer_id": customer_id, "name": name}
        self.recorded.append(("set_last_customer_ref", chann_uid, oa, customer_id, name))

    async def get_last_customer_ref(self, chann_uid, oa):
        self.recorded.append(("get_last_customer_ref", chann_uid, oa))
        return self._last_customer_ref

    async def upsert_product(self, license_id, product_id, payload, actor_id=None):
        self.recorded.append(("upsert_product", license_id, product_id, payload, actor_id))
        if self._raises:
            raise self._raises
        return {
            "id": f"PROD-{product_id}", "license_id": license_id,
            "product_id": product_id, "product_name": payload["product_name"],
            "sku": payload.get("sku"), "category": payload.get("category"),
            "unit_price": payload.get("unit_price"), "description": payload.get("description"),
        }

    async def create_invite(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_invite", license_id, payload, actor_id))
        return {"invite_code": "ABC234XY7Z", "role": payload["role"],
                "license_id": license_id}

    # ------------------------------------------------------------ Phase 9 CRM

    async def create_customer(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_customer", license_id, payload, actor_id))
        row = {
            "id": f"CUST-{len(self._customers) + 1}", "license_id": license_id,
            "customer_chann_uid": None, "stage": "lead", "owner_member_id": None,
            "first_name": payload.get("first_name"), "last_name": payload.get("last_name"),
            "phone": payload.get("phone"), "email": payload.get("email"),
            "address": payload.get("address"), "notes": payload.get("notes"),
        }
        self._customers.append(row)
        return row

    async def list_customers(self, license_id, stage=None):
        self.recorded.append(("list_customers", license_id, stage))
        if stage:
            return [c for c in self._customers if c["stage"] == stage]
        return list(self._customers)

    async def update_customer(self, license_id, customer_id, fields, actor_id=None):
        self.recorded.append(("update_customer", license_id, customer_id, fields, actor_id))
        if self._raises:
            raise self._raises
        row = next(c for c in self._customers if c["id"] == customer_id)
        row.update(fields)
        return row

    async def promote_customer(self, license_id, customer_id, actor_id=None):
        self.recorded.append(("promote_customer", license_id, customer_id, actor_id))
        if self._raises:
            raise self._raises
        row = next(c for c in self._customers if c["id"] == customer_id)
        row["stage"] = "contact"
        return row

    async def create_deal(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_deal", license_id, payload, actor_id))
        if self._raises:
            raise self._raises
        deal_id = f"D-2026-{self._next_deal_n:04d}"
        self._next_deal_n += 1
        row = {
            "id": f"DEAL-{len(self._deals) + 1}", "license_id": license_id,
            "deal_id": deal_id, "contact_id": payload["contact_id"],
            "stage": "new", "owner_member_id": None, "notes": payload.get("notes"),
            "products": [],
        }
        self._deals.append(row)
        return row

    async def list_deals(self, license_id, stage=None):
        self.recorded.append(("list_deals", license_id, stage))
        if stage:
            return [d for d in self._deals if d["stage"] == stage]
        return list(self._deals)

    async def transition_deal_stage(self, license_id, deal_id, stage, *,
                                     allow_reopen=False, actor_id=None):
        self.recorded.append(
            ("transition_deal_stage", license_id, deal_id, stage, allow_reopen, actor_id)
        )
        row = next(d for d in self._deals if d["id"] == deal_id)
        row["stage"] = stage
        return row

    # ------------------------------------------------------------ Phase 10

    async def create_quote(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_quote", license_id, payload, actor_id))
        if self._raises:
            raise self._raises
        quote_id = f"Q-2026-{len(self._quotes) + 1:04d}"
        row = {
            "id": f"QUOTE-{len(self._quotes) + 1}", "license_id": license_id,
            "quote_id": quote_id, "deal_id": payload["deal_id"], "status": "draft",
            "generated_document_id": None, "owner_member_id": None,
        }
        self._quotes.append(row)
        return row

    async def storefront_search(self, q, limit=10):
        self.recorded.append(("storefront_search", q, limit))
        return list(self._storefront_results)

    async def storefront_record_interest(self, *, chann_uid, license_id, product_name):
        self.recorded.append(
            ("storefront_record_interest", chann_uid, license_id, product_name)
        )
        return {"id": "CUST-STOREFRONT-1", "license_id": license_id, "stage": "lead"}


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
                 "fields": {"first_name": "สมชาย", "last_name": "ใจดี",
                            "phone": "0812345678"},
                 "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.create"]),
            message="เพิ่มลูกค้าชื่อสมชาย ใจดี เบอร์ 0812345678", ctx=_ctx(), ai_client=ai,
        )
        assert "กรุณาระบุ" not in reply.text
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
             "fields": {"first_name": "สมชาย", "last_name": "ใจดี",
                        "phone": "0812345678"},
             "missing": []}, ensure_ascii=False)))
        reply = await handle_chat_message(
            FakeDataClient(permission_keys=["customer.create"]),
            message="เพิ่มลูกค้าชื่อสมชาย ใจดี เบอร์ 0812345678", ctx=_ctx(), ai_client=ai,
        )
        assert "สมชาย" in reply.text
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
             "fields": {"product_id": "AC-001", "product_name": "แอร์"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["product.manage"])
        reply = await handle_chat_message(
            client, message="เพิ่มสินค้าแอร์", ctx=_ctx(), ai_client=ai,
        )
        assert "แอร์" in reply.text
        assert any(r[0] == "upsert_product" for r in client.recorded)


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
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=list(PERMISSION_KEYS),
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
        )
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "D-2026-" in reply.text
        assert any(r[0] == "create_deal" for r in client.recorded)


class TestTechnicianInviteRequest:
    """Sales OA only: mints the one-time code a technician redeems on the
    Technician OA to actually become one at this company. Reported gap: a
    Sales-registered account could message Technician OA and immediately
    see technician-scoped capabilities with no invite/registration step at
    all — see TestOAIdentityIsolation for the resolve_context-level fix;
    these test the chat-side command that produces the code in the first
    place.
    """

    async def test_holder_of_member_manage_gets_a_code(self):
        client = FakeDataClient(permission_keys=["member.manage"])
        reply = await handle_chat_message(
            client, message="ขอรหัสเชิญช่าง",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "ABC234XY7Z" in reply.text
        call = next(r for r in client.recorded if r[0] == "create_invite")
        _, license_id, payload, actor_id = call
        assert payload["role"] == "technician"
        assert payload["max_uses"] == 1
        assert actor_id == "CHN-S-000001"

    async def test_without_member_manage_is_refused(self):
        client = FakeDataClient(permission_keys=["customer.read"])
        reply = await handle_chat_message(
            client, message="ขอรหัสเชิญช่าง",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "ต้องมีสิทธิ์จัดการสมาชิก" in reply.text
        assert not any(r[0] == "create_invite" for r in client.recorded)

    async def test_never_triggers_outside_sales_oa(self):
        """The trigger check is gated on ctx.oa == "sales" specifically —
        even an account holding member.manage tenant-wide must not be able
        to mint a technician invite by texting the Technician OA itself."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "suggestions": []})))
        client = FakeDataClient(permission_keys=["member.manage"])
        reply = await handle_chat_message(
            client, message="ขอรหัสเชิญช่าง",
            ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert not any(r[0] == "create_invite" for r in client.recorded)
        assert "ABC234XY7Z" not in reply.text


class TestPhase9CustomerChat:
    """Master Spec 9.5/9.7 — chat-side dispatch. Data-tier correctness
    (uniqueness, cross-tenant isolation) is covered by the Postgres
    integration suite; these cover intent -> real Data-tier call wiring."""

    async def test_create_customer_with_a_name_succeeds(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมชาย", "last_name": "ใจดี",
                        "phone": "0812345678"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย ใจดี เบอร์ 0812345678",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "สมชาย" in reply.text
        assert reply.entity_type == "customer"
        assert any(r[0] == "create_customer" for r in client.recorded)

    async def test_create_customer_with_nothing_at_all_is_refused(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer", "fields": {}, "missing": []})))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้า", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "กรุณาระบุ" in reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)

    async def test_first_name_only_is_not_enough_to_create(self):
        """Owner's explicit rule: last_name AND phone are both mandatory —
        a first name alone (even with email) must not be enough, since a
        shared first name can't reliably identify anyone later and staff
        need a phone to actually follow up."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมชาย", "email": "somchai@example.com"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย อีเมล somchai@example.com",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "นามสกุล" in reply.text and "เบอร์โทร" in reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)
        # sets pending_intent too, so a bare follow-up answer can complete
        # this request instead of being parsed as a new, meaningless message
        assert any(r[0] == "set_pending_intent" for r in client.recorded)

    async def test_last_name_without_phone_is_not_enough(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมชาย", "last_name": "ใจดี"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย ใจดี",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "เบอร์โทร" in reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)

    async def test_phone_without_last_name_is_not_enough(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมชาย", "phone": "0812345678"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย เบอร์ 0812345678",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "นามสกุล" in reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)

    async def test_last_name_and_phone_together_is_enough_even_without_first_name(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"last_name": "ใจดี", "phone": "0812345678"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้านามสกุลใจดี เบอร์ 0812345678",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert any(r[0] == "create_customer" for r in client.recorded)

    async def test_a_bare_reply_completes_a_hard_validation_refusal(self):
        """The exact scenario asked about directly: type "เพิ่มลูกค้า
        สมหญิง" (first name only, AI itself thought this was complete —
        missing=[]), get told last_name is needed, then reply with JUST
        the last name and have it actually complete the request.

        This only works because the hard validation in _handle_customer_
        intent registers its own pending_intent when it refuses — without
        that, a bare follow-up reply would have nothing to merge against
        and would be parsed as a brand new, meaningless message instead.
        """
        # Turn 1: AI thought "สมหญิง" alone was a complete request.
        ai_1 = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมหญิง"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        first_reply = await handle_chat_message(
            client, message="เพิ่มลูกค้า สมหญิง",
            ctx=_ctx(primary_role="sales"), ai_client=ai_1,
        )
        assert "นามสกุล" in first_reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)
        stored = next(r for r in client.recorded if r[0] == "set_pending_intent")
        assert stored[3]["missing"] == ["last_name", "phone"]
        assert stored[3]["fields"] == {"first_name": "สมหญิง"}

        # Turn 2: bare reply naming only the last name — the model, told
        # about the pending state, merges it with what's already known.
        ai_2 = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"last_name": "ใจดี"}, "missing": ["phone"]},
            ensure_ascii=False)))
        client._pending = {
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมหญิง"}, "missing": ["last_name", "phone"],
        }
        second_reply = await handle_chat_message(
            client, message="ใจดี", ctx=_ctx(primary_role="sales"), ai_client=ai_2,
        )
        assert "เบอร์โทร" in second_reply.text
        assert not any(r[0] == "create_customer" for r in client.recorded)

        # Turn 3: the phone number arrives — now everything required is
        # present and the customer is actually created.
        ai_3 = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"phone": "0812345678"}, "missing": []})))
        client._pending = {
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมหญิง", "last_name": "ใจดี"}, "missing": ["phone"],
        }
        third_reply = await handle_chat_message(
            client, message="0812345678", ctx=_ctx(primary_role="sales"), ai_client=ai_3,
        )
        assert any(r[0] == "create_customer" for r in client.recorded)
        call = next(r for r in client.recorded if r[0] == "create_customer")
        assert call[2] == {"first_name": "สมหญิง", "last_name": "ใจดี", "phone": "0812345678"}
        assert "สมหญิง" in third_reply.text

    async def test_update_customer_by_name(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "customer",
             "fields": {"target_name": "สมชาย", "phone": "0899999999"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
        )
        reply = await handle_chat_message(
            client, message="แก้เบอร์ลูกค้าสมชายเป็น 0899999999",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert any(r[0] == "update_customer" for r in client.recorded)
        assert "สมชาย" in reply.text

    async def test_update_customer_not_found(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "customer",
             "fields": {"target_name": "วิชัย", "phone": "0899999999"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.update"], customers=[])
        reply = await handle_chat_message(
            client, message="แก้เบอร์ลูกค้าวิชัย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "ไม่พบลูกค้า" in reply.text
        assert not any(r[0] == "update_customer" for r in client.recorded)

    async def test_update_customer_ambiguous_name_asks_to_clarify(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "customer",
             "fields": {"target_name": "สมชาย", "phone": "0899999999"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[
                {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                 "phone": "0811111111", "email": None, "stage": "lead"},
                {"id": "CUST-2", "first_name": "สมชาย", "last_name": "มั่งมี",
                 "phone": "0822222222", "email": None, "stage": "lead"},
            ],
        )
        reply = await handle_chat_message(
            client, message="แก้เบอร์ลูกค้าสมชาย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "หลายคน" in reply.text
        assert not any(r[0] == "update_customer" for r in client.recorded)

    async def test_promote_lead_to_contact(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "promote", "entity": "customer",
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
        )
        reply = await handle_chat_message(
            client, message="ยืนยันลูกค้าสมชาย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert any(r[0] == "promote_customer" for r in client.recorded)
        assert "สมชาย" in reply.text


class TestPhase9DealChat:
    async def test_create_deal_for_an_existing_customer(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["deal.create"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "contact"}],
        )
        reply = await handle_chat_message(
            client, message="สร้างดีลให้สมชาย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "D-2026-" in reply.text
        assert any(r[0] == "create_deal" for r in client.recorded)

    async def test_create_deal_without_naming_a_customer_is_refused(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal", "fields": {}, "missing": []})))
        client = FakeDataClient(permission_keys=["deal.create"])
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "กรุณาระบุชื่อลูกค้า" in reply.text
        assert not any(r[0] == "create_deal" for r in client.recorded)

    async def test_deal_stage_command_is_matched_directly_not_via_ai(self):
        """9.6 — closed pattern, no AI call needed at all: ai_client=None
        must still work."""
        client = FakeDataClient(
            permission_keys=["deal.update"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "new",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="ดีล D-2026-0001 เสนอราคาแล้ว",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "D-2026-0001" in reply.text
        assert "proposed" in reply.text
        assert any(r[0] == "transition_deal_stage" for r in client.recorded)

    async def test_deal_stage_command_requires_deal_update_permission(self):
        client = FakeDataClient(permission_keys=[], deals=[])
        reply = await handle_chat_message(
            client, message="ดีล D-2026-0001 ปิดสำเร็จ",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert not any(r[0] == "transition_deal_stage" for r in client.recorded)

    async def test_reopen_without_deal_reopen_permission_is_refused(self):
        client = FakeDataClient(
            permission_keys=["deal.update"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "won",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="เปิดดีล D-2026-0001 ใหม่",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "deal.reopen" in reply.text
        assert not any(r[0] == "transition_deal_stage" for r in client.recorded)

    async def test_reopen_with_deal_reopen_permission_succeeds(self):
        client = FakeDataClient(
            permission_keys=["deal.update", "deal.reopen"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "won",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="เปิดดีล D-2026-0001 ใหม่",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert any(r[0] == "transition_deal_stage" for r in client.recorded)
        assert "new" in reply.text

    async def test_deal_stage_command_only_recognised_on_sales_oa(self):
        """A Technician OA account must not be able to close deals just
        because the deal code happens to appear in their message."""
        client = FakeDataClient(
            permission_keys=["deal.update"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "new",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "suggest", "suggestions": []})))
        reply = await handle_chat_message(
            client, message="D-2026-0001 เสนอราคาแล้ว",
            ctx=_ctx(primary_role="technician"), ai_client=ai,
        )
        assert not any(r[0] == "transition_deal_stage" for r in client.recorded)

    async def test_deal_not_found_is_reported(self):
        client = FakeDataClient(permission_keys=["deal.update"], deals=[])
        reply = await handle_chat_message(
            client, message="ดีล D-2026-9999 ปิดสำเร็จ",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "ไม่พบดีล" in reply.text

    async def test_lost_is_not_misparsed_as_won(self):
        """"ไม่สำเร็จ" (lost) contains "สำเร็จ" (won) as a literal substring
        — checking won's keyword first would misclassify every lost deal as
        won. Regression for exactly that ordering mistake."""
        client = FakeDataClient(
            permission_keys=["deal.update"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="ดีล D-2026-0001 ปิดไม่สำเร็จ",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        call = next(r for r in client.recorded if r[0] == "transition_deal_stage")
        assert call[3] == "lost"
        assert "lost" in reply.text

    async def test_reopen_phrase_with_deal_code_in_the_middle(self):
        """"เปิดดีล D-2026-0001 ใหม่" splits the reopen phrase across the
        deal code — the code must be stripped before keyword matching, not
        after, or this never matches at all."""
        client = FakeDataClient(
            permission_keys=["deal.update", "deal.reopen"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "lost",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="เปิดดีล D-2026-0001 ใหม่",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        call = next(r for r in client.recorded if r[0] == "transition_deal_stage")
        assert call[3] == "new"


class TestPhase9Storefront:
    """Master Spec 9.4 — cross-tenant product search, independent of
    registration status. Exercises maybe_handle_storefront directly since
    the webhook-level wiring (checked before is_unregistered) is what
    actually calls it in production; these confirm the function's own
    behaviour in isolation.
    """

    async def test_search_trigger_returns_a_numbered_list(self):
        client = FakeDataClient(storefront_results=[
            {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
             "category": None, "unit_price": "3500", "license_id": "LIC-A",
             "company_name": "ร้าน A"},
            {"product_id": "P2", "product_name": "พัดลมตั้งพื้น", "sku": None,
             "category": None, "unit_price": "1200", "license_id": "LIC-B",
             "company_name": "ร้าน B"},
        ])
        reply = await maybe_handle_storefront(
            client, message="ค้นหา พัดลม",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "1. พัดลมไอเย็น" in reply.text
        assert "2. พัดลมตั้งพื้น" in reply.text
        assert any(r[0] == "set_pending_intent" for r in client.recorded)

    async def test_no_results_says_so(self):
        client = FakeDataClient(storefront_results=[])
        reply = await maybe_handle_storefront(
            client, message="ค้นหา จรวด",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "ไม่พบสินค้า" in reply.text

    async def test_trigger_with_no_keyword_asks_for_one(self):
        client = FakeDataClient()
        reply = await maybe_handle_storefront(
            client, message="ค้นหา",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "ค้นหา" in reply.text

    async def test_non_search_message_with_no_pending_returns_none(self):
        """The caller (webhook.py) must fall through to normal handling —
        this is the whole reason the function returns None rather than
        always producing a reply."""
        client = FakeDataClient(pending_intent=None)
        reply = await maybe_handle_storefront(
            client, message="สวัสดีครับ",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None

    async def test_a_bare_product_name_with_no_trigger_word_asks_to_confirm_first(self):
        """Reported live: typing just "พัดลม" (no "ค้นหา" prefix) fell
        straight through to the registration flow's shop-name search
        instead. A bare word is genuinely ambiguous for a customer (search
        for one to buy? ask about a repair ticket for one already filed?),
        so this asks for confirmation rather than assuming a product
        search and listing results outright."""
        client = FakeDataClient(storefront_results=[
            {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
             "category": None, "unit_price": "3500", "license_id": "LIC-A",
             "company_name": "ร้าน A"},
        ])
        reply = await maybe_handle_storefront(
            client, message="พัดลม",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "พัดลม" in reply.text
        assert "ต้องการดูรายการสินค้าไหม" in reply.text
        assert "พัดลมไอเย็น" not in reply.text  # results not shown yet
        assert any(r[0] == "storefront_search" for r in client.recorded)
        assert any(r[0] == "set_pending_intent" for r in client.recorded)

    async def test_confirming_after_a_bare_word_then_shows_the_results(self):
        client = FakeDataClient(pending_intent={
            "action": "confirm", "entity": "storefront_confirm",
            "fields": {"query": "พัดลม", "results": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
                 "category": None, "unit_price": "3500", "license_id": "LIC-A",
                 "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="ใช่",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "พัดลมไอเย็น" in reply.text
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)
        assert any(
            r[0] == "set_pending_intent" and r[3]["entity"] == "storefront"
            for r in client.recorded
        )

    async def test_declining_after_a_bare_word_lets_the_message_be_handled_normally(self):
        """The exact scenario asked about: "พัดลมที่แจ้งซ่อมไว้เป็นยังไง
        บ้าง" after being asked to confirm — this is clearly NOT a product
        search, so it must fall through (return None) rather than being
        forced into the storefront flow."""
        client = FakeDataClient(pending_intent={
            "action": "confirm", "entity": "storefront_confirm",
            "fields": {"query": "พัดลม", "results": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
                 "category": None, "unit_price": "3500", "license_id": "LIC-A",
                 "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="พัดลมที่แจ้งซ่อมไว้เป็นยังไงบ้าง",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_retyping_the_explicit_search_trigger_also_confirms(self):
        client = FakeDataClient(pending_intent={
            "action": "confirm", "entity": "storefront_confirm",
            "fields": {"query": "พัดลม", "results": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
                 "category": None, "unit_price": "3500", "license_id": "LIC-A",
                 "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="ค้นหา พัดลม",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "พัดลมไอเย็น" in reply.text

    async def test_explicit_search_trigger_still_skips_confirmation(self):
        """An explicit "ค้นหา [term]" is already unambiguous — it must go
        straight to results, never through the confirm step."""
        client = FakeDataClient(storefront_results=[
            {"product_id": "P1", "product_name": "พัดลมไอเย็น", "sku": None,
             "category": None, "unit_price": "3500", "license_id": "LIC-A",
             "company_name": "ร้าน A"},
        ])
        reply = await maybe_handle_storefront(
            client, message="ค้นหา พัดลม",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "พัดลมไอเย็น" in reply.text
        assert not any(r[0] == "set_pending_intent" and r[3]["entity"] == "storefront_confirm"
                      for r in client.recorded)

    async def test_a_bare_word_with_no_matching_products_falls_through_untouched(self):
        """Must not regress the existing shop-name/registration flow: if
        nothing matches as a product, this returns None exactly as before
        so the caller's normal handling still runs."""
        client = FakeDataClient(storefront_results=[])
        reply = await maybe_handle_storefront(
            client, message="ร้านสมชาย",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None

    async def test_a_bare_company_code_shaped_message_never_tries_a_product_search(self):
        """An 8-character company code must always be handled by the
        registration flow (linking to a shop), never intercepted as a
        product search attempt — even if it happened to also look like a
        product name, which real company codes (letters+digits, no O/0/I/1/L)
        essentially never will."""
        client = FakeDataClient(storefront_results=[
            {"product_id": "P1", "product_name": "ตัวอย่าง", "sku": None,
             "category": None, "unit_price": None, "license_id": "LIC-A",
             "company_name": "ร้าน A"},
        ])
        reply = await maybe_handle_storefront(
            client, message="ABCD2345",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None
        assert not any(r[0] == "storefront_search" for r in client.recorded)

    async def test_a_single_character_message_never_triggers_a_product_search(self):
        """Too short to be a meaningful search — avoids firing a search on
        every one-letter reply in an otherwise ordinary conversation."""
        client = FakeDataClient(storefront_results=[
            {"product_id": "P1", "product_name": "ตัวอย่าง", "sku": None,
             "category": None, "unit_price": None, "license_id": "LIC-A",
             "company_name": "ร้าน A"},
        ])
        reply = await maybe_handle_storefront(
            client, message="ก",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None
        assert not any(r[0] == "storefront_search" for r in client.recorded)

    async def test_selecting_a_valid_number_records_interest_and_clears_pending(self):
        client = FakeDataClient(pending_intent={
            "action": "select", "entity": "storefront",
            "fields": {"options": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น",
                 "license_id": "LIC-A", "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="1",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "พัดลมไอเย็น" in reply.text
        assert "ร้าน A" in reply.text
        assert any(r[0] == "storefront_record_interest" for r in client.recorded)
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_selecting_an_out_of_range_number_is_refused(self):
        client = FakeDataClient(pending_intent={
            "action": "select", "entity": "storefront",
            "fields": {"options": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น",
                 "license_id": "LIC-A", "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="9",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert "1-1" in reply.text
        assert not any(r[0] == "storefront_record_interest" for r in client.recorded)

    async def test_pending_selection_that_is_not_a_number_is_refused(self):
        client = FakeDataClient(pending_intent={
            "action": "select", "entity": "storefront",
            "fields": {"options": [
                {"product_id": "P1", "product_name": "พัดลมไอเย็น",
                 "license_id": "LIC-A", "company_name": "ร้าน A"},
            ]},
            "missing": [],
        })
        reply = await maybe_handle_storefront(
            client, message="เอาอันแรก",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is not None
        assert not any(r[0] == "storefront_record_interest" for r in client.recorded)

    async def test_a_second_unrelated_pending_intent_is_not_mistaken_for_storefront(self):
        """entity != "storefront" — e.g. a leftover create-customer
        continuation from Sales OA — must never be treated as a product
        selection."""
        client = FakeDataClient(pending_intent={
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย"}, "missing": ["phone"],
        })
        reply = await maybe_handle_storefront(
            client, message="สวัสดี",
            ctx=_ctx(primary_role="customer", oa="customer"), language="th",
        )
        assert reply is None


class TestLastCustomerReference:
    """Reported live: "บันทึกสมชายเป็น Contact แล้ว" followed immediately by
    "สร้างดีล" with no name at all — a completely natural way to talk once
    a customer has already been named once in the conversation."""

    async def test_creating_a_customer_remembers_them(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "สมชาย", "last_name": "ใจดี",
                        "phone": "0812345678"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["customer.create"])
        await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อสมชาย ใจดี เบอร์ 0812345678",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        call = next(r for r in client.recorded if r[0] == "set_last_customer_ref")
        assert call[4] == "สมชาย ใจดี"

    async def test_promoting_a_customer_remembers_them(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "promote", "entity": "customer",
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
        )
        await handle_chat_message(
            client, message="ยืนยันลูกค้าสมชาย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert any(r[0] == "set_last_customer_ref" for r in client.recorded)

    async def test_deal_create_with_no_name_falls_back_to_last_customer(self):
        """The exact reported scenario: no target_name at all in the
        parsed intent, but a customer was just discussed."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal", "fields": {}, "missing": []})))
        client = FakeDataClient(
            permission_keys=["deal.create"],
            last_customer_ref={"customer_id": "CUST-1", "name": "สมชาย"},
        )
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "D-2026-" in reply.text
        assert "สมชาย" in reply.text
        # says explicitly that it used the recently-discussed customer,
        # rather than silently guessing
        assert "เพิ่งคุยถึง" in reply.text
        call = next(r for r in client.recorded if r[0] == "create_deal")
        assert call[2]["contact_id"] == "CUST-1"

    async def test_deal_create_with_no_name_and_no_context_still_asks(self):
        """No fallback exists — must not fail silently or guess; asks like
        before."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal", "fields": {}, "missing": []})))
        client = FakeDataClient(permission_keys=["deal.create"], last_customer_ref=None)
        reply = await handle_chat_message(
            client, message="สร้างดีล", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "กรุณาระบุชื่อลูกค้า" in reply.text
        assert not any(r[0] == "create_deal" for r in client.recorded)

    async def test_deal_create_with_an_explicit_name_ignores_stale_context(self):
        """An explicit name in this message must win over whatever was
        remembered from an earlier one — the fallback is last resort only."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"target_name": "วิชัย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["deal.create"],
            customers=[{"id": "CUST-2", "first_name": "วิชัย", "last_name": None,
                        "phone": "0899999999", "email": None, "stage": "contact"}],
            last_customer_ref={"customer_id": "CUST-1", "name": "สมชาย"},
        )
        reply = await handle_chat_message(
            client, message="สร้างดีลให้วิชัย", ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "วิชัย" in reply.text
        assert "เพิ่งคุยถึง" not in reply.text
        call = next(r for r in client.recorded if r[0] == "create_deal")
        assert call[2]["contact_id"] == "CUST-2"


class TestChatNeverGoesSilentOnADataTierError:
    """Reported live: typing a message got NO reply at all. Root cause: the
    Data-tier calls these handlers make had no exception handling — an
    invalid price, a race-condition not-found, or any bug at all propagated
    all the way up uncaught, and webhook.py's main loop had no top-level
    safety net either, so the request died before reply_text() ever ran.
    These confirm each handler now degrades to a friendly reply instead of
    raising; the webhook-level safety net itself is defense-in-depth and
    isn't exercised by these (webhook.py has no dedicated test harness).
    """

    async def test_product_create_with_an_invalid_price_gets_a_friendly_reply(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "product",
             "fields": {"product_id": "AC-001", "product_name": "แอร์",
                        "unit_price": "3500 บาท"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["product.manage"], raises=ConflictForTest("bad price"),
        )
        reply = await handle_chat_message(
            client, message="เพิ่มสินค้าแอร์ ราคา 3500 บาท",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.text  # got SOME reply, not a crash
        assert "ไม่ถูกต้อง" in reply.text

    async def test_customer_update_hitting_a_not_found_gets_a_friendly_reply(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "customer",
             "fields": {"target_name": "สมชาย", "phone": "0899999999"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
            raises=NotFoundForTest("gone"),
        )
        reply = await handle_chat_message(
            client, message="แก้เบอร์ลูกค้าสมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.text
        assert "ไม่พบลูกค้า" in reply.text

    async def test_customer_promote_hitting_a_not_found_gets_a_friendly_reply(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "promote", "entity": "customer",
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "lead"}],
            raises=NotFoundForTest("gone"),
        )
        reply = await handle_chat_message(
            client, message="ยืนยันลูกค้าสมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.text
        assert "ไม่พบลูกค้า" in reply.text

    async def test_deal_create_hitting_a_not_found_gets_a_friendly_reply(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "deal",
             "fields": {"target_name": "สมชาย"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["deal.create"],
            customers=[{"id": "CUST-1", "first_name": "สมชาย", "last_name": None,
                        "phone": "0812345678", "email": None, "stage": "contact"}],
            raises=NotFoundForTest("gone"),
        )
        reply = await handle_chat_message(
            client, message="สร้างดีลให้สมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.text
        assert "ไม่พบลูกค้า" in reply.text


class TestPhase10QuoteChat:
    """Master Spec 10.1/10.7 — chat-side dispatch for quote creation. The
    DOCX-authoring/AI-mapping/SmartBrowz-render pipeline (10.4-10.6) isn't
    wired to chat at all yet (needs real Zoho Catalyst SmartBrowz
    credentials this environment doesn't have — see phase10.py's module
    docstring); these only cover the quote-from-deal creation that already
    works standalone (Quote.generated_document_id is nullable by 10.3's
    own design, precisely so a quote can exist before any document does).
    """

    async def test_create_quote_from_an_existing_deal(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote",
             "fields": {"deal_code": "D-2026-0001"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["quote.create"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคาจากดีล D-2026-0001",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "Q-2026-" in reply.text
        assert "D-2026-0001" in reply.text
        assert reply.entity_type == "quote"
        call = next(r for r in client.recorded if r[0] == "create_quote")
        assert call[2]["deal_id"] == "DEAL-1"

    async def test_create_quote_without_a_deal_code_is_refused(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote", "fields": {}, "missing": []})))
        client = FakeDataClient(permission_keys=["quote.create"])
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคา",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "กรุณาระบุรหัสดีล" in reply.text
        assert not any(r[0] == "create_quote" for r in client.recorded)

    async def test_create_quote_for_a_deal_code_that_does_not_exist(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote",
             "fields": {"deal_code": "D-2026-9999"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(permission_keys=["quote.create"], deals=[])
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคาจากดีล D-2026-9999",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "ไม่พบดีล" in reply.text
        assert not any(r[0] == "create_quote" for r in client.recorded)

    async def test_quote_create_hitting_a_not_found_gets_a_friendly_reply(self):
        """Mirrors the same defensive pattern already proven for customer/
        deal/product handlers: never let a Data-tier error propagate
        uncaught and kill the reply silently."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote",
             "fields": {"deal_code": "D-2026-0001"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["quote.create"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
            raises=NotFoundForTest("gone"),
        )
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคาจากดีล D-2026-0001",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert reply.text
        assert "ไม่พบดีล" in reply.text

    async def test_quote_create_requires_quote_create_permission(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote",
             "fields": {"deal_code": "D-2026-0001"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=[],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคาจากดีล D-2026-0001",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert not any(r[0] == "create_quote" for r in client.recorded)

    async def test_quote_creation_message_is_not_misread_as_a_deal_stage_command(self):
        """Regression for a real bug found while building this feature:
        "เสนอราคา" (propose a price — the deal-stage keyword for
        "proposed") is also the literal root of "ใบเสนอราคา" (a quote, this
        entity's own noun). "สร้างใบเสนอราคาจากดีล D-2026-0001" contains
        BOTH a valid deal code and that substring, and was being
        intercepted as a deal-stage-transition command (moving the deal to
        "proposed") instead of reaching quote creation at all — silently
        wrong, not even an error, since the deal-stage path never checked
        entity="quote" was actually what the AI parsed."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "quote",
             "fields": {"deal_code": "D-2026-0001"}, "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["quote.create", "deal.update"],  # holds BOTH, to prove
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "new",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="สร้างใบเสนอราคาจากดีล D-2026-0001",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert any(r[0] == "create_quote" for r in client.recorded)
        assert not any(r[0] == "transition_deal_stage" for r in client.recorded)
        assert "Q-2026-" in reply.text

    async def test_a_genuine_deal_stage_command_still_works_after_the_fix(self):
        """The counterpart, so the collision fix cannot pass by simply
        disabling the "proposed" stage transition altogether."""
        client = FakeDataClient(
            permission_keys=["deal.update"],
            deals=[{"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "new",
                    "contact_id": "CUST-1", "notes": None, "products": []}],
        )
        reply = await handle_chat_message(
            client, message="ดีล D-2026-0001 เสนอราคาแล้ว",
            ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert any(r[0] == "transition_deal_stage" for r in client.recorded)
        assert "proposed" in reply.text


class TestCustomerDisambiguation:
    """Requested directly: if "สร้างดีลให้สมชาย" matches several customers
    named สมชาย, offer a numbered list to pick from — not just "please be
    more specific." """

    async def test_ambiguous_update_shows_a_numbered_list_and_sets_pending(self):
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "update", "entity": "customer",
             "fields": {"target_name": "สมชาย", "phone": "0899999999"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[
                {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                 "phone": "0812345678", "email": None, "stage": "lead"},
                {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                 "phone": "0899999999", "email": None, "stage": "lead"},
            ],
        )
        reply = await handle_chat_message(
            client, message="แก้เบอร์ลูกค้าสมชาย",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "1." in reply.text and "2." in reply.text
        assert "ใจดี" in reply.text and "รักไทย" in reply.text
        assert not any(r[0] == "update_customer" for r in client.recorded)
        stored = next(r for r in client.recorded if r[0] == "set_pending_intent")
        assert stored[3]["entity"] == "customer_disambiguation"
        assert stored[3]["fields"]["resume_entity"] == "customer"
        assert stored[3]["fields"]["resume_action"] == "update"
        assert len(stored[3]["fields"]["candidates"]) == 2

    async def test_picking_a_number_completes_the_original_update(self):
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[
                {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                 "phone": "0812345678", "email": None, "stage": "lead"},
                {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                 "phone": "0800000000", "email": None, "stage": "lead"},
            ],
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "customer", "resume_action": "update",
                    "resume_fields": {"phone": "0899999999"},
                    "candidates": [
                        {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                         "phone": "0812345678"},
                        {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                         "phone": "0800000000"},
                    ],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="2", ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "รักไทย" in reply.text
        call = next(r for r in client.recorded if r[0] == "update_customer")
        assert call[2] == "CUST-2"
        assert call[3] == {"phone": "0899999999"}
        assert any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_picking_a_number_completes_the_original_promote(self):
        client = FakeDataClient(
            permission_keys=["customer.update"],
            customers=[
                {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                 "phone": "0812345678", "email": None, "stage": "lead"},
                {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                 "phone": "0800000000", "email": None, "stage": "lead"},
            ],
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "customer", "resume_action": "promote",
                    "resume_fields": {},
                    "candidates": [
                        {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                         "phone": "0812345678"},
                        {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                         "phone": "0800000000"},
                    ],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="1", ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "ใจดี" in reply.text
        call = next(r for r in client.recorded if r[0] == "promote_customer")
        assert call[2] == "CUST-1"

    async def test_picking_a_number_completes_the_original_deal_create(self):
        client = FakeDataClient(
            permission_keys=["deal.create"],
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "deal", "resume_action": "create",
                    "resume_fields": {"notes": None},
                    "candidates": [
                        {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                         "phone": "0812345678"},
                        {"id": "CUST-2", "first_name": "สมชาย", "last_name": "รักไทย",
                         "phone": "0800000000"},
                    ],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="2", ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "D-2026-" in reply.text
        assert "รักไทย" in reply.text
        call = next(r for r in client.recorded if r[0] == "create_deal")
        assert call[2]["contact_id"] == "CUST-2"

    async def test_an_out_of_range_number_asks_again_without_completing(self):
        client = FakeDataClient(
            permission_keys=["customer.update"],
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "customer", "resume_action": "update",
                    "resume_fields": {"phone": "0899999999"},
                    "candidates": [
                        {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                         "phone": "0812345678"},
                    ],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="9", ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert "1" in reply.text
        assert not any(r[0] == "update_customer" for r in client.recorded)
        assert not any(r[0] == "clear_pending_intent" for r in client.recorded)

    async def test_resuming_without_the_right_permission_is_refused(self):
        client = FakeDataClient(
            permission_keys=[],  # no customer.update
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "customer", "resume_action": "update",
                    "resume_fields": {"phone": "0899999999"},
                    "candidates": [
                        {"id": "CUST-1", "first_name": "สมชาย", "last_name": "ใจดี",
                         "phone": "0812345678"},
                    ],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="1", ctx=_ctx(primary_role="sales"), ai_client=None,
        )
        assert not any(r[0] == "update_customer" for r in client.recorded)

    async def test_a_non_numeric_reply_with_disambiguation_pending_falls_through_to_ai(self):
        """Only a bare digit is treated as a selection — anything else
        (the user changed their mind, or answered a different question
        entirely) must still go through the normal parser, not get stuck
        demanding a number."""
        ai = httpx.AsyncClient(transport=_ai(json.dumps(
            {"action": "create", "entity": "customer",
             "fields": {"first_name": "วิชัย", "last_name": "ดี", "phone": "0811111111"},
             "missing": []}, ensure_ascii=False)))
        client = FakeDataClient(
            permission_keys=["customer.create"],
            pending_intent={
                "action": "resolve", "entity": "customer_disambiguation",
                "fields": {
                    "resume_entity": "customer", "resume_action": "update",
                    "resume_fields": {}, "candidates": [],
                },
                "missing": [],
            },
        )
        reply = await handle_chat_message(
            client, message="เพิ่มลูกค้าชื่อวิชัย ดี เบอร์ 0811111111",
            ctx=_ctx(primary_role="sales"), ai_client=ai,
        )
        assert "วิชัย" in reply.text
        assert any(r[0] == "create_customer" for r in client.recorded)
