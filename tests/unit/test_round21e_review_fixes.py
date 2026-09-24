"""Round 21E fix round 2 — the whole-branch review's Important findings.

1. "Linked" means a uid AND a LINE user behind it — one predicate, used by
   the push, by the invoice's `customer_has_line` and by the quote detail's
   `customer_has_line` (the quote page stops deriving it from the uid).
2. A customer the MODEL named is the customer served: `{code: "C-…"}`,
   `{code: "สมหญิง"}`, `{customer: "สมหญิง"}` sent Q-0001 to สมชาย because
   the quote in view won. Context is used only when the model named nothing.
3. On the strict road the row is written only after LINE accepted: a
   refused push records nothing, and a retry is never "(again)".
+  The failure reply names the cause in plain Thai; the raw LINE error and
   any env-var name stay in the log.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.line.client import LineReplyError  # noqa: E402
from chann_app.services import chat, document_send, notify  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from test_round21c_document_send import COMPANY, INVOICE, LINKED, STAFF, FakeRouteClient  # noqa: E402
from test_round21e_quote_send import KEYS, _shop  # noqa: E402
from test_phase6_chat import LICENSE_ID, _ctx  # noqa: E402


class _Targets:
    def __init__(self, targets):
        self.targets = targets
        self.asked = []

    async def line_target_of(self, chann_uid):
        self.asked.append(chann_uid)
        return self.targets.get(chann_uid)


# ------------------------------------------------------------ 1. one predicate


class TestLinkedMeansALineUserBehindTheUid:
    @pytest.mark.asyncio
    async def test_the_predicate(self):
        client = _Targets({"CHN-1": "U-1"})
        assert await document_send.customer_has_line(client, {"customer_chann_uid": "CHN-1"}) is True
        assert await document_send.customer_has_line(client, {"customer_chann_uid": "CHN-2"}) is False
        assert await document_send.customer_has_line(client, {"customer_chann_uid": None}) is False
        assert await document_send.customer_has_line(client, {}) is False
        assert client.asked == ["CHN-1", "CHN-2"]      # no uid, no question

    @pytest.mark.asyncio
    async def test_the_invoice_says_not_linked_for_a_uid_without_a_target(self):
        class Client(FakeRouteClient):
            async def line_target_of(self, chann_uid):
                return None
        assert await routers_phase2._invoice_customer_has_line(
            Client(), "L1", {"contact_id": "c1"}) is False

        class Linked(FakeRouteClient):
            async def line_target_of(self, chann_uid):
                return "U-1"
        assert await routers_phase2._invoice_customer_has_line(
            Linked(), "L1", {"contact_id": "c1"}) is True

    @pytest.mark.asyncio
    async def test_the_quote_detail_answers_it_the_same_way(self):
        class Client(FakeRouteClient):
            target = None

            async def line_target_of(self, chann_uid):
                return self.target
        client = Client()
        reader = TenantPrincipal(
            license_id="L1", chann_uid="CHN-S-000001", role="sales", is_owner=False,
            permission_keys=frozenset({"quote.read"}), audience="staff")
        out = await routers_phase2.get_quote_detail(
            license_id="L1", quote_id="q1", principal=reader, client=client)
        assert out["customer_has_line"] is False
        client.target = "U-1"
        out = await routers_phase2.get_quote_detail(
            license_id="L1", quote_id="q1", principal=reader, client=client)
        assert out["customer_has_line"] is True

    def test_the_quote_page_reads_it_off_the_response(self):
        page = (ROOT / "presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx").read_text(encoding="utf-8")
        assert "detail?.customer_has_line" in page
        assert "Boolean(detail?.customer?.customer_chann_uid)" not in page


# ------------------------------------------- 2. the customer the model named


def _in_view_is_somchais(client):
    client._last_entity_ref = {"entity_type": "quote", "entity_id": "QUOTE-1", "code": "Q-2026-0001"}
    return client


async def _send(client, fields):
    return await chat._handle_quote_intent(
        client, intent={"action": "send", "entity": "quote", "fields": fields},
        ctx=_ctx(oa="sales", license_id=LICENSE_ID), license_id=LICENSE_ID, language="th",
        permission_keys=KEYS, message="ส่งใบเสนอราคาให้ลูกค้า")


class TestTheCustomerTheModelNamedIsServed:
    @pytest.mark.parametrize("fields", [
        {"code": "C-2026-0002"},
        {"code": "สมหญิง"},
        {"customer": "สมหญิง"},
        {"customer": "C-2026-0002"},
    ])
    @pytest.mark.asyncio
    async def test_her_quote_goes_to_her_not_the_one_in_view(self, fields, line_accepts):
        client = _in_view_is_somchais(_shop())
        reply = await _send(client, fields)
        assert "Q-2026-0002" in reply.text and "สมหญิง" in reply.text, reply.text
        assert line_accepts and line_accepts[-1][1] == "U-somying" and "Q-2026-0002" in line_accepts[-1][2]

    @pytest.mark.asyncio
    async def test_she_has_no_quote_so_it_says_so_and_sends_nothing(self, line_accepts):
        client = _in_view_is_somchais(_shop())
        client._quotes = [q for q in client._quotes if q["deal_id"] != "DEAL-2"]
        reply = await _send(client, {"customer": "สมหญิง"})
        assert "สมหญิง" in reply.text and "ยังไม่มีใบเสนอราคา" in reply.text, reply.text
        assert not line_accepts

    @pytest.mark.asyncio
    async def test_a_name_nobody_has_is_not_found_and_sends_nothing(self, line_accepts):
        client = _in_view_is_somchais(_shop())
        reply = await _send(client, {"code": "มานพ"})
        assert "มานพ" in reply.text
        assert not line_accepts

    @pytest.mark.asyncio
    async def test_context_is_still_used_when_nothing_was_named(self, line_accepts):
        client = _in_view_is_somchais(_shop())
        reply = await _send(client, {})
        assert "Q-2026-0001" in reply.text
        assert line_accepts[-1][1] == "U-somchai"


# ------------------------------------------------ 3. no row for a failed push


class _Rows:
    def __init__(self, target="U-somchai"):
        self.target = target
        self.rows = []

    async def line_target_of(self, chann_uid):
        return self.target

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kwargs):
        row = {"id": f"n{len(self.rows) + 1}", **kwargs}
        self.rows.append(row)
        return row

    async def record_message_entity(self, *args, **kwargs):
        return None

    async def list_notifications(self, license_id, **kwargs):
        # The rows as the customer's list would show them, so `already_sent`
        # sees what was written.
        return [{"type": r["type"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
                 "message": r["message"]} for r in self.rows]


class TestAFailedPushRecordsNothing:
    @pytest.mark.asyncio
    async def test_line_refuses_and_no_row_is_written(self, monkeypatch):
        async def refuse(*a, **k):
            raise LineReplyError("LINE push failed: 500 boom")
        monkeypatch.setattr(notify, "push_text", refuse)
        monkeypatch.setattr("chann_app.services.chat.document_download_url", lambda l, d: f"https://x/d/{d}")
        client = _Rows()
        with pytest.raises(document_send.DocumentSendFailed):
            await document_send.send_document_to_customer(
                client, license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert client.rows == []

    @pytest.mark.asyncio
    async def test_a_retry_after_a_failure_is_not_again(self, monkeypatch, caplog):
        state = {"refuse": True}

        async def push(oa, to, text, **k):
            if state["refuse"]:
                raise LineReplyError("LINE push failed: 500 boom")
            return ["m1"]
        monkeypatch.setattr(notify, "push_text", push)
        monkeypatch.setattr("chann_app.services.chat.document_download_url", lambda l, d: f"https://x/d/{d}")
        client = _Rows()
        with pytest.raises(document_send.DocumentSendFailed):
            await document_send.send_document_to_customer(
                client, license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        state["refuse"] = False
        with caplog.at_level(logging.INFO):
            out = await document_send.send_document_to_customer(
                client, license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True and out["resent"] is False
        assert len(client.rows) == 1
        assert not any("(again)" in r.getMessage() for r in caplog.records)


# ---------------------------------------------- the failure in plain words


class TestTheFailureIsSaidInPlainThai:
    @pytest.mark.parametrize("raw, words", [
        ("LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED", "โทเค็น LINE ของร้านยังไม่ตั้งค่า"),
        ("LINE push failed: 400 {\"message\":\"Failed to send messages\"}", "ลูกค้าอาจบล็อก OA"),
        ("LINE push failed: 403 {\"message\":\"Forbidden\"}", "โทเค็น/สิทธิ์ของ OA ไม่ผ่าน"),
        ("LINE push failed: 500 Internal Server Error", "LINE ไม่รับข้อความ"),
    ])
    @pytest.mark.asyncio
    async def test_chat_names_the_cause_not_the_raw_error(self, raw, words, monkeypatch):
        async def refuse(*a, **k):
            raise LineReplyError(raw)
        monkeypatch.setattr(notify, "push_text", refuse)
        client = _shop()
        reply = await chat._handle_quote_intent(
            client, intent={"action": "send", "entity": "quote", "fields": {"code": "Q-2026-0002"}},
            ctx=_ctx(oa="sales", license_id=LICENSE_ID), license_id=LICENSE_ID, language="th",
            permission_keys=KEYS, message="ส่งใบเสนอราคา Q-2026-0002 ให้ลูกค้า")
        assert words in reply.text, reply.text
        assert "ไม่สำเร็จ" in reply.text and "ทางไลน์แล้ว" not in reply.text
        for raw_word in ("REQUIRED_NOT_CONFIGURED", "ACCESS_TOKEN", "Failed to send", "Internal Server Error", "{"):
            assert raw_word not in reply.text

    @pytest.mark.asyncio
    async def test_the_route_answers_a_reason_code_not_the_raw_error(self, monkeypatch):
        async def refuse(*a, **k):
            raise LineReplyError("LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN is REQUIRED_NOT_CONFIGURED")
        monkeypatch.setattr(notify, "push_text", refuse)
        monkeypatch.setattr("chann_app.services.chat.document_download_url", lambda l, d: f"https://x/d/{d}")

        class Client(FakeRouteClient):
            async def line_target_of(self, chann_uid):
                return "U-1"

            async def get_display_preferences(self, chann_uid):
                return {}

            async def create_notification(self, license_id, **kwargs):
                return {"id": "n1"}
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_quote_to_customer(
                license_id="L1", quote_id="q1", principal=STAFF, client=Client())
        assert exc.value.status_code == 502
        assert exc.value.detail == {"error": "push_failed", "reason": "not_configured"}

    def test_both_screens_say_each_reason(self):
        for page in ("invoices/InvoiceList.tsx", "quotes/[id]/QuoteDetail.tsx"):
            src = (ROOT / "presentation/app/liff/sales" / page).read_text(encoding="utf-8")
            assert "sendPushNotConfigured" in src and "sendPushBlocked" in src, page
        for lang in ("th", "en"):
            text = (ROOT / f"presentation/lib/i18n/{lang}.ts").read_text(encoding="utf-8")
            assert "sendPushNotConfigured:" in text and "sendPushBlocked:" in text
