"""Round 20V — four things that were built and that no person could reach.

Owner, 21 ก.ย. 2569: "ทำได้ในโค้ด แต่คนหาไม่เจอ". The Data tier had
PATCH .../owner and reassign_records since 6 Sep, assignment rules with
no way to show or switch one off, a product archive with no button, and
surveys answered and never read. Each road is played through the real
router with the model stubbed to the answer DEV's model gave on
21 ก.ย. 2569 (scripts/dev/ask-model.py), and the rows written are read
back — a reply is not proof.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import chat  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_app.services.chat import ACTION_PERMISSIONS, handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ai, _ctx  # noqa: E402

MEMBERS = [
    {"id": "m-me", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "first_name": "เจ้าของ", "last_name": "ร้าน"},
    {"id": "m-somying", "chann_uid": "CHN-SOMYING", "role": "sales", "status": "active", "first_name": "สมหญิง", "last_name": "ขยัน"},
    {"id": "m-somchai", "chann_uid": "CHN-SOMCHAI", "role": "sales", "status": "active", "first_name": "สมชาย", "last_name": "ขาย"},
    {"id": "m-somying2", "chann_uid": "CHN-SOMYING2", "role": "cs", "status": "active", "first_name": "สมหญิง", "last_name": "ซีเอส"},
    # A technician-side row: never a target for a customer or a deal.
    {"id": "m-tech", "chann_uid": "CHN-TECH", "role": "technician", "status": "active", "channel": "technician", "first_name": "สมหญิง", "last_name": "ช่าง"},
]
ALL_KEYS = ["customer.read", "deal.read", "reassign_records", "setting.manage", "view_reports"]

# What DEV's model returned for each sentence (ask-model.py, 21 ก.ย. 2569).
TRANSFER_ONE = {"action": "transfer", "entity": "customer", "fields": {"target_name": "สมชาย", "to_name": "สมหญิง ขยัน"}, "missing": []}
TRANSFER_DEAL = {"action": "transfer", "entity": "deal", "fields": {"deal_code": "D-2026-0001", "to_name": "สมหญิง ขยัน"}, "missing": []}
TRANSFER_ALL = {"action": "transfer", "entity": "customer", "fields": {"from_name": "สมชาย ขาย", "to_name": "สมหญิง ขยัน", "all": True}, "missing": []}
SURVEY_READ = {"action": "read", "entity": "survey", "fields": {}, "missing": []}
SURVEY_MONTH = {"action": "read", "entity": "survey", "fields": {"period": "month"}, "missing": []}
SURVEY_TECH = {"action": "read", "entity": "survey", "fields": {"target_name": "สมศักดิ์"}, "missing": []}
RULE_DELETE = {"action": "delete", "entity": "assignment_rule", "fields": {}, "missing": []}
SUGGEST = {"action": "suggest", "fields": {}, "missing": [], "suggestions": []}

SUMMARY = {
    "scale": {"1": "ไม่ดี", "2": "พอใช้", "3": "ดีเยี่ยม"}, "answered": 12, "pending": 3, "average": 2.67,
    "response_rate": 0.8, "distribution": {"1": 1, "2": 2, "3": 9},
    "technicians": [{"target_type": "technician", "target_ref": "m-tech", "display_name": "สมศักดิ์ ช่าง",
                     "answered": 7, "average": 2.9, "distribution": {"1": 0, "2": 1, "3": 6}}],
    "recent": [{"survey_id": "s1", "ticket_id": "t1", "ticket_number": "T-2026-0012", "score": 3,
                "score_label": "ดีเยี่ยม", "comment": "ช่างมาเร็ว", "submitted_at": "2026-09-20T03:00:00+00:00"}],
}
RULE = {"id": "r1", "scope": "technician", "is_active": True, "rules_json": {
    "version": 1, "scope": "technician", "selection_strategy": "least_load",
    "match_criteria": [{"field": "product.category", "operator": "equals", "value": "แอร์", "assign_to_team": "AC"}],
}}


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _shop(keys=ALL_KEYS):
    client = FakeDataClient(permission_keys=list(keys), customers=[
        {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี", "stage": "contact", "phone": "0812345678", "owner_member_id": "m-somchai"},
        {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมศรี", "last_name": "มีสุข", "stage": "lead", "phone": "0899999999", "owner_member_id": "m-somchai"},
    ], deals=[{"id": "d1", "deal_id": "D-2026-0001", "stage": "new", "contact_id": "c1", "products": [], "owner_member_id": None}])
    client._members = [dict(m) for m in MEMBERS]
    client._line_targets = {"CHN-SOMYING": "line-somying"}
    return client


async def _say(client, message, intent=None):
    ai = httpx.AsyncClient(transport=_ai(json.dumps(intent or SUGGEST, ensure_ascii=False)))
    return await handle_chat_message(client, message=message, ctx=_ctx(), ai_client=ai)


def _writes(client, *names):
    return [r for r in client.recorded if r[0] in names]


class TestRegistry:
    def test_the_four_roads_are_registered_behind_their_keys(self):
        assert ACTION_PERMISSIONS[("transfer", "customer")] == "reassign_records"
        assert ACTION_PERMISSIONS[("transfer", "deal")] == "reassign_records"
        assert ACTION_PERMISSIONS[("read", "survey")] == "view_reports"
        for verb in ("read", "create", "update", "delete"):
            assert ACTION_PERMISSIONS[(verb, "assignment_rule")] == "setting.manage"

    def test_the_models_own_verb_for_a_bulk_transfer_is_an_alias(self):
        # "โอนลูกค้าทั้งหมดของ สมชาย ให้ สมหญิง" came back as action
        # "reassign_records" before the prompt taught the verb.
        assert chat.ACTION_ALIASES["reassign_records"] == "transfer"
        assert "transfer" in chat._MUTATING_ACTIONS

    def test_the_prompt_teaches_the_new_readings_to_sales_only(self):
        from chann_app.services.ai.intent import build_prompt

        sales = build_prompt(chann_uid="u", role="sales", license_id="l", permission_keys=ALL_KEYS, oa="sales")
        assert 'action="transfer"' in sales and 'entity="survey"' in sales and 'entity="assignment_rule"' in sales
        tech = build_prompt(chann_uid="u", role="technician", license_id="l", permission_keys=["ticket.read"], oa="technician")
        assert 'entity="survey"' not in tech and 'entity="assignment_rule"' not in tech and 'action="transfer"' not in tech


class TestTransferInChat:
    async def test_a_customer_is_handed_over_and_the_new_owner_is_told(self):
        client = _shop()
        reply = await _say(client, "โอนลูกค้า สมชาย ให้ สมหญิง ขยัน", TRANSFER_ONE)
        assert "สมชาย ใจดี (C-2026-0001)" in reply.text and "จาก สมชาย ขาย" in reply.text and "ให้ สมหญิง ขยัน" in reply.text, reply.text
        assert _writes(client, "set_customer_owner") == [("set_customer_owner", LICENSE_ID, "c1", "m-somying")]
        told = _writes(client, "create_notification")
        assert len(told) == 1 and told[0][2] == "CHN-SOMYING" and told[0][3] == "record_reassigned"
        assert "C-2026-0001" in told[0][4]

    async def test_a_deal_is_handed_over_by_code(self):
        client = _shop()
        reply = await _say(client, "โอนดีล D-2026-0001 ให้ สมหญิง ขยัน", TRANSFER_DEAL)
        assert "D-2026-0001" in reply.text and "ยังไม่มีผู้ดูแล" in reply.text and "สมหญิง ขยัน" in reply.text, reply.text
        assert _writes(client, "set_deal_owner") == [("set_deal_owner", LICENSE_ID, "d1", "m-somying")]

    async def test_a_duplicate_colleague_name_is_a_choice_never_a_guess(self):
        client = _shop()
        intent = {**TRANSFER_ONE, "fields": {"target_name": "สมชาย", "to_name": "สมหญิง"}}
        reply = await _say(client, "โอนลูกค้า สมชาย ให้ สมหญิง", intent)
        assert "หลายคน" in reply.text and "1. สมหญิง ขยัน" in reply.text and "2. สมหญิง ซีเอส" in reply.text, reply.text
        # The technician-side สมหญิง is not offered: a deal cannot go there.
        assert "สมหญิง ช่าง" not in reply.text
        assert _writes(client, "set_customer_owner") == []
        sends = [send for _, send in reply.quick_replies]
        assert sends[0] == "โอนลูกค้า สมชาย ให้ CHN-SOMYING"
        # Tapping the button re-runs the sentence with the uid, which
        # resolves exactly.
        reply = await _say(client, sends[0], {**intent, "fields": {"target_name": "สมชาย", "to_name": "CHN-SOMYING"}})
        assert _writes(client, "set_customer_owner") == [("set_customer_owner", LICENSE_ID, "c1", "m-somying")]

    async def test_everything_one_person_holds_is_listed_confirmed_then_moved(self):
        client = _shop()
        reply = await _say(client, "โอนลูกค้าทั้งหมดของ สมชาย ขาย ให้ สมหญิง ขยัน", TRANSFER_ALL)
        assert "2 คน" in reply.text and "สมชาย ใจดี" in reply.text and "สมศรี มีสุข" in reply.text, reply.text
        assert _writes(client, "set_customer_owner") == []
        assert ("ยืนยันโอน", "ยืนยันโอน") in reply.quick_replies
        reply = await _say(client, "ยืนยันโอน")
        assert "2/2" in reply.text, reply.text
        assert [w[2] for w in _writes(client, "set_customer_owner")] == ["c1", "c2"]
        assert len(_writes(client, "create_notification")) == 1

    async def test_cancelling_the_bulk_moves_nothing(self):
        client = _shop()
        await _say(client, "โอนลูกค้าทั้งหมดของ สมชาย ขาย ให้ สมหญิง ขยัน", TRANSFER_ALL)
        reply = await _say(client, "ยกเลิก")
        assert "ยังอยู่กับ สมชาย ขาย" in reply.text
        assert _writes(client, "set_customer_owner") == []

    async def test_without_the_key_nothing_moves(self):
        client = _shop(keys=["customer.read", "deal.read"])
        reply = await _say(client, "โอนลูกค้า สมชาย ให้ สมหญิง ขยัน", TRANSFER_ONE)
        assert "โอนงานให้ผู้รับผิดชอบคนอื่น" in reply.text
        assert _writes(client, "set_customer_owner") == []

    async def test_a_refusal_the_model_read_as_a_transfer_writes_nothing(self):
        client = _shop()
        reply = await _say(client, "ไม่ต้องโอนลูกค้า สมชาย ให้ สมหญิง ขยัน แล้ว", TRANSFER_ONE)
        assert "ยังไม่ได้โอน" in reply.text
        assert _writes(client, "set_customer_owner") == []

    async def test_an_unknown_colleague_is_named_back(self):
        client = _shop()
        reply = await _say(client, "โอนลูกค้า สมชาย ให้ วิชัย", {**TRANSFER_ONE, "fields": {"target_name": "สมชาย", "to_name": "วิชัย"}})
        assert "ไม่พบพนักงานชื่อ วิชัย" in reply.text
        assert _writes(client, "set_customer_owner") == []


class TestSurveysInChat:
    async def test_the_figures_in_a_few_lines(self):
        client = _shop()
        client._survey_summary = dict(SUMMARY)
        reply = await _say(client, "ความพึงพอใจเดือนนี้", SURVEY_MONTH)
        assert "2.67/3" in reply.text and "ตอบแล้ว 12" in reply.text and "80%" in reply.text, reply.text
        assert "สมศักดิ์ ช่าง 2.9 (7)" in reply.text and "T-2026-0012" in reply.text
        assert len(reply.text.split("\n")) <= 8
        assert _writes(client, "survey_summary") == [("survey_summary", LICENSE_ID, 30)]

    async def test_a_technician_is_answered_on_their_own(self):
        client = _shop()
        client._survey_summary = dict(SUMMARY)
        reply = await _say(client, "ความพึงพอใจของช่าง สมศักดิ์", SURVEY_TECH)
        assert reply.text.startswith("ความพึงพอใจของช่าง สมศักดิ์ ช่าง") and "2.9/3" in reply.text and "7 คำตอบ" in reply.text, reply.text

    async def test_the_year_is_asked_for_365_days(self):
        client = _shop()
        client._survey_summary = dict(SUMMARY)
        # DEV's model read this one as report/type=customer_satisfaction;
        # the sentence's own word re-labels it a survey read.
        await _say(client, "ความพึงพอใจปีนี้", {"action": "read", "entity": "report", "fields": {"type": "customer_satisfaction", "period": "year"}, "missing": []})
        assert _writes(client, "survey_summary") == [("survey_summary", LICENSE_ID, 365)]

    async def test_no_answers_says_so(self):
        client = _shop()
        reply = await _say(client, "คะแนนความพึงพอใจ", SURVEY_READ)
        assert "ยังไม่มีคำตอบแบบสำรวจใน 30 วันล่าสุด" in reply.text

    async def test_needs_view_reports(self):
        client = _shop(keys=["customer.read", "deal.read"])
        reply = await _say(client, "คะแนนความพึงพอใจ", SURVEY_READ)
        assert "ยังไม่มีสิทธิ์" in reply.text
        assert _writes(client, "survey_summary") == []


class TestAssignmentRuleInChat:
    async def test_show_then_close_asks_first_and_switches_off(self):
        client = _shop()
        client._assignment_rules = [json.loads(json.dumps(RULE))]
        reply = await _say(client, "ดูกฎมอบหมาย")
        assert "ทีม AC" in reply.text and "งานน้อยที่สุด" in reply.text
        reply = await _say(client, "ปิดกฎมอบหมาย")
        assert "จะปิดกฎมอบหมายช่าง" in reply.text and "ทีม AC" in reply.text and ("ยืนยันปิดกฎ", "ยืนยันปิดกฎ") in reply.quick_replies
        assert _writes(client, "deactivate_assignment_rule") == []
        reply = await _say(client, "ยืนยันปิดกฎ")
        assert "ปิดกฎมอบหมายช่างแล้ว" in reply.text
        assert _writes(client, "deactivate_assignment_rule") == [("deactivate_assignment_rule", LICENSE_ID, "technician")]
        assert client._assignment_rules[0]["is_active"] is False

    async def test_cancel_keeps_the_rule(self):
        client = _shop()
        client._assignment_rules = [json.loads(json.dumps(RULE))]
        await _say(client, "ปิดกฎมอบหมาย")
        reply = await _say(client, "ยกเลิก")
        assert "ยังใช้กฎมอบหมายเดิม" in reply.text
        assert client._assignment_rules[0]["is_active"] is True

    async def test_two_rules_ask_which(self):
        client = _shop()
        client._assignment_rules = [json.loads(json.dumps(RULE)), {**json.loads(json.dumps(RULE)), "id": "r2", "scope": "sales", "rules_json": {"version": 1, "scope": "sales", "match_criteria": [], "selection_strategy": "round_robin"}}]
        reply = await _say(client, "ปิดกฎมอบหมาย")
        assert "2 กฎ" in reply.text and ("ปิดกฎมอบหมายฝ่ายขาย", "ปิดกฎมอบหมายฝ่ายขาย") in reply.quick_replies
        reply = await _say(client, "ปิดกฎมอบหมายฝ่ายขาย")
        assert "จะปิดกฎมอบหมายฝ่ายขาย" in reply.text

    async def test_no_rule_is_said_honestly(self):
        client = _shop()
        reply = await _say(client, "ปิดกฎมอบหมาย")
        assert "ยังไม่ได้ตั้งกฎมอบหมาย" in reply.text

    async def test_the_models_reading_without_the_trigger_word_reaches_the_same_road(self):
        client = _shop()
        client._assignment_rules = [json.loads(json.dumps(RULE))]
        reply = await _say(client, "ไม่ต้องแจกงานอัตโนมัติแล้ว", RULE_DELETE)
        assert "จะปิดกฎมอบหมายช่าง" in reply.text

    async def test_needs_setting_manage(self):
        client = _shop(keys=["customer.read"])
        client._assignment_rules = [json.loads(json.dumps(RULE))]
        reply = await _say(client, "ปิดกฎมอบหมาย")
        assert "ยังไม่มีสิทธิ์" in reply.text


# ---------------------------------------------------------------- HTTP

class RouteFake(FakeDataClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._members = [dict(m) for m in MEMBERS]
        self._teams = [{"id": "team-1", "team_name": "AC"}]
        self._assignment_rules = [json.loads(json.dumps(RULE))]
        self._survey_summary = dict(SUMMARY)

    async def list_sales_groups(self, license_id):
        return []


def _harness(keys, *, is_owner=False):
    client = RouteFake(permission_keys=list(keys))
    principal = TenantPrincipal(license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="owner" if is_owner else "sales",
                                is_owner=is_owner, permission_keys=frozenset(keys), audience="sales")

    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


class TestRoutes:
    def test_rules_are_listed_in_words_behind_setting_manage(self):
        http, _ = _harness(["setting.manage"])
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules").json()
        assert rows[0]["scope"] == "technician" and "ทีม AC" in rows[0]["summary"]
        http, _ = _harness(["customer.read"])
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules").status_code == 403

    def test_a_rule_is_replaced_from_json_and_validated(self):
        http, client = _harness(["setting.manage"])
        body = {"scope": "technician", "rules_json": {"version": 1, "scope": "technician", "match_criteria": [
            {"field": "product.category", "operator": "equals", "value": "ตู้เย็น", "assign_to_team": "AC"}], "selection_strategy": "round_robin"}}
        saved = http.put(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules", json=body).json()
        assert saved["scope"] == "technician" and "ตู้เย็น" in saved["summary"]
        assert any(r[0] == "upsert_assignment_rule" for r in client.recorded)
        # A team that does not exist is refused, as chat refuses it.
        bad = dict(body); bad["rules_json"] = {**body["rules_json"], "match_criteria": [{**body["rules_json"]["match_criteria"][0], "assign_to_team": "Nowhere"}]}
        refused = http.put(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules", json=bad)
        assert refused.status_code == 422 and refused.json()["detail"]["error"] == "rule_invalid"

    def test_a_rule_is_switched_off_and_404_when_none(self):
        http, client = _harness(["setting.manage"])
        assert http.delete(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules/technician").status_code == 200
        assert client._assignment_rules[0]["is_active"] is False
        assert http.delete(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules/technician").status_code == 404
        assert http.delete(f"/api/v1/licenses/{LICENSE_ID}/assignment-rules/nope").status_code == 422

    def test_the_survey_summary_is_behind_view_reports_and_takes_three_windows(self):
        http, client = _harness(["view_reports"])
        body = http.get(f"/api/v1/licenses/{LICENSE_ID}/surveys/summary", params={"days": 90}).json()
        assert body["answered"] == 12 and body["days"] == 90
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/surveys/summary", params={"days": 7}).status_code == 422
        http, _ = _harness(["customer.read"])
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/surveys/summary").status_code == 403

    def test_the_owner_routes_still_want_reassign_records(self):
        http, client = _harness(["reassign_records", "customer.read"])
        client._customers.append({"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "stage": "lead", "owner_member_id": None})
        assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/customers/c1/owner", json={"owner_member_id": "m-somying"}).status_code == 200
        assert client._customers[-1]["owner_member_id"] == "m-somying"
        http, _ = _harness(["customer.update"])
        assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/customers/c1/owner", json={"owner_member_id": "m-somying"}).status_code == 403

    def test_reassign_records_alone_may_read_the_roster(self):
        # The owner picker on the detail pages needs the names.
        http, _ = _harness(["reassign_records"])
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/members").json()
        assert any(r["chann_uid"] == "CHN-SOMYING" for r in rows)
        http, _ = _harness(["customer.read"])
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/members").status_code == 403


class TestReportsWhitelist:
    def test_surveys_average_score_is_a_valid_spec_on_both_tiers(self):
        from chann_app.services import reports_ai
        from chann_data.repositories.phase17 import NUMERIC_FIELDS, validate_spec

        spec = reports_ai.validate_query_spec({"entity": "surveys", "metric": "avg", "field": "score", "date_range": "this_month", "date_field": "submitted_at"})
        assert spec["field"] == "score" and spec["date_field"] == "submitted_at"
        assert validate_spec(spec)["entity"] == "surveys"
        assert set(NUMERIC_FIELDS["surveys"]) == set(reports_ai.NUMERIC_FIELDS["surveys"])
        assert "ความพึงพอใจ" in reports_ai.describe(spec, "th")
        with pytest.raises(Exception):
            validate_spec({"entity": "surveys", "group_by": "assigned_to"})
