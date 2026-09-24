"""Round 21C — the five reports in chat: free, fixed, and reachable by button."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

from chann_app.config import settings  # noqa: E402
from chann_app.services import chat  # noqa: E402


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    """The chooser is a model call; without a key it declines, and a test
    that passes because nothing was asked proves nothing."""
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")

REPORT = {
    "key": "pipeline_value", "title_th": "มูลค่าดีลทั้งหมด", "title_en": "Pipeline value",
    "unit": "money",
    "headline": {"label_th": "มูลค่ารวมทุกดีล", "label_en": "All deals", "value": 379000.0},
    "rows": [{"key": "new", "label_th": "ใหม่", "label_en": "New", "value": 379000.0, "count": 3}],
    "notes_th": ["ยังเปิดอยู่ 379000 บาท"], "notes_en": ["Open 379000"],
    "generated_at": "2026-09-23T04:00:00+00:00",
}


class FakeClient:
    def __init__(self):
        self.asked = []
        self.quota_spent = 0

    async def basic_report(self, license_id, key):
        self.asked.append(key)
        return REPORT

    async def consume_ai_chart_quota(self, license_id, month):
        self.quota_spent += 1
        return {"allowed": True, "used": self.quota_spent, "allowance": 30}


class TestTheFiveAreFree:
    @pytest.mark.asyncio
    async def test_a_basic_report_never_spends_a_credit(self, monkeypatch):
        client = FakeClient()
        reply = await chat._handle_basic_report(
            client, ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        assert "379,000.00" in reply.text
        assert client.quota_spent == 0

    @pytest.mark.asyncio
    async def test_without_view_reports_it_says_so_and_asks_nothing(self):
        client = FakeClient()
        reply = await chat._handle_basic_report(
            client, ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=[], language="th")
        assert client.asked == []
        assert "สิทธิ์" in reply.text

    @pytest.mark.asyncio
    async def test_the_other_four_are_offered_as_buttons(self):
        reply = await chat._handle_basic_report(
            FakeClient(), ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        said = [label for label, _ in reply.quick_replies]
        assert "ยอดค้างชำระ" in said
        assert any("รูป" in label for label in said)

    @pytest.mark.asyncio
    async def test_the_picture_button_leads_to_the_metered_road(self):
        """"ดูเป็นรูป" is the one road that costs a credit (spec §5), so the
        button says the sentence the Phase 17 engine answers — not a second
        free report."""
        reply = await chat._handle_basic_report(
            FakeClient(), ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        picture = [says for label, says in reply.quick_replies if "รูป" in label]
        assert picture and chat.ai_report_asked_outright(picture[0]), reply.quick_replies
        assert chat._wants_a_picture(picture[0]), picture[0]

    @pytest.mark.asyncio
    async def test_the_other_four_buttons_say_a_key_the_router_reads_back(self):
        from chann_app.services import basic_reports

        reply = await chat._handle_basic_report(
            FakeClient(), ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        said = {
            chat._basic_report_key(says)
            for label, says in reply.quick_replies if "รูป" not in label
        }
        assert said == set(basic_reports.REPORT_KEYS) - {"pipeline_value"}

    @pytest.mark.asyncio
    async def test_this_handler_cannot_draw_and_so_cannot_charge(self):
        """No `with_chart` here at all (review, minor 3): the picture of one
        of the five is `chart_plan.publish_for_basic_report` on the metered
        road, which the "ดูเป็นรูป" sentence reaches. This one answers in
        words, carries no image, and has no way to spend a credit."""
        import inspect

        client = FakeClient()
        assert "with_chart" not in inspect.signature(chat._handle_basic_report).parameters
        reply = await chat._handle_basic_report(
            client, ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        assert reply.images == [] and client.quota_spent == 0


class TestTheKeyInASentence:
    def test_a_button_names_one_of_the_five(self):
        assert chat._basic_report_key("รายงาน: pipeline_value") == "pipeline_value"
        assert chat._basic_report_key("รายงาน:outstanding_invoices") == "outstanding_invoices"

    def test_anything_else_is_not_a_key(self):
        assert chat._basic_report_key("รายงาน: อะไรก็ได้") is None
        assert chat._basic_report_key("รายงานยอดขาย") is None
        assert chat._basic_report_key("") is None


class TestItIsReachedBeforePhase17:
    @pytest.mark.asyncio
    async def test_the_button_answers_without_asking_the_model(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai(
            '{"action": "suggest", "fields": {}, "missing": []}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="รายงาน: outstanding_invoices", ctx=_ctx(),
                language="th", ai_client=ai,
            )
        assert "ยอดค้างชำระ" in reply.text and "เลยกำหนด" in reply.text, reply.text
        assert not any(call[0] == "consume_ai_chart_quota" for call in client.recorded)

    @pytest.mark.asyncio
    async def test_a_sentence_the_model_reads_as_one_of_the_five(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai('{"report": "open_jobs_by_tech"}')) as ai:
            reply = await chat.handle_chat_message(
                client, message="สรุปงานค้างแยกตามช่าง", ctx=_ctx(), language="th",
                ai_client=ai,
            )
        assert "งานซ่อมค้างแยกตามช่าง" in reply.text, reply.text
        assert not any(call[0] == "consume_ai_chart_quota" for call in client.recorded)

    @pytest.mark.asyncio
    async def test_a_report_the_model_names_no_key_for_keeps_phase_17(self):
        """None means the old road, unchanged — including its credit rules."""
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai(
            '{"entity": "deals", "metric": "count", "filters": {}, "group_by": "stage"}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="สรุปจำนวนดีลแยกตามสถานะ", ctx=_ctx(), language="th",
                ai_client=ai,
            )
        assert "มูลค่าดีลทั้งหมด" not in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_a_picture_is_never_answered_by_a_free_report(self):
        """The five answer in words; a picture is the metered road, so a
        sentence asking for one must not be swallowed by the free answer.

        Final fix, item 8: this sentence is the "ดูเป็นรูป" button's own,
        and it is now drawn from the report's numbers — so the words DO
        appear, beside the picture (or the sentence saying it could not be
        made). What must never happen is the free, words-only reply: that
        one always offers "ดูเป็นรูป" again, and this one must not."""
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai('{"report": "won_this_month"}')) as ai:
            reply = await chat._handle_ai_report(
                client, ctx=_ctx(), license_id=LICENSE_ID,
                message="สร้างรายงานด้วย AI: ยอดปิดสำเร็จเดือนนี้ เป็นกราฟ",
                permission_keys=["deal.read", "view_reports"], language="th",
                ai_client=ai, with_chart=True,
            )
        assert not [label for label, _ in reply.quick_replies if "รูป" in label], reply.quick_replies
        assert reply.images or "กราฟ" in reply.text, reply.text


class TestWhatTheCreditPaysFor:
    """The five are free; the picture is the one thing that is not.

    A shop that can be sent a picture at all is described here the way
    tests/unit/test_round20c_chart_quota.py describes one — the counter
    only moves when a chart reaches the person, so without a document
    store nothing is ever charged and a test that skipped it would prove
    nothing.
    """

    @pytest.fixture(autouse=True)
    def _a_shop_that_can_receive_pictures(self, monkeypatch):
        from chann_app.services import reports_ai
        from chann_app.services.storage.base import StoredDocument, sha256_hex

        class _Store:
            async def put(self, *, key, content, content_type):
                return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content),
                                      size=len(content))

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: _Store())

    @pytest.mark.asyncio
    async def test_the_free_report_then_its_picture_costs_exactly_one(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])

        async with httpx.AsyncClient(transport=_ai('{"report": "pipeline_value"}')) as ai:
            free = await chat.handle_chat_message(
                client, message="สรุปมูลค่าดีลทั้งหมด", ctx=_ctx(), language="th", ai_client=ai,
            )
        assert "มูลค่าดีลทั้งหมด" in free.text, free.text
        assert [w for w in client.recorded if w[0] == "consume_ai_chart_quota"] == []

        picture = next(says for label, says in free.quick_replies if "รูป" in label)
        async with httpx.AsyncClient(transport=_ai(
            '{"entity": "deals", "metric": "sum", "field": "amount", '
            '"filters": {}, "group_by": "stage"}'
        )) as ai:
            drawn = await chat.handle_chat_message(
                client, message=picture, ctx=_ctx(), language="th", ai_client=ai,
            )
        spent = [w for w in client.recorded if w[0] == "consume_ai_chart_quota"]
        assert len(spent) == 1, client.recorded
        assert drawn.images, drawn.text


class TestInEnglish:
    @pytest.mark.asyncio
    async def test_the_picture_button_still_names_the_metered_road(self):
        reply = await chat._handle_basic_report(
            FakeClient(), ctx=_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="en")
        labels = [label for label, _ in reply.quick_replies]
        assert "Outstanding invoices" in labels, labels
        picture = next(says for label, says in reply.quick_replies if label == "As a picture")
        assert chat.ai_report_asked_outright(picture) and chat._wants_a_picture(picture), picture


class TestTheOwnersOwnSentence:
    """Hook 2 — `_handle_report_intent`, the road four of the five arrive on.

    Review finding 3: every test above enters through `_handle_ai_report`,
    because "สรุป…" passes `_is_ai_report_request`. The owner's own sentence
    does not, and it is the reason this task exists.
    """

    @pytest.mark.asyncio
    async def test_the_sentence_that_used_to_answer_zero(self):
        assert chat._is_ai_report_request("ยอดมูลค่าดีลทั้งหมด") is False  # hook 2, not hook 1
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai(
            # The deployed model's own two answers, measured 23 ก.ย. 2569:
            # read/report for the router, pipeline_value for the chooser.
            '{"action": "read", "entity": "report", "fields": {"type": "sales"}, '
            '"missing": [], "report": "pipeline_value"}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="ยอดมูลค่าดีลทั้งหมด", ctx=_ctx(), language="th", ai_client=ai,
            )
        assert "มูลค่าดีลทั้งหมด" in reply.text and "มูลค่ารวมทุกดีล" in reply.text, reply.text
        assert reply.intent == {"action": "report", "entity": "pipeline_value"}
        assert not any(c[0] == "consume_ai_chart_quota" for c in client.recorded)

    @pytest.mark.asyncio
    async def test_a_question_that_wants_the_jobs_themselves_keeps_its_list(self):
        """Ruling 22 (review finding 1). "มีงานซ่อมค้างไหม" has answered with
        the ticket list since 11 ก.ย. 2569 (tests/unit/chat_corpus.py:982),
        and the chooser now answers `null` for it — measured before and
        after the CHOOSE_PROMPT change. A count per technician is not
        something a person can act on; a job code is."""
        client = FakeDataClient(permission_keys=["deal.read", "view_reports", "ticket.read"])
        client._tickets = [{
            "id": "t1", "ticket_number": "T-2026-0001", "status": "open",
            "customer_name": "สมชาย", "issue_description": "แอร์ไม่เย็น",
        }]
        async with httpx.AsyncClient(transport=_ai(
            # No "report" key: that IS the measured reading (None).
            '{"action": "read", "entity": "report", "fields": {"type": "jobs"}, "missing": []}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="มีงานซ่อมค้างไหม", ctx=_ctx(), language="th", ai_client=ai,
            )
        assert "T-2026-0001" in reply.text, reply.text
        assert "งานค้างทั้งหมด" not in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_when_the_chooser_cannot_be_asked_the_old_road_answers(self):
        """A model that times out, or garbage JSON, or an invented sixth
        report: `choose_report` returns None and nothing is lost."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:  # the router's read succeeds
                return httpx.Response(200, json={"choices": [{"message": {
                    "role": "assistant",
                    "content": '{"action": "read", "entity": "report", "fields": {"type": "sales"}, "missing": []}',
                }}], "usage": {}})
            return httpx.Response(503, json={"error": "upstream is down"})

        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as ai:
            reply = await chat.handle_chat_message(
                client, message="ยอดมูลค่าดีลทั้งหมด", ctx=_ctx(), language="th", ai_client=ai,
            )
        assert calls["n"] >= 2, calls
        assert "สรุปการขาย" in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_the_button_without_the_permission_says_so_and_reads_nothing(self):
        client = FakeDataClient(permission_keys=["deal.read"])
        async with httpx.AsyncClient(transport=_ai(
            '{"action": "suggest", "fields": {}, "missing": []}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="รายงาน: pipeline_value", ctx=_ctx(), language="th", ai_client=ai,
            )
        assert "สิทธิ์" in reply.text, reply.text
        assert not any(c[0] == "basic_report" for c in client.recorded), client.recorded


class TestTheButtonsFitOnLine:
    def test_no_label_is_cut_by_lines_twenty_character_limit(self):
        from chann_app.line.client import _fit_label
        from chann_app.services import basic_reports

        for language in ("th", "en"):
            for key in basic_reports.REPORT_KEYS:
                label = chat._t(
                    chat.BASIC_REPORT_SHORT.get(key) or basic_reports.TITLES[key], language)
                assert _fit_label(label) == label, (language, key, label)
            assert _fit_label(chat._t(chat.BASIC_REPORT_PICTURE, language)) == \
                chat._t(chat.BASIC_REPORT_PICTURE, language)


class TestTheLineRoadChargesTheQuestionLikeTheDashboard:
    """Final fix, item 7: `_handle_ai_report` (the LINE road) spends the
    question credit the way `routers_phase2._charge_for_the_question`
    does — exactly once, after an ad-hoc question is ANSWERED; never for a
    clarifying question, never for a refusal, never for the five."""

    SPEC = ('{"entity": "deals", "metric": "count", "filters": {}, '
            '"group_by": "stage"}')

    @staticmethod
    def _spent(client) -> list:
        return [c for c in client.recorded if c[0] == "consume_ai_chart_quota"]

    async def _ask(self, client, model_says: str, message="สรุปจำนวนดีลแยกตามสถานะ"):
        async with httpx.AsyncClient(transport=_ai(model_says)) as ai:
            return await chat._handle_ai_report(
                client, ctx=_ctx(), license_id=LICENSE_ID, message=message,
                permission_keys=["deal.read", "view_reports"], language="th", ai_client=ai,
            )

    @pytest.mark.asyncio
    async def test_an_answered_ad_hoc_question_costs_exactly_one(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply = await self._ask(client, self.SPEC)
        assert "มูลค่าดีลทั้งหมด" not in reply.text, reply.text      # not a free report
        assert len(self._spent(client)) == 1, client.recorded

    @pytest.mark.asyncio
    async def test_a_clarifying_question_costs_nothing(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply = await self._ask(client, '{"clarify": "อยากดูแยกตามอะไรครับ"}',
                                message="สรุปให้หน่อย")
        assert "แยกตามอะไร" in reply.text, reply.text
        assert self._spent(client) == []

    @pytest.mark.asyncio
    async def test_a_refused_spec_costs_nothing(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        await self._ask(client, '{"entity": "salaries", "metric": "sum"}')
        assert self._spent(client) == []

    @pytest.mark.asyncio
    async def test_a_basic_report_still_costs_nothing(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply = await self._ask(client, '{"report": "pipeline_value"}',
                                message="สรุปมูลค่าดีลทั้งหมด")
        assert "มูลค่าดีลทั้งหมด" in reply.text, reply.text
        assert self._spent(client) == []

    @pytest.mark.asyncio
    async def test_the_dashboard_and_the_line_road_share_one_rule(self):
        """One helper behind both, so the two cannot drift apart."""
        import inspect

        from chann_app import routers_phase2
        from chann_app.services import chart_quota

        assert "chart_quota.charge_for_the_question" in inspect.getsource(
            routers_phase2._charge_for_the_question)
        assert "chart_quota.charge_for_the_question" in inspect.getsource(chat._handle_ai_report)
        assert callable(chart_quota.charge_for_the_question)


class TestThePictureOfOneOfTheFive:
    """Final fix, item 8: `chart_plan.publish_for_basic_report` had no
    caller, so "ดูเป็นรูป" on one of the five went to the Phase 17 engine,
    which asked the model for a NEW spec and drew whatever that spec
    computed — a picture that could disagree with the free numbers right
    above it. The button's own sentence now draws the report it sits under,
    from those numbers, and spends the picture credit once."""

    @pytest.fixture
    def drawn(self, monkeypatch):
        from chann_app.services import chart_plan

        calls = []

        async def publish_for_basic_report(client, *, report, license_id, language="th",
                                           ai_client=None, company_name="", store=None):
            calls.append({"key": report["key"], "language": language,
                          "values": [row["value"] for row in report["rows"]]})
            return "https://x/c.png", True

        monkeypatch.setattr(chart_plan, "publish_for_basic_report", publish_for_basic_report)
        return calls

    @staticmethod
    def _spent(client) -> list:
        return [c for c in client.recorded if c[0] == "consume_ai_chart_quota"]

    async def _press(self, client, key, language="th"):
        from chann_app.services import basic_reports

        said = "th" if language != "en" else "en"
        sentence = chat._t(chat.BASIC_REPORT_PICTURE_SAYS, language).format(
            title=basic_reports.TITLES[key][said])
        async with httpx.AsyncClient(transport=_ai('{"clarify": "should not be asked"}')) as ai:
            return await chat.handle_chat_message(
                client, message=sentence, ctx=_ctx(), language=language, ai_client=ai)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("language", ["th", "en"])
    async def test_the_button_draws_the_report_it_sits_under(self, drawn, language):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply = await self._press(client, "outstanding_invoices", language)
        assert [c["key"] for c in drawn] == ["outstanding_invoices"], reply.text
        assert drawn[0]["language"] == language
        assert reply.images == ["https://x/c.png"], reply.text
        assert not [c for c in client.recorded if c[0] == "run_report_query"]
        assert len(self._spent(client)) == 1, client.recorded

    @pytest.mark.asyncio
    async def test_the_picture_and_the_words_are_the_same_numbers(self, drawn):
        from chann_app.services import basic_reports

        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        report = await basic_reports.fetch(client, license_id=str(LICENSE_ID),
                                           key="pipeline_value")
        reply = await self._press(client, "pipeline_value")
        assert drawn[0]["values"] == [row["value"] for row in report["rows"]]
        assert basic_reports.as_text(report, "th").splitlines()[0] in reply.text

    @pytest.mark.asyncio
    async def test_over_the_allowance_the_numbers_stay_and_the_picture_is_withheld(self, drawn):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        client._ai_chart_quota = 0
        reply = await self._press(client, "won_this_month")
        assert reply.images == []
        assert "ครบ 0 ครั้งของเดือนนี้แล้ว" in reply.text, reply.text
        assert "ยอดปิดสำเร็จเดือนนี้" in reply.text, reply.text

    @pytest.mark.asyncio
    async def test_nothing_drawn_costs_nothing(self, monkeypatch):
        from chann_app.services import chart_plan

        async def nothing(client, **kwargs):
            return None, True

        monkeypatch.setattr(chart_plan, "publish_for_basic_report", nothing)
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        reply = await self._press(client, "satisfaction_avg")
        assert reply.images == [] and self._spent(client) == []

    @pytest.mark.asyncio
    async def test_without_view_reports_nothing_is_drawn(self, drawn):
        client = FakeDataClient(permission_keys=["deal.read"])
        await self._press(client, "pipeline_value")
        assert drawn == [] and self._spent(client) == []

    def test_only_the_buttons_own_sentence_is_read_this_way(self):
        assert chat._basic_report_picture_key(
            "สร้างรายงานด้วย AI: ยอดค้างชำระ เป็นกราฟ") == "outstanding_invoices"
        assert chat._basic_report_picture_key(
            "AI report: Outstanding invoices as a chart") == "outstanding_invoices"
        # Anything that is not the button stays with the made-to-order engine.
        assert chat._basic_report_picture_key(
            "สร้างรายงานด้วย AI: ยอดค้างชำระแยกตามลูกค้า เป็นกราฟ") is None
        assert chat._basic_report_picture_key("ยอดค้างชำระ") is None
        assert chat._basic_report_picture_key("") is None
