"""Round 21C ship-blocker fixes (final whole-branch review, 23 ก.ย. 2569).

I2 — the credit is described as what it is, charged only when the model
     answered, and said on LINE for a words-only answer too.
I3 — the dashboard question box offers the five first, exactly as chat
     does, so the same sentence is free on both surfaces.
I8a — a TYPED picture sentence (not the button's) is never answered by a
     free report, on either surface.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

from chann_app import routers_phase2  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import basic_reports, chart_quota, chat, reports_ai  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

AI_REPORTS = (ROOT / "presentation/app/liff/sales/reports/ai/AiReports.tsx").read_text(encoding="utf-8")
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
EN = (ROOT / "presentation/lib/i18n/en.ts").read_text(encoding="utf-8")

REPORT = {
    "key": "outstanding_invoices", "title_th": "ยอดค้างชำระ", "title_en": "Outstanding invoices",
    "unit": "money",
    "headline": {"label_th": "ค้างชำระทั้งหมด", "label_en": "All outstanding", "value": 96300.0},
    "rows": [{"key": "overdue", "label_th": "เลยกำหนด", "label_en": "Overdue", "value": 64200.0},
             {"key": "not_due", "label_th": "ยังไม่ถึงกำหนด", "label_en": "Not yet due",
              "value": 32100.0}],
    "notes_th": [], "notes_en": [], "generated_at": "2026-09-23T04:00:00+00:00",
}
STAFF = TenantPrincipal(
    license_id="L1", chann_uid="CHN-S-000001", role="owner", is_owner=True,
    permission_keys=frozenset({"view_reports"}), audience="staff",
)


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class RouteClient:
    def __init__(self):
        self.basic_asked = []

    async def basic_report(self, license_id, key):
        self.basic_asked.append(key)
        return REPORT


@pytest.fixture
def spent(monkeypatch):
    calls = []

    async def spend_one(client, *, license_id):
        calls.append(license_id)
        return {"allowed": True, "used": len(calls), "allowance": 30}

    monkeypatch.setattr(chart_quota, "spend_one", spend_one)
    return calls


@pytest.fixture
def engine(monkeypatch):
    """The Phase 17 engine, recorded: the AI road was taken iff it ran."""
    calls = []

    async def handle_report_request(client, **kwargs):
        calls.append(kwargs)
        return {"spec": {"entity": "invoices", "metric": "sum"}, "result": {"rows": [], "total": 1},
                "text": "ยอดค้าง 1 บาท", "files": {}, "chart": None, "plottable": False}

    monkeypatch.setattr(reports_ai, "handle_report_request", handle_report_request)
    return calls


def _chooser(monkeypatch, answer):
    asked = []

    async def choose_report(message, *, client=None, language="th"):
        asked.append(message)
        return answer

    monkeypatch.setattr(basic_reports, "choose_report", choose_report)
    return asked


# ------------------------------------------------------------------ I3


class TestTheDashboardOffersTheFiveFirst:
    @pytest.mark.asyncio
    async def test_one_of_the_five_is_answered_free_by_its_report(self, monkeypatch, spent, engine):
        asked = _chooser(monkeypatch, "outstanding_invoices")
        client = RouteClient()
        out = await routers_phase2.ai_report_ask(
            license_id="L1", body=routers_phase2.AiReportAskBody(message="ยอดค้างชำระ"),
            principal=STAFF, client=client)
        assert asked == ["ยอดค้างชำระ"]
        assert out["basic"]["key"] == "outstanding_invoices" and out["free"] is True
        assert "96,300" in out["text"]
        assert client.basic_asked == ["outstanding_invoices"]
        assert engine == [] and spent == [] and "quota" not in out

    @pytest.mark.asyncio
    async def test_none_keeps_the_ai_road_and_its_credit(self, monkeypatch, spent, engine):
        _chooser(monkeypatch, None)
        out = await routers_phase2.ai_report_ask(
            license_id="L1", body=routers_phase2.AiReportAskBody(message="ยอดค้างแยกตามสถานะ"),
            principal=STAFF, client=RouteClient())
        assert len(engine) == 1 and "basic" not in out
        assert spent == ["L1"] and out["quota"]["charged_for"] == "question"

    @pytest.mark.asyncio
    async def test_a_typed_picture_sentence_never_meets_the_chooser(self, monkeypatch, spent, engine):
        # I8a on the dashboard: a picture is the metered road.
        asked = _chooser(monkeypatch, "outstanding_invoices")
        out = await routers_phase2.ai_report_ask(
            license_id="L1", body=routers_phase2.AiReportAskBody(message="ขอยอดค้างชำระเป็นกราฟ"),
            principal=STAFF, client=RouteClient())
        assert asked == [] and len(engine) == 1 and "basic" not in out

    @pytest.mark.asyncio
    async def test_both_surfaces_use_one_chooser(self):
        import inspect

        assert "basic_report_asked_for" in inspect.getsource(routers_phase2.ai_report_ask)
        assert "basic_report_asked_for" in inspect.getsource(chat._handle_ai_report)

    def test_the_page_renders_the_free_report_and_says_it_is_free(self):
        assert "answer?.basic" in AI_REPORTS
        assert "copy.basic.answeredFree" in AI_REPORTS
        assert "answeredFree" in TH and "answeredFree" in EN


# ------------------------------------------------------------------ I8a


class TestATypedPictureSentenceIsNeverAFreeReport:
    @pytest.mark.asyncio
    async def test_on_line(self, monkeypatch):
        """Not the "ดูเป็นรูป" button's sentence — one a person types. The
        chooser must never be asked, and the free words-only reply (which
        always offers "ดูเป็นรูป") must never come back."""
        asked = _chooser(monkeypatch, "outstanding_invoices")
        client = FakeDataClient(permission_keys=["invoice.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai(
            '{"entity": "invoices", "metric": "sum", "field": "outstanding", '
            '"filters": {}, "group_by": "status"}'
        )) as ai:
            reply = await chat.handle_chat_message(
                client, message="ขอดูยอดค้างชำระแยกตามสถานะเป็นกราฟ", ctx=_ctx(),
                language="th", ai_client=ai)
        assert asked == [], reply.text
        assert not [label for label, _ in reply.quick_replies if "รูป" in label], reply.quick_replies


# ------------------------------------------------------------------ I2


class TestTheCreditIsSaidAsWhatItIs:
    @pytest.mark.asyncio
    async def test_a_words_only_line_answer_says_the_credit_it_spent(self):
        client = FakeDataClient(permission_keys=["deal.read", "view_reports"])
        async with httpx.AsyncClient(transport=_ai(
            '{"entity": "deals", "metric": "count", "filters": {}, "group_by": "stage"}'
        )) as ai:
            reply = await chat._handle_ai_report(
                client, ctx=_ctx(), license_id=LICENSE_ID, message="สรุปจำนวนดีลแยกตามสถานะ",
                permission_keys=["deal.read", "view_reports"], language="th", ai_client=ai)
        spent = [c for c in client.recorded if c[0] == "consume_ai_chart_quota"]
        assert len(spent) == 1
        assert chat.AI_CREDIT_USED["th"].split("{")[0] in reply.text, reply.text
        assert "กราฟนี้สร้างด้วย AI" not in reply.text
        assert len(reply.text) <= 700 and reply.text.count("\n") + 1 <= 15, reply.text

    def test_the_picture_reply_carries_the_same_credit_line(self):
        credit = chat.AI_CREDIT_USED["th"]
        assert credit in chat.CHART_MADE_BY_AI["th"]
        assert chat.AI_CREDIT_USED["en"] in chat.CHART_MADE_BY_AI["en"]

    def test_the_dashboard_never_calls_a_words_answer_a_chart(self):
        assert "quotaUsedWords" in AI_REPORTS
        assert "quotaUsedWords" in TH and "quotaUsedWords" in EN
        words = TH[TH.index("quotaUsedWords"):].split("\n")[0]
        assert "กราฟ" not in words and "เครดิตรายงาน AI" in words

    def test_the_receipt_says_what_it_was_charged_for(self):
        assert chart_quota.receipt({"allowed": True, "used": 1, "allowance": 30},
                                   charged_for="picture")["charged_for"] == "picture"


class TestTheSpecEditorIsFreeWithoutAModelCall:
    @pytest.fixture
    def run(self, monkeypatch):
        class Client:
            async def run_report_query(self, license_id, spec, actor_id=None):
                return self.result

        async def files(*args, **kwargs):
            return {}

        monkeypatch.setattr(reports_ai, "publish_files", files)
        monkeypatch.setattr(reports_ai, "validate_query_spec", lambda spec: spec)
        return Client()

    @pytest.mark.asyncio
    async def test_a_rerun_with_no_picture_costs_nothing(self, monkeypatch, spent, run):
        async def no_chart(*args, **kwargs):
            return None, False

        monkeypatch.setattr(reports_ai, "publish_chart_for", no_chart)
        run.result = {"rows": [{"label": "ก", "value": 1}], "total": 1}
        out = await routers_phase2.ai_report_run(
            license_id="L1", body=routers_phase2.AiReportRunBody(
                spec={"entity": "deals", "metric": "count", "group_by": "stage"}),
            principal=STAFF, client=run)
        assert spent == [] and "quota" not in out

    @pytest.mark.asyncio
    async def test_a_single_number_card_drawn_by_code_costs_nothing(self, monkeypatch, spent, run):
        async def value_card(*args, **kwargs):
            return "https://x/v.png", True

        monkeypatch.setattr(reports_ai, "publish_chart_for", value_card)
        run.result = {"rows": [], "total": 5}
        out = await routers_phase2.ai_report_run(
            license_id="L1", body=routers_phase2.AiReportRunBody(
                spec={"entity": "deals", "metric": "count", "group_by": None}),
            principal=STAFF, client=run)
        assert spent == [] and out["chart"] == "https://x/v.png"

    @pytest.mark.asyncio
    async def test_a_designed_picture_is_the_one_thing_charged(self, monkeypatch, spent, run):
        async def designed(*args, **kwargs):
            return "https://x/c.png", True

        monkeypatch.setattr(reports_ai, "publish_chart_for", designed)
        run.result = {"rows": [{"label": "ก", "value": 1}, {"label": "ข", "value": 2}], "total": 3}
        out = await routers_phase2.ai_report_run(
            license_id="L1", body=routers_phase2.AiReportRunBody(
                spec={"entity": "deals", "metric": "count", "group_by": "stage"}),
            principal=STAFF, client=run)
        assert spent == ["L1"] and out["quota"]["charged_for"] == "picture"


# ------------------------------------------------------------------ I4


class TestEveryDealValueReadsTheOneHelper:
    """Ruling 23 (I4): the three display reads that used `amount` alone —
    the chat customer card's open deals, the deal archive confirmation and
    the chat side-panel's customer context — go through `deal_value`, so a
    deal valued by its lines shows that value instead of nothing."""

    LINES_ONLY = {"deal_id": "D-2026-0001", "amount": None,
                  "products": [{"quoted_unit_price": "15000.00", "qty": 2}]}

    def test_a_line_only_deal_shows_its_value(self):
        assert chat._deal_value_tail(self.LINES_ONLY) == " · 30,000"

    def test_a_typed_value_wins_and_a_typed_zero_is_an_answer(self):
        assert chat._deal_value_tail({**self.LINES_ONLY, "amount": "25000.00"}) == " · 25,000"
        assert chat._deal_value_tail({**self.LINES_ONLY, "amount": "0"}) == " · 0"

    def test_a_deal_with_nothing_on_it_says_nothing(self):
        assert chat._deal_value_tail({"amount": None, "products": []}) == ""

    def test_the_two_chat_sites_use_it(self):
        import inspect

        source = inspect.getsource(chat)
        assert "_amount_tail(d.get('amount'))" not in source
        assert 'amount = deal.get("amount")\n    return ChatReply(' not in source

    def test_the_screens_share_one_typescript_mirror(self):
        helper = (ROOT / "presentation/app/liff/sales/_deal-value.ts").read_text(encoding="utf-8")
        assert "export function dealValue" in helper
        context = (ROOT / "presentation/app/liff/sales/chats/_customer-context.tsx").read_text(
            encoding="utf-8")
        detail = (ROOT / "presentation/app/liff/sales/deals/[id]/DealDetail.tsx").read_text(
            encoding="utf-8")
        deals = (ROOT / "presentation/app/liff/sales/deals/DealList.tsx").read_text(encoding="utf-8")
        assert "Number(deal.amount) > 0" not in context
        for page in (context, detail, deals):
            assert "dealValue(" in page and "_deal-value" in page

    @pytest.mark.asyncio
    async def test_the_deal_card_says_a_typed_value_that_differs_from_its_lines(self):
        text = chat._deal_value_line(
            {**self.LINES_ONLY, "amount": "25000.00"}, subtotal=__import__("decimal").Decimal("30000"),
            language="th")
        assert "25,000.00" in text
        assert chat._deal_value_line(
            {**self.LINES_ONLY, "amount": None}, subtotal=__import__("decimal").Decimal("30000"),
            language="th") == ""


# ------------------------------------------------------------------ I5


class TestAFullPaymentOnTheDashboardSaysTheDealClosed:
    """Chat says "ปิดดีล D-… … มูลค่าดีล"; the dashboard used to close the
    deal silently. The sheet reads the payment response's `closed_deal`
    (never infers it from status) and says so; nothing when it is null."""

    INVOICES = (ROOT / "presentation/app/liff/sales/invoices/InvoiceList.tsx").read_text(
        encoding="utf-8")

    def test_the_sheet_reads_closed_deal_from_the_payment_response(self):
        assert "closed_deal" in self.INVOICES
        assert "t.dashboard.invoices.dealClosed" in self.INVOICES
        assert "saved.closed_deal ?" in self.INVOICES

    def test_both_languages_have_the_sentence(self):
        for table in (TH, EN):
            line = table[table.index("dealClosed"):].split("\n")[0]
            assert "{deal}" in line and "{amount}" in line
