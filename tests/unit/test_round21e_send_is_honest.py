"""Round 21E fix round 1 — a send never says "ทางไลน์แล้ว" when nothing went.

Controller ruling (concern 3 of the 21E report, ship-blocker): the push
went through `notify.send_notification`, which records the row and then
SWALLOWS a LINE failure — right for an approval ping, whose business
action must not roll back over a LINE hiccup, and wrong for "send this
document to the customer", whose whole business action IS the push.
And a customer with a uid but no LINE target was logged and skipped, with
the chat still answering "ส่ง … ทางไลน์แล้ว".

Now:
  * uid but no LINE target -> `CustomerNotLinked`, by name, nothing recorded;
  * the push fails -> `DocumentSendFailed`, which chat answers
    "ส่ง … ไม่สำเร็จ (LINE ตอบ: …) — ลองใหม่อีกครั้ง" and the routes answer 502;
  * every other notification keeps its swallow-and-log behaviour.
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
from test_round21c_document_send import (  # noqa: E402
    COMPANY, INVOICE, LINKED, STAFF, FakeClient, FakeRouteClient,
)


class _Recording(FakeClient):
    def __init__(self, target="U-somchai"):
        super().__init__()
        self.target = target
        self.rows = []

    async def line_target_of(self, chann_uid):
        return self.target

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kwargs):
        self.rows.append(kwargs)
        return {"id": f"n{len(self.rows)}", **kwargs}

    async def record_message_entity(self, *args, **kwargs):
        return None


class _RouteRecording(FakeRouteClient):
    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kwargs):
        return {"id": "n1", **kwargs}

    async def record_message_entity(self, *args, **kwargs):
        return None


@pytest.fixture
def links(monkeypatch):
    monkeypatch.setattr("chann_app.services.chat.document_download_url",
                        lambda license_id, document_id: f"https://x/d/{document_id}")


@pytest.fixture
def line_down(monkeypatch):
    async def refuse(*args, **kwargs):
        raise LineReplyError("LINE push failed: 500 Internal Server Error")
    monkeypatch.setattr(notify, "push_text", refuse)
    monkeypatch.setattr(notify, "push_messages", refuse)


@pytest.fixture
def line_up(monkeypatch):
    pushed = []

    async def accept(oa, to, text, **kwargs):
        pushed.append((oa, to, text))
        return ["m1"]
    monkeypatch.setattr(notify, "push_text", accept)
    return pushed


async def _send(client):
    return await document_send.send_document_to_customer(
        client, license_id="L1", kind="invoice", record=INVOICE,
        document_id="d1", customer=LINKED, company=COMPANY)


class TestTheServiceTellsTheTruth:
    @pytest.mark.asyncio
    async def test_a_push_that_goes_is_sent(self, links, line_up):
        out = await _send(_Recording())
        assert out["sent"] is True
        assert line_up and line_up[0][1] == "U-somchai"

    @pytest.mark.asyncio
    async def test_a_uid_without_a_line_target_is_not_linked_by_name(self, links, line_up, caplog):
        client = _Recording(target=None)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(document_send.CustomerNotLinked) as exc:
                await _send(client)
        assert exc.value.customer_name == "สมชาย ใจดี"
        assert not client.rows and not line_up
        # The owner can find it in DEV's log by the document's code.
        assert any("INV-2026-0003" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_push_that_fails_is_a_failure_not_a_send(self, links, line_down, caplog):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(document_send.DocumentSendFailed) as exc:
                await _send(_Recording())
        assert "500" in exc.value.detail
        assert any("INV-2026-0003" in r.getMessage() and "LINE push failed" in r.getMessage()
                   and r.levelno >= logging.WARNING for r in caplog.records)


class TestOtherNotificationsStillSwallow:
    @pytest.mark.asyncio
    async def test_the_default_road_records_and_logs_but_does_not_raise(self, line_down):
        client = _Recording()
        row = await notify.send_notification(
            client, license_id="L1", target_chann_uid="CHN-S-1", target_line_user_id="U-1",
            type="approval_requested", message="x")
        assert row["id"] == "n1"

    @pytest.mark.asyncio
    async def test_the_strict_road_raises(self, line_down):
        with pytest.raises(notify.NotificationNotDelivered):
            await notify.send_notification(
                _Recording(), license_id="L1", target_chann_uid="CHN-S-1", target_line_user_id="U-1",
                type="document_sent", message="x", raise_on_failure=True)


class TestTheRoutesSayItFailed:
    @pytest.mark.asyncio
    async def test_the_invoice_route_is_a_502_push_failed(self, links, line_down):
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_invoice_to_customer(
                license_id="L1", invoice_id="i1", payload=None, principal=STAFF,
                client=_RouteRecording())
        assert exc.value.status_code == 502
        assert exc.value.detail["error"] == "push_failed"

    @pytest.mark.asyncio
    async def test_the_quote_route_is_a_502_push_failed(self, links, line_down):
        with pytest.raises(HTTPException) as exc:
            await routers_phase2.send_quote_to_customer(
                license_id="L1", quote_id="q1", principal=STAFF, client=_RouteRecording())
        assert exc.value.status_code == 502
        assert exc.value.detail["error"] == "push_failed"


class TestChatSaysItFailed:
    @pytest.mark.asyncio
    async def test_the_reply_is_a_failure_with_a_way_to_retry(self, line_down):
        from test_round21e_quote_send import KEYS, _shop, _send as send_quote

        client = _shop()
        reply = await send_quote(client, {"code": "Q-2026-0002"}, "ส่งใบเสนอราคา Q-2026-0002 ให้ลูกค้า")
        assert "ไม่สำเร็จ" in reply.text and "ลองใหม่อีกครั้ง" in reply.text, reply.text
        assert "ทางไลน์แล้ว" not in reply.text
        assert ("ลองส่งอีกครั้ง", "ส่งใบเสนอราคา Q-2026-0002 ให้ลูกค้า") in reply.quick_replies
        assert KEYS

    @pytest.mark.asyncio
    async def test_a_uid_without_a_line_target_is_refused_by_name_in_chat(self, line_up):
        from test_round21e_quote_send import _shop, _send as send_quote

        client = _shop()
        client._line_targets = {}
        reply = await send_quote(client, {"code": "Q-2026-0002"}, "ส่งใบเสนอราคา Q-2026-0002 ให้ลูกค้า")
        assert "สมหญิง" in reply.text and "ยังไม่ได้ผูกไลน์" in reply.text
        assert "ทางไลน์แล้ว" not in reply.text


class TestTheScreensReadTheCode:
    def test_both_screens_say_a_push_failure(self):
        app = ROOT / "presentation/app/liff/sales"
        for page in ("invoices/InvoiceList.tsx", "quotes/[id]/QuoteDetail.tsx"):
            src = (app / page).read_text(encoding="utf-8")
            assert 'failure.code === "push_failed"' in src, page
            assert "sendPushFailed" in src, page
        for lang in ("th", "en"):
            assert "sendPushFailed:" in (ROOT / f"presentation/lib/i18n/{lang}.ts").read_text(encoding="utf-8")
