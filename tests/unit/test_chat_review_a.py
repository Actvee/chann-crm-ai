"""Review round 2 (6 Sep 2026), section A — chat and rich menu.

One class per finding, A1…A26, then the ⚪ Low list and E13. Each test
re-creates the transcript the reviewer ran against the real handler and
pins the behaviour the fix established. The FakeDataClient of
test_phase6_chat stands in for the Data tier, as everywhere else.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services import live_chat  # noqa: E402
from chann_app.services import registration  # noqa: E402
from chann_app.services.chat import handle_chat_message, maybe_handle_storefront  # noqa: E402
from chann_app.services.identity import ResolvedContext, TenantResolution  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx  # noqa: E402

TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read", "warranty.read"]
SALES_KEYS = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
ME = "CHN-S-000001"


def _suggest():
    return httpx.AsyncClient(transport=_ai(json.dumps(
        {"action": "suggest", "entity": None, "fields": {}, "missing": []}, ensure_ascii=False,
    )))


def _ai_json(payload: dict):
    return httpx.AsyncClient(transport=_ai(json.dumps(payload, ensure_ascii=False)))


async def say(client, oa, message, *, ai=None, role=None, language="th"):
    ctx = _ctx(oa=oa, primary_role=role or ("technician" if oa == "technician" else "customer" if oa == "customer" else "sales"))
    return await handle_chat_message(client, message=message, ctx=ctx, language=language, ai_client=ai or _suggest())


def _ticket(n=1, **over):
    row = {
        "id": f"t{n}", "ticket_number": f"T-2026-{n:04d}", "status": "assigned", "accept_status": "accepted",
        "assigned_to_ref": "member-1", "assigned_target_type": "technician", "customer_name": "สมชาย",
        "customer_chann_uid": ME, "service_address": "99/1 สุขุมวิท", "issue_description": "แอร์ไม่เย็น",
        "scheduled_date": "2026-09-10", "scheduled_time": "10:00",
    }
    row.update(over)
    return row


def _tech(*tickets):
    c = FakeDataClient(permission_keys=TECH_KEYS, role="technician")
    c._tickets = list(tickets) or [_ticket(1, status="in_progress")]
    return c


def _customer(*tickets, pending=None):
    c = FakeDataClient(permission_keys=[], role="customer")
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "customer_chann_uid": ME, "warranty_end": "2027-01-01"}]
    c._tickets = list(tickets)
    c._pending = pending
    return c


def _sales(keys=None, **kw):
    c = FakeDataClient(permission_keys=list(keys if keys is not None else SALES_KEYS), role="sales", **kw)
    c._members = [
        {"id": "member-1", "chann_uid": ME, "role": "member", "status": "active"},
        {"id": "cs-1", "chann_uid": "CHN-CS", "role": "cs", "status": "active"},
        {"id": "owner-1", "chann_uid": "CHN-OWN", "role": "owner", "status": "active"},
    ]
    c._line_targets = {ME: "U-me", "CHN-CS": "U-cs", "CHN-OWN": "U-owner"}
    return c


def _writes(client, name):
    return [r for r in client.recorded if r[0] == name]


def _awaiting_address(client):
    client._pending = {"action": "report", "entity": "customer_ticket", "fields": {"ticket_id": "t1"}, "missing": ["address"]}


# ----------------------------------------------------------------- A1 / A8

class TestA1DraftSurvivesTiles:
    TILES = ["เช็คอิน", "ปิดงาน", "วิธีใช้", "ปฏิเสธงาน", "ข้อมูลของฉัน", "สิทธิ์ของฉัน", "สลับภาษา", "รับงาน", "ถึงแล้ว", "งานวันนี้", "งานของฉัน"]

    @pytest.mark.parametrize("tile", TILES)
    async def test_a_tile_mid_report_is_answered_and_the_draft_is_kept(self, tile):
        client = _tech()
        await say(client, "technician", "ปิดงาน")
        assert client._pending["entity"] == "service_report"
        reply = await say(client, "technician", tile)
        assert client._pending and client._pending["entity"] == "service_report"
        assert client._pending["fields"].get("found_issue") != tile
        assert reply.text and "ระบบไม่พร้อม" not in reply.text
        # the draft still takes the real answer afterwards
        await say(client, "technician", "คอมเพรสเซอร์รั่ว")
        assert client._pending["fields"]["found_issue"] == "คอมเพรสเซอร์รั่ว"

    async def test_a_lone_digit_and_a_placeholder_are_not_report_lines(self):
        client = _tech()
        await say(client, "technician", "ปิดงาน")
        reply = await say(client, "technician", "1")
        assert "found_issue" not in client._pending["fields"] and "พบปัญหา" in reply.text
        reply = await say(client, "technician", "ข้าม")
        assert "found_issue" not in client._pending["fields"] and "ต้องมี" in reply.text
        await say(client, "technician", "รั่ว")
        await say(client, "technician", "เปลี่ยน")
        reply = await say(client, "technician", "-")  # optional parts: "-" means none
        assert _writes(client, "check_out_ticket") and client._pending is None

    async def test_cancel_still_drops_the_draft(self):
        client = _tech()
        await say(client, "technician", "ปิดงาน")
        reply = await say(client, "technician", "ยกเลิก")
        assert client._pending is None and "ยกเลิกการปิดงาน" in reply.text


class TestA8HelpNeverOverwritesAFlow:
    async def test_help_mid_address_keeps_the_prompt_and_the_next_line_is_the_address(self):
        client = _customer(_ticket(1, status="open", service_address=None))
        _awaiting_address(client)
        reply = await say(client, "customer", "วิธีใช้")
        assert "วิธีใช้" in reply.text and client._pending["entity"] == "customer_ticket"
        await say(client, "customer", "99/1 ถ.สุขุมวิท กรุงเทพ")
        assert client._tickets[0]["service_address"] == "99/1 ถ.สุขุมวิท กรุงเทพ"
        assert len(client._tickets) == 1

    async def test_help_mid_slot_fill_keeps_the_slot_fill(self):
        client = _sales()
        client._pending = {"action": "create", "entity": "customer", "fields": {"first_name": "สมหญิง"}, "missing": ["phone"]}
        await say(client, "sales", "วิธีใช้")
        assert client._pending["entity"] == "customer"


# ---------------------------------------------------------------- A2 / A14

class TestA2CancelButtonBeatsTheAddressPrompt:
    async def test_the_bots_own_confirm_button_cancels_and_is_not_the_street(self):
        client = _customer(_ticket(1, status="open", service_address=None))
        _awaiting_address(client)
        reply = await say(client, "customer", "ยืนยันยกเลิกงาน T-2026-0001")
        assert client._tickets[0]["status"] == "cancelled"
        assert client._tickets[0].get("service_address") is None
        assert client._pending is None and "ยกเลิกงาน T-2026-0001" in reply.text

    async def test_a_bare_cancel_asks_to_confirm_and_names_the_button(self):
        client = _customer(_ticket(1, status="open"))
        _awaiting_address(client)
        reply = await say(client, "customer", "ยกเลิก")
        assert "ยืนยัน" in reply.text and client._tickets[0]["status"] == "open"


class TestA14LiveChatDoesNotSwallowTheReportFlow:
    async def test_cancel_confirm_during_a_conversation_cancels(self, monkeypatch):
        async def _push(oa, to, text, client=None):
            return ["m"]
        monkeypatch.setattr(live_chat, "push_text", _push)
        client = ChatFake(role="customer", permission_keys=[])
        client._tickets = [_ticket(1, status="open")]
        await say(client, "customer", "คุยกับร้าน")
        await say(client, "customer", "ยืนยันยกเลิกงาน T-2026-0001")
        assert client._tickets[0]["status"] == "cancelled"
        assert not [r for r in client.recorded if r[0] == "add_chat_message" and "ยกเลิก" in str(r)]

    async def test_the_fault_asked_for_by_the_bot_is_filed_not_chatted(self, monkeypatch):
        async def _push(oa, to, text, client=None):
            return ["m"]
        monkeypatch.setattr(live_chat, "push_text", _push)
        client = ChatFake(role="customer", permission_keys=[])
        client._warranties = [{"id": "w-1", "serial_number": "SN1", "product_name": "แอร์", "status": "active", "customer_chann_uid": ME}]
        await say(client, "customer", "คุยกับร้าน")
        reply = await say(client, "customer", "แจ้งซ่อม")
        assert "อาการ" in reply.text
        await say(client, "customer", "แอร์ไม่เย็น")
        assert _writes(client, "create_ticket")


# --------------------------------------------------------------------- A3

class TestA3QuestionsAreNotActions:
    async def test_a_question_about_the_queue_does_not_approve(self):
        client = _sales()
        reply = await say(client, "sales", "มีอะไรรออนุมัติไหม")
        assert not _writes(client, "act_on_approval_step")
        assert "รอ" in reply.text

    async def test_who_took_the_job_does_not_claim_it(self):
        client = _sales()
        client._tickets = [_ticket(1, status="open", accept_status="pending", assigned_to_ref=None)]
        await say(client, "sales", "ใครรับงาน T-2026-0001")
        assert not _writes(client, "claim_ticket")

    async def test_a_note_that_mentions_approval_is_not_an_approval(self):
        client = _sales()
        reply = await say(client, "sales", "บันทึกว่า C-2026-0001 ลูกค้ารออนุมัติงบจากผู้บริหาร")
        assert not _writes(client, "act_on_approval_step")
        assert "ไม่มีรายงานที่รอคุณอนุมัติ" not in reply.text

    def test_action_commands_are_start_anchored_or_coded(self):
        assert chat._action_command("อนุมัติ SR-2026-0001", chat.APPROVAL_APPROVE_TRIGGERS, chat.SERVICE_REPORT_CODE_RE)
        assert chat._action_command("ช่วยอนุมัติหน่อยครับ", chat.APPROVAL_APPROVE_TRIGGERS, chat.SERVICE_REPORT_CODE_RE)
        assert chat._action_command("SR-2026-0001 อนุมัติ", chat.APPROVAL_APPROVE_TRIGGERS, chat.SERVICE_REPORT_CODE_RE)
        assert not chat._action_command("มีอะไรรออนุมัติไหม", chat.APPROVAL_APPROVE_TRIGGERS, chat.SERVICE_REPORT_CODE_RE)
        assert not chat._action_command("ใครรับงาน T-2026-0001", chat.TICKET_CLAIM_TRIGGERS)
        assert not chat._action_command("บันทึกว่า D-2026-0001 หัวหน้ามอบหมายให้ติดตามต่อ", chat.TICKET_ASSIGN_TRIGGERS)


# --------------------------------------------------------------------- A4

class TestA4DoneOnTheSalesOA:
    async def test_done_is_an_acknowledgement_not_a_report_draft(self):
        client = _sales()
        client._tickets = [_ticket(1, status="in_progress")]
        await say(client, "sales", "done")
        assert not (client._pending and client._pending.get("entity") == "service_report")
        await say(client, "sales", "เสร็จแล้ว")
        assert not (client._pending and client._pending.get("entity") == "service_report")

    async def test_a_technician_still_closes_with_done(self):
        client = _tech()
        reply = await say(client, "technician", "เสร็จแล้ว")
        assert client._pending["entity"] == "service_report" and "พบปัญหา" in reply.text


# --------------------------------------------------------------------- A5

class TestA5StorefrontListDoesNotLockTheChat:
    def _pending(self):
        return {"action": "select", "entity": "storefront", "missing": [],
                "fields": {"options": [{"product_id": "P1", "product_name": "พัดลม", "license_id": "L", "company_name": "ร้าน"}]}}

    @pytest.mark.parametrize("text", ["แจ้งซ่อม", "วิธีใช้", "ยกเลิก", "แอร์ไม่เย็น มีน้ำหยด", "จบการสนทนา", "ช่างจะมากี่โมง"])
    async def test_anything_but_a_pick_releases_the_list(self, text):
        client = FakeDataClient(pending_intent=self._pending())
        reply = await maybe_handle_storefront(client, message=text, ctx=_ctx(primary_role="customer", oa="customer"), language="th")
        assert reply is None and client._pending is None

    async def test_a_short_pick_attempt_is_still_asked_for_its_number(self):
        client = FakeDataClient(pending_intent=self._pending())
        reply = await maybe_handle_storefront(client, message="เอาอันแรก", ctx=_ctx(primary_role="customer", oa="customer"), language="th")
        assert reply is not None and "หมายเลข" in reply.text

    async def test_a_tile_is_never_searched_as_a_product(self):
        client = FakeDataClient(storefront_results=[{"product_id": "P1", "product_name": "x", "license_id": "L", "company_name": "ร้าน"}])
        reply = await maybe_handle_storefront(client, message="แจ้งซ่อม", ctx=_ctx(primary_role="customer", oa="customer"), language="th")
        assert reply is None and not _writes(client, "storefront_search")


# --------------------------------------------------------------------- A6

class TestA6DefaultRolesCanUseEveryTile:
    SALES_TILES = ["งานวันนี้", "รายชื่อลูกค้า", "รายการรออนุมัติ", "นัดหมายทั้งหมด", "วิธีใช้", "รายการดีล", "รายการสินค้า", "ทีมช่าง", "ข้อมูลบริษัท", "สลับภาษา", "เปิดแดชบอร์ด"]

    @pytest.mark.parametrize("role", ["member", "cs"])
    @pytest.mark.parametrize("tile", SALES_TILES)
    async def test_no_permission_wall_for_a_shipped_role(self, role, tile):
        client = _sales(sorted(DEFAULT_ROLE_TEMPLATES[role]))
        client._pending = None
        reply = await say(client, "sales", tile, role=role)
        assert "⛔" not in reply.text and "setting.manage" not in reply.text and "ระบบไม่พร้อม" not in reply.text, (role, tile, reply.text)

    async def test_cs_today_lists_the_scheduled_visits(self):
        client = _sales(sorted(DEFAULT_ROLE_TEMPLATES["cs"]))
        client._tickets = [_ticket(1, scheduled_date=chat.local_today().isoformat())]
        reply = await say(client, "sales", "งานวันนี้", role="cs")
        assert "T-2026-0001" in reply.text


# --------------------------------------------------------------------- A7

class TestA7ContactTheShop:
    def _client(self):
        client = ChatFake(role="customer", permission_keys=[])
        client._company_profile = {
            "legal_name": None, "company_name": "ร้านเย็นสบาย", "tax_id": None,
            "company_address": "12 ถ.ลาดพร้าว", "company_phone": "021234567", "company_email": "shop@x.com", "vat_rate": None,
        }
        return client

    async def test_the_tile_shows_the_phone_and_email(self):
        client = FakeDataClient(permission_keys=[], role="customer", company_profile=self._client()._company_profile)
        reply = await say(client, "customer", "ติดต่อร้าน")
        assert "021234567" in reply.text and "shop@x.com" in reply.text
        assert client._pending["entity"] == "customer_contact"

    async def test_the_next_line_reaches_the_shop_and_opens_no_repair(self, monkeypatch):
        async def _push(oa, to, text, client=None):
            return ["m"]
        monkeypatch.setattr(live_chat, "push_text", _push)
        client = self._client()
        await say(client, "customer", "ติดต่อร้าน")
        reply = await say(client, "customer", "อยากสอบถามค่าบริการล้างแอร์")
        assert not _writes(client, "create_ticket")
        assert [r for r in client.recorded if r[0] == "add_chat_message" and "ล้างแอร์" in str(r)]
        assert "ร้าน" in reply.text


# --------------------------------------------------------------------- A9

class TestA9NotEveryLineIsAFault:
    @pytest.mark.parametrize("text", ["99/1 ถ.สุขุมวิท แขวงคลองตัน", "บ้านอยู่ซอยแตกต่าง 5", "ราคาล้างแอร์", "สอบถามค่าบริการล้างแอร์", "เช็คอิน", "ทีมช่าง", "นัดหมายทั้งหมด", "นัดหมาย", "สิทธิ์ของฉัน"])
    async def test_addresses_prices_and_staff_tiles_open_no_job(self, text):
        client = _customer()
        reply = await say(client, "customer", text)
        assert not _writes(client, "create_ticket"), (text, reply.text)
        assert reply.text

    async def test_negation_cancels_instead_of_opening_a_job(self):
        client = _customer(_ticket(1, status="assigned"))
        reply = await say(client, "customer", "ไม่เอาแล้วค่ะ ซ่อมเองได้แล้ว")
        assert not _writes(client, "create_ticket") and "ยกเลิกงาน T-2026-0001" in reply.text

    async def test_the_technician_cannot_come_that_day_asks_for_another(self):
        client = _customer(_ticket(1, status="assigned"))
        reply = await say(client, "customer", "ช่างมาพรุ่งนี้ไม่ได้นะ")
        assert not _writes(client, "create_ticket")
        assert client._pending["missing"] == ["schedule"] and "วันไหน" in reply.text
        await say(client, "customer", "วันศุกร์ บ่าย 2")
        assert client._tickets[0]["scheduled_time"] == "14:00:00" and client._pending is None

    def test_short_fault_markers_respect_place_names(self):
        assert not chat._looks_like_fault("99/1 ถ.สุขุมวิท แขวงคลองตัน")
        assert not chat._looks_like_fault("บ้านอยู่ซอยแตกต่าง 5")
        assert chat._looks_like_fault("ท่อตัน")
        assert chat._looks_like_fault("น้ำหยดจากแอร์")
        assert chat._looks_like_fault("ประตูเลื่อนไม่ได้")


# -------------------------------------------------------------------- A10

class TestA10HelpMenuDigits:
    @pytest.mark.parametrize("digit", ["0", "9", "99"])
    async def test_out_of_range_never_says_the_owner_has_no_permissions(self, digit):
        client = _sales()
        await say(client, "sales", "วิธีใช้")
        reply = await say(client, "sales", digit)
        assert "ยังไม่มีสิทธิ์ใช้งาน" not in reply.text

    @pytest.mark.parametrize("digit", ["2.", "ข้อ 2"])
    async def test_dotted_and_worded_numbers_pick_a_topic(self, digit):
        client = _sales()
        await say(client, "sales", "วิธีใช้")
        reply = await say(client, "sales", digit)
        assert "ยังไม่แน่ใจ" not in reply.text and reply.text


# -------------------------------------------------------------------- A12

class TestA12PhoneWhileTheAddressIsAsked:
    @pytest.mark.parametrize("phone", ["0812345678", "081-234-5678", "+66812345678"])
    async def test_a_phone_is_saved_as_the_phone_and_the_address_still_asked(self, phone):
        client = _customer(_ticket(1, status="open", service_address=None))
        _awaiting_address(client)
        reply = await say(client, "customer", phone)
        assert client._tickets[0]["customer_phone"].startswith(("0", "+66"))
        assert client._tickets[0].get("service_address") is None
        assert client._pending["missing"] == ["address"] and "ที่อยู่" in reply.text
        assert "หมายเลข" not in reply.text


# -------------------------------------------------------------------- A13

class TestA13UnlinkedCustomerTiles:
    TILES = ["สถานะการซ่อม", "ประกันของฉัน", "ติดต่อร้าน", "ประวัติการซื้อ", "สินค้าทั้งหมด", "ข้อมูลของฉัน", "สลับภาษา", "งานวันนี้", "วิธีใช้ 2", "สถานะการซ่อมครับ"]

    @pytest.mark.parametrize("tile", TILES)
    async def test_a_tile_is_answered_with_how_to_link_not_held_as_a_problem(self, tile):
        client = FakeDataClient(permission_keys=[], role="customer")
        ctx = ResolvedContext(chann_uid="CHN-X-1", primary_role="customer", display_name="x",
                              resolution=TenantResolution.NONE, memberships=[], oa="customer")
        reply = await registration.handle_registration(client, message=tile, ctx=ctx, audience="customer", language="th")
        text = reply if isinstance(reply, str) else reply.text
        assert "รับเรื่อง" not in text and client._pending is None
        assert text == registration._t(registration.WELCOME_CUSTOMER, "th")


# -------------------------------------------------------------------- A15

class TestA15WhoIsTold:
    async def test_a_cancellation_reaches_the_dispatcher_who_holds_ticket_assign(self, monkeypatch):
        client = FakeDataClient(permission_keys=["ticket.assign"], role="cs")
        client._tickets = [_ticket(1, assigned_to_ref="tech-1")]
        client._members = [
            {"id": "cs-1", "chann_uid": "CHN-CS", "role": "cs", "status": "active"},
            {"id": "tech-1", "chann_uid": "CHN-T", "role": "technician", "status": "active"},
        ]
        client._line_targets = {"CHN-CS": "U-cs", "CHN-T": "U-tech"}
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to)); return ["m"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        await chat._notify_ticket_change(client, LICENSE_ID, "t1", "ลูกค้ายกเลิกงาน T-2026-0001", "th")
        assert ("sales", "U-cs") in pushed and ("technician", "U-tech") in pushed

    async def test_a_member_role_salesperson_is_a_chat_agent(self):
        client = ChatFake(role="customer", permission_keys=[])
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-M", "role": "member", "status": "active"},
            {"id": "t-1", "chann_uid": "CHN-T", "role": "technician", "status": "active"},
        ]
        agents = await live_chat._agents(client, LICENSE_ID)
        assert [m["chann_uid"] for m in agents] == ["CHN-M"]


# -------------------------------------------------------------------- A16

class TestA16BareApproveMeansTheOneWaiting:
    async def test_the_single_pending_report_beats_the_one_last_looked_at(self, monkeypatch):
        client = _sales()
        client._last_entity_ref = {"entity_type": "service_report", "entity_id": "sr-1", "code": "SR-2026-0001"}
        step = {"id": "step-2", "entity_id": "sr-2"}
        report = {"id": "sr-2", "report_id": "SR-2026-0002", "ticket_id": "t1", "report_data": {}}
        async def _candidates(client, *, ctx, license_id):
            return [(step, report, {})]
        acted = []
        async def _act(client, *, license_id, step_id, approve, actor_chann_uid, reason=None, language="th"):
            acted.append(step_id); return {"report_status": "approved", "survey_sent": False}
        monkeypatch.setattr(chat, "_approval_candidates", _candidates)
        from chann_app.services import approval as approval_service
        monkeypatch.setattr(approval_service, "act", _act)
        reply = await say(client, "sales", "อนุมัติ")
        assert acted == ["step-2"] and "SR-2026-0002" in reply.text


# -------------------------------------------------------------------- A17

class TestA17ClosedAndCheckedInStates:
    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    async def test_closing_a_finished_job_is_not_told_to_check_in(self, status):
        client = _tech(_ticket(1, status=status))
        reply = await say(client, "technician", "ปิดงาน T-2026-0001")
        assert "ยังไม่ได้เช็คอิน" not in reply.text and "แล้ว" in reply.text
        assert not reply.quick_replies

    async def test_a_second_bare_check_in_says_already_checked_in(self):
        client = _tech(_ticket(1, status="in_progress"))
        reply = await say(client, "technician", "เช็คอิน")
        assert "T-2026-0001" in reply.text and "ไม่แน่ใจว่างานไหน" not in reply.text
        assert reply.quick_replies == [("ปิดงาน", "ปิดงาน T-2026-0001")]

    async def test_check_in_with_nothing_assigned_says_so_with_the_right_example(self):
        client = _tech(_ticket(1, assigned_to_ref="member-9"))
        reply = await say(client, "technician", "เช็คอิน")
        assert "ปิดงาน" not in reply.text and "เช็คอิน" in reply.text


# -------------------------------------------------------------------- A18

class TestA18TwoShopCustomer:
    async def test_a_fault_typed_before_choosing_the_shop_is_held_then_filed(self):
        client = _customer()
        ctx = _ctx(resolution=TenantResolution.MULTIPLE, primary_role="customer", oa="customer")
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=ctx, ai_client=_suggest())
        assert client._pending["entity"] == "pending_customer_message" and reply.quick_replies
        reply = await handle_chat_message(client, message="บริษัท ก", ctx=ctx, ai_client=_suggest())
        assert _writes(client, "set_active_tenant") and _writes(client, "create_ticket")
        assert "บริษัท ก" in reply.text and "T-2026-0001" in reply.text


# -------------------------------------------------------------------- A19

class TestA19ClaimRespectsAcceptance:
    async def test_a_bare_claim_does_not_offer_a_job_somebody_accepted(self):
        client = _tech(_ticket(1, assigned_to_ref="member-9", accept_status="accepted"))
        reply = await say(client, "technician", "รับงาน")
        assert not _writes(client, "claim_ticket") and "T-2026-0001" not in reply.text

    async def test_an_explicit_claim_of_a_taken_job_is_said_plainly(self):
        client = _tech(_ticket(1, assigned_to_ref="member-9", accept_status="accepted"))
        reply = await say(client, "technician", "รับงาน T-2026-0001")
        assert not _writes(client, "claim_ticket") and "รับไปแล้ว" in reply.text


# -------------------------------------------------------------------- A20

class TestA20DashboardTiles:
    @pytest.mark.parametrize("oa,text", [("sales", "เปิดแดชบอร์ด"), ("technician", "เปิดหน้าจอช่าง"), ("customer", "เปิดหน้าจอลูกค้า")])
    async def test_without_a_liff_id_the_tile_says_so_deterministically(self, oa, text, monkeypatch):
        monkeypatch.setattr(settings, "liff_sales_id", "")
        monkeypatch.setattr(settings, "liff_technician_id", "")
        monkeypatch.setattr(settings, "liff_customer_id", "")
        client = _customer() if oa == "customer" else (_tech() if oa == "technician" else _sales())
        reply = await say(client, oa, text)
        assert "ยังไม่ได้ตั้งค่า" in reply.text and reply.quick_replies
        assert not _writes(client, "create_ticket")

    async def test_with_a_liff_id_the_tile_links(self, monkeypatch):
        monkeypatch.setattr(settings, "liff_sales_id", "1234-abcd")
        reply = await say(_sales(), "sales", "เปิดแดชบอร์ด")
        assert "https://liff.line.me/1234-abcd" in reply.text


# -------------------------------------------------------------------- A21

class TestA21AppointmentsToday:
    async def test_appointments_today_lists_rather_than_creates(self):
        client = _sales()
        await say(client, "sales", "นัดหมายวันนี้ครับ")
        assert _writes(client, "due_follow_ups") and not _writes(client, "create_follow_up")

    async def test_what_is_on_today_is_the_day_not_the_help_menu(self):
        client = _sales()
        reply = await say(client, "sales", "วันนี้มีอะไรบ้าง")
        assert _writes(client, "due_follow_ups") and "วิธีใช้ LINE" not in reply.text


# -------------------------------------------------------------------- A22

class TestA22QuoteCommandsWithCodes:
    def _client(self):
        client = _sales(deals=[{"id": "d1", "deal_id": "D-2026-0001", "stage": "proposed", "contact_id": "c1", "owner_member_id": "owner-1"}],
                        quotes=[{"id": "q1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "sent"}])
        client._quote_lines = []
        return client

    async def test_a_whole_quote_discount_in_baht_is_a_discount_not_a_line_edit(self):
        client = self._client()
        reply = await say(client, "sales", "ลดราคา Q-2026-0001 500 บาท")
        assert _writes(client, "set_quote_terms") and "500" in reply.text

    async def test_the_customer_agreed_on_the_deal_accepts_its_quote(self):
        client = self._client()
        reply = await say(client, "sales", "ดีล D-2026-0001 ลูกค้าตกลงแล้ว")
        assert [r for r in client.recorded if r[0] == "set_quote_status" and r[3] == "accepted"]
        assert "Q-2026-0001" in reply.text


# -------------------------------------------------------------------- A23

class TestA23NamesWithParticles:
    async def test_delete_customer_with_a_glued_particle(self):
        client = _sales(customers=[{"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}])
        reply = await say(client, "sales", "ลบลูกค้าสมชายหน่อย")
        assert "ไม่พบ" not in reply.text and "ยืนยัน" in reply.text

    async def test_assign_to_a_name_with_a_particle(self):
        client = _sales()
        client._tickets = [_ticket(1, status="open", assigned_to_ref=None)]
        client._members.append({"id": "tech-1", "chann_uid": "CHN-T", "role": "technician", "status": "active"})
        client._profiles = {"CHN-T": {"first_name": "สมศักดิ์", "last_name": "ดี"}}
        client._line_targets["CHN-T"] = "U-t"
        await say(client, "sales", "มอบหมาย T-2026-0001 ให้สมศักดิ์ครับ")
        assert _writes(client, "assign_ticket")

    def test_strip_polite_tail(self):
        assert chat._strip_polite_tail("สมชายหน่อย") == "สมชาย"
        assert chat._strip_polite_tail("สมศักดิ์ครับ") == "สมศักดิ์"
        assert chat._strip_polite_tail("สมชาย") == "สมชาย"


# -------------------------------------------------------------------- A24

class TestA24NoPermissionWallForAHeldPermission:
    async def test_a_technician_who_holds_the_permission_is_told_what_to_type(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "k")
        monkeypatch.setattr(settings, "openrouter_model", "m")
        client = _tech()
        reply = await say(client, "technician", "ลูกค้าไม่อยู่บ้าน",
                          ai=_ai_json({"action": "update", "entity": "ticket", "fields": {"status": "customer_absent"}, "missing": []}))
        assert "⛔" not in reply.text and "ไม่มีสิทธิ์" not in reply.text
        assert "เช่น" in reply.text and "T-2026-0001" in reply.text


# -------------------------------------------------------------------- A25

class TestA25ExamplesPerOA:
    async def test_a_customer_never_sees_the_sales_reminder_example(self):
        client = _customer(_ticket(1, status="assigned"))
        reply = await say(client, "customer", "ขอเลื่อนนัดหน่อยค่ะ")
        assert "D-2026-0001" not in reply.text and "พรุ่งนี้" in reply.text

    def test_the_technician_fallback_names_technician_commands(self):
        text = chat.suggest_what_you_can_do(TECH_KEYS, [], "th", oa="technician")
        assert "งานของฉัน" in text and "ดูลูกค้า" not in text and "รายชื่อช่าง" not in text


# -------------------------------------------------------------------- A26

class TestA26SameAddress:
    async def test_the_usual_address_is_the_one_on_file(self):
        client = _customer(_ticket(1, status="open", service_address=None))
        client._profiles = {ME: {"first_name": "สมชาย", "address": "12 หมู่ 3 ต.บางพลี"}}
        _awaiting_address(client)
        reply = await say(client, "customer", "ที่อยู่เดิมครับ")
        assert client._tickets[0]["service_address"] == "12 หมู่ 3 ต.บางพลี"
        assert client._pending["missing"] == ["schedule"] and "12 หมู่ 3" in reply.text

    async def test_without_one_on_file_it_is_asked_for(self):
        client = _customer(_ticket(1, status="open", service_address=None))
        _awaiting_address(client)
        reply = await say(client, "customer", "ที่อยู่เดิมครับ")
        assert client._tickets[0].get("service_address") is None and "พิมพ์ที่อยู่" in reply.text


# ------------------------------------------------------------------- Low

class TestLowList:
    async def test_decline_asks_to_confirm_with_reasons_and_a_no_keeps_the_job(self):
        client = _tech(_ticket(1, accept_status="pending"))
        reply = await say(client, "technician", "ปฏิเสธงาน")
        assert "ใช่ไหม" in reply.text and any(send == "ยกเลิก" for _l, send in reply.quick_replies)
        assert not _writes(client, "reject_ticket")
        reply = await say(client, "technician", "ยกเลิก")
        assert not _writes(client, "reject_ticket") and "ยังอยู่กับคุณ" in reply.text
        await say(client, "technician", "ปฏิเสธงาน")
        await say(client, "technician", "ไม่ว่างวันนั้น")
        assert _writes(client, "reject_ticket")

    async def test_a_command_during_the_decline_prompt_wins(self):
        client = _tech(_ticket(1, accept_status="pending"), _ticket(2, assigned_to_ref=None, status="open", accept_status="pending"))
        await say(client, "technician", "ปฏิเสธงาน T-2026-0001")
        await say(client, "technician", "รับงาน T-2026-0002")
        assert _writes(client, "claim_ticket") and not _writes(client, "reject_ticket")

    def test_the_english_team_push_names_a_real_trigger(self):
        source = Path(chat.__file__).read_text(encoding="utf-8")
        assert "take job" not in source

    @pytest.mark.parametrize("text", ["รับงานT-2026-0001", "รับงาน T-๒๐๒๖-๐๐๐๑", "รับงาน T-2026-0001."])
    async def test_code_variants_are_read(self, text):
        client = _tech(_ticket(1, accept_status="pending"), _ticket(2, accept_status="pending"))
        await say(client, "technician", text)
        assert [r for r in client.recorded if r[0] == "claim_ticket" and r[2] == "t1"]

    def test_message_normalisation(self):
        assert chat._normalise_message("ข้อมูลลูกค้า C-2026-0001.") == "ข้อมูลลูกค้า C-2026-0001"
        assert chat._normalise_message("ดีลเกิน ๑๐๐๐๐") == "ดีลเกิน 10000"
        assert chat._normalise_message("ดีลเกิน 10k") == "ดีลเกิน 10000"
        assert chat._DEAL_VALUE_RE.search("ดีลเกิน 1 หมื่น").group(2) == "หมื่น"
        assert chat._thai_amount("1.5", "หมื่น") == 15000

    def test_a_price_with_baht_leaves_the_product_name_clean(self):
        m = chat._NEW_PRICE_RE.search("แก้ราคาพัดลมเหลือ 1,200 บาท")
        assert m.group(1) == "1,200" and "แก้ราคาพัดลมเหลือ 1,200 บาท".replace(m.group(0), " ").strip() == "แก้ราคาพัดลม"

    async def test_relinking_the_same_shop_is_said_kindly(self):
        client = FakeDataClient(permission_keys=[], role="customer")
        async def _link(*, chann_uid, company_code):
            raise DataTierError(409, "already linked")
        client.link_customer = _link
        ctx = ResolvedContext(chann_uid="CHN-X-1", primary_role="customer", display_name="x",
                              resolution=TenantResolution.NONE, memberships=[], oa="customer")
        text = await registration.handle_registration(client, message="ABCDEFGH", ctx=ctx, audience="customer", language="th")
        assert "ผูก" in str(text) and "ไม่พบรหัส" not in str(text)

    async def test_a_warranty_without_a_number_prints_a_dash_not_none(self):
        client = _customer()
        client._warranties[0]["warranty_number"] = None
        reply = await say(client, "customer", "ประกันของฉัน")
        assert "None" not in reply.text and "· -" in reply.text

    async def test_no_serial_with_nothing_held_is_answered_in_words(self):
        client = _customer()
        reply = await say(client, "customer", "ไม่มีหมายเลขเครื่อง")
        assert "ยังไม่แน่ใจ" not in reply.text and "แจ้งซ่อม" in reply.text and not _writes(client, "create_ticket")

    def test_every_help_example_is_a_trigger_or_a_coded_command(self):
        for _title, entries in chat.HELP_SECTIONS:
            for _key, cmd, _what in entries:
                if cmd.startswith("นัด"):
                    assert re.search(r"[CD]-\d{4}-\d{4}", cmd), cmd


# ------------------------------------------------------------------- E13

class TestE13DealStageNotifiesTheOwner:
    async def test_the_owner_hears_when_someone_else_moves_the_deal(self, monkeypatch):
        pushed = []
        async def _push(oa, to, text, client=None):
            pushed.append((oa, to, text)); return ["m"]
        monkeypatch.setattr(chat._notify_mod, "push_text", _push)
        client = _sales(deals=[{"id": "11111111-1111-1111-1111-111111111111", "deal_id": "D-2026-0001", "stage": "new",
                                "contact_id": "c1", "owner_member_id": "owner-1"}])
        await say(client, "sales", "ปิดสำเร็จ D-2026-0001")
        told = [r for r in client.recorded if r[0] == "create_notification" and r[3] == "deal_stage_changed"]
        assert told and told[0][2] == "CHN-OWN"
        assert any(to == "U-owner" for _oa, to, _t in pushed)


# ------------------------------------------------------- the tile table

class TestTileTableMatchesTheRichMenu:
    def test_every_message_tile_is_in_the_table(self):
        spec = importlib.util.spec_from_file_location("richmenu_generate", ROOT / "scripts" / "richmenu" / "generate.py")
        generate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generate)  # type: ignore[union-attr]
        for oa, pages in generate.TILES.items():
            for tiles in pages.values():
                for _th, _en, _icon, action in tiles:
                    if action.get("type") == "message":
                        assert action["text"] in chat.RICH_MENU_TILE_TEXTS[oa], (oa, action["text"])


# ------------------------------------------- the rich menu follows the language

class TestLanguageSwitchSyncsTheRichMenu:
    async def test_the_sync_hook_is_called_after_the_preference_is_saved(self, monkeypatch):
        calls = []

        async def _sync(client, *, oa, chann_uid, language):
            calls.append((oa, chann_uid, language))

        monkeypatch.setattr(chat, "sync_rich_menu", _sync)
        client = _tech()
        reply = await say(client, "technician", "สลับภาษา")
        assert calls == [("technician", ME, "en")] and "English" in reply.text

    async def test_without_the_module_the_switch_still_works(self, monkeypatch):
        monkeypatch.setattr(chat, "sync_rich_menu", None)
        reply = await say(_tech(), "technician", "สลับภาษา")
        assert "English" in reply.text
