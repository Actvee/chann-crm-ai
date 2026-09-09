"""A template designed in chat becomes the shop's real quotation.

`test_http_journey.py` proves the same journey starting from a Word file
someone uploaded on the dashboard. This one starts from a sentence typed
in LINE — the owner's 9 Sep 2026 ask — and ends in the same place: a
customer's quotation, rendered through the template the AI drew.

Everything below the model and the PDF renderer is real: both tiers over
HTTP, the real `document_templates` tables, the real publish state
machine, the real snapshot builder, the real fill engine, and the real
template resolution in `quote_issue.py`. The renderer is stubbed in
exactly the place and the way `tests/unit/test_quote_issue.py` stubs it —
the renderer object — so the HTML that would have gone to SmartBrowz is
captured and asserted on. The model is stubbed by an in-process httpx
transport, because a design is not a deterministic thing to assert on and
because a test must never spend a real key.

The claim this file exists to make: the AI-designed path and the
uploaded-Word path converge on the same published version, and a shop
that used the first one gets its own layout on the customer's document.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from .test_http_journey import _api, shop  # noqa: E402,F401  (fixture)

# What the model would have drawn: a real quotation layout, in the
# vocabulary `samples.LEGEND` defines, with the one repeating row.
DESIGNED = """<!DOCTYPE html><html><head>
<style>.brand{color:#178a50}</style></head><body>
<header class="brand"><h1>{{company.name}}</h1><p>{{company.address}}</p>
<p>เลขประจำตัวผู้เสียภาษี {{company.tax_id}}</p></header>
<h2>ใบเสนอราคา (ออกแบบด้วย AI)</h2>
<p>เลขที่ {{quote.quote_id}} · วันที่ {{issued_on}}</p>
<p>เรียน {{customer.name}}</p>
<table><thead><tr><th>#</th><th>รายการ</th><th>จำนวน</th><th>รวม</th></tr></thead>
<tbody>{{#line_items}}<tr><td>{{item.index}}</td><td>{{item.product_name}}</td>
<td>{{item.qty}}</td><td>{{item.line_total}}</td></tr>{{/line_items}}</tbody></table>
<table><tr><th>รวมทั้งสิ้น</th><td>{{totals.grand_total}}</td></tr></table>
</body></html>"""

MARKER = "ใบเสนอราคา (ออกแบบด้วย AI)"


def _model(answer: str) -> httpx.AsyncClient:
    def _handle(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x",
        })

    return httpx.AsyncClient(transport=httpx.MockTransport(_handle))


class TestATemplateDesignedInChatIssuesARealQuote:
    async def test_the_shop_types_a_sentence_and_its_quotations_change(
        self, shop, memory_cache, monkeypatch,
    ):
        from chann_app.config import settings
        from chann_app.services import quote_issue
        from chann_app.services.chat import handle_chat_message
        from chann_app.services.identity import ResolvedContext, TenantResolution
        from chann_app.services.pdf import base as pdf_base
        from chann_app.services.storage import base as storage_base
        from chann_app.services.storage.base import StoredDocument, sha256_hex

        client, license_id = shop

        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
        monkeypatch.setattr(settings, "openrouter_model", "test-model")

        stored: dict[str, bytes] = {}

        class _MemoryStore:
            async def put(self, *, key, content, content_type=None):
                stored[key] = content
                return StoredDocument(
                    path=f"mem://{key}", sha256=sha256_hex(content), size=len(content),
                )

            async def get(self, *, path):
                return stored[path.removeprefix("mem://")]

        memory = _MemoryStore()
        monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: memory)

        rendered: dict[str, str] = {}

        class _Renderer:
            async def render(self, html, options, idempotency_key=None):
                rendered["html"] = html
                return type(
                    "Result", (),
                    {"content": b"%PDF-1.4 stub", "renderer": "smartbrowz"},
                )()

        monkeypatch.setattr(pdf_base, "get_renderer", lambda *a, **k: _Renderer())
        monkeypatch.setattr(quote_issue, "get_renderer", lambda *a, **k: _Renderer())
        monkeypatch.setattr(quote_issue, "get_document_store", lambda *a, **k: memory)

        # The chat engine needs the same wired DataClient the HTTP routes
        # got, so both surfaces are talking to one Data tier.
        from chann_app.main import app as application_app
        from chann_app.routers_admin import get_data_client
        from chann_app.routers_phase2 import get_tenant_principal

        data = application_app.dependency_overrides[get_data_client]()
        principal = application_app.dependency_overrides[get_tenant_principal]()

        ctx = ResolvedContext(
            chann_uid=principal.chann_uid,
            display_name="เจ้าของร้าน",
            resolution=TenantResolution.SINGLE,
            memberships=[{
                "license_id": license_id, "license_code": "E2E",
                "company_name": "ร้านทดสอบ", "chann_uid": principal.chann_uid,
                "role": "sales", "status": "active",
            }],
            oa="sales",
            primary_role="sales",
        )

        # ---- the company details a document is not legal without
        client.patch(
            _api(license_id, "/company-profile"),
            json={
                "legal_name": "ร้านออกแบบด้วยเอไอ", "tax_id": "0105500000009",
                "company_address": "9 ถนนเทมเพลต", "company_phone": "020000009",
            },
        )

        # ---- 1. a sentence in LINE
        reply = await handle_chat_message(
            data, message="ออกแบบใบเสนอราคาให้หน่อย", ctx=ctx, language="th",
            ai_client=_model(DESIGNED),
        )
        assert "ฉบับร่าง" in reply.text, reply.text
        assert [label for label, _ in reply.quick_replies] == ["ใช้เลย", "แก้เพิ่ม", "ทิ้ง"]

        # It is a real row in the real tables, and it is NOT published.
        templates = client.get(_api(license_id, "/document-templates")).json()
        assert len(templates) == 1
        template_id = templates[0]["id"]
        versions = client.get(
            _api(license_id, f"/document-templates/{template_id}/versions")
        ).json()
        assert [v["status"] for v in versions] == ["previewed"]

        # The preview the reply linked to is the engine's own, filled with
        # the real sample snapshot — no placeholders left in it.
        preview = next(v for k, v in stored.items() if k.endswith("-preview.html"))
        assert MARKER in preview.decode("utf-8")
        assert "{{" not in preview.decode("utf-8")

        # ---- 2. nothing has changed for the shop's customers yet
        quote = self._quote(client, license_id, "0866000001")
        issued = client.post(_api(license_id, f"/quotes/{quote['id']}/issue"))
        assert issued.status_code in (200, 201), issued.text
        assert MARKER not in rendered["html"], "an unpublished draft reached a customer"

        # ---- 3. the person says so, and only then
        reply = await handle_chat_message(
            data, message="ใช้เลย", ctx=ctx, language="th", ai_client=_model(DESIGNED),
        )
        assert "เผยแพร่แล้ว" in reply.text
        # The confirmation names what it will be used for.
        assert "ใบเสนอราคา" in reply.text

        versions = client.get(
            _api(license_id, f"/document-templates/{template_id}/versions")
        ).json()
        assert [v["status"] for v in versions] == ["published"]

        # ---- 4. the next customer's quotation comes out in the new layout
        rendered.clear()
        quote = self._quote(client, license_id, "0866000002")
        issued = client.post(_api(license_id, f"/quotes/{quote['id']}/issue"))
        assert issued.status_code in (200, 201), issued.text

        html = rendered["html"]
        assert MARKER in html, "the published design did not reach the document"
        # Real values, from the real snapshot builder — not the sample.
        assert "ร้านออกแบบด้วยเอไอ" in html
        assert "0105500000009" in html
        assert "พัดลมไอเย็น" in html
        # The repeating row actually repeated, and nothing was left unfilled.
        assert "{{" not in html
        # The house frame survives whatever the model wrote.
        assert "Noto Sans Thai" in html and "size: A4" in html

        # It is this quote's own values, not the sample the preview used —
        # a real snapshot through the real fill engine.
        assert "QT-2026-0042" not in html
        assert "3600.00" in html

        # And the document is recorded with the proof of what was sent.
        document = issued.json()
        assert document["generated_document_id"]
        assert document["sha256"]

    def _quote(self, client, license_id, phone):
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "ลูกค้า", "last_name": phone[-4:], "phone": phone},
        ).json()
        deal = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        ).json()
        client.post(
            _api(license_id, f"/deals/{deal['id']}/products"),
            json={"product_name": "พัดลมไอเย็น", "quoted_unit_price": "1200.00", "qty": 3},
        )
        return client.post(
            _api(license_id, "/quotes"), json={"deal_id": deal["id"]},
        ).json()
