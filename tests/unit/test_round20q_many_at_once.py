"""Round 20Q — several records in one sentence, and a question that
remembers what it asked.

Owner, 20 ก.ย. 2569:
  - "อัพเดตสถานะแบบหลายรายการทั้งในแชทและ Dashboard … อัพเดตจากลูกค้ามุ่งหวัง
    เป็นยืนยันหลายๆคน หรือการลบลูกค้าหรือดีล … ทีละหลาย record"
  - the AI report asked "เทียบตามเจ้าของ หรือ แยกตามช่วงเวลา", the owner
    typed "แยกตามเจ้าของ", and got a list of deals.

The model's readings here are the ones DEV's model returned for these
sentences (scripts/dev/ask-model.py, 20 ก.ย. 2569): names come back as an
and_then chain, codes as a list under deal_codes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat, reports_ai  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient as _Fake, _ai, _ai_configured, _ctx  # noqa: E402,F401


class FakeDataClient(_Fake):
    """The base fake never had the customer archive (test_user_review_fixes
    carries its own); the bulk road needs both archives on one client."""

    async def archive_customer(self, license_id, customer_id, actor_id=None):
        self.recorded.append(("archive_customer", license_id, customer_id, actor_id))
        row = next(c for c in self._customers if c["id"] == customer_id)
        row["archived_at"] = "2026-09-20T00:00:00+00:00"
        return row


def _lead(n: int, first: str, last: str = "ใจดี") -> dict:
    return {
        "id": f"CUST-{n}", "customer_id": f"C-2026-{n:04d}", "first_name": first,
        "last_name": last, "phone": f"08123456{n:02d}", "stage": "lead",
    }


def _deal(n: int, stage: str = "proposed") -> dict:
    return {"id": f"DEAL-{n}", "deal_id": f"D-2026-{n:04d}", "stage": stage, "contact_id": "CUST-1", "amount": 1000}


def _chain(action: str, entity: str, names: list[str]) -> str:
    head, *rest = [{"action": action, "entity": entity, "fields": {"target_name": n}, "missing": []} for n in names]
    return json.dumps({**head, "and_then": rest}, ensure_ascii=False)


class TestTheReadingsBecomeSingularIntents:
    def test_a_chain_of_the_same_verb_is_one_bulk(self):
        items = chat._items_done_together(json.loads(_chain("promote", "customer", ["ก", "ข", "ค"])))
        assert [i["fields"]["target_name"] for i in items] == ["ก", "ข", "ค"]

    def test_a_chain_that_mixes_verbs_is_not(self):
        intent = json.loads(_chain("promote", "customer", ["ก", "ข"]))
        intent["and_then"][0]["action"] = "archive"
        assert chat._items_done_together(intent) == []

    def test_codes_under_one_key_fan_out_and_keep_the_stage(self):
        items = chat._items_done_together({
            "action": "update", "entity": "deal",
            "fields": {"deal_codes": ["DL-0001", "DL-0002"], "status": "won"}, "missing": [],
        })
        assert [i["fields"]["deal_code"] for i in items] == ["DL-0001", "DL-0002"]
        assert all(i["fields"]["status"] == "won" and "deal_codes" not in i["fields"] for i in items)

    def test_one_record_goes_its_usual_way(self):
        assert chat._items_done_together({"action": "promote", "entity": "customer", "fields": {"target_name": "ก"}}) == []
        assert chat._items_done_together({"action": "archive", "entity": "deal", "fields": {"deal_codes": ["D-1"]}}) == []

    def test_a_pair_the_bulk_road_does_not_carry_is_left_alone(self):
        assert chat._items_done_together(json.loads(_chain("create", "customer", ["ก", "ข"]))) == []


class TestSeveralCustomers:
    async def test_three_leads_are_confirmed_in_one_sentence(self):
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.update"],
            customers=[_lead(1, "สมชาย"), _lead(2, "สมหญิง"), _lead(3, "สมศรี")],
        )
        ai = httpx.AsyncClient(transport=_ai(_chain("promote", "customer", ["สมชาย", "สมหญิง", "สมศรี"])))
        reply = await handle_chat_message(
            client, message="เปลี่ยน สมชาย สมหญิง สมศรี เป็นลูกค้ายืนยัน", ctx=_ctx(), ai_client=ai,
        )
        promoted = [r for r in client.recorded if r[0] == "promote_customer"]
        assert [r[2] for r in promoted] == ["CUST-1", "CUST-2", "CUST-3"]
        assert "3/3" in reply.text
        assert all(c["stage"] == "contact" for c in client._customers)

    async def test_a_name_that_matches_nobody_is_said_and_the_rest_go_ahead(self):
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.update"],
            customers=[_lead(1, "สมชาย"), _lead(3, "สมศรี")],
        )
        ai = httpx.AsyncClient(transport=_ai(_chain("promote", "customer", ["สมชาย", "สมหญิง", "สมศรี"])))
        reply = await handle_chat_message(client, message="ยืนยัน สมชาย สมหญิง สมศรี", ctx=_ctx(), ai_client=ai)
        assert "2/3" in reply.text
        assert "สมหญิง" in reply.text and "ไม่พบ" in reply.text

    async def test_a_duplicate_name_is_never_guessed(self):
        """Rule 3: two สมชาย get a choice. In a batch the choice cannot be a
        picker (the next name would overwrite it), so the line says several
        match and names them; the unambiguous ones are still done."""
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.update"],
            customers=[_lead(1, "สมชาย", "ใจดี"), _lead(2, "สมชาย", "รักดี"), _lead(3, "สมศรี")],
        )
        ai = httpx.AsyncClient(transport=_ai(_chain("promote", "customer", ["สมชาย", "สมศรี"])))
        reply = await handle_chat_message(client, message="ยืนยัน สมชาย กับ สมศรี", ctx=_ctx(), ai_client=ai)
        promoted = [r[2] for r in client.recorded if r[0] == "promote_customer"]
        assert promoted == ["CUST-3"]
        assert "1/2" in reply.text
        assert client._pending is None  # no picker parked for a later sentence

    async def test_removing_two_asks_once_and_names_both(self):
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.archive"],
            customers=[_lead(1, "สมชาย"), _lead(2, "สมหญิง"), _lead(3, "สมศรี")],
        )
        ai = httpx.AsyncClient(transport=_ai(_chain("archive", "customer", ["สมชาย", "สมหญิง"])))
        reply = await handle_chat_message(client, message="ลบลูกค้า สมชาย กับ สมหญิง", ctx=_ctx(), ai_client=ai)
        assert not [r for r in client.recorded if r[0] == "archive_customer"], "nothing before the confirmation"
        assert client._pending["entity"] == "customer_archive_confirm"
        assert [c["id"] for c in client._pending["fields"]["customers"]] == ["CUST-1", "CUST-2"]
        assert "2 คน" in reply.text and "สมชาย" in reply.text and "สมหญิง" in reply.text
        assert ("ยืนยันลบ", "ยืนยันลบ") in reply.quick_replies

        done = await handle_chat_message(client, message="ยืนยันลบ", ctx=_ctx())
        archived = [r[2] for r in client.recorded if r[0] == "archive_customer"]
        assert archived == ["CUST-1", "CUST-2"]
        assert "2 คน" in done.text and "C-2026-0001" in done.text and "C-2026-0002" in done.text
        assert client._pending is None

    async def test_cancelling_the_batch_archives_nobody(self):
        client = FakeDataClient(
            permission_keys=["customer.read", "customer.archive"],
            customers=[_lead(1, "สมชาย"), _lead(2, "สมหญิง")],
            pending_intent={
                "action": "resolve", "entity": "customer_archive_confirm",
                "fields": {"customers": [_lead(1, "สมชาย"), _lead(2, "สมหญิง")]}, "missing": [],
            },
        )
        await handle_chat_message(client, message="ยกเลิก", ctx=_ctx())
        assert not [r for r in client.recorded if r[0] == "archive_customer"]

    async def test_without_the_key_nothing_is_touched(self):
        client = FakeDataClient(permission_keys=["customer.read"], customers=[_lead(1, "สมชาย"), _lead(2, "สมหญิง")])
        ai = httpx.AsyncClient(transport=_ai(_chain("promote", "customer", ["สมชาย", "สมหญิง"])))
        await handle_chat_message(client, message="ยืนยัน สมชาย สมหญิง", ctx=_ctx(), ai_client=ai)
        assert not [r for r in client.recorded if r[0] == "promote_customer"]


class TestSeveralDeals:
    async def test_three_codes_are_archived_after_one_confirmation(self):
        client = FakeDataClient(
            permission_keys=["deal.read", "deal.archive"],
            customers=[_lead(1, "สมชาย")], deals=[_deal(1), _deal(2), _deal(3)],
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "archive", "entity": "deal",
            "fields": {"deal_codes": ["D-2026-0001", "D-2026-0002", "D-2026-0003"]}, "missing": [],
        })))
        reply = await handle_chat_message(client, message="ลบดีล D-2026-0001 D-2026-0002 D-2026-0003", ctx=_ctx(), ai_client=ai)
        assert client._pending["entity"] == "deal_archive_confirm"
        assert [d["deal_id"] for d in client._pending["fields"]["deals"]] == ["D-2026-0001", "D-2026-0002", "D-2026-0003"]
        assert "3 รายการ" in reply.text
        done = await handle_chat_message(client, message="ยืนยันลบ", ctx=_ctx())
        assert [r[2] for r in client.recorded if r[0] == "archive_deal"] == ["DEAL-1", "DEAL-2", "DEAL-3"]
        assert "3 รายการ" in done.text

    async def test_a_code_that_does_not_exist_is_named_and_the_rest_wait_for_the_yes(self):
        client = FakeDataClient(permission_keys=["deal.read", "deal.archive"], customers=[_lead(1, "สมชาย")], deals=[_deal(1)])
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "archive", "entity": "deal", "fields": {"deal_codes": ["D-2026-0001", "D-2026-0009"]}, "missing": [],
        })))
        reply = await handle_chat_message(client, message="ลบดีล D-2026-0001 D-2026-0009", ctx=_ctx(), ai_client=ai)
        assert "D-2026-0009" in reply.text and "1 รายการ" in reply.text
        assert [d["deal_id"] for d in client._pending["fields"]["deals"]] == ["D-2026-0001"]

    async def test_two_deals_are_won_in_one_sentence(self):
        client = FakeDataClient(
            permission_keys=["deal.read", "deal.update"],
            customers=[_lead(1, "สมชาย")], deals=[_deal(1), _deal(2)],
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "update", "entity": "deal",
            "fields": {"deal_codes": ["D-2026-0001", "D-2026-0002"], "status": "won"}, "missing": [],
        })))
        reply = await handle_chat_message(client, message="ย้ายดีล D-2026-0001 กับ D-2026-0002 ไปชนะ", ctx=_ctx(), ai_client=ai)
        moved = [(r[2], r[3]) for r in client.recorded if r[0] == "transition_deal_stage"]
        assert moved == [("DEAL-1", "won"), ("DEAL-2", "won")]
        assert "2/2" in reply.text

    async def test_two_deals_lost_together_are_asked_why_once(self):
        client = FakeDataClient(
            permission_keys=["deal.read", "deal.update"],
            customers=[_lead(1, "สมชาย")], deals=[_deal(1), _deal(2)],
        )
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "update", "entity": "deal",
            "fields": {"deal_codes": ["D-2026-0001", "D-2026-0002"], "status": "lost"}, "missing": [],
        })))
        reply = await handle_chat_message(client, message="ดีล D-2026-0001 กับ D-2026-0002 แพ้ทั้งคู่", ctx=_ctx(), ai_client=ai)
        assert client._pending["entity"] == "deal_lost_reason"
        assert client._pending["fields"]["deal_ids"] == ["DEAL-1", "DEAL-2"]
        assert ("ข้าม", "ข้าม") in reply.quick_replies

        saved = await handle_chat_message(client, message="ราคาสูงกว่าคู่แข่ง", ctx=_ctx())
        reasons = [(r[2], r[3]) for r in client.recorded if r[0] == "update_deal"]
        assert reasons == [("DEAL-1", {"lost_reason": "ราคาสูงกว่าคู่แข่ง"}), ("DEAL-2", {"lost_reason": "ราคาสูงกว่าคู่แข่ง"})]
        assert "D-2026-0001" in saved.text and "D-2026-0002" in saved.text

    async def test_details_other_than_the_stage_stay_one_deal_at_a_time(self):
        client = FakeDataClient(permission_keys=["deal.read", "deal.update"], customers=[_lead(1, "สมชาย")], deals=[_deal(1), _deal(2)])
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "update", "entity": "deal",
            "fields": {"deal_codes": ["D-2026-0001", "D-2026-0002"], "amount": 5000}, "missing": [],
        })))
        reply = await handle_chat_message(client, message="ตั้งมูลค่า D-2026-0001 กับ D-2026-0002 เป็น 5000", ctx=_ctx(), ai_client=ai)
        assert "ทีละดีล" in reply.text
        assert not [r for r in client.recorded if r[0] in ("update_deal", "transition_deal_stage")]


class TestTheReportRemembersItsQuestion:
    """"สร้างรายงานด้วย AI: จำนวนลูกค้าเทียบกับจำนวนดีล" → "เทียบตามเจ้าของ หรือ
    แยกตามช่วงเวลา" → "แยกตามเจ้าของ" → a list of deals (owner, 20 ก.ย.
    2569). The answer must go back to the report engine with the question."""

    @pytest.fixture
    def engine(self, monkeypatch):
        calls: list[dict] = []
        answers: list[dict] = []

        async def fake(client, **kwargs):
            calls.append(kwargs)
            return answers.pop(0)

        monkeypatch.setattr(reports_ai, "handle_report_request", fake)
        return calls, answers

    async def test_the_answer_is_handed_back_with_the_question(self, engine):
        calls, answers = engine
        question = "เทียบตามเจ้าของ หรือ แยกตามช่วงเวลา"
        answers.append({"clarify": question})
        answers.append({
            "spec": {"entity": "deals"}, "result": {}, "text": "ดีลแยกตามเจ้าของ: ก 3 · ข 2",
            "files": {}, "chart": None, "plottable": True,
        })
        client = FakeDataClient(permission_keys=["view_reports", "deal.read"])
        first = await handle_chat_message(
            client, message="สร้างรายงานด้วย AI: จำนวนลูกค้าเทียบกับจำนวนดีลที่เปิด", ctx=_ctx(),
        )
        assert first.text == question
        assert client._pending["entity"] == "ai_report_clarify"
        assert client._pending["fields"]["question"] == question
        assert calls[0]["clarified"] is None

        second = await handle_chat_message(client, message="แยกตามเจ้าของ", ctx=_ctx())
        assert calls[1]["clarified"] == (question, "แยกตามเจ้าของ")
        assert calls[1]["message"] == calls[0]["message"], "the original request, not the bare answer"
        assert calls[1]["with_chart"] is True, "asked outright = a picture, remembered across the question"
        assert "ดีลแยกตามเจ้าของ" in second.text
        assert client._pending is None

    async def test_a_new_command_typed_instead_drops_the_question(self, engine):
        calls, answers = engine
        answers.append({"clarify": "อยากได้แบบไหน"})
        client = FakeDataClient(permission_keys=["view_reports", "customer.read", "customer.create"])
        await handle_chat_message(client, message="สร้างรายงานด้วย AI: อะไรสักอย่าง", ctx=_ctx())
        assert client._pending["entity"] == "ai_report_clarify"
        ai = httpx.AsyncClient(transport=_ai(json.dumps({
            "action": "create", "entity": "customer",
            "fields": {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}, "missing": [],
        }, ensure_ascii=False)))
        await handle_chat_message(client, message="สร้างลูกค้า สมชาย ใจดี 0812345678", ctx=_ctx(), ai_client=ai)
        assert len(calls) == 1, "the report engine was not asked again"
        assert (client._pending or {}).get("entity") != "ai_report_clarify"


class TestTheEngineCarriesTheAnswer:
    def test_the_prompt_says_what_to_do_with_a_comparison_and_an_answer(self):
        prompt = reports_ai.build_system_prompt()
        assert "compares TWO" in prompt
        assert "The user answered" in prompt or "user's answer" in prompt

    async def test_the_question_and_answer_reach_the_model(self, monkeypatch):
        seen = {}

        async def fake_complete(*, system_prompt, user_message, **kwargs):
            seen["user"] = user_message
            return json.dumps({"entity": "deals", "metric": "count", "field": None, "filter": {},
                               "group_by": "owner_member_id", "date_range": None, "date_field": None})

        monkeypatch.setattr(reports_ai, "complete", fake_complete)
        await reports_ai.generate_query_spec("จำนวนดีล", language="th", clarified=("แบบไหน", "แยกตามเจ้าของ"))
        assert "Request: จำนวนดีล" in seen["user"]
        assert "You asked: แบบไหน" in seen["user"] and "The user answered: แยกตามเจ้าของ" in seen["user"]


class TestTheHowToReadsOneLineAtATime:
    """"เมนูวิธีใช้ตัวอักษรเคลื่อน อ่านไม่รู้เรื่อง" (owner, 20 ก.ย. 2569): the
    sales steps were twelve-clause paragraphs indented with three spaces,
    which a LINE bubble renders as a ragged wall."""

    def test_no_line_is_indented_and_none_runs_on(self):
        from chann_app.services.guides import GUIDES, render_help_step, render_help_text

        for oa in GUIDES:
            for text in [render_help_text(oa, "th"), render_help_text(oa, "en")] + [
                render_help_step(oa, n, "th")[0] for n in range(1, len(GUIDES[oa]["steps"]) + 1)
            ]:
                for line in text.split("\n"):
                    assert not line.startswith(" "), (oa, line)
                    # One idea per line: the old sales step 1 was 1,355
                    # characters in ONE line. (The customer and technician
                    # guides keep their sentences; only the sales one was
                    # rewritten this round.)
                    assert oa != "sales" or len(line) <= 320, (oa, len(line), line[:60])

    def test_a_sales_step_says_what_to_type_at_the_end_of_each_line(self):
        from chann_app.services.guides import render_help_step

        text, _ = render_help_step("sales", 6, "th")
        lines = [l for l in text.split("\n") if l.startswith("•")]
        assert len(lines) >= 20
        assert sum('→ พิมพ์ "' in l for l in lines) >= 15
        assert "▪ ลูกค้า" in text and "▪ ดีล" in text and "▪ นัดหมายและบันทึก" in text
        # The whole step is still one bubble.
        assert len(text) < 5000

    def test_the_bulk_roads_are_in_the_guide_on_both_surfaces(self):
        from chann_app.services.guides import render_help_step

        text, _ = render_help_step("sales", 6, "th")
        assert "เปลี่ยน สมชาย สมหญิง สมศรี เป็นลูกค้ายืนยัน" in text
        assert "เลือกหลายรายการ" in text
        assert "ย้ายดีล D-2026-0001 กับ D-2026-0002 ไปชนะ" in text

    def test_the_page_gets_the_lines_as_data(self):
        """The dashboard guide renders the same lines — as a list, not a
        paragraph — from the JSON the Application composes."""
        from chann_app.services.guides import GUIDES

        step = next(s for s in GUIDES["sales"]["steps"] if s["key"] == "crm")
        assert step["how"] and all(("group" in item) or ("th" in item and "en" in item) for item in step["how"])
        assert step["body"]["th"].count(" · ") == 0, "the lead is one sentence now"
