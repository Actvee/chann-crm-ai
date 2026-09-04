"""User review, 4 Sep 2026 — the four issues, end to end through the chat
engine with the Data tier faked.

1. capabilities are explained in detail, from permissions
2. a duplicate phone/email is a conversation with real choices, not a loop
3. a lead can be deleted (archived) with confirmation and permission;
   inactive-lead cleanup is a per-tenant setting, off by default
4. a deal keeps the right person, its amount and its closing date
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import deal_fields, lead_cleanup  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.thai_datetime import local_today  # noqa: E402
from test_phase6_chat import PERMISSION_KEYS, FakeDataClient, _ai, _catalog, _ctx, describe  # noqa: E402

SALES_KEYS = ["customer.read", "customer.create", "customer.update", "customer.archive",
              "deal.read", "deal.create", "deal.update", "ticket.read", "setting.manage"]


class ReviewFake(FakeDataClient):
    """The Phase 6 fake plus the calls these fixes make: duplicate checks on
    create, archive, settings, inactive-lead cleanup."""

    def __init__(self, **kwargs):
        kwargs.setdefault("permission_keys", SALES_KEYS)
        super().__init__(**kwargs)
        self.settings: dict = {}
        self.archived: list[str] = []
        self.cleanup_calls: list[tuple] = []

    async def create_customer(self, license_id, payload, actor_id=None):
        phone = "".join(ch for ch in str(payload.get("phone") or "") if ch.isdigit())
        email = str(payload.get("email") or "").strip().lower()
        for row in self._customers:
            if row.get("archived_at"):
                continue
            row_phone = "".join(ch for ch in str(row.get("phone") or "") if ch.isdigit())
            if phone and row_phone == phone:
                raise DataTierError(409, "duplicate", {"error": "duplicate", "existing_id": row["id"],
                                                        "existing_code": row["customer_id"], "field": "phone"})
            if email and str(row.get("email") or "").strip().lower() == email:
                raise DataTierError(409, "duplicate", {"error": "duplicate", "existing_id": row["id"],
                                                        "existing_code": row["customer_id"], "field": "email"})
        return await super().create_customer(license_id, payload, actor_id)

    async def get_customer(self, license_id, customer_id):
        self.recorded.append(("get_customer", license_id, customer_id))
        return next((c for c in self._customers if c["id"] == customer_id), None)

    async def archive_customer(self, license_id, customer_id, actor_id=None):
        self.recorded.append(("archive_customer", license_id, customer_id, actor_id))
        row = next(c for c in self._customers if c["id"] == customer_id)
        row["archived_at"] = "2026-09-04T00:00:00+00:00"
        self.archived.append(customer_id)
        return row

    async def create_deal(self, license_id, payload, actor_id=None):
        row = await super().create_deal(license_id, payload, actor_id)
        row.update({k: payload.get(k) for k in ("amount", "currency", "expected_close_date")})
        return row

    async def list_license_settings(self, license_id):
        return [{"setting_key": k, "setting_value": v} for k, v in self.settings.items()]

    async def put_license_setting(self, license_id, setting_key, setting_value, actor_id=None):
        self.recorded.append(("put_license_setting", license_id, setting_key, setting_value))
        self.settings[setting_key] = setting_value
        return {"setting_key": setting_key, "setting_value": setting_value}

    async def list_licenses(self, status=None, exclude_status=None):
        return [{"id": "L1", "status": "active"}]

    async def archive_inactive_leads(self, license_id, days, actor_id=None):
        self.cleanup_calls.append((license_id, days, actor_id))
        return [{"id": "CUST-old"}]


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _model(payload: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_ai(json.dumps(payload)))


async def _seed(client, first="สมชาย", last="ใจดี", phone="0812345678", email=None):
    return await FakeDataClient.create_customer(client, "L1", {"first_name": first, "last_name": last, "phone": phone, "email": email})


# ===================================================================== Issue 1

class TestCapabilities:
    async def test_what_can_you_do_returns_the_guide_plus_the_areas_open_to_me(self):
        client = ReviewFake()
        reply = await handle_chat_message(client, message="ระบบทำอะไรได้บ้าง", ctx=_ctx())
        assert reply.text.startswith("วิธีใช้ LINE ทีมขาย")
        assert "หมวดที่คุณใช้ได้" in reply.text and "ลูกค้า" in reply.text and "ดีล" in reply.text
        assert "ทำอะไรกับ Lead ได้บ้าง" in reply.text  # invites the follow-up
        assert not [r for r in client.recorded if r[0] == "parse_intent"]

    async def test_lead_follow_up_lists_commands_and_permissions_for_that_area(self):
        client = ReviewFake()
        reply = await handle_chat_message(client, message="แล้ว Lead ทำอะไรได้บ้าง", ctx=_ctx())
        assert reply.text.startswith("ลูกค้า —")
        assert "รายชื่อลูกค้า" in reply.text and "ลบ Lead สมชาย" in reply.text
        assert describe("customer.archive") in reply.text
        assert "ดีล" not in reply.text.split("สิทธิ์ที่มี")[0]  # only this area's commands

    async def test_permission_sensitive_commands_are_not_advertised(self):
        client = ReviewFake(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="ทำอะไรกับ Lead ได้บ้าง", ctx=_ctx())
        assert "รายชื่อลูกค้า" in reply.text
        assert "ลบ Lead" not in reply.text and "สร้างลูกค้า" not in reply.text
        assert "ยังไม่มีสิทธิ์" in reply.text and describe("customer.archive") in reply.text

    async def test_deal_follow_up_and_no_permission_area(self):
        client = ReviewFake()
        reply = await handle_chat_message(client, message="ทำอะไรกับดีลได้บ้าง", ctx=_ctx())
        assert reply.text.startswith("ดีล —") and "สร้างดีลให้" in reply.text
        client = ReviewFake(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="ใบเสนอราคาทำอะไรได้บ้าง", ctx=_ctx())
        assert "ยังไม่มีสิทธิ์ในหมวดนี้" in reply.text

    async def test_what_am_i_allowed_to_do_is_grouped_from_the_catalogue(self):
        client = ReviewFake()
        reply = await handle_chat_message(client, message="ฉันมีสิทธิ์ทำอะไร", ctx=_ctx())
        assert reply.text.startswith("สิทธิ์ของคุณตามหมวด")
        assert "• ลูกค้า:" in reply.text and describe("customer.create") in reply.text
        assert describe("billing.manage") not in reply.text

    async def test_ordinary_conversation_is_untouched(self):
        client = ReviewFake()
        ai = _model({"action": "read", "entity": "customer", "fields": {}, "missing": []})
        reply = await handle_chat_message(client, message="รายชื่อลูกค้า", ctx=_ctx(), ai_client=ai)
        assert "หมวดที่คุณใช้ได้" not in reply.text


# ===================================================================== Issue 2

class TestDuplicateCustomer:
    async def test_unique_phone_creates(self):
        client = ReviewFake()
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมหญิง", "last_name": "ดีใจ", "phone": "0899999999"}, "missing": []})
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า สมหญิง ดีใจ 0899999999", ctx=_ctx(), ai_client=ai)
        assert "เพิ่มลูกค้า" in reply.text and len(client._customers) == 1

    async def test_existing_phone_is_named_with_choices_not_a_loop(self):
        client = ReviewFake()
        existing = await _seed(client, phone="081-234-5678")
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใหม่", "phone": "0812345678"}, "missing": []})
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใหม่ 0812345678", ctx=_ctx(), ai_client=ai)
        assert "มีลูกค้าคนนี้อยู่แล้ว" in reply.text and existing["customer_id"] in reply.text
        assert "กรุณาระบุ" not in reply.text
        assert [q[0] for q in reply.quick_replies] == ["ใช้รายชื่อเดิม", "อัปเดตข้อมูลเดิม", "ยกเลิก"]
        assert len(client._customers) == 1

    async def test_existing_email_is_detected(self):
        client = ReviewFake()
        await _seed(client, phone="0811111111", email="Somchai@Example.com")
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0822222222", "email": "somchai@example.com "}, "missing": []})
        reply = await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจดี 0822222222 somchai@example.com", ctx=_ctx(), ai_client=ai)
        assert "อีเมล" in reply.text and "มีลูกค้าคนนี้อยู่แล้ว" in reply.text
        assert len(client._customers) == 1

    async def _duplicate(self, client):
        await _seed(client, phone="0812345678")
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "email": "new@example.com"}, "missing": []})
        return await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจดี 0812345678 new@example.com", ctx=_ctx(), ai_client=ai)

    async def test_cancel_after_duplicate(self):
        client = ReviewFake()
        await self._duplicate(client)
        reply = await handle_chat_message(client, message="ยกเลิก", ctx=_ctx())
        assert "ยกเลิกแล้ว" in reply.text and len(client._customers) == 1
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_use_existing_after_duplicate(self):
        client = ReviewFake()
        await self._duplicate(client)
        reply = await handle_chat_message(client, message="1", ctx=_ctx())
        assert "ใช้รายชื่อเดิม" in reply.text and len(client._customers) == 1
        assert (await client.get_last_customer_ref("CHN-S-000001", "sales"))["customer_id"] == client._customers[0]["id"]

    async def test_merge_fills_empty_fields_and_asks_before_overwriting(self):
        client = ReviewFake()
        await _seed(client, phone="0812345678", email=None)
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "ใจกว้าง", "phone": "0812345678", "email": "new@example.com"}, "missing": []})
        await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจกว้าง 0812345678", ctx=_ctx(), ai_client=ai)
        reply = await handle_chat_message(client, message="อัปเดตข้อมูลเดิม", ctx=_ctx())
        row = client._customers[0]
        assert row["email"] == "new@example.com"            # empty field filled
        assert row["last_name"] == "ใจดี"                     # conflict NOT overwritten yet
        assert "นามสกุล" in reply.text and "ใจดี → ใจกว้าง" in reply.text
        reply = await handle_chat_message(client, message="แทนที่ทั้งหมด", ctx=_ctx())
        assert client._customers[0]["last_name"] == "ใจกว้าง" and "แทนที่" in reply.text
        assert len(client._customers) == 1

    async def test_keep_existing_on_conflict(self):
        client = ReviewFake()
        await _seed(client, phone="0812345678")
        ai = _model({"action": "create", "entity": "customer", "fields": {"first_name": "สมชาย", "last_name": "อื่น", "phone": "0812345678"}, "missing": []})
        await handle_chat_message(client, message="เพิ่มลูกค้า สมชาย ใจกว้าง 0812345678", ctx=_ctx(), ai_client=ai)
        await handle_chat_message(client, message="2", ctx=_ctx())
        reply = await handle_chat_message(client, message="เก็บของเดิม", ctx=_ctx())
        assert client._customers[0]["last_name"] == "ใจดี" and "เก็บข้อมูลเดิม" in reply.text


# ===================================================================== Issue 3

class TestLeadDeletion:
    async def test_delete_with_permission_asks_then_archives(self):
        client = ReviewFake()
        row = await _seed(client)
        reply = await handle_chat_message(client, message="ลบ Lead สมชาย", ctx=_ctx())
        assert "ลบ สมชาย ใจดี" in reply.text and "ยืนยันลบ" in reply.text and not client.archived
        reply = await handle_chat_message(client, message="ยืนยันลบ", ctx=_ctx())
        assert client.archived == [row["id"]] and "เก็บถาวร" in reply.text
        assert ("archive_customer", _ctx().license_id, row["id"], "CHN-S-000001") in client.recorded

    async def test_cancel_keeps_the_lead(self):
        client = ReviewFake()
        await _seed(client)
        await handle_chat_message(client, message="ลบลูกค้ารายนี้ออกจาก Lead สมชาย", ctx=_ctx())
        reply = await handle_chat_message(client, message="ยกเลิก", ctx=_ctx())
        assert not client.archived and "ยังอยู่ในรายชื่อ" in reply.text

    async def test_this_lead_uses_the_customer_in_context(self):
        client = ReviewFake()
        row = await _seed(client)
        await client.set_last_customer_ref("CHN-S-000001", "sales", customer_id=row["id"], name="สมชาย ใจดี")
        reply = await handle_chat_message(client, message="ลบ Lead นี้", ctx=_ctx())
        assert "สมชาย" in reply.text and "ยืนยันลบ" in reply.text

    async def test_without_permission_is_refused_and_nothing_is_archived(self):
        client = ReviewFake(permission_keys=["customer.read", "customer.create"])
        await _seed(client)
        reply = await handle_chat_message(client, message="ลบ Lead สมชาย", ctx=_ctx())
        assert "ยังไม่มีสิทธิ์" in reply.text and not client.archived
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_the_ai_archive_intent_reaches_the_same_confirmation(self):
        client = ReviewFake()
        await _seed(client)
        ai = _model({"action": "archive", "entity": "customer", "fields": {"target_name": "สมชาย"}, "missing": []})
        reply = await handle_chat_message(client, message="เอาสมชายออกไปเลย", ctx=_ctx(), ai_client=ai)
        assert "ยืนยันลบ" in reply.text and not client.archived


class TestInactiveLeadCleanup:
    async def test_off_by_default_and_configurable_in_chat(self):
        client = ReviewFake()
        reply = await handle_chat_message(client, message="การตั้งค่าลบ lead", ctx=_ctx())
        assert "ปิด (ค่าเริ่มต้น)" in reply.text
        reply = await handle_chat_message(client, message="ตั้งค่าลบ lead อัตโนมัติ 90 วัน", ctx=_ctx())
        assert "เกิน 90 วัน" in reply.text and client.settings["lead_auto_archive_days"] == 90
        reply = await handle_chat_message(client, message="ปิดการลบ lead อัตโนมัติ", ctx=_ctx())
        assert "ปิด" in reply.text and client.settings["lead_auto_archive_days"] == 0

    async def test_needs_setting_manage(self):
        client = ReviewFake(permission_keys=["customer.read"])
        reply = await handle_chat_message(client, message="ตั้งค่าลบ lead อัตโนมัติ 30 วัน", ctx=_ctx())
        assert "ยังไม่มีสิทธิ์" in reply.text and not client.settings

    async def test_sweep_only_runs_for_tenants_that_opted_in(self):
        client = ReviewFake()
        assert (await lead_cleanup.sweep_inactive_leads(client))["enabled"] == 0 and not client.cleanup_calls
        client.settings["lead_auto_archive_days"] = 45
        summary = await lead_cleanup.sweep_inactive_leads(client)
        assert summary == {"tenants": 1, "enabled": 1, "archived": 1, "failed": 0}
        assert client.cleanup_calls == [("L1", 45, "lead_cleanup")]
        client.settings["lead_auto_archive_days"] = "not a number"
        assert await lead_cleanup.lead_auto_archive_days(client, "L1") == 0


# ===================================================================== Issue 4

def _deal_ai(fields: dict) -> httpx.AsyncClient:
    return _model({"action": "create", "entity": "deal", "fields": fields, "missing": []})


def _created(client) -> dict:
    created = [r for r in client.recorded if r[0] == "create_deal"]
    assert len(created) == 1, created
    return created[0][2]


class TestDealCreation:
    async def test_the_named_person_gets_the_deal_not_the_last_one_mentioned(self):
        client = ReviewFake()
        arthit = await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        somchai = await _seed(client, "สมชาย", "ใจดี", "0822222222")
        await client.set_last_customer_ref("CHN-S-000001", "sales", customer_id=somchai["id"], name="สมชาย ใจดี")
        ai = _deal_ai({"target_name": "อาทิตย์", "amount": "500,000", "expected_close_date": "30/09/2026"})
        reply = await handle_chat_message(client, message="ดีลนี้ของอาทิตย์ มูลค่า 500,000 บาท คาดว่าจะปิดวันที่ 30/09/2026", ctx=_ctx(), ai_client=ai)
        payload = _created(client)
        assert payload["contact_id"] == arthit["id"]
        assert payload["amount"] == "500000.00" and payload["expected_close_date"] == "2026-09-30"
        assert "อาทิตย์" in reply.text and "500,000" in reply.text

    async def test_no_name_in_the_message_confirms_the_context_customer_instead_of_assuming(self):
        client = ReviewFake()
        somchai = await _seed(client, "สมชาย", "ใจดี", "0822222222")
        await client.set_last_customer_ref("CHN-S-000001", "sales", customer_id=somchai["id"], name="สมชาย ใจดี")
        ai = _deal_ai({"amount": "250000"})
        reply = await handle_chat_message(client, message="เปิดดีลมูลค่า 250,000 บาท", ctx=_ctx(), ai_client=ai)
        assert not [r for r in client.recorded if r[0] == "create_deal"]
        assert "สมชาย" in reply.text and "ใช่ไหม" in reply.text and "250,000" in reply.text
        reply = await handle_chat_message(client, message="ใช่", ctx=_ctx())
        assert _created(client)["contact_id"] == somchai["id"] and "D-2026-" in reply.text

    async def test_saying_no_to_the_context_customer_creates_nothing(self):
        client = ReviewFake()
        somchai = await _seed(client, "สมชาย", "ใจดี", "0822222222")
        await client.set_last_customer_ref("CHN-S-000001", "sales", customer_id=somchai["id"], name="สมชาย ใจดี")
        await handle_chat_message(client, message="เปิดดีลมูลค่า 250,000 บาท", ctx=_ctx(), ai_client=_deal_ai({"amount": "250000"}))
        reply = await handle_chat_message(client, message="ไม่ใช่", ctx=_ctx())
        assert not [r for r in client.recorded if r[0] == "create_deal"] and "ยังไม่ได้สร้างดีล" in reply.text

    @pytest.mark.parametrize("message,ai_amount,expected", [
        ("สร้างดีลให้อาทิตย์ มูลค่า 250000", "250000", "250000.00"),
        ("สร้างดีลให้อาทิตย์ มูลค่าดีล 250,000 บาท", "250,000", "250000.00"),
        ("สร้างดีลให้อาทิตย์ ดีลประมาณ 250K", "250K", "250000.00"),
        ("สร้างดีลให้อาทิตย์ มูลค่า 1.2 ล้าน", "1.2 ล้าน", "1200000.00"),
    ])
    async def test_amount_formats(self, message, ai_amount, expected):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        await handle_chat_message(client, message=message, ctx=_ctx(), ai_client=_deal_ai({"target_name": "อาทิตย์", "amount": ai_amount}))
        payload = _created(client)
        assert payload["amount"] == expected and payload["currency"] == "THB" and payload.get("expected_close_date") is None

    async def test_a_phone_number_is_never_the_amount(self):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        await handle_chat_message(client, message="สร้างดีลให้อาทิตย์ เบอร์ 0811111111", ctx=_ctx(),
                                  ai_client=_deal_ai({"target_name": "อาทิตย์", "amount": "0811111111"}))
        assert _created(client).get("amount") is None

    async def test_explicit_closing_date_and_thai_month_end(self):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        await handle_chat_message(client, message="สร้างดีลให้อาทิตย์ ปิดวันที่ 30 กันยายน", ctx=_ctx(),
                                  ai_client=_deal_ai({"target_name": "อาทิตย์", "expected_close_date": "30 กันยายน"}))
        assert _created(client)["expected_close_date"].endswith("-09-30")
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        await handle_chat_message(client, message="สร้างดีลให้อาทิตย์ คาดว่าจะปิดสิ้นเดือนนี้", ctx=_ctx(),
                                  ai_client=_deal_ai({"target_name": "อาทิตย์", "expected_close_date": "สิ้นเดือนนี้"}))
        today = local_today()
        end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        assert _created(client)["expected_close_date"] == end.isoformat()

    async def test_amount_and_date_in_one_sentence(self):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        # The model swapped them; the message decides.
        await handle_chat_message(client, message="สร้างดีลให้อาทิตย์ มูลค่า 1.2 ล้าน ปิดวันที่ 15 ต.ค.", ctx=_ctx(),
                                  ai_client=_deal_ai({"target_name": "อาทิตย์", "amount": "15", "expected_close_date": "1.2 ล้าน"}))
        payload = _created(client)
        assert payload["amount"] == "1200000.00" and payload["expected_close_date"].endswith("-10-15")

    async def test_ambiguous_amount_asks_only_for_the_amount(self):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        reply = await handle_chat_message(client, message="สร้างดีลให้อาทิตย์ มูลค่า 200,000 หรือ 300,000", ctx=_ctx(),
                                          ai_client=_deal_ai({"target_name": "อาทิตย์"}))
        assert not [r for r in client.recorded if r[0] == "create_deal"]
        assert "มูลค่าดีลคือเท่าไหร่" in reply.text and "200,000" in reply.text
        pending = await client.get_pending_intent("CHN-S-000001", "sales")
        assert pending["entity"] == "deal" and pending["missing"] == ["amount"] and pending["fields"]["target_name"] == "อาทิตย์"

    async def test_the_quick_reply_button_still_works_and_reads_trailing_details(self):
        client = ReviewFake()
        await _seed(client, "อาทิตย์", "แสงจันทร์", "0811111111")
        reply = await handle_chat_message(client, message="สร้างดีลให้ อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้", ctx=_ctx())
        payload = _created(client)
        assert payload["amount"] == "250000.00" and payload["expected_close_date"] and "D-2026-" in reply.text

    def test_extraction_helpers(self):
        today = date(2026, 9, 4)
        assert deal_fields.parse_amount("ดีลประมาณ 250K")[0] == Decimal("250000.00")
        assert deal_fields.parse_amount("แอร์ 3 ตัว")[0] is None
        assert deal_fields.parse_amount("เบอร์ 081-234-5678")[0] is None
        assert deal_fields.parse_close_date("ปิดวันที่ 15 ต.ค.", today) == date(2026, 10, 15)
        assert deal_fields.parse_close_date("ดีลของอาทิตย์ 30/09/2026", today) == date(2026, 9, 30)
        assert deal_fields.parse_close_date("ให้อาทิตย์", today) is None
        assert deal_fields.format_amount(Decimal("1200000.00")) == "1,200,000 บาท"
