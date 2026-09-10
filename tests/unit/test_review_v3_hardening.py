"""Review v3, T01-T04: the four technical findings, kept honest.

These started as the review's own probe file
(`scripts/agent-test/scenarios/review-v3/test_technical_review.py`), which
runs only when someone points pytest at it. The assertions there were the
evidence that the bugs existed; these are the same assertions in the suite
the project actually runs before a deploy, so they cannot rot unnoticed.

What each finding is, and — as the review was careful to say — what the
evidence does and does not cover, is written above each class.

Everything here is synthetic: made-up tenants, a store in memory, a LINE
signature computed against a made-up secret. Nothing is rendered, nothing
is fetched, and no message reaches LINE.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from chann_app.services.documents.design import (
    TemplateRejected, active_content_in, frame, sanitise,
)
from chann_app.services.documents.docx import DocxConversionError, check_docx_bytes
from test_document_templates_docx import _app, _Client, _Store, _url


# =========================================================== T01 (P1)

class WebhookStore:
    """The Data tier's `line_webhook_events` table, in memory.

    Small enough to read, and it implements the same four answers the real
    claim endpoint gives, so a test that passes here is testing the
    protocol rather than a mock's convenience.
    """

    def __init__(self):
        self.events: dict[str, dict] = {}

    async def claim_webhook_event(self, event_id: str, oa: str) -> dict:
        row = self.events.get(event_id)
        if row is None:
            self.events[event_id] = {"status": "processing", "reply": None, "oa": oa}
            return {"state": "new", "reply": None}
        if row["status"] == "handled":
            return {"state": "reply_pending", "reply": row["reply"]}
        if row["status"] == "processing":
            return {"state": "in_progress", "reply": None}
        return {"state": "duplicate", "reply": None}

    async def finish_webhook_event(self, event_id, state, reply=None) -> None:
        row = self.events.get(event_id)
        if row is None:
            return
        if state == "failed":
            if row["status"] != "handled":
                self.events.pop(event_id, None)
            return
        if state == "handled" and row["status"] != "done":
            row["status"] = "handled"
            row["reply"] = reply
        elif state == "done":
            row["status"] = "done"
            row["reply"] = None

    async def record_message_entity(self, *args, **kwargs):
        return None

    async def get_display_preferences(self, uid):
        return {}

    async def aclose(self):
        return None


def _webhook(monkeypatch):
    from chann_app.line import webhook as w

    store = WebhookStore()
    monkeypatch.setattr(w, "DataClient", lambda: store)
    monkeypatch.setattr(w, "channel_secret", lambda oa: "synthetic-review-secret")
    monkeypatch.setattr(w, "is_unregistered", lambda ctx: False)
    monkeypatch.setattr(w.live_chat, "sweep", AsyncMock())
    ctx = SimpleNamespace(chann_uid="synthetic-user", license_id="synthetic-tenant")
    monkeypatch.setattr(w, "resolve_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(
        w, "handle_chat_message",
        AsyncMock(return_value=w.ChatReply(text="synthetic reply")),
    )
    monkeypatch.setattr(w, "reply_messages", AsyncMock(return_value=["synthetic-outbound"]))
    monkeypatch.setattr(w, "push_messages", AsyncMock(return_value=["synthetic-pushed"]))
    app = FastAPI()
    app.include_router(w.router)
    event = {
        "type": "message",
        "webhookEventId": "synthetic-event-1",
        "source": {"type": "user", "userId": "synthetic-line-user"},
        "message": {"type": "text", "id": "synthetic-inbound", "text": "สวัสดี"},
        "replyToken": "synthetic-token",
    }
    return w, app, event, ctx, store


async def _send(http, event):
    raw = json.dumps({"events": [event]}, ensure_ascii=False).encode()
    signature = base64.b64encode(
        hmac.new(b"synthetic-review-secret", raw, hashlib.sha256).digest()
    ).decode()
    return await http.post(
        "/webhook/line/sales", content=raw,
        headers={"x-line-signature": signature, "Content-Type": "application/json"},
    )


def _client_for(app, *, raise_app_exceptions=True):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


class TestARedeliveredLineEventFinishesWhatWasStarted:
    """T01 (P1). The event id used to be recorded BEFORE the identity
    lookup and before the reply, so a first delivery that failed left the
    event looking finished and LINE's redelivery was thanked for nothing.

    Proven here: the ASGI route, with a real HMAC signature and a store in
    memory. NOT proven, and not claimed: nothing was sent to LINE, no real
    reply token was expired, and no redelivery came from LINE's own
    infrastructure. LINE's redelivery is neither ordered nor guaranteed
    (https://developers.line.biz/en/docs/messaging-api/receiving-messages/),
    which is why the fix also leaves the work visible in the record rather
    than relying on a retry arriving.
    """

    async def test_a_failed_identity_lookup_is_retried_on_redelivery(self, monkeypatch):
        w, app, event, ctx, store = _webhook(monkeypatch)
        w.resolve_context.side_effect = [
            RuntimeError("synthetic temporary dependency outage"), ctx,
        ]
        async with _client_for(app, raise_app_exceptions=False) as http:
            first = await _send(http, event)
            event["deliveryContext"] = {"isRedelivery": True}
            second = await _send(http, event)
        assert first.status_code == 500
        assert second.status_code == 200
        assert w.handle_chat_message.await_count == 1, (
            "the redelivery was acknowledged but the message was never processed"
        )

    async def test_a_failed_reply_is_retried_without_repeating_the_work(self, monkeypatch):
        w, app, event, ctx, store = _webhook(monkeypatch)
        w.reply_messages.side_effect = [
            w.LineReplyError("synthetic temporary send failure"), ["synthetic-retry-id"],
        ]
        async with _client_for(app, raise_app_exceptions=False) as http:
            first = await _send(http, event)
            event["deliveryContext"] = {"isRedelivery": True}
            second = await _send(http, event)
        assert first.status_code == 503 and second.status_code == 200
        assert w.handle_chat_message.await_count == 1, "the work must happen once"
        assert w.reply_messages.await_count == 2, "the owed answer must be retried"

    async def test_an_expired_reply_token_falls_back_to_a_push(self, monkeypatch):
        """A reply token is short-lived, so the ordinary retry cannot use
        it. The answer still has to arrive."""
        w, app, event, ctx, store = _webhook(monkeypatch)
        w.reply_messages.side_effect = [
            w.LineReplyError("synthetic send failure"),
            w.LineReplyError("Invalid reply token"),
        ]
        async with _client_for(app, raise_app_exceptions=False) as http:
            assert (await _send(http, event)).status_code == 503
            event["deliveryContext"] = {"isRedelivery": True}
            assert (await _send(http, event)).status_code == 200
        assert w.handle_chat_message.await_count == 1
        assert w.push_messages.await_count == 1
        assert store.events["synthetic-event-1"]["status"] == "done"

    async def test_an_undeliverable_answer_stays_owed_rather_than_lost(self, monkeypatch):
        """Neither route worked. The event must not be marked finished:
        the record is what makes the work visible."""
        w, app, event, ctx, store = _webhook(monkeypatch)
        w.reply_messages.side_effect = w.LineReplyError("synthetic send failure")
        w.push_messages.side_effect = w.LineReplyError("synthetic push failure")
        async with _client_for(app, raise_app_exceptions=False) as http:
            assert (await _send(http, event)).status_code == 503
            event["deliveryContext"] = {"isRedelivery": True}
            assert (await _send(http, event)).status_code == 503
        assert w.handle_chat_message.await_count == 1
        row = store.events["synthetic-event-1"]
        assert row["status"] == "handled"
        assert row["reply"]["text"] == "synthetic reply"

    async def test_a_successful_redelivery_does_not_repeat_the_work(self, monkeypatch):
        """The control the review checked and this fix must keep true."""
        w, app, event, ctx, store = _webhook(monkeypatch)
        async with _client_for(app) as http:
            assert (await _send(http, event)).status_code == 200
            assert (await _send(http, event)).status_code == 200
        assert w.handle_chat_message.await_count == 1
        assert w.reply_messages.await_count == 1

    async def test_a_bad_signature_reaches_neither_identity_nor_business(self, monkeypatch):
        """The other control: authentication still comes first."""
        w, app, event, ctx, store = _webhook(monkeypatch)
        async with _client_for(app) as http:
            response = await http.post(
                "/webhook/line/sales", json={"events": [event]},
                headers={"x-line-signature": "invalid"},
            )
        assert response.status_code == 401
        assert w.resolve_context.await_count == 0
        assert w.handle_chat_message.await_count == 0
        assert store.events == {}


# =========================================================== T02 (P1)

ACTIVE_HTML = [
    '<p>synthetic</p><script>console.log("review-only")</script>',
    '<p onclick="console.log(1)">synthetic</p>',
    '<iframe src="https://example.invalid/"></iframe><p>synthetic</p>',
]


class TestAnUploadedTemplateIsFilteredLikeADesignedOne:
    """T02 (P1). `upload_document_template` stored what it was given while
    the AI-designed path next door rebuilt from a whitelist.

    Proven: active content was STORED and REACHABLE. Not proven, and not
    claimed: that any of it ever executed, in the dashboard or in LINE's
    in-app browser. Nothing was rendered here.
    """

    @pytest.mark.parametrize("html", ACTIVE_HTML)
    def test_active_content_never_reaches_the_store(self, monkeypatch, html):
        from chann_app.services.storage import base as storage

        store = _Store()
        monkeypatch.setattr(storage, "get_document_store", lambda: store)
        response = _app(_Client()).post(
            _url("/upload"), json={"template_name": "Audit synthetic", "html": html},
        )
        if response.status_code == 201:
            assert not any(
                html.encode() in blob for blob in store.objects.values()
            ), "active HTML was accepted and stored unchanged"
        else:
            assert response.status_code in (400, 422), response.text

    def test_the_refusal_says_what_was_wrong(self, monkeypatch):
        from chann_app.services.storage import base as storage

        monkeypatch.setattr(storage, "get_document_store", lambda: _Store())
        response = _app(_Client()).post(_url("/upload"), json={
            "template_name": "Audit synthetic",
            "html": '<p>hi</p><script>x</script>',
        })
        assert response.status_code == 400
        assert "สคริปต์" in response.json()["detail"]

    def test_an_ordinary_layout_still_uploads(self, monkeypatch):
        from chann_app.services.storage import base as storage

        store = _Store()
        monkeypatch.setattr(storage, "get_document_store", lambda: store)
        response = _app(_Client()).post(_url("/upload"), json={
            "template_name": "ปกติ",
            "html": "<h1>{{company.name}}</h1><table><tr><td>{{quote.quote_id}}</td></tr></table>",
        })
        assert response.status_code == 201, response.text
        stored = b"".join(store.objects.values()).decode()
        assert "{{company.name}}" in stored and "{{quote.quote_id}}" in stored

    @pytest.mark.parametrize("html", ACTIVE_HTML)
    def test_a_version_stored_before_the_filter_is_not_served(self, monkeypatch, html):
        """The review asked for the OLD versions too, not only new ones."""
        from chann_app.services.storage import base as storage

        path = f"{_url()}/legacy.html"
        store = _Store({path: html.encode()})
        monkeypatch.setattr(storage, "get_document_store", lambda: store)
        template = {
            "id": "22222222-2222-2222-2222-222222222222",
            "document_type": "quote", "template_name": "เก่า",
        }
        version = {
            "id": "33333333-3333-3333-3333-333333333333",
            "template_id": template["id"], "version": 1,
            "status": "published", "compiled_template_path": f"gs://test-bucket/{path}",
        }
        client = _Client(templates=[template], versions=[version])
        response = _app(client).post(
            _url(f"/{template['id']}/versions/{version['id']}/preview"),
        )
        assert response.status_code == 409, response.text

    def test_the_scanner_passes_a_framed_document(self):
        """The frame this system writes fetches the Thai font with
        `@import url(...)`. That is ours and it is not active content —
        a read-side check that flagged it would refuse every template."""
        body, css = sanitise("<h1>{{company.name}}</h1>")
        assert active_content_in(frame(body, css)) == []


# =========================================================== T03 (P1)

class TestTheCssCheckIsNotFooledByEscapes:
    """T03 (P1). `_css_problem` matched literal words, so the same rules
    spelled with CSS escapes walked past it.

    Proven: the sanitiser accepted `u\\72l(...)`, `\\75rl(...)` and
    `@\\69mport`, and now rejects them. NOT proven, and explicitly not
    claimed: nothing was rendered and no URL was fetched, so this is not
    an SSRF result. It closes "the filter can be spelled around".
    """

    @pytest.mark.parametrize("css", [
        r"p { background: u\72l(https://example.invalid/a) }",
        r'@\69mport "https://example.invalid/a.css";',
        r"p { background: \75rl(https://example.invalid/a) }",
        r"p { background: url\28 https://example.invalid/a\29  }",
        r"p { behavio\72 : url(x) }",
    ])
    def test_an_escaped_external_fetch_is_refused(self, css):
        with pytest.raises(TemplateRejected):
            sanitise("<style>" + css + "</style><p>synthetic template</p>")

    @pytest.mark.parametrize("css", [
        "p { color: #333; font-size: 12px }",
        "table td { border: 1px solid #bbb; padding: 6px 8px }",
        "/* the shop's own note */ h1 { margin: 0 0 10px }",
    ])
    def test_an_ordinary_stylesheet_still_passes(self, css):
        body, kept = sanitise("<style>" + css + "</style><p>ok</p>")
        assert body == "<p>ok</p>"
        assert kept.strip()


# =========================================================== T04 (P2)

class TestADocxIsBoundedAfterItExpands:
    """T04 (P2). The size check was on the compressed bytes; `testzip()`
    read every member for its CRC and enforced no size at all.

    The budget chosen is `min(24 MB, 120 x the uploaded bytes)` with a
    2 MB floor — see `documents/docx.py` for why those numbers. The
    review's probe expects a <20 KB file expanding to 8 MiB to be
    refused, and it is, by the ratio rule.

    Not claimed: no file was made large enough to exhaust anything. This
    proves the preflight refuses before it finishes expanding, not that
    the old code would have fallen over.
    """

    def _bomb(self, payload_bytes: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "<document>synthetic</document>")
            archive.writestr("word/unused.xml", "A" * payload_bytes)
        return buffer.getvalue()

    def test_a_small_file_that_expands_enormously_is_refused(self):
        payload = self._bomb(8 * 1024 * 1024)
        assert len(payload) < 20_000
        with pytest.raises(DocxConversionError):
            check_docx_bytes(payload, filename="synthetic.docx")

    def test_an_implausible_number_of_parts_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "<document>synthetic</document>")
            for index in range(2_100):
                archive.writestr(f"word/media/{index}.bin", b"x")
        with pytest.raises(DocxConversionError) as caught:
            check_docx_bytes(buffer.getvalue(), filename="synthetic.docx")
        assert "parts" in caught.value.message_en

    def test_a_real_word_document_is_still_accepted(self):
        from test_document_templates_docx import build_sample_docx

        check_docx_bytes(build_sample_docx("quote"), filename="quotation.docx")

    def test_a_lie_in_the_header_does_not_buy_anything(self):
        """The declared size is a fast rejection, never a permission: the
        running total of what actually comes out is what decides."""
        payload = bytearray(self._bomb(8 * 1024 * 1024))
        # Rewrite every declared uncompressed size to a plausible 1 KB.
        # If the check trusted the header, this would sail through.
        payload = payload.replace(
            (8 * 1024 * 1024).to_bytes(4, "little"), (1024).to_bytes(4, "little"),
        )
        with pytest.raises(DocxConversionError):
            check_docx_bytes(bytes(payload), filename="synthetic.docx")
