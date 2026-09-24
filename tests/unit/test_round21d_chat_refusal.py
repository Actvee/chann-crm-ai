"""Round 21D — chat on a plan without the feature (spec §5.3, §6.2).

The plan check runs where the permission check runs: after the model read
the sentence. Order: unknown feature → wrong OA → PLAN → permission."""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services import entitlements
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx


STARTER_REFUSAL = (
    "🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter\n"
    "ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ"
)
OPEN_A_JOB = {"action": "create", "entity": "ticket",
              "fields": {"target_name": "สมชาย", "issue_description": "แอร์ไม่เย็น"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "chann_sales_contact", "LINE @channcrm|https://line.me/R/ti/p/@channcrm")


async def _say(client, message, ai, *, plan="starter", oa="sales", role="owner"):
    ctx = _ctx(primary_role=role, oa=oa)
    if plan is not None:
        ctx.memberships[0]["plan"] = plan_payload(plan)
    return await handle_chat_message(
        client, message=message, ctx=ctx,
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _owner(keys=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=keys or ["ticket.create", "ticket.read", "setting.manage",
                                                      "customer.read", "deal.read"], role="owner")
    client._is_owner = True
    return client


class TestTheRefusalItself:
    async def test_the_owner_hears_the_plan_and_the_contact_in_three_lines(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB)
        assert reply.text == STARTER_REFUSAL + "\nอยากเปิดใช้: ติดต่อทีม Chann1 CRM AI LINE @channcrm"
        assert reply.quick_reply_url == ("ติดต่อทีม Chann1", "https://line.me/R/ti/p/@channcrm")

    async def test_a_salesperson_hears_to_tell_the_owner(self):
        client = FakeDataClient(permission_keys=["ticket.create", "customer.read"], role="sales")
        reply = await _say(client, "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, role="sales")
        assert reply.text == STARTER_REFUSAL + "\nถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย"

    async def test_plan_comes_before_permission(self):
        # This salesperson's role does not hold ticket.create either — the
        # plan is still the truer answer (spec §5.3).
        client = FakeDataClient(permission_keys=["customer.read"], role="sales")
        reply = await _say(client, "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, role="sales")
        assert reply.text.startswith("🔒 «งานบริการ")

    async def test_without_a_contact_the_owner_is_told_who_to_ask(self, monkeypatch):
        monkeypatch.setattr(settings, "chann_sales_contact", "")
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB)
        assert reply.text.endswith("อยากเปิดใช้: ติดต่อทีม Chann1 CRM AI ที่ดูแลร้านของคุณ")
        assert reply.quick_reply_url is None          # no contact → no button (owner decision Q5)

    async def test_five_lines_at_most_even_with_the_ai_hint(self):
        reply = C._plan_refusal("quota.ai_reports_per_month", "th")
        assert reply.text.count("\n") <= 4

    async def test_english(self):
        client = _owner()
        ctx = _ctx(primary_role="owner", oa="sales")
        ctx.memberships[0]["plan"] = plan_payload("starter")
        reply = await handle_chat_message(client, message="open a repair job for Somchai", ctx=ctx, language="en",
                                          ai_client=httpx.AsyncClient(transport=_ai(json.dumps(OPEN_A_JOB))))
        assert reply.text.startswith("🔒 «Service jobs and technicians» is included from the Pro plan — this shop is on Starter.")


class TestOnPro:
    async def test_the_same_sentence_reaches_the_handler(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, plan="pro")
        assert "🔒" not in reply.text

    async def test_no_plan_in_the_membership_is_pro(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, plan=None)
        assert "🔒" not in reply.text

    async def test_making_an_api_key_names_enterprise(self):
        reply = await _say(_owner(["setting.manage"]), "สร้าง API key",
                           {"action": "create", "entity": "api_key", "fields": {}, "missing": []}, plan="pro")
        assert reply.text.startswith("🔒 «เชื่อมต่อ API ภายนอก» อยู่ในแพ็กเกจ Enterprise ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Pro")

    async def test_listing_api_keys_stays_open(self):
        reply = await _say(_owner(["setting.manage"]), "รายการ API key",
                           {"action": "read", "entity": "api_key", "fields": {}, "missing": []}, plan="pro")
        assert "🔒" not in reply.text


class TestSendingADocument:
    """Final fix (Task 8): the central gate is the only plan check on the
    send road — the handler's own copy was dead and is gone."""

    async def test_sending_a_quote_on_starter_is_the_plan_and_the_handler_is_not_reached(self, monkeypatch):
        reached = []

        async def spy(*args, **kwargs):
            reached.append(kwargs)
            return C.ChatReply(text="sent")

        monkeypatch.setattr(C, "_handle_document_send", spy)
        client = _owner(["quote.update", "quote.read", "customer.read"])
        send = {"action": "send", "entity": "quote", "fields": {"code": "Q-2026-0001"}, "missing": []}
        reply = await _say(client, "ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า", send)
        assert reply.text.startswith("🔒 «ผูก LINE ลูกค้ากับร้าน»"), reply.text
        assert reached == []
        on_pro = await _say(client, "ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า", send, plan="pro")
        assert on_pro.text == "sent" and len(reached) == 1


#: Final fix (Task 7): every chat handler that checks the plan, audited
#: against the matrix (data/chann_data/plans.py ROW_MAP). Each guards a
#: capability the matrix LOCKS; none guards an always-on one (sales
#: groups, basic reports, quotes/invoices themselves carry no plan check).
#: A new `_plan_has` site fails this test until it is audited and added.
AUDITED_PLAN_GUARDS = {
    "_ai_reports_locked": "quota.ai_reports_per_month",
    "_execute_intent": "the central gate — feature_for_intent(action, entity)",
    "_handle_approval_list": "feature.service (approval.* family)",
    "_handle_assignment_confirm": "feature.service for a technician-scope rule (Ruling 26)",
    "_handle_basic_report": "the two service reports + the AI picture button only",
    "_handle_invite_request": "feature.service for a technician code only",
    "_handle_survey_summary": "feature.service (named check read/survey)",
    "_handle_team_intent": "feature.service — technician teams (sales groups route elsewhere)",
    "_handle_technician_list": "feature.service — the technician roster",
    "_handle_template_design": "feature.custom_documents",
    "_maybe_chat_policy_setting": "feature.live_chat — the chat answer-time / quiet-close minutes (Ruling 29)",
    "_maybe_handle_teams": "feature.service — technician teams",
    "_no_permission": "Ruling 24 — only when every key's feature is locked",
    "_publish_template_draft": "feature.custom_documents",
    "_template_draft_reply": "feature.custom_documents",
    "suggest_what_you_can_do": "feature_for_intent(action, entity)",
}


def test_every_plan_guard_in_chat_is_audited():
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(C.__file__).read_text(encoding="utf-8"))
    sites = {
        fn.name for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_plan_has"
    }
    assert sites == set(AUDITED_PLAN_GUARDS)


class TestTheHelpers:
    def test_no_permission_names_the_plan_for_a_locked_key(self):
        token = C._PLAN.set((entitlements.PlanView.from_payload(plan_payload("starter")), False))
        try:
            assert C._no_permission("th", "ticket.update").text.startswith("🔒 «งานบริการ")
            assert C._no_permission("th", "setting.manage").text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
            assert C._no_permission("th").text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
        finally:
            C._PLAN.reset(token)

    def test_outside_a_message_the_plan_is_pro(self):
        assert C._plan_has("feature.service") and not C._plan_has("feature.external_api")

    def test_suggest_names_the_plan(self):
        token = C._PLAN.set((entitlements.PlanView.from_payload(plan_payload("starter")), False))
        try:
            text = C.suggest_what_you_can_do(["customer.read"], [], "th",
                                             requested_action="read", requested_entity="warranty")
            assert text.startswith("🔒 «ทะเบียนเครื่อง / ประกัน»")
        finally:
            C._PLAN.reset(token)

    def test_a_data_tier_refusal_body_in_words(self):
        reply = C._plan_reply_from({"error": "member_limit_reached", "limit": 5, "plan": "starter"}, "th")
        assert reply.text == ("ร้านมีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว — "
                              "เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม")


class TestTheInviteTrigger:
    """The invite phrases are a trigger road that never reaches the central
    gate. A technician code on a shop without service would be minted and
    then refused at redeem — so the trigger DECLINES first (decline-only:
    the phrase still acts on nothing it did not act on before)."""

    def _minter(self):
        client = FakeDataClient(permission_keys=["member.manage", "setting.manage"], role="owner")
        client._is_owner = True
        return client

    async def test_a_starter_owner_asking_for_a_technician_code_hears_the_plan(self):
        client = self._minter()
        reply = await _say(client, "ขอรหัสเชิญช่าง", {"action": "read", "entity": "invite", "fields": {}, "missing": []})
        assert reply.text == STARTER_REFUSAL + "\nอยากเปิดใช้: ติดต่อทีม Chann1 CRM AI LINE @channcrm"
        assert not [r for r in client.recorded if r[0] == "create_invite"]

    async def test_on_pro_the_code_is_made(self):
        client = self._minter()
        reply = await _say(client, "ขอรหัสเชิญช่าง", {"action": "read", "entity": "invite", "fields": {}, "missing": []},
                           plan="pro")
        assert "🔒" not in reply.text
        assert [r for r in client.recorded if r[0] == "create_invite"]

    async def test_a_sales_code_on_starter_is_still_made(self):
        client = self._minter()
        await _say(client, "ขอรหัสเชิญทีมขาย", {"action": "read", "entity": "invite", "fields": {}, "missing": []})
        assert [r for r in client.recorded if r[0] == "create_invite"]


class TestAnyOfGuards:
    """`not held & PRODUCT_VIEW_KEYS` passes the whole any-of set. The plan
    is the reason only when EVERY key that would open the door is locked;
    if a role grant of an always-on key would do, the permission lead is
    the true sentence (a Starter member without product.read must not be
    told products need the Pro plan)."""

    def _on(self, code):
        return C._PLAN.set((entitlements.PlanView.from_payload(plan_payload(code)), False))

    def test_a_door_an_always_on_key_opens_is_a_permission_question(self):
        token = self._on("starter")
        try:
            for _ in range(5):     # frozenset order must not decide it
                assert C._no_permission("th", *C.PRODUCT_VIEW_KEYS).text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
                assert C._no_permission("th", *C.WORK_VIEW_KEYS).text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
        finally:
            C._PLAN.reset(token)

    def test_a_door_only_locked_keys_open_is_the_plan(self):
        token = self._on("starter")
        try:
            assert C._no_permission("th", "approval.manage", "approval.view").text.startswith("🔒 «งานบริการ")
        finally:
            C._PLAN.reset(token)


SHRUG = {"action": "suggest", "suggestions": [], "entity": None, "fields": {}, "missing": []}


class TestTheChatMinutes:
    """Ruling 29 (final fix round 1): chat_sla_minutes and
    chat_timeout_minutes are feature.live_chat. On Starter the write is the
    plan refusal and nothing is saved; on Pro it saves, as before. Viewing
    the two values stays open (as GET settings does)."""

    @pytest.mark.parametrize("message,key", [
        ("ตั้งเวลาตอบแชท 15", "chat_sla_minutes"),
        ("ตั้งปิดแชทเมื่อเงียบ 60", "chat_timeout_minutes"),
    ])
    async def test_starter_is_the_plan_and_pro_saves(self, message, key):
        starter = _owner()
        reply = await _say(starter, message, SHRUG, plan="starter")
        assert reply.text.startswith("🔒 «"), reply.text
        assert entitlements.feature_label("feature.live_chat", "th") in reply.text
        assert not [r for r in starter.recorded if r[0] == "put_license_setting"]
        pro = _owner()
        saved = await _say(pro, message, SHRUG, plan="pro")
        assert "🔒" not in saved.text, saved.text
        assert [r[2] for r in pro.recorded if r[0] == "put_license_setting"] == [key]

    async def test_viewing_them_stays_open_on_starter(self):
        reply = await _say(_owner(), "ตั้งค่าแชท", SHRUG, plan="starter")
        assert "🔒" not in reply.text, reply.text
