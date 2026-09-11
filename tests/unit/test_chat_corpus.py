"""The chat corpus as a regression test (review of 6 Sep 2026, section B).

Every utterance in chat_corpus.py is played through the real router on its
OA, with the same fakes test_phase6_chat.py uses; the handler that fired
is recorded by wrapping every handler in chat.py, and the reply is
classified against SPEC — which handler (and which arguments) the intent
expects, or which text, or that the model is legitimately consulted.

Two things must hold:
  * no utterance classifies WORSE than chat_corpus_baseline.json says it
    did when the baseline was committed (RANK below);
  * the per-OA share of understood utterances stays above the floor the
    review set (customer 85 %, sales 85 %, technician 90 %).

Refresh the baseline after an intentional improvement:
    CHAT_CORPUS_BASELINE_WRITE=1 pytest tests/unit/test_chat_corpus.py
and read the diff before committing it — a line that got worse is a
regression, not a baseline change.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chat_corpus  # noqa: E402
import test_phase6_chat as T  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "chat_corpus_baseline.json"
LICENSE = T.LICENSE_ID
ME = "CHN-S-000001"
TCODE = re.compile(r"\b[TDQC]-\d{4}-\d{4}\b|\bSR-\d{4}-\d{4}\b", re.I)
DATE_WORDS = ("พรุ่ง", "มะรืน", "วัน", "เช้า", "บ่าย", "เย็น", "โมง", "ทุ่ม", "ศุกร์", "เสาร์", "จันทร์", "อังคาร", "พุธ",
              "พฤหัส", "อาทิตย์", "/", "กันยา", "ก.ย.", ".00", ".30")
FLOOR = {"customer": 85, "sales": 85, "technician": 90}

# ------------------------------------------------------------------ handler trace
CALLS: list[str] = []
LOGGED: list[str] = []
INTERESTING = ("search_term", "for_customer", "open_only", "mine", "team_only", "cancel", "delete", "remove",
               "days", "approve", "kind", "target", "allow_reissue", "trigger", "new_issue")
WRAP_PREFIXES = ("_handle_", "_maybe_", "_resolve_", "_help_", "_storefront_", "_switch_", "_tenant_chooser",
                 "_customer_fallback", "_appointment_net", "_ask_")
WRAP_NAMES = {"suggest_what_you_can_do", "ask_for_missing", "greet", "capability_detail", "permission_summary",
              "_pending_execution_reply"}
NEVER_WRAP = {"handle_chat_message", "_route_chat_message"}


def _sig(name: str, kwargs: dict) -> str:
    parts = []
    for k in INTERESTING:
        if k in kwargs and kwargs[k] not in (None, False, ""):
            parts.append(f"{k}={kwargs[k]}")
    if name == "_handle_customer_amend" and "cancel" in kwargs:
        parts.append(f"cancel={kwargs['cancel']}")
    if name == "_handle_approval_act" and "approve" in kwargs:
        parts = [f"approve={kwargs['approve']}"]
    return name + ("|" + ",".join(sorted(set(parts))) if parts else "")


def _wrap(name, fn):
    maybe = name.startswith("_maybe_") or name in ("_help_step_from_pending", "_appointment_net")
    if inspect.iscoroutinefunction(fn):
        async def w(*a, **k):
            r = await fn(*a, **k)
            if not maybe or r is not None:
                CALLS.append(_sig(name, k))
            return r
    else:
        def w(*a, **k):
            r = fn(*a, **k)
            if not maybe or r is not None:
                CALLS.append(_sig(name, k))
            return r
    w.__name__ = name
    w.__wrapped__ = fn
    return w


class _Hook(logging.Handler):
    def emit(self, record):
        if record.name == "chann_app.services.notify":
            return  # no LINE token in a unit test: the push failing is not the router failing
        if record.exc_info or record.levelno >= logging.ERROR:
            LOGGED.append(f"{record.levelname} {record.name}: {record.getMessage()[:120]}")


class AiProbe:
    """The model, answering "suggest" unless the entry crafted a reply."""

    def __init__(self, crafted=None):
        self.calls = 0
        self.crafted = crafted
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        self.calls += 1
        body = self.crafted or {"action": "suggest", "entity": None, "fields": {}, "missing": []}
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(body, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x"})


# ------------------------------------------------------------------ fixtures
SALES_KEYS = ["customer.create", "customer.read", "customer.update", "customer.archive", "deal.create", "deal.read",
              "deal.update", "quote.create", "quote.read", "quote.update", "product.manage", "followup.create",
              "followup.read", "followup.update", "note.create", "note.read", "ticket.read", "ticket.create",
              "ticket.update", "ticket.assign", "service_report.read", "warranty.read", "warranty.create", "team.manage",
              "member.manage", "setting.manage", "approval.view", "approval.approve", "approval.reject", "approval.manage",
              "view_reports"]
TECH_KEYS = ["ticket.read", "ticket.update", "ticket.close", "service_report.create", "service_report.read", "warranty.read"]


async def make_sales():
    c = T.FakeDataClient(permission_keys=SALES_KEYS)
    c._products = [{"id": "p1", "product_id": "AC12", "product_name": "แอร์ 12000 BTU", "unit_price": "15900.00"}]
    c._members = [{"id": "member-1", "chann_uid": ME, "role": "sales", "status": "active", "first_name": "พนักงาน"},
                  {"id": "m-tech", "chann_uid": "CHN-T-000001", "role": "technician", "status": "active", "first_name": "สมศักดิ์"}]
    cust = await c.create_customer("L1", {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
    await c.create_deal("L1", {"contact_id": cust["id"]})
    c._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open", "accept_status": "pending",
                   "owner_member_id": "member-1", "customer_name": "สมชาย", "customer_phone": "0812345678",
                   "service_address": "99/1", "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08",
                   "scheduled_time": "10:00"}]
    c._reports = [{"id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1", "status": "submitted",
                   "technician_member_id": "m-tech", "report_data": {"found_issue": "คอมรั่ว", "work_done": "เปลี่ยนคอม"}}]
    await c.open_approval_steps(LICENSE, "sr-1")
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "warranty_end": "2027-01-01", "customer_name": "สมชาย"}]
    return c


async def make_tech():
    c = T.FakeDataClient(permission_keys=TECH_KEYS, role="technician")
    c._tickets = [
        {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned", "accept_status": "accepted",
         "assigned_to_ref": "member-1", "customer_name": "สมชาย", "customer_phone": "0812345678",
         "service_address": "99/1 สุขุมวิท", "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08",
         "scheduled_time": "10:00"},
        {"id": "t2", "ticket_number": "T-2026-0002", "status": "open", "accept_status": "pending", "visibility": "public",
         "customer_name": "สมหญิง", "service_address": "12 ลาดพร้าว", "issue_description": "ตู้เย็นไม่เย็น",
         "scheduled_date": "2026-09-09", "scheduled_time": "13:00"}]
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "warranty_end": "2027-01-01"}]
    c._reports = [{"id": "sr-0", "report_id": "SR-2026-0000", "ticket_id": "t1", "status": "approved",
                   "technician_member_id": "member-1", "report_data": {}}]
    return c


async def make_customer():
    c = ChatFake(permission_keys=[])
    c._warranties = [{"id": "w-1", "serial_number": "SN12345678", "product_name": "แอร์", "status": "active",
                      "customer_chann_uid": ME, "warranty_end": "2027-01-01"}]
    c._tickets = [{"id": "t0", "ticket_number": "T-2026-0001", "status": "assigned", "customer_chann_uid": ME,
                   "customer_name": "สมชาย", "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
                   "scheduled_date": "2026-09-08", "scheduled_time": "10:00", "assigned_to_name": "สมศักดิ์"}]
    c._profiles = {ME: {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678", "address": "99/1"}}
    return c


FIXTURES = {"sales": make_sales, "technician": make_tech, "customer": make_customer}

# ------------------------------------------------------------------ expectations
SMALL = [chat.SMALL_TALK_REPLY["th"], chat.SMALL_TALK_REPLY["en"]]
STATUS_TXT = ["งาน T-", "T-2026", "ยังไม่มีงานแจ้งซ่อม", "คุณมีงานเปิดอยู่", "Your job", "No open jobs", "You have"]
FAULT_TXT = ["รับแจ้งแล้ว", "รับเรื่อง", "เครื่องไหนครับ", "Logged as", "Noted:", "Which machine"]
ASK_ISSUE = [chat.REPORT_ASK_ISSUE["th"][:20], "What is wrong"]
PRODUCT_TXT = ["สินค้าและราคา", "Products and prices"]
SITUATION = "_handle_technician_situation"
A = lambda **k: k  # noqa: E731
SPEC = {
    # customer
    "c.greet": [A(text=["สวัสดี", "Hello", "hello"], noai=True)],
    "c.small": [A(text=SMALL)],
    "c.help": [A(h=["_help_reply"])],
    "c.fault": [A(h=["_handle_customer_report"], text=FAULT_TXT + ASK_ISSUE, forbid=["_customer_fallback"])],
    "c.fault_question": [A(h=["_handle_customer_report"], text=FAULT_TXT + STATUS_TXT, forbid=["_customer_fallback"]), A(h=["_handle_customer_chat_start"])],
    "c.product_or_fault": [A(h=["_handle_customer_report"], text=FAULT_TXT + ASK_ISSUE, forbid=["_customer_fallback"]), A(h=["storefront", "_storefront_browse_reply"]), A(text=PRODUCT_TXT)],
    "c.urgent_vague": [A(h=["_handle_customer_report"], text=FAULT_TXT + ASK_ISSUE, forbid=["_customer_fallback"]), A(h=["_handle_customer_chat_start"])],
    "c.status": [A(h=["_handle_customer_report"], text=STATUS_TXT, forbid=["_customer_fallback"])],
    "c.warranty": [A(h=["_handle_warranty_mine"]), A(h=["_handle_serial_enquiry"])],
    "c.register": [A(h=["_handle_warranty_register"])],
    "c.help_register": [A(h=["_help_reply"]), A(h=["_handle_warranty_register"])],
    "c.contact": [A(h=["_handle_customer_contact"])],
    # The handler is the same; what it does inside changed on 11 ก.ย. 2569.
    # A customer may not move a visit — only someone with the permission, in
    # the Sales OA, may — so this road records the request against the job
    # and pushes it to the shop and the assigned technician. The intent name
    # is kept so the baseline does not churn; what it MEANS is "the request
    # was understood and forwarded", not "the visit moved".
    "c.resched": [A(h=["_handle_customer_amend|cancel=False"])],
    "c.cancel": [A(h=["_handle_customer_amend|cancel=True"])],
    "c.correction": [A(h=["_handle_customer_amend"]), A(h=["_handle_customer_report"], text=STATUS_TXT, forbid=["_customer_fallback"]), A(h=["_handle_customer_chat_start"])],
    "c.profile_edit": [A(ai=True, h=["_handle_profile_intent"])],
    "c.chat": [A(h=["_handle_customer_chat_start"])],
    "c.complaint": [A(h=["_handle_customer_chat_start"]), A(h=["_handle_customer_report"], text=STATUS_TXT, forbid=["_customer_fallback"])],
    "c.complaint_fault": [A(h=["_handle_customer_chat_start"]), A(h=["_handle_customer_report"], text=FAULT_TXT + STATUS_TXT, forbid=["_customer_fallback"])],
    "c.price": [A(text=PRODUCT_TXT), A(h=["storefront", "_storefront_browse_reply"]), A(h=["_handle_customer_chat_start"])],
    "c.product_list": [A(h=["storefront", "_storefront_browse_reply"]), A(text=PRODUCT_TXT, partial=True)],
    "c.search": [A(h=["storefront"])],
    "c.payment": [A(h=["_handle_customer_chat_start"]), A(text=["คุยกับร้าน", "talk to the shop"], partial=True)],
    "c.profile": [A(h=["_handle_customer_profile_view"])],
    "c.orders": [A(h=["_handle_orders_mine"])],
    "c.lang": [A(h=["_switch_language"])],
    "c.address_answer": [A(text=["บันทึกที่อยู่แล้ว", "Address saved"])],
    # "ที่อยู่เดิม" is the address on file, read back (A26) — not those words saved.
    "c.address_same": [A(text=["บันทึกที่อยู่แล้ว", "Address saved", "ใช้ที่อยู่เดิม", "Using your usual address"])],
    "c.date_answer": [A(text=["นัดวันที่", "Booked for"])],
    # "any day" books nothing the customer did not say: the shop picks, and is told.
    "c.date_any": [A(text=["นัดวันที่", "Booked for", "สะดวกทุกวัน", "any day suits"])],
    # sales
    "s.greet": [A(text=["สวัสดี", "Hello"], noai=True)],
    "s.small": [A(text=SMALL)],
    "s.help": [A(h=["_help_reply"])],
    "s.help_or_create": [A(h=["_help_reply"]), A(h=["_handle_bare_create_prompt"]), A(ai=True)],
    "s.capability": [A(h=["_maybe_capability_question"]), A(h=["_help_reply"], partial=True)],
    "s.customer_list": [A(h=["_handle_customer_list$"])],
    "s.customer_search": [A(h=["_handle_customer_list|search_term="]), A(ai=True, h=["_handle_customer_detail", "_handle_customer_list"]), A(ai=True, partial=True)],
    "s.customer_detail": [A(h=["_handle_customer_detail"])],
    "s.customer_context": [A(h=["_handle_customer_detail"]), A(ai=True, partial=True)],
    "s.customer_create": [A(ai=True, h=["_handle_customer_intent"])],
    "s.customer_create_bare": [A(h=["_handle_bare_create_prompt"]), A(ai=True, h=["ask_for_missing"]), A(ai=True, partial=True)],
    "s.customer_update": [A(ai=True, h=["_handle_customer_intent"])],
    "s.lead_archive": [A(h=["_handle_lead_archive_request"]), A(ai=True, partial=True)],
    "s.deal_list": [A(h=["_handle_deal_list$"]), A(ai=True, h=["_handle_sales_summary"])],
    "s.deal_open": [A(h=["_handle_deal_list|open_only=True"]), A(ai=True, h=["_handle_deal_list", "_handle_sales_summary"], partial=True)],
    "s.deal_for_customer": [A(h=["_handle_deal_list|for_customer="])],
    "s.deal_detail": [A(h=["_handle_deal_detail"])],
    "s.deal_create": [A(h=["_handle_deal_create_direct"]), A(ai=True, h=["_handle_deal_intent"])],
    "s.deal_create_ai": [A(ai=True)],
    "s.deal_create_bare": [A(h=["_handle_bare_create_prompt", "_handle_deal_create_direct"]), A(ai=True, h=["ask_for_missing"]), A(ai=True, partial=True)],
    "s.deal_stage": [A(h=["_handle_deal_stage_command"])],
    "s.sales_summary": [A(h=["_handle_sales_summary", "_handle_ai_report"])],
    # Phase 17 ตาราง/กราฟ: the picture, or the report engine answering the
    # same question with one (the reply carries an image URL when a
    # document store is configured; there is none in a unit test).
    "s.sales_chart": [A(h=["_handle_sales_chart", "_handle_ai_report"])],
    "s.deal_value": [A(h=["_handle_deal_query|kind=over_value"])],
    "s.deal_by_amount": [A(h=["_handle_deal_query"]), A(ai=True, partial=True)],
    "s.deal_query": [A(h=["_handle_deal_query", "_handle_ai_report"]), A(ai=True, h=["_handle_deal_query"]), A(ai=True, partial=True)],
    "s.quote_create": [A(h=["_handle_quote_create_direct", "_handle_quote_issue"]), A(ai=True, h=["_handle_quote_intent"]), A(ai=True, h=["ask_for_missing"], partial=True)],
    "s.quote_list": [A(h=["_handle_quote_list"])],
    "s.quote_list_or_create": [A(h=["_handle_quote_list", "_handle_quote_create_direct", "_handle_bare_create_prompt"])],
    "s.quote_discount": [A(h=["_handle_quote_discount"]), A(ai=True, h=["_handle_quote_intent", "ask_for_missing"], partial=True)],
    "s.quote_accept": [A(h=["_handle_quote_status|target=accepted"]), A(ai=True, h=["_handle_quote_intent", "ask_for_missing"], partial=True)],
    "s.quote_send": [A(h=["_handle_quote_issue"]), A(ai=True, partial=True)],
    "s.note_create": [A(h=["_handle_note_create"]), A(ai=True, h=["_handle_note_intent"], partial=True)],
    "s.note_ai": [A(ai=True)],
    "s.note_list": [A(h=["_handle_note_list"]), A(ai=True, h=["_handle_customer_detail"], partial=True)],
    "s.reminder_create": [A(h=["_handle_reminder_create"]), A(ai=True, h=["_handle_ai_understood_intent"], partial=True)],
    "s.reminder_list": [A(h=["_handle_reminder_list", "_handle_work_list"])],
    "s.reminder_move": [A(h=["_handle_reminder_move"])],
    "s.reminder_cancel": [A(h=["_handle_reminder_cancel"])],
    "s.work_today": [A(h=["_handle_work_list|days=1", "_handle_reminder_list"])],
    "s.work_upcoming": [A(h=["_handle_work_list|days=7", "_handle_reminder_list", "_handle_deal_list|open_only=True"]), A(h=["_handle_ticket_list"], partial=True)],
    "s.ticket_list": [A(h=["_handle_ticket_list"])],
    "s.ticket_assign": [A(h=["_handle_ticket_assign"]), A(ai=True, h=["ask_for_missing", "_handle_ai_understood_intent"], partial=True)],
    "s.ticket_detail": [A(h=["_handle_ticket_detail"])],
    "s.ticket_create": [A(ai=True, h=["_handle_staff_ticket_create"])],
    "s.technician_list": [A(h=["_handle_technician_list"]), A(ai=True, h=["_handle_team_intent"], partial=True)],
    "s.approval_list": [A(h=["_handle_approval_list"])],
    "s.approve": [A(h=["_handle_approval_act|approve=True"]), A(ai=True, h=["_handle_approval_list"], partial=True)],
    "s.reject": [A(h=["_handle_approval_act|approve=False"])],
    "s.shop_info": [A(h=["_handle_shop_info"]), A(ai=True, h=["_handle_company_profile_view"], partial=True)],
    # _handle_technician_invite_request now delegates to _handle_invite_request,
    # which issues the sales-side code too (10 ก.ย. 2569).
    "s.invite": [A(h=["_handle_invite_request", "_handle_technician_invite_request"])],
    # "ช่างใหม่จะเข้าร้านยังไง" is a question about HOW, and it used to
    # answer by issuing a real invite code. It now explains and writes
    # nothing.
    "s.invite_howto": [A(text=["ขอรหัสเชิญช่าง"], noai=True)],
    "s.company_view": [A(h=["_handle_company_profile_view"])],
    "s.company_update": [A(h=["_handle_company_profile_command"])],
    "s.settings": [A(h=["_help_reply", "_handle_company_profile_view", "_handle_shop_info"], partial=True)],
    "s.team_list": [A(h=["_maybe_handle_teams"])],
    "s.team_create": [A(h=["_maybe_handle_teams"]), A(ai=True, partial=True)],
    "s.warranty_register": [A(h=["_handle_warranty_register"])],
    "s.warranty_book": [A(h=["_handle_warranty_book"])],
    "s.serial_enquiry": [A(h=["_handle_serial_enquiry"])],
    "s.product_list": [A(h=["_handle_product_list"])],
    "s.product_create": [A(ai=True)],
    "s.product_price": [A(h=["_handle_product_list"])],
    # line items, the way the owner typed them (8 Sep 2026)
    "s.line_add": [A(h=["_handle_line_item_command", "_handle_deal_product_add", "_hold_new_line"], forbid=["_handle_line_edit"])],
    "s.line_more": [A(h=["_handle_line_item_command"], text=["รวมเป็น", "Now"])],
    "s.line_more_en": [A(h=["_handle_line_item_command"], text=["รวมเป็น", "Now"]), A(h=["_handle_line_item_command"], partial=True)],
    "s.line_fewer": [A(h=["_handle_line_item_command"], text=["เหลือ", "Now", "off"])],
    "s.line_set": [A(h=["_handle_line_edit"], text=["แก้ พัดลม เป็น 3", "Updated"])],
    "s.line_price": [A(h=["_handle_line_edit"], text=["2,000.00"])],
    "s.line_delete": [A(h=["_handle_line_edit|remove=True"], text=["ลบ ", "Removed"])],
    "s.line_new_answer": [A(h=["_resolve_line_item_add"])],
    "s.deal_latest": [A(h=["_handle_latest_deal", "_handle_deal_detail"], text=["D-2026-0001 ·"])],
    "s.deal_latest_none": [A(h=["_handle_latest_deal", "_handle_deal_list"])],
    "s.customer_latest": [A(h=["_handle_latest_customer", "_handle_customer_detail"])],
    "s.product_search": [A(h=["_handle_product_list"], text=["แอร์ 12000 BTU"])],
    "s.interest": [A(h=["_handle_sales_interest"], text=["ต้องการสร้างดีลสำหรับ สมชาย"])],
    "s.profile": [A(h=["_handle_staff_profile_view"])],
    "s.lang": [A(h=["_switch_language"])],
    "s.correction": [A(ai=True, partial=True)],
    # "ยกเลิกอันเมื่อกี้": the last reminder is cancelled when one is in context, else asked which.
    "s.undo": [A(h=["_handle_reminder_cancel"], partial=True), A(ai=True, partial=True)],
    "s.offtopic": [A(ai=True), A(h=["_help_reply"], partial=True)],
    "s.report_ai": [A(h=["_handle_ai_report"]), A(ai=True, partial=True)],
    # Nothing behind this pair yet — the reply must SAY so and name the
    # dashboard page, never pretend and never come back a permission list.
    "s.no_handler": [A(ai=True, h=["_pending_execution_reply"], text=["ในแดชบอร์ด", "in the dashboard"])],
    # technician
    "t.greet": [A(text=["สวัสดี", "Hello"], noai=True)],
    "t.small": [A(text=SMALL)],
    "t.help": [A(h=["_help_reply"])],
    "t.capability": [A(h=["_maybe_capability_question"]), A(h=["_help_reply"], partial=True)],
    "t.mine": [A(h=["_handle_ticket_list|mine=True"]), A(ai=True, h=["_handle_ticket_list"], partial=True)],
    "t.mine_or_open": [A(h=["_handle_ticket_list|mine=True", "_handle_ticket_list|open_only=True"])],
    "t.open": [A(h=["_handle_ticket_list|open_only=True"])],
    "t.team": [A(h=["_handle_ticket_list|team_only=True"]), A(ai=True, h=["_handle_ticket_list"], partial=True)],
    "t.all": [A(h=["_handle_ticket_list$"]), A(ai=True, h=["_handle_ticket_list"], partial=True)],
    "t.detail": [A(h=["_handle_ticket_detail"])],
    "t.current": [A(h=["_handle_ticket_detail"]), A(ai=True, h=["_handle_ticket_list|mine=True"], partial=True)],
    "t.claim": [A(h=["_handle_ticket_claim"])],
    "t.reject": [A(h=["_handle_ticket_reject"])],
    "t.checkin": [A(h=["_handle_check_in"])],
    "t.checkout": [A(h=["_handle_check_out"])],
    "t.checkout_no_checkin": [A(h=["_handle_check_out"])],
    "t.report_answer": [A(h=["_handle_check_out"])],
    "t.report_cancel": [A(h=["_handle_check_out"])],
    "t.correction": [A(h=["_handle_check_out", "_handle_ticket_detail"])],
    # B12: the road and the doorstep have handlers now; the model is the fallback.
    "t.on_my_way": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.late": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.reschedule": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.not_home": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.need_parts": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.cannot_finish": [A(h=[SITUATION]), A(ai=True, partial=True)],
    "t.photo": [A(ai=True, partial=True)], "t.status_free": [A(ai=True, partial=True)],
    "t.checkout_or_report": [A(h=["_handle_check_out", "_handle_report_list"])],
    "t.report_list": [A(h=["_handle_report_list"])],
    "t.report_pdf": [A(h=["_handle_report_pdf"])],
    "t.serial": [A(h=["_handle_serial_enquiry"])],
    "t.serial_no_sn": [A(h=["_handle_serial_enquiry"]), A(ai=True, h=["ask_for_missing"], partial=True)],
    "t.profile": [A(h=["_handle_staff_profile_view"])],
    "t.profile_edit": [A(ai=True, h=["_handle_profile_intent"])],
    "t.lang": [A(h=["_switch_language"])],
    "t.switch_shop": [A(text=["ร้านเดียว", "only"]), A(h=["_tenant_chooser"])],
    "t.ai_ticket_update": [A(h=[SITUATION]), A(ai=True, h=["_handle_ai_understood_intent"])],
    "t.ai_report_create": [A(ai=True, h=["_handle_ai_understood_intent", "_handle_check_out"])],
    "t.ai_status_update": [A(h=[SITUATION]), A(ai=True, h=["_handle_ai_understood_intent"])],
}

BAD = OrderedDict([
    ("exception", ("EXCEPTION",)),
    ("permission", ("ยังไม่มีสิทธิ์", "ไม่มีสิทธิ์", "permission", "not allowed", "Not yet allowed")),
    ("not_feature", ("ยังไม่มีฟังก์ชัน", "ยังไม่รองรับ", "ไม่มีฟีเจอร์", "not a feature", "no such feature", "ยังไม่มีคำสั่งนี้")),
    ("ai_down", ("ระบบไม่พร้อมใช้งาน", "temporarily unavailable")),
    ("generic_error", ("ขออภัย", "Sorry")),
    ("not_sure", ("ยังไม่แน่ใจว่าต้องการอะไร", "ยังไม่เข้าใจ", "ไม่เข้าใจคำขอ", "Not sure what you need", "not sure what", "didn't understand", "did not understand")),
    ("not_found", ("ไม่พบ", "No customer matching", "No deal", "No ticket")),
])
RANK = {"exception": 0, "exception_logged": 0, "permission": 1, "not_feature": 1, "generic_error": 1, "asked_given": 1,
        "not_sure": 2, "wrong_handler": 2, "ai_fallback": 3, "partial": 4, "ai_expected": 5, "correct": 5}
UNDERSTOOD = {"correct", "partial", "ai_expected"}


def _matched(calls, spec_h):
    for s in spec_h:
        if s.endswith("$"):
            if s[:-1] in calls:
                return True
        elif any(c.startswith(s) for c in calls):
            return True
    return False


def kind_of(text):
    for label, needles in BAD.items():
        if any(n in text for n in needles):
            return label
    return "ok"


def asked_given(message, text):
    """The bot asked for something the message already carried."""
    m = message or ""
    if any(w in text for w in ("ระบุเลขงาน", "พิมพ์เลขงาน", "ปฏิเสธงานไหน", "Include the ticket number", "ไม่แน่ใจว่างานไหน")) and TCODE.search(m):
        return True
    if "ไม่เข้าใจวันที่" in text and any(w in m for w in DATE_WORDS):
        return True
    if "ระบุด้วยว่ามอบหมายให้ใคร" in text and "ให้" in m:
        return True
    # A report verb is not a symptom: "แจ้งซอม" carries none, so asking is right.
    if "อาการเสียเป็นอย่างไร" in text and chat._looks_like_fault(re.sub(r"แจ้งซ่อม|แจ้งซอม|แจ้งซ้อม|แจ้งเสีย|แจ้งปัญหา|ซ่อม|ซอม|ซ้อม|แปง", "", m)):
        return True
    if "พิมพ์ชื่อที่ต้องการค้นหา" in text and len(chat._normalise(m)) > 8:
        return True
    if "ระบุรหัสด้วยว่าเตือน" in text and (TCODE.search(m) or "สมชาย" in m):
        return True
    return False


def classify(entry, calls, text, probe_calls, logged):
    tags = set(entry["tags"]); ai_ok = "ai" in tags or "offtopic" in tags
    if text.startswith("EXCEPTION"):
        return "exception", "exception"
    k = kind_of(text)
    for alt in SPEC.get(entry["intent"], []):
        if alt.get("noai") and probe_calls:
            continue
        if alt.get("ai"):
            if not probe_calls:
                continue
            if alt.get("h") and not _matched(calls, alt["h"]):
                continue
        else:
            if alt.get("h") and not _matched(calls, alt["h"]):
                continue
        if alt.get("forbid") and _matched(calls, alt["forbid"]):
            continue
        if alt.get("text") and not any(t in text for t in alt["text"]):
            continue
        if alt.get("partial"):
            return "partial", k
        return "correct", k
    if logged:
        return "exception_logged", k
    if probe_calls:
        return ("ai_expected" if ai_ok else "ai_fallback"), k
    if asked_given(entry["text"], text):
        return "asked_given", k
    if k in ("permission", "not_feature", "generic_error", "not_sure", "ai_down"):
        return k, k
    return "wrong_handler", k


# ------------------------------------------------------------------ play
async def _send(client, message, ctx, probe, language):
    if ctx.oa == "customer":
        r = await T.maybe_handle_storefront(client, message=message, ctx=ctx, language=language)
        if r is not None:
            CALLS.append("storefront")
            return r
    return await T.handle_chat_message(client, message=message, ctx=ctx, ai_client=probe.client, language=language)


async def play(entry, language="th"):
    oa = entry["oa"]
    client = await FIXTURES[oa]()
    ctx = T._ctx(oa=oa, primary_role="technician" if oa == "technician" else ("customer" if oa == "customer" else "sales"))
    for pre in entry.get("pre", []):
        CALLS.clear(); LOGGED.clear()
        await _send(client, pre, ctx, AiProbe(), language)
    CALLS.clear(); LOGGED.clear()
    probe = AiProbe(entry.get("ai"))
    try:
        r = await _send(client, entry["text"], ctx, probe, language)
        text = r.text or ""
    except Exception as exc:  # noqa: BLE001
        text = f"EXCEPTION {type(exc).__name__}: {exc}"
    calls = list(CALLS); logged = list(LOGGED)
    cls, kind = classify(entry, calls, text, probe.calls, logged)
    return {"oa": oa, "text": entry["text"], "intent": entry["intent"], "tags": entry["tags"], "handlers": calls,
            "logged": logged, "class": cls, "kind": kind, "lines": text.count("\n") + 1,
            "first_line": text.splitlines()[0] if text else ""}


async def _run_all():
    settings.openrouter_api_key = settings.openrouter_api_key or "k"
    settings.openrouter_model = settings.openrouter_model or "m"
    originals = {}
    for name, fn in list(vars(chat).items()):
        if callable(fn) and name not in NEVER_WRAP and (name.startswith(WRAP_PREFIXES) or name in WRAP_NAMES):
            originals[name] = fn
            setattr(chat, name, _wrap(name, fn))
    hook = _Hook()
    logging.getLogger().addHandler(hook)
    try:
        return [await play(e) for e in chat_corpus.ALL]
    finally:
        logging.getLogger().removeHandler(hook)
        for name, fn in originals.items():
            setattr(chat, name, fn)


@pytest.fixture(scope="module")
def results():
    rows = asyncio.run(_run_all())
    return {(r["oa"], r["text"]): r for r in rows}


def _by_oa(results):
    tot = defaultdict(lambda: [0, 0])
    for r in results.values():
        tot[r["oa"]][0] += 1
        tot[r["oa"]][1] += r["class"] in UNDERSTOOD
    return {oa: (ok, n) for oa, (n, ok) in tot.items()}


def test_no_exceptions_or_logged_errors(results):
    bad = [(k, r["logged"] or r["first_line"]) for k, r in results.items() if r["class"] in ("exception", "exception_logged")]
    assert not bad, bad


def test_accuracy_floor_per_oa(results):
    shares = {oa: round(100 * ok / n) for oa, (ok, n) in _by_oa(results).items()}
    below = {oa: share for oa, share in shares.items() if share < FLOOR[oa]}
    assert not below, f"understood share below the floor: {below} (all: {shares})"


def test_no_regression_against_baseline(results):
    current = {f"{oa}\t{t}": r["class"] for (oa, t), r in results.items()}
    if os.environ.get("CHAT_CORPUS_BASELINE_WRITE") or not BASELINE.exists():
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        pytest.skip("baseline written")
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    worse = [(k, base[k], c) for k, c in current.items() if k in base and RANK[c] < RANK[base[k]]]
    assert not worse, "\n".join(f"{k!r}: {b} -> {n}" for k, b, n in worse)
    missing = [k for k in current if k not in base]
    assert not missing, f"utterances not in the baseline — refresh it: {missing[:5]}"
