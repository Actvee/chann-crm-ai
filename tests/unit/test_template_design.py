"""The AI designing a document template from a sentence in chat.

Owner, 9 Sep 2026: "ตอนนี้รองรับให้ผู้ใช้พิมพ์ในแชทเพื่อให้ AI ช่วยออกแบบให้ใน
แชทแล้วใช่ไหม สำหรับ Sale OA กับคนที่มีสิทธิ์".

The tests that matter most here are the refusals. A template is markup a
tenant caused to be written, stored on our server, and later rendered over
snapshot data — `fill.py`'s docstring explains why that is the thing to be
careful about — so every dangerous shape gets its own assertion, and the
placeholder check gets one too, because a template that stores fine and
prints a blank company name is the bug `samples.py` was written to kill.
"""
from __future__ import annotations

import json
import sys
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.documents import design  # noqa: E402
from chann_app.services.documents.fill import fill_template, placeholders_in  # noqa: E402
from chann_app.services.documents.html_docx import html_to_docx  # noqa: E402
from chann_app.services.documents.samples import LEGEND, sample_snapshot  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ctx  # noqa: E402

SALES_KEYS = ["customer.read", "deal.read", "quote.create", "setting.manage"]
NO_SETTING_KEYS = ["customer.read", "deal.read", "quote.create"]

A_QUOTE_DESIGN = """<!DOCTYPE html><html><head>
<style>.brand{color:#178a50;font-weight:700}</style></head><body>
<header class="brand"><h1>{{company.name}}</h1><p>{{company.address}}</p>
<p>เลขประจำตัวผู้เสียภาษี {{company.tax_id}} · โทร {{company.phone}}</p></header>
<h2>ใบเสนอราคา</h2>
<p>เลขที่ {{quote.quote_id}} · วันที่ {{issued_on}} · ยืนราคาถึง {{quote.valid_until}}</p>
<p>เรียน {{customer.name}} — {{customer.address}}</p>
<table><thead><tr><th>#</th><th>รายการ</th><th>จำนวน</th><th>ราคา/หน่วย</th><th>รวม</th></tr></thead>
<tbody>{{#line_items}}<tr><td>{{item.index}}</td><td>{{item.product_name}}</td>
<td>{{item.qty}}</td><td>{{item.unit_price}}</td><td>{{item.line_total}}</td></tr>{{/line_items}}</tbody></table>
<table><tr><th>รวมเป็นเงิน</th><td>{{totals.subtotal}}</td></tr>
<tr><th>รวมทั้งสิ้น</th><td>{{totals.grand_total}}</td></tr></table>
<p>ผู้เสนอราคา ..........................</p></body></html>"""


# --------------------------------------------------------------- the fakes

class _Store:
    """The document store, remembering what was put in it."""

    def __init__(self, fail: bool = False):
        self.puts: list[dict] = []
        self.fail = fail
        self._by_path: dict[str, bytes] = {}

    async def put(self, *, key, content, content_type):
        if self.fail:
            raise RuntimeError("bucket exploded")
        self.puts.append({"key": key, "content": content, "content_type": content_type})
        self._by_path[key] = content

        class _Stored:
            path = key
        return _Stored()

    async def get(self, *, path):
        return self._by_path[path]


def _ai(answer: str | Exception):
    """An httpx client that answers every model call with `answer`."""
    calls = {"n": 0}

    def _handle(request):
        calls["n"] += 1
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "provider": "x",
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handle))
    client.calls = calls
    return client


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")


@pytest.fixture
def store(monkeypatch):
    from chann_app.services.storage import base as storage_base

    made = _Store()
    monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: made)
    return made


async def _say(client, message, *, ai=None, language="th"):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(oa="sales"), language=language,
        ai_client=ai if ai is not None else _ai(A_QUOTE_DESIGN),
    )


# ------------------------------------------------------------- the trigger

class TestTheTriggerFires:
    @pytest.mark.parametrize("message,expected", [
        ("ออกแบบใบเสนอราคา", "quote"),
        ("ทำแม่แบบใบเสนอราคา", "quote"),
        ("ทำเทมเพลตใบเสนอราคาใหม่", "quote"),
        ("ออกแบบฟอร์มรายงานการซ่อม", "service_report"),
        ("ออกแบบใบรายงานการซ่อม", "service_report"),
        ("ช่วยออกแบบใบเสนอราคาให้หน่อยได้ไหม", "quote"),
        ("design a quotation template", "quote"),
        ("design a service report template", "service_report"),
    ])
    def test_the_phrases_people_type_start_the_flow(self, message, expected):
        assert chat._is_template_design_request(message)
        assert chat._template_type_from(message) == expected

    @pytest.mark.parametrize("message", [
        "สร้างใบเสนอราคา", "ออกเอกสาร Q-2026-0001", "รายงานยอดขายเดือนนี้",
        "รายชื่อลูกค้า", "อนุมัติ SR-2026-0001", "งานวันนี้",
    ])
    def test_issuing_a_document_is_not_designing_a_form(self, message):
        """The words overlap and the consequences do not: one produces a
        quotation for a customer, the other changes what every quotation
        looks like."""
        assert not chat._is_template_design_request(message)

    @pytest.mark.parametrize("message", [
        # "รายงาน" (AI_REPORT_TRIGGERS) and "quotation" (QUOTE_CREATE_TRIGGERS)
        # are substrings of these. `scripts/dev/check-triggers.py` reads the
        # dispatcher's order from where each trigger NAME first appears in
        # the source, so it cannot see a trigger reached through a predicate
        # function and reports these as swallowed. They are not: the design
        # check runs at the top of the sales block, ahead of both. This test
        # is the evidence the static check cannot produce.
        "ออกแบบฟอร์มรายงานการซ่อม",
        "ออกแบบใบรายงานการซ่อม",
        "ทำแม่แบบใบรายงานการซ่อม",
        "ทำเทมเพลตรายงานการซ่อม",
        "design a service report template",
        "design a quotation template",
        "make a quotation template",
        "create a quotation template",
    ])
    async def test_the_overlapping_phrases_reach_the_designer_not_the_other_flow(
        self, message, store,
    ):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(client, message)

        assert "ฉบับร่าง" in reply.text, reply.text
        assert len(client._templates) == 1
        # Not an AI report, and not a quotation issued to a customer.
        assert "ยอดขาย" not in reply.text
        assert client._quotes == []

    def test_a_request_that_names_neither_document_is_asked_about(self):
        assert chat._template_type_from("ออกแบบเอกสารให้หน่อย") is None

    def test_a_request_that_names_both_is_asked_about_too(self):
        """First-match-wins would silently design the wrong one."""
        assert chat._template_type_from("ออกแบบใบเสนอราคาและใบรายงานการซ่อม") is None


# ----------------------------------------------------------- the sanitiser

class TestTheSanitiserRefuses:
    @pytest.mark.parametrize("name,markup", [
        ("script", "<div><script>fetch('//evil')</script></div>"),
        ("iframe", '<div><iframe src="//evil"></iframe></div>'),
        ("object", '<object data="x.swf"></object>'),
        ("embed", '<embed src="x">'),
        ("onclick", '<div onclick="steal()">hi</div>'),
        ("onerror", "<p onerror=alert(1)>x</p>"),
        ("onload attribute", '<div onload="x()">y</div>'),
        ("javascript: url", '<div style="background:javascript:alert(1)">x</div>'),
        ("external image", '<img src="https://evil/pixel.png">'),
        ("link", '<a href="https://evil">x</a>'),
        ("stylesheet", '<link rel="stylesheet" href="//evil">'),
        ("css @import", '<style>@import url("//evil");</style><p>x</p>'),
        ("css url()", "<style>body{background:url(//evil/x.png)}</style><p>x</p>"),
        ("form", '<form action="//evil"><p>x</p></form>'),
        ("svg", "<svg onload=alert(1)></svg>"),
        ("meta refresh", '<meta http-equiv="refresh" content="0;url=//evil"><p>x</p>'),
        ("base", '<base href="//evil">'),
        ("css expression", "<style>p{width:expression(alert(1))}</style><p>x</p>"),
    ])
    def test_each_dangerous_shape_is_rejected(self, name, markup):
        with pytest.raises(design.TemplateRejected) as caught:
            design.sanitise(markup)
        assert caught.value.reasons, f"{name} was rejected without saying why"

    def test_nothing_is_returned_to_be_stored_when_it_is_rejected(self):
        """Rejected, not repaired: `sanitise` has no path that returns
        markup it had to strip something out of."""
        with pytest.raises(design.TemplateRejected):
            design.sanitise("<p>ok</p><script>bad()</script>")

    def test_an_empty_answer_is_rejected(self):
        with pytest.raises(design.TemplateRejected):
            design.sanitise("")

    def test_a_real_design_survives_intact(self):
        body, css = design.sanitise(A_QUOTE_DESIGN)
        assert "{{company.name}}" in body
        assert "{{#line_items}}" in body and "{{/line_items}}" in body
        assert "<table>" in body and "<th>" in body
        assert ".brand{color:#178a50" in css

    def test_the_frame_is_the_houses_and_not_the_models(self):
        """A renderer without a Thai font prints boxes, silently — so the
        font import and the A4 page are added here every time, whatever
        the model returned."""
        html = design.frame(*design.sanitise(A_QUOTE_DESIGN))
        assert "fonts.googleapis.com" in html
        assert "size: A4" in html
        assert "Noto Sans Thai" in html

    def test_the_output_is_rebuilt_rather_than_passed_through(self):
        """An attribute nobody whitelisted does not survive by being
        left alone."""
        body, _ = design.sanitise('<p title="x" contenteditable="true" class="k">hi</p>')
        assert 'class="k"' in body
        assert "contenteditable" not in body and "title=" not in body


# ------------------------------------------------------ placeholder checks

class TestPlaceholderValidation:
    def test_an_invented_placeholder_is_named(self):
        html = design.frame("<p>{{customer.tax_id}} {{company.name}}</p>")
        assert design.invented_placeholders(html, "quote") == ["customer.tax_id"]

    def test_the_real_vocabulary_all_resolves(self):
        """The list handed to the model must be one the engine can fill —
        the bug `samples.py` describes was a hand-written list that could
        not."""
        for document_type in design.DESIGNABLE_DOCUMENT_TYPES:
            names = [n for n, _ in design.vocabulary_for(document_type)]
            html = design.frame(
                "<table>{{#line_items}}<tr><td>"
                + " ".join(f"{{{{{n}}}}}" for n in names)
                + "</td></tr>{{/line_items}}</table>"
            )
            assert design.invented_placeholders(html, document_type) == []

    def test_the_vocabulary_is_derived_from_the_legend(self):
        assert [n for n, _ in design.vocabulary_for("service_report")] == [
            n for n, _ in LEGEND["service_report"]
        ]

    def test_the_prompt_lists_only_placeholders_that_resolve(self):
        prompt = design.build_user_prompt(document_type="quote", description="เรียบ ๆ")
        for name in design.known_placeholders("quote"):
            assert f"{{{{{name}}}}}" in prompt

    def test_a_document_missing_its_number_and_total_is_flagged(self):
        html = design.frame("<p>{{company.name}}</p>")
        assert set(design.missing_essentials(html, "quote")) == {
            "quote.quote_id", "totals.grand_total",
        }


# --------------------------------------------------------------- the docx

class TestTheWordDownload:
    def test_the_draft_comes_back_as_a_valid_docx(self):
        html = design.frame(*design.sanitise(A_QUOTE_DESIGN))
        data = html_to_docx(html)
        archive = zipfile.ZipFile(BytesIO(data))
        assert archive.testzip() is None
        assert "word/document.xml" in archive.namelist()

    def test_the_word_file_round_trips_back_through_the_uploader(self):
        """The point of the download: edit it in Word, upload it back. If
        the placeholders do not survive the trip it is a decoration."""
        from chann_app.services.documents.docx import convert_docx_to_html

        html = design.frame(*design.sanitise(A_QUOTE_DESIGN))
        back = convert_docx_to_html(html_to_docx(html), filename="t.docx")
        assert design.invented_placeholders(back, "quote") == []
        assert {"company.name", "quote.quote_id", "totals.grand_total"} <= placeholders_in(back)
        assert "{{#line_items}}" in back


# ------------------------------------------------------- the flow in chat

class TestTheFlowInChat:
    async def test_the_draft_is_shown_before_it_is_real(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(client, "ออกแบบใบเสนอราคา")

        assert "ฉบับร่าง" in reply.text
        labels = [label for label, _ in reply.quick_replies]
        assert labels == ["ใช้เลย", "แก้เพิ่ม", "ทิ้ง"]
        assert reply.quick_reply_url is not None
        # A draft, and only a draft: nothing is published by asking.
        assert [v["status"] for v in client._template_versions] == ["previewed"]

    async def test_the_preview_shows_the_document_filled_with_real_values(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        preview = next(p for p in store.puts if p["key"].endswith("-preview.html"))
        rendered = preview["content"].decode("utf-8")
        assert "{{company.name}}" not in rendered
        assert "ชาญ แอร์" in rendered
        assert "QT-2026-0042" in rendered

    async def test_a_word_file_is_offered_too(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(client, "ออกแบบใบเสนอราคา")

        docx = next(p for p in store.puts if p["key"].endswith("-design.docx"))
        assert zipfile.ZipFile(BytesIO(docx["content"])).testzip() is None
        assert "https://app.example/api/v1/assets/" in reply.text

    async def test_an_ambiguous_request_asks_which_document(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(client, "ออกแบบเอกสารให้หน่อย")

        assert "ใบเสนอราคา" in reply.text and "ใบรายงานการซ่อม" in reply.text
        assert client._templates == []
        # Answering the question gets on with it.
        reply = await _say(client, "ใบรายงานการซ่อม")
        assert client._templates[0]["document_type"] == "service_report"

    async def test_the_invented_placeholder_is_said_plainly(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(
            client, "ออกแบบใบเสนอราคา",
            ai=_ai("<p>{{company.name}} {{quote.quote_id}} {{totals.grand_total}} "
                   "{{customer.tax_id}}</p>"),
        )
        assert "{{customer.tax_id}}" in reply.text
        assert "ว่าง" in reply.text

    async def test_a_dangerous_draft_is_refused_and_nothing_is_stored(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(
            client, "ออกแบบใบเสนอราคา",
            ai=_ai("<p>{{company.name}}</p><script>fetch('//evil')</script>"),
        )
        assert "ไม่ได้บันทึก" in reply.text
        assert "สคริปต์" in reply.text
        assert client._templates == [] and client._template_versions == []
        assert store.puts == []
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None


class TestRefineAndPublish:
    async def test_an_edit_regenerates_from_the_previous_draft(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        reply = await _say(client, "แก้เพิ่ม")
        assert "อยากแก้ตรงไหน" in reply.text

        seen = {}

        def _handle(request):
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={
                "choices": [{"message": {"content": A_QUOTE_DESIGN}}],
                "usage": {}, "provider": "x"})

        await _say(client, "เพิ่มช่องเลขที่ผู้เสียภาษี",
                   ai=httpx.AsyncClient(transport=httpx.MockTransport(_handle)))

        asked = seen["body"]["messages"][1]["content"]
        assert "เพิ่มช่องเลขที่ผู้เสียภาษี" in asked
        # The previous HTML goes back in, or "ตัดโลโก้ออก" has no subject.
        assert "{{#line_items}}" in asked
        assert "แบบฟอร์มเดิม" in asked
        # A second draft version of the same template, not a second template.
        assert len(client._templates) == 1 and len(client._template_versions) == 2

    async def test_an_edit_said_outright_is_not_treated_as_a_new_request(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        await _say(client, "ตัดโลโก้ออก")
        assert len(client._template_versions) == 2

    async def test_publishing_takes_the_person_saying_so(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        assert [v["status"] for v in client._template_versions] == ["previewed"]

        reply = await _say(client, "ใช้เลย")
        assert [v["status"] for v in client._template_versions] == ["published"]
        # It must say what it will be used for — this changes every one.
        assert "ใบเสนอราคา" in reply.text
        assert "✅" in reply.text

    async def test_discarding_leaves_the_draft_unpublished(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        reply = await _say(client, "ทิ้ง")
        assert "ทิ้งร่างแล้ว" in reply.text
        assert [v["status"] for v in client._template_versions] == ["previewed"]
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_the_permission_is_checked_again_at_publish(self, store):
        """A role can be taken away between drafting and publishing, and
        publishing is the act that changes every document."""
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        client._permission_keys = list(NO_SETTING_KEYS)
        reply = await _say(client, "ใช้เลย")
        assert "ไม่มีสิทธิ์" in reply.text
        assert [v["status"] for v in client._template_versions] == ["previewed"]


class TestTheConversationKeepsItsThread:
    """Review v3, B08.

    The three decisions were exact vocabularies, so a reply that plainly
    continued the conversation was read as a new subject: the pending
    state was cleared, nothing was published, and the chat flow could not
    go on. The saved draft was never lost — it stayed on the templates
    page as a DRAFT version — but the only way back to it was the
    dashboard.

    What must stay true, and is asserted below: publishing still takes a
    clear confirmation. "Keep the context" must not become "publish on
    anything vaguely positive"; publishing changes every document the
    shop issues from then on.
    """

    async def test_a_confirmation_with_a_polite_particle_publishes(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        await _say(client, "ใช้เลยครับ")
        assert [v["status"] for v in client._template_versions] == ["published"]

    async def test_a_confirmation_spelled_out_publishes(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        await _say(client, "ยืนยันใช้แบบนี้")
        assert [v["status"] for v in client._template_versions] == ["published"]

    async def test_asking_to_look_first_keeps_the_draft_and_publishes_nothing(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        reply = await _say(client, "ขอดูก่อน")
        assert [v["status"] for v in client._template_versions] == ["previewed"]
        assert await client.get_pending_intent("CHN-S-000001", "sales") is not None
        assert [label for label, _ in reply.quick_replies] == ["ใช้เลย", "แก้เพิ่ม", "ทิ้ง"]

        # ...and the conversation can still be finished afterwards.
        await _say(client, "ใช้เลย")
        assert [v["status"] for v in client._template_versions] == ["published"]

    async def test_a_refusal_that_contains_the_word_use_is_still_a_refusal(self, store):
        """"ไม่ใช้แบบนี้" holds "ใช้แบบนี้" inside it. Reading agreement
        out of a refusal would be the worst possible way to fail."""
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        reply = await _say(client, "ไม่ใช้แบบนี้")
        assert "ทิ้งร่างแล้ว" in reply.text
        assert [v["status"] for v in client._template_versions] == ["previewed"]

    async def test_an_unclear_answer_asks_rather_than_publishing(self, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        reply = await _say(client, "โอเค")
        assert [v["status"] for v in client._template_versions] == ["previewed"]
        assert await client.get_pending_intent("CHN-S-000001", "sales") is not None
        assert "ใช้เลย" in reply.text and "ทิ้ง" in reply.text

    async def test_a_different_subject_still_moves_on(self, store):
        """The other half of the rule: someone who abandons the design and
        asks something else must get their answer, not the draft again."""
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")
        await _say(client, "ลูกค้าทั้งหมดมีใครบ้าง")
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None
        assert [v["status"] for v in client._template_versions] == ["previewed"]


class TestReadingAnAnswerAboutTheDraft:
    """The decision table itself, so the boundaries are written down.

    The two that matter most are at the ends: a refusal that CONTAINS a
    confirmation must stay a refusal, and a sentence that merely mentions
    templates on its way to a different question must still be a
    different question — otherwise "keep the context" becomes a trap the
    person cannot get out of.
    """

    @pytest.mark.parametrize("message,expected", [
        ("ใช้เลย", "publish"),
        ("ใช้เลยครับ", "publish"),
        ("ยืนยันใช้แบบนี้", "publish"),
        ("เอาอันนี้เลยครับ", "publish"),
        ("ทิ้ง", "drop"),
        ("ไม่ใช้แบบนี้", "drop"),
        ("ยกเลิกเลยครับ", "drop"),
        ("ขอดูก่อน", "look"),
        ("ขอดูตัวอย่างก่อนนะ", "look"),
        ("แก้เพิ่ม", "refine"),
        ("ขอแก้หน่อยครับ", "refine"),
        ("เพิ่มช่องเลขที่ผู้เสียภาษี", "edit"),
        ("โอเค", "unclear"),
        ("อันนี้", "unclear"),
        ("ลูกค้าทั้งหมดมีใครบ้าง", "other"),
        ("แบบฟอร์มมีกี่แบบ", "other"),
        ("ขอดูใบเสนอราคาล่าสุด", "other"),
    ])
    def test_the_decision(self, message, expected):
        from chann_app.services.chat import _template_draft_decision

        assert _template_draft_decision(message) == expected


class TestTheGates:
    async def test_without_setting_manage_the_flow_never_starts(self, store):
        client = FakeDataClient(permission_keys=NO_SETTING_KEYS)
        ai = _ai(A_QUOTE_DESIGN)
        reply = await _say(client, "ออกแบบใบเสนอราคา", ai=ai)

        assert "ไม่มีสิทธิ์" in reply.text
        assert client._templates == [] and store.puts == []
        # Not even a model call: the gate is before the spend.
        assert ai.calls["n"] == 0

    @pytest.mark.parametrize("oa", ["technician", "customer"])
    async def test_it_is_the_sales_oa_only(self, oa, store):
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await handle_chat_message(
            client, message="ออกแบบใบเสนอราคา", ctx=_ctx(oa=oa, primary_role=oa),
            language="th", ai_client=_ai(A_QUOTE_DESIGN),
        )
        assert client._templates == []
        assert "ฉบับร่าง" not in reply.text

    async def test_the_model_being_down_says_so_and_leaves_nothing_behind(self, store):
        from chann_app.services.ai.intent import unavailable_reply

        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(
            client, "ออกแบบใบเสนอราคา",
            ai=_ai(httpx.ConnectError("no route to host")),
        )
        assert reply.text == unavailable_reply("th")
        assert client._templates == [] and client._template_versions == []
        assert store.puts == []
        assert await client.get_pending_intent("CHN-S-000001", "sales") is None

    async def test_an_unconfigured_model_is_the_same_reply_not_a_traceback(self, store, monkeypatch):
        from chann_app.services.ai.intent import unavailable_reply

        monkeypatch.setattr(settings, "openrouter_api_key", "")
        client = FakeDataClient(permission_keys=SALES_KEYS)
        reply = await _say(client, "ออกแบบใบเสนอราคา")
        assert reply.text == unavailable_reply("th")
        assert client._templates == []


class TestTheStoredTemplateActuallyWorks:
    async def test_what_is_stored_renders_a_real_document(self, store):
        """The end of the line: the thing chat saved, filled by the engine
        that fills it at issue time."""
        client = FakeDataClient(permission_keys=SALES_KEYS)
        await _say(client, "ออกแบบใบเสนอราคา")

        stored = next(p for p in store.puts if p["key"].endswith(".html")
                      and not p["key"].endswith("-preview.html"))
        rendered = fill_template(stored["content"].decode("utf-8"), sample_snapshot("quote"))
        assert "บริษัท ชาญ แอร์ เซอร์วิส จำกัด" in rendered
        assert "QT-2026-0042" in rendered
        assert "เครื่องปรับอากาศ" in rendered
        assert "Noto Sans Thai" in rendered
        assert "{{" not in rendered
