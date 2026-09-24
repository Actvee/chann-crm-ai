"""Round 21C — the picture road."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))


class TestTheRendererSeamImports:
    def test_the_package_re_exports_what_reports_ai_asks_for(self):
        from chann_app.services.pdf import PdfOptions, get_renderer

        assert PdfOptions().page_format == "A4"
        assert get_renderer("null").name == "null"

    def test_the_report_pdf_path_no_longer_swallows_an_import_error(self):
        source = (ROOT / "application/chann_app/services/reports_ai.py").read_text(encoding="utf-8")
        assert "from .pdf import PdfOptions, get_renderer" in source


import pytest  # noqa: E402

from chann_app.services import chart_plan  # noqa: E402

LABELS = ["เลยกำหนด", "ยังไม่ถึงกำหนด"]
GOOD = {"kind": "bar", "title": "ยอดค้างชำระ", "subtitle": "23 ก.ย. 2569",
        "unit": "money", "series_label": "บาท", "highlight": "เลยกำหนด",
        "labels": ["เลยกำหนด", "ยังไม่ถึงกำหนด"], "note": "เลยกำหนดคิดเป็นสองในสาม"}


class TestTheValidator:
    def test_a_good_plan_becomes_a_frozen_plan(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        assert plan.kind == "bar" and plan.highlight == "เลยกำหนด"
        with pytest.raises(Exception):
            plan.kind = "line"          # frozen

    @pytest.mark.parametrize("bad,why", [
        ({**GOOD, "kind": "pie"}, "is not allowed"),
        ({**GOOD, "labels": ["เลยกำหนด", "อย่างอื่น"]}, "do not match"),
        ({**GOOD, "labels": ["เลยกำหนด"]}, "do not match"),
        ({**GOOD, "unit": "count"}, "does not match"),
        ({**GOOD, "highlight": "ไม่มีอันนี้"}, "not one of the labels"),
        ({**GOOD, "title": "x" * 200}, "too long"),
        ({**GOOD, "note": "<script>alert(1)</script>"}, "markup"),
        ({**GOOD, "values": [1, 2]}, "unknown field"),
    ])
    def test_every_refusal_is_named(self, bad, why):
        with pytest.raises(chart_plan.ChartPlanInvalid, match=why):
            chart_plan.validate_chart_plan(bad, labels=LABELS, unit="money", rows=2)

    def test_the_model_can_never_send_a_number(self):
        assert "values" not in chart_plan.ChartPlan.__dataclass_fields__
        assert "values" not in chart_plan.ALLOWED_FIELDS

    def test_a_value_card_needs_exactly_one_number(self):
        with pytest.raises(chart_plan.ChartPlanInvalid, match="nothing to plot"):
            chart_plan.validate_chart_plan(
                {**GOOD, "kind": "bar", "labels": ["เดียว"]}, labels=["เดียว"], unit="money", rows=1)

    def test_too_many_labels_for_the_kind_is_refused(self):
        many = [f"ช่าง {i}" for i in range(12)]
        with pytest.raises(chart_plan.ChartPlanInvalid, match="too many labels"):
            chart_plan.validate_chart_plan(
                {**GOOD, "kind": "bar", "labels": many, "highlight": None, "unit": "count"},
                labels=many, unit="count", rows=12)


class TestTheRenderer:
    def test_the_page_is_self_contained_and_has_no_javascript(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[10000.0, 5000.0], company_name="ร้านทดสอบ")
        assert "<script" not in html.lower()
        assert "http://" not in html and "https://" not in html   # nothing fetched at render time
        assert "<svg" in html
        # "10,000", not "10,000.00": the brief's fixture pinned the .00 form,
        # but charts.py has always printed money the other way and says why
        # ("nobody scans [1250000.00] correctly on a phone"). Two formats for
        # one report — one when SmartBrowz answers and one when it does not —
        # is the defect; see TestOneFormatterForBothRoads (round 1 review).
        assert "10,000" in html and "ร้านทดสอบ" in html

    def test_the_highlighted_bar_is_the_one_the_plan_named(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[10000.0, 5000.0])
        assert html.count('class="bar highlight"') == 1

    def test_a_long_label_does_not_leave_the_card(self):
        labels = ["ชื่อที่ยาวมากจริง ๆ นะครับยาวจนน่าจะล้นกรอบแน่นอน", "สั้น"]
        plan = chart_plan.validate_chart_plan(
            {**GOOD, "labels": labels, "highlight": None}, labels=labels, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[1234567.0, 1.0])
        assert "…" in html           # truncated, not overflowing


class TestTheFallback:
    @pytest.mark.asyncio
    async def test_a_refused_plan_still_produces_a_picture(self, monkeypatch):
        """An invalid plan is not an error the person sees: the code builds
        the deterministic plan instead (spec §7.5)."""
        async def bad_design(*args, **kwargs):
            raise chart_plan.ChartPlanInvalid("kind 'pie' is not allowed")

        monkeypatch.setattr(chart_plan, "design", bad_design)
        monkeypatch.setattr(chart_plan, "_screenshot", _no_screenshot)
        monkeypatch.setattr(chart_plan, "publish_chart", _fake_publish)
        url, plottable = await chart_plan.publish_for_spec(
            {"entity": "invoices", "metric": "sum", "field": "outstanding", "group_by": "status",
             "date_range": None, "date_field": "issue_date", "filter": {}},
            {"rows": [{"key": "issued", "label": "ออกแล้ว", "value": 10000.0}], "total": 10000.0},
            "th", license_id="L1")
        assert plottable is True
        assert url == "https://example/chart.png"     # the Pillow fallback was published


async def _no_screenshot(html: str) -> bytes | None:
    return None


async def _fake_publish(png, *, license_id, store=None):
    return "https://example/chart.png"


class TestArithmeticIsNeverTheModels:
    """Ruling 1: a plan that names a number, a total, a percentage or any
    arithmetic result is rejected. The schema already makes a value
    impossible; these are the places the model could still type one."""

    def test_a_note_may_not_carry_a_numeral(self):
        with pytest.raises(chart_plan.ChartPlanInvalid, match="may not state a number"):
            chart_plan.validate_chart_plan(
                {**GOOD, "note": "เลยกำหนด 10,000 บาท"}, labels=LABELS, unit="money", rows=2)

    def test_a_thai_numeral_is_a_numeral_too(self):
        with pytest.raises(chart_plan.ChartPlanInvalid, match="may not state a number"):
            chart_plan.validate_chart_plan(
                {**GOOD, "note": "เลยกำหนด ๑๐ ราย"}, labels=LABELS, unit="money", rows=2)

    def test_a_percentage_is_an_arithmetic_result_anywhere(self):
        for field in ("title", "subtitle", "series_label", "note"):
            with pytest.raises(chart_plan.ChartPlanInvalid, match="percentage"):
                chart_plan.validate_chart_plan(
                    {**GOOD, field: "คิดเป็น 66%"}, labels=LABELS, unit="money", rows=2)

    def test_a_finding_said_in_words_is_still_allowed(self):
        # The design is the model's; only the arithmetic is not.
        plan = chart_plan.validate_chart_plan(
            {**GOOD, "note": "เลยกำหนดคิดเป็นสองในสาม"}, labels=LABELS, unit="money", rows=2)
        assert plan.note == "เลยกำหนดคิดเป็นสองในสาม"

    def test_a_date_window_keeps_its_digits(self):
        # "23 ก.ย. 2569" is a window, not a sum.
        plan = chart_plan.validate_chart_plan(
            {**GOOD, "title": "ยอดค้างชำระ 3 เดือนล่าสุด"}, labels=LABELS, unit="money", rows=2)
        assert plan.subtitle == "23 ก.ย. 2569" and "3 เดือน" in plan.title


# --------------------------------------------------------------- fix round 1

ROWS = [("สมชาย", 12.0), ("สมหญิง", 9.0), ("ประวิทย์", 2.0)]
SPEC_ROWS = [{"key": "a", "label": "สมชาย", "value": 12.0},
             {"key": "b", "label": "สมหญิง", "value": 9.0},
             {"key": "c", "label": "ประวิทย์", "value": 2.0}]
REORDERED = ("ประวิทย์", "สมชาย", "สมหญิง")


def _hbar_pairs(html: str):
    """(label, value) in the order the SVG actually draws them."""
    import re

    labels = re.findall(r'<text class="hl"[^>]*>([^<]*)</text>', html)
    values = re.findall(r'<text class="hv"[^>]*>([^<]*)</text>', html)
    return list(zip(labels, values))


def _plan(**over):
    base = {"kind": "hbar", "title": "จำนวนงานแยกตามช่าง", "subtitle": "",
            "unit": "count", "series_label": "งาน", "highlight": None,
            "labels": list(REORDERED), "note": ""}
    return chart_plan.validate_chart_plan(
        {**base, **over}, labels=[label for label, _ in ROWS], unit="count", rows=3)


class TestEachLabelKeepsItsOwnNumber:
    """The model may choose the ORDER; the PAIRING is never its to choose.

    Round 1 review: `zip(plan.labels, values)` married the model's order to
    the Data tier's order, so สมชาย's 12 was drawn under ประวิทย์'s name on
    BOTH roads — the numbers were right and the picture lied."""

    def test_the_values_follow_their_own_labels(self):
        assert chart_plan._values_in_plan_order(_plan(), ROWS) == [2.0, 12.0, 9.0]

    def test_a_label_that_is_not_in_the_result_is_refused(self):
        plan = chart_plan.ChartPlan(
            kind="hbar", title="x", subtitle="", unit="count", series_label="",
            highlight=None, labels=("สมชาย", "ไม่มีคนนี้"), note="")
        with pytest.raises(chart_plan.ChartPlanInvalid, match="not in the result"):
            chart_plan._values_in_plan_order(plan, ROWS)

    def test_duplicate_labels_are_paired_in_the_order_the_rows_came(self):
        rows = [("อื่น ๆ", 5.0), ("แอร์", 9.0), ("อื่น ๆ", 1.0)]
        plan = chart_plan.ChartPlan(
            kind="bar", title="x", subtitle="", unit="count", series_label="",
            highlight=None, labels=("แอร์", "อื่น ๆ", "อื่น ๆ"), note="")
        assert chart_plan._values_in_plan_order(plan, rows) == [9.0, 5.0, 1.0]

    def test_the_svg_draws_each_value_beside_its_own_label(self):
        plan = _plan()
        html = chart_plan.render_chart_html(
            plan, values=chart_plan._values_in_plan_order(plan, ROWS))
        assert _hbar_pairs(html) == [("ประวิทย์", "2"), ("สมชาย", "12"), ("สมหญิง", "9")]

    @pytest.mark.asyncio
    async def test_both_roads_pair_correctly_end_to_end(self, monkeypatch):
        seen: dict = {}

        async def reordering_design(*args, **kwargs):
            return _plan()

        async def capture_page(page: str):
            seen["html"] = page
            return None                       # force the Pillow road as well

        def capture_chart(chart):
            seen["chart"] = chart
            return b"\x89PNG"

        monkeypatch.setattr(chart_plan, "design", reordering_design)
        monkeypatch.setattr(chart_plan, "_screenshot", capture_page)
        monkeypatch.setattr(chart_plan.charts, "render_or_none", capture_chart)
        monkeypatch.setattr(chart_plan, "publish_chart", _fake_publish)
        url, plottable = await chart_plan.publish_for_spec(
            {"entity": "tickets", "metric": "count", "field": None, "group_by": "assigned_to",
             "date_range": None, "date_field": "created_at", "filter": {}},
            {"rows": SPEC_ROWS, "total": 23.0}, "th", license_id="L1")
        assert url and plottable
        # The drawn road: charts.Chart carries (label, value) pairs itself.
        assert seen["chart"].points == [("ประวิทย์", 2.0), ("สมชาย", 12.0), ("สมหญิง", 9.0)]
        # The designed road: the same pairs, in the same order, in the SVG.
        assert _hbar_pairs(seen["html"]) == [("ประวิทย์", "2"), ("สมชาย", "12"), ("สมหญิง", "9")]


class TestOneFormatterForBothRoads:
    """A shop must not read "379,000" when SmartBrowz answered and
    "379,000.00" when it did not — same report, same number, one shape."""

    @pytest.mark.parametrize("value,unit", [
        (379000.0, "money"), (10000.0, "money"), (1340000.0, "money"),
        (0.0, "money"), (2.67, "score"), (12.0, "count"), (66.5, "percent"),
    ])
    def test_the_designed_road_formats_exactly_as_the_drawn_one(self, value, unit):
        from chann_app.services import charts

        assert chart_plan._fmt(value, unit) == charts._fmt(value, money=unit == "money")


class TestTheSmallSharpEdges:
    def test_a_long_title_is_cut_on_a_cell_not_inside_one(self):
        # A plain slice can strand a tone mark; code_plan must not.
        title = "ยอดค้างชำระแล้ว" * 12
        plan = chart_plan.code_plan(title=title, subtitle=title,
                                    labels=["ก", "ข"], unit="money")
        assert len(chart_plan._cells(plan.title)) <= chart_plan.MAX_TEXT
        assert plan.title.endswith("…") and plan.subtitle.endswith("…")
        import unicodedata

        assert unicodedata.category(plan.title[0]) not in ("Mn", "Me")

    def test_the_empty_state_speaks_the_readers_language(self):
        plan = chart_plan.ChartPlan(kind="bar", title="x", subtitle="", unit="count",
                                    series_label="", highlight=None, labels=(), note="")
        from chann_app.services import charts

        assert charts.NO_DATA["en"] in chart_plan.render_chart_html(
            plan, values=[], language="en")
        assert charts.NO_DATA["th"] in chart_plan.render_chart_html(plan, values=[])

    def test_no_dead_rule_is_shipped_in_every_page(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        assert ".pill" not in chart_plan.render_chart_html(plan, values=[1.0, 2.0])

    @pytest.mark.asyncio
    async def test_the_store_reaches_the_place_that_writes_the_file(self, monkeypatch):
        """`publish_chart_for(..., store=…)` used to be dropped on the floor
        the moment the result had rows."""
        seen: dict = {}

        async def capture_publish(png, *, license_id, store=None):
            seen["store"] = store
            return "https://example/chart.png"

        async def no_shot(page: str):
            return None

        monkeypatch.setattr(chart_plan, "_screenshot", no_shot)
        monkeypatch.setattr(chart_plan, "publish_chart", capture_publish)
        sentinel = object()
        await chart_plan.publish_for_spec(
            {"entity": "tickets", "metric": "count", "field": None, "group_by": "assigned_to",
             "date_range": None, "date_field": "created_at", "filter": {}},
            {"rows": SPEC_ROWS, "total": 23.0}, "th", license_id="L1", store=sentinel)
        assert seen["store"] is sentinel

    def test_reports_ai_hands_the_store_on(self):
        source = (ROOT / "application/chann_app/services/reports_ai.py").read_text(encoding="utf-8")
        body = source[source.index("async def publish_chart_for"):]
        body = body[: body.index("async def publish_files")]
        call = body[body.index("chart_plan.publish_for_spec("):]
        assert "store=store" in call[: call.index(")")]


class TestTheQuestionIsChargedOnceAndOnlyWhenAnswered:
    """`_charge_for_the_question` is a money path (round 1 review: nothing
    pinned it, and a rename of out["quota"] would double-charge every shop)."""

    @staticmethod
    def _spent(monkeypatch, calls: list):
        from chann_app.services import chart_quota

        async def spend_one(client, *, license_id):
            calls.append(license_id)
            return {"allowed": True, "used": len(calls), "allowance": 30}

        monkeypatch.setattr(chart_quota, "spend_one", spend_one)

    @pytest.mark.asyncio
    async def test_an_answered_question_costs_exactly_one(self, monkeypatch):
        from chann_app import routers_phase2

        calls: list = []
        self._spent(monkeypatch, calls)
        out = await routers_phase2._charge_for_the_question(
            None, "L1", {"spec": {}, "result": {"rows": [], "total": 1}, "chart": None})
        assert calls == ["L1"]
        assert out["quota"] == {"allowed": True, "used": 1, "allowance": 30, "unknown": False,
                                "charged_for": "question"}

    @pytest.mark.asyncio
    async def test_a_clarifying_question_costs_nothing(self, monkeypatch):
        from chann_app import routers_phase2

        calls: list = []
        self._spent(monkeypatch, calls)
        out = await routers_phase2._charge_for_the_question(
            None, "L1", {"clarify": "แยกตามอะไรดีครับ"})
        assert calls == [] and "quota" not in out

    @pytest.mark.asyncio
    async def test_a_refusal_with_no_result_costs_nothing(self, monkeypatch):
        from chann_app import routers_phase2

        calls: list = []
        self._spent(monkeypatch, calls)
        out = await routers_phase2._charge_for_the_question(
            None, "L1", {"error": "spec_invalid", "message": "…"})
        assert calls == [] and "quota" not in out

    @pytest.mark.asyncio
    async def test_a_request_that_drew_a_picture_is_never_charged_twice(self, monkeypatch):
        from chann_app import routers_phase2

        calls: list = []
        self._spent(monkeypatch, calls)
        already = {"allowed": True, "used": 4, "allowance": 30, "unknown": False}
        out = await routers_phase2._charge_for_the_question(
            None, "L1", {"result": {"total": 1}, "chart": "https://x/c.png", "quota": already})
        assert calls == [] and out["quota"] == already


# ------------------------------------------- final fix round (items 9, 10)


class TestTheReportPdfIsBoundedAndOnlySkipsWhatItShould:
    """The PDF half of the LINE webhook: `reports_ai.publish_files`.

    Item 9: it swallowed every RuntimeError as "renderer not available",
    so a bug of ours that happened to be one read as a provider outage.
    Item 10: it had no bound, while the chart half is cut at
    `chart_plan.SHOT_TIMEOUT_S` — a hung Zoho held the person's reply."""

    @pytest.fixture
    def store(self, monkeypatch):
        from chann_app.config import settings
        from chann_app.services import reports_ai
        from chann_app.services.storage.base import StoredDocument, sha256_hex

        class _Store:
            def __init__(self):
                self.keys = []

            async def put(self, *, key, content, content_type):
                self.keys.append(key)
                return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content),
                                      size=len(content))

        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        it = _Store()
        monkeypatch.setattr(reports_ai, "get_document_store", lambda *a, **k: it)
        return it

    @staticmethod
    def _renderer(monkeypatch, render):
        from chann_app.services import pdf

        class _R:
            name = "fake"

            async def render(self, html, options, idempotency_key):
                return await render()

        monkeypatch.setattr(pdf, "get_renderer", lambda name: _R())

    async def _files(self):
        from chann_app.services import reports_ai

        spec = reports_ai.validate_query_spec({"entity": "deals"})
        return await reports_ai.publish_files(
            spec, {**spec, "rows": [], "total": 1}, "th", license_id="L1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["SmartBrowzNotConfigured", "SmartBrowzRenderError",
                                      "SmartBrowzUnavailable"])
    async def test_the_renderers_own_refusals_skip_the_pdf(self, monkeypatch, store, name):
        from chann_app.services.pdf import smartbrowz

        async def refuse():
            raise getattr(smartbrowz, name)("no")

        self._renderer(monkeypatch, refuse)
        files = await self._files()
        assert files["csv"] and files["html"] and files["pdf"] is None

    @pytest.mark.asyncio
    async def test_the_null_renderer_skips_the_pdf(self, monkeypatch, store):
        async def not_yet():
            raise NotImplementedError("null renderer")

        self._renderer(monkeypatch, not_yet)
        assert (await self._files())["pdf"] is None

    @pytest.mark.asyncio
    async def test_a_runtime_error_that_is_a_bug_of_ours_surfaces(self, monkeypatch, store):
        async def bug():
            raise RuntimeError("dictionary changed size during iteration")

        self._renderer(monkeypatch, bug)
        with pytest.raises(RuntimeError, match="dictionary changed size"):
            await self._files()

    @pytest.mark.asyncio
    async def test_a_hung_renderer_is_cut_at_the_charts_own_bound(self, monkeypatch, store, caplog):
        import asyncio
        import logging

        from chann_app.services import chart_plan

        monkeypatch.setattr(chart_plan, "SHOT_TIMEOUT_S", 0.05)

        async def hang():
            await asyncio.sleep(5)

        self._renderer(monkeypatch, hang)
        with caplog.at_level(logging.INFO, logger="chann_app.services.reports_ai"):
            files = await asyncio.wait_for(self._files(), 2)
        assert files["csv"] and files["pdf"] is None
        assert any("did not answer" in r.getMessage() for r in caplog.records), caplog.text

    def test_one_bound_for_both_halves(self):
        import inspect

        from chann_app.services import reports_ai

        assert "SHOT_TIMEOUT_S" in inspect.getsource(reports_ai.publish_files)


class TestTheAccountsHostIsBoundedToo:
    """Item 10: the SDK refreshes its token against
    `zcatalyst_sdk._constants.ACCOUNTS_URL` (credentials.py:88), whose
    default host is `accounts.localzoho.com` — NOT under "zoho.com", so it
    ran on the SDK's own (60, 30) and a misconfigured accounts URL could
    hold a webhook for minutes."""

    def test_the_sdks_default_accounts_host_gets_our_timeout(self):
        from chann_app.services.pdf import smartbrowz

        assert smartbrowz._is_zoho("https://accounts.localzoho.com/oauth/v2/token")

    def test_whatever_accounts_url_the_sdk_was_given_gets_our_timeout(self):
        from zcatalyst_sdk import _constants as sdk_constants

        from chann_app.services.pdf import smartbrowz

        assert smartbrowz._is_zoho(f"{sdk_constants.ACCOUNTS_URL}/oauth/v2/token")
        assert smartbrowz.SDK_ACCOUNTS_HOST in smartbrowz._ZOHO_HOSTS

    def test_the_timeout_is_really_applied_to_that_host(self, monkeypatch):
        import requests

        from chann_app.services.pdf import smartbrowz

        seen = {}

        def original(self, method, url, *args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")

        monkeypatch.setattr(smartbrowz, "_ORIGINAL_SESSION_REQUEST", original)
        smartbrowz._patched_session_request(
            requests.Session(), "POST", "https://accounts.localzoho.com/oauth/v2/token",
            timeout=(60, 30))
        assert seen["timeout"] == smartbrowz.ZOHO_TIMEOUT

    def test_google_storage_keeps_its_own_limits(self):
        from chann_app.services.pdf import smartbrowz

        assert not smartbrowz._is_zoho("https://storage.googleapis.com/b/o")


# --------------------------------------------- final review I1 (23 ก.ย. 2569)


class TestADonutIsOnlyForPartsOfAWhole:
    """A ring prints the sum of its slices and each slice's share. That sum
    means something only when the numbers ARE parts of one whole: counts or
    sums split by category. Two monthly averages drawn as a donut printed
    "5.3 รวม · 47%/53%" — a number no report computed and nobody can use."""

    DONUT = {**GOOD, "kind": "donut", "highlight": None}

    def test_a_share_of_a_whole_may_be_a_donut(self):
        plan = chart_plan.validate_chart_plan(
            self.DONUT, labels=LABELS, unit="money", rows=2, additive=True)
        assert plan.kind == "donut"

    def test_anything_else_may_not(self):
        with pytest.raises(chart_plan.ChartPlanInvalid, match="parts of one whole"):
            chart_plan.validate_chart_plan(self.DONUT, labels=LABELS, unit="money", rows=2)

    @pytest.mark.parametrize("key,additive", [
        ("pipeline_value", True), ("open_jobs_by_tech", True), ("outstanding_invoices", True),
        ("won_this_month", False), ("satisfaction_avg", False),
    ])
    def test_the_five_say_which_are_parts_of_a_whole(self, key, additive):
        assert (key in chart_plan.SHARE_OF_WHOLE_REPORTS) is additive

    @pytest.mark.parametrize("spec,additive", [
        ({"metric": "count", "group_by": "status"}, True),
        ({"metric": "sum", "group_by": "stage"}, True),
        ({"metric": "avg", "group_by": "status"}, False),
        ({"metric": "min", "group_by": "status"}, False),
        ({"metric": "max", "group_by": "status"}, False),
        ({"metric": "count", "group_by": None}, False),
    ])
    def test_an_ad_hoc_result_is_a_whole_only_when_it_is_a_split_count_or_sum(self, spec, additive):
        assert chart_plan.spec_is_share_of_whole(spec) is additive

    @pytest.mark.parametrize("spec,unit", [
        ({"entity": "surveys", "metric": "avg", "field": "score"}, "score"),
        ({"entity": "surveys", "metric": "count", "field": None}, "count"),
        ({"entity": "deals", "metric": "sum", "field": "amount"}, "money"),
        ({"entity": "invoices", "metric": "avg", "field": "total"}, "money"),
    ])
    def test_the_unit_comes_from_what_was_measured(self, spec, unit):
        # A survey average designed as "money" invited the model to write
        # series_label "บาท" under a satisfaction score.
        assert chart_plan.spec_unit(spec) == unit

    @pytest.mark.asyncio
    async def test_the_design_is_told_whether_a_donut_is_allowed(self, monkeypatch):
        told = {}

        async def fake_complete(*, system_prompt, user_message, max_tokens, client=None):
            told["message"] = user_message
            return {**self.DONUT, "unit": "score", "series_label": "คะแนน",
                    "labels": ["เดือนนี้", "เดือนที่แล้ว"], "note": ""}

        monkeypatch.setattr("chann_app.services.ai.client.complete", fake_complete)
        monkeypatch.setattr(chart_plan, "_screenshot", _no_screenshot)
        monkeypatch.setattr(chart_plan, "publish_chart", _fake_publish)
        drawn = {}
        real = chart_plan._publish

        async def spy(plan, **kwargs):
            drawn["plan"] = plan
            return await real(plan, **kwargs)

        monkeypatch.setattr(chart_plan, "_publish", spy)
        report = {"key": "satisfaction_avg", "unit": "score", "title_th": "คะแนนเฉลี่ย",
                  "rows": [{"label_th": "เดือนนี้", "value": 2.5},
                           {"label_th": "เดือนที่แล้ว", "value": 2.8}]}
        await chart_plan.publish_for_basic_report(None, report=report, license_id="L1")
        assert '"shares_of_whole": false' in told["message"]
        # The model's donut was refused; the code's own design is drawn.
        assert drawn["plan"].kind != "donut"
        assert drawn["plan"].unit == "score"

    @pytest.mark.asyncio
    async def test_a_survey_average_is_designed_as_a_score_not_money(self, monkeypatch):
        seen = {}

        async def fake_plan(question, *, rows, unit, **kwargs):
            seen["unit"], seen["additive"] = unit, kwargs.get("additive")
            return chart_plan.code_plan(title=question, subtitle="", labels=[r[0] for r in rows],
                                        unit=unit), [r[1] for r in rows]

        monkeypatch.setattr(chart_plan, "_plan_and_values", fake_plan)
        monkeypatch.setattr(chart_plan, "_screenshot", _no_screenshot)
        monkeypatch.setattr(chart_plan, "publish_chart", _fake_publish)
        await chart_plan.publish_for_spec(
            {"entity": "surveys", "metric": "avg", "field": "score", "group_by": "status",
             "date_range": None, "date_field": "submitted_at", "filter": {}},
            {"rows": [{"label": "ก", "value": 2.5}, {"label": "ข", "value": 2.8}]},
            "th", license_id="L1")
        assert seen == {"unit": "score", "additive": False}
