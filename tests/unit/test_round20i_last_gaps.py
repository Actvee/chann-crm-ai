"""Round 20i — the last four chat capabilities, and one nobody could reach.

measure-capabilities named them (audit, 17 ก.ย. 2569): `sales member.update`
and `sales role.create/.read/.update` were registered, answered
"ยังทำรายการนี้ไม่ได้", and had working screens the whole time. Separately,
`clear_recent_turns` had no caller anywhere — there was no way to tell the
assistant to forget a thread it had misread, which is the moment a person
most wants one.

Every intent below is the shape the DEPLOYED model really returns for that
sentence (ask-model.py, 18 ก.ย. 2569), not a shape invented to suit the
handler.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["member.manage", "role.manage", "customer.read"]


def _shop(keys=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=keys or KEYS, role="owner")
    client._members = [
        {"id": "m1", "chann_uid": "U-owner", "role": "owner", "channel": "sales",
         "status": "active", "is_owner": True, "first_name": "สมชาย", "last_name": "ใจดี"},
        {"id": "m2", "chann_uid": "U-cs", "role": "cs", "channel": "sales",
         "status": "active", "is_owner": False, "first_name": "สมหญิง", "last_name": "ขยัน"},
        {"id": "m3", "chann_uid": "U-tech", "role": "technician", "channel": "technician",
         "status": "active", "is_owner": False, "first_name": "สมศักดิ์", "last_name": "อดทน"},
    ]
    return client


async def _say(client, message, ai, *, oa="sales", role="owner"):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role=role, oa=oa),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestNoCapabilityIsRegisteredWithNothingBehindIt:
    async def test_every_pair_has_a_handler(self):
        """The gate measures this too; pinned here so a new row in
        ACTION_PERMISSIONS with no handler fails a test rather than only
        moving a number in a report."""
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            [sys.executable, str(root / "scripts/dev/measure-capabilities.py")],
            capture_output=True, text=True, cwd=root, timeout=600,
        ).stdout
        assert "· 0 with no handler ·" in out, out[-400:]


class TestChangingWhatOnePersonMayDo:
    async def test_a_role_change_reaches_the_tier(self):
        client = _shop()
        reply = await _say(client, "เปลี่ยนบทบาทสมหญิงเป็น admin", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมหญิง", "role": "admin"}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "set_member_role"]
        assert wrote and wrote[0][3] == "admin", (wrote, reply.text)
        assert "สมหญิง" in reply.text

    async def test_a_role_this_shop_does_not_have_is_refused_by_name(self):
        client = _shop()
        reply = await _say(client, "เปลี่ยนบทบาทสมหญิงเป็นผู้จัดการ", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมหญิง", "role": "ผู้จัดการ"}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "set_member_role"]
        assert "ผู้จัดการ" in reply.text and "cs" in reply.text

    async def test_the_owner_cannot_be_demoted_from_chat(self):
        client = _shop()
        reply = await _say(client, "เปลี่ยนบทบาทสมชายเป็น cs", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมชาย", "role": "cs"}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "set_member_role"]
        assert "เจ้าของ" in reply.text

    async def test_taking_someone_off_says_what_survives(self):
        client = _shop()
        reply = await _say(client, "เอาสมศักดิ์ออกจากร้าน", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมศักดิ์", "status": "removed"}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "set_member_status"]
        assert wrote and wrote[0][3] == "removed", wrote
        assert "ยังอยู่" in reply.text, reply.text

    async def test_putting_them_back(self):
        client = _shop()
        await _say(client, "ให้สมศักดิ์กลับมาใช้งานได้", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมศักดิ์", "status": "active"}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "set_member_status"]
        assert wrote and wrote[0][3] == "active", wrote

    async def test_a_name_this_shop_does_not_employ(self):
        client = _shop()
        reply = await _say(client, "เปลี่ยนบทบาทสมปองเป็น cs", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมปอง", "role": "cs"}, "missing": [],
        })
        assert "ไม่พบ" in reply.text
        assert not [r for r in client.recorded if r[0] == "set_member_role"]

    async def test_without_member_manage_nothing_is_written(self):
        client = _shop(keys=["customer.read"])
        await _say(client, "เปลี่ยนบทบาทสมหญิงเป็น admin", {
            "action": "update", "entity": "member",
            "fields": {"target_name": "สมหญิง", "role": "admin"}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "set_member_role"]


class TestRoles:
    async def test_the_list_answers(self):
        reply = await _say(_shop(), "มีบทบาทอะไรบ้าง", {
            "action": "read", "entity": "role", "fields": {}, "missing": [],
        })
        assert "ยังทำรายการนี้ไม่ได้" not in reply.text
        assert "cs" in reply.text and "เจ้าของ" in reply.text

    async def test_creating_one(self):
        client = _shop()
        reply = await _say(client, "สร้างบทบาทใหม่ชื่อ หัวหน้าช่าง", {
            "action": "create", "entity": "role",
            "fields": {"role_name": "หัวหน้าช่าง"}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "create_role"]
        assert wrote and wrote[0][2]["role_name"] == "หัวหน้าช่าง", wrote
        assert "หัวหน้าช่าง" in reply.text

    async def test_a_permission_is_ADDED_not_substituted(self):
        """The model returns only what the SENTENCE named. Sending that as
        the whole set would strip everything the role already had."""
        client = _shop()
        client._roles = [
            {"role_name": "cs", "is_owner": False,
             "permission_keys": ["customer.read", "ticket.read"]},
        ]
        await _say(client, "ให้บทบาท cs ดูใบเสนอราคาได้ด้วย", {
            "action": "update", "entity": "role",
            "fields": {"role_name": "cs", "permissions": ["quote.read"]}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "update_role"]
        assert wrote, client.recorded
        sent = set(wrote[0][3]["permission_keys"])
        assert {"customer.read", "ticket.read", "quote.read"} <= sent, sent

    async def test_a_permission_named_in_the_shops_own_words(self):
        client = _shop()
        client._roles = [{"role_name": "cs", "is_owner": False, "permission_keys": []}]
        await _say(client, "ให้บทบาท cs ดูใบเสนอราคาได้ด้วย", {
            "action": "update", "entity": "role",
            "fields": {"role_name": "cs", "permissions": ["ดูใบเสนอราคา"]}, "missing": [],
        })
        wrote = [r for r in client.recorded if r[0] == "update_role"]
        assert wrote and wrote[0][3]["permission_keys"], wrote

    async def test_a_word_that_is_no_permission_is_said_out_loud(self):
        client = _shop()
        client._roles = [{"role_name": "cs", "is_owner": False, "permission_keys": []}]
        reply = await _say(client, "ให้บทบาท cs ทำอะไรก็ได้", {
            "action": "update", "entity": "role",
            "fields": {"role_name": "cs", "permissions": ["ทำอะไรก็ได้"]}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "update_role"]
        assert "ไม่รู้จัก" in reply.text, reply.text

    async def test_the_owner_role_is_not_editable(self):
        client = _shop()
        reply = await _say(client, "ให้บทบาท owner ดูใบเสนอราคา", {
            "action": "update", "entity": "role",
            "fields": {"role_name": "owner", "permissions": ["quote.read"]}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "update_role"]
        assert "เจ้าของ" in reply.text

    async def test_without_role_manage_nothing_is_written(self):
        client = _shop(keys=["customer.read"])
        await _say(client, "สร้างบทบาทใหม่ชื่อ หัวหน้าช่าง", {
            "action": "create", "entity": "role",
            "fields": {"role_name": "หัวหน้าช่าง"}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "create_role"]


class TestStartingOver:
    async def test_the_thread_is_cleared_and_nothing_else_is(self):
        client = _shop()
        reply = await _say(client, "ลืมที่คุยไปก่อนหน้านี้", {
            "action": "delete", "entity": "conversation", "fields": {}, "missing": [],
        })
        assert [r for r in client.recorded if r[0] == "clear_recent_turns"], client.recorded
        # It says so, because "ล้าง" sounds like it might have deleted records.
        assert "ยังอยู่ครบ" in reply.text, reply.text

    def test_the_escape_hatch_matches_only_whole_messages(self):
        """"เริ่มใหม่" inside another sentence must not wipe the thread."""
        assert C._asks_to_start_over("เริ่มใหม่")
        assert C._asks_to_start_over("ล้างบทสนทนา")
        assert C._asks_to_start_over("start over")
        for keeps_going in (
            "เปิดงานใหม่", "สร้างดีลใหม่ให้สมชาย", "ลูกค้าใหม่วันนี้กี่คน",
            "เริ่มใหม่ได้ไหมถ้าลูกค้าขอเปลี่ยนสินค้า",
        ):
            assert not C._asks_to_start_over(keeps_going), keeps_going

    async def test_it_works_with_a_form_already_waiting(self):
        """The reason it is deterministic: a pending form owns the next
        message and the model never sees it."""
        client = _shop()
        await client.set_pending_intent(
            "CHN-S-000001", "sales", action="create", entity="customer",
            fields={"first_name": "สม"}, missing=["phone"], ttl_seconds=600,
        )
        reply = await handle_chat_message(
            client, message="เริ่มใหม่", ctx=_ctx(primary_role="owner", oa="sales"),
            ai_client=None,
        )
        assert "ยังอยู่ครบ" in reply.text, reply.text
        assert await client.get_pending_intent("CHN-S-000001", "sales") in (None, {})

    async def test_clearing_needs_no_permission_at_all(self):
        """It is this person's own scratchpad, not the shop's data."""
        client = _shop(keys=[])
        reply = await handle_chat_message(
            client, message="เริ่มใหม่", ctx=_ctx(primary_role="owner", oa="sales"),
            ai_client=None,
        )
        assert "ยังอยู่ครบ" in reply.text, reply.text
        assert C.required_permission("delete", "conversation") is None


# --------------------------------------- the customer-chat list: loading vs empty

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CHATS = ROOT / "presentation/app/liff/sales/chats/SalesChats.tsx"


class TestALoadingListNeverLooksLikeAnEmptyOne:
    """Owner, 18 ก.ย. 2569: "ตอนเปิดไม่มีสัญลักษณ์ loading อะไรเลย พอไม่มี
    รายการแชทเลยผู้ใช้จะคิดว่ารายการแชทหาย".

    Switching tabs kept the previous tab's rows — and therefore its EMPTY
    state — on screen until the new ones arrived, so a shop with no live
    conversation pressed "ทั้งหมด" and read the wait as their history
    having vanished.
    """

    def test_the_list_has_a_loading_state_at_all(self):
        assert "loadingList" in CHATS.read_text(encoding="utf-8")

    def test_it_draws_rows_while_loading_not_the_empty_state(self):
        text = CHATS.read_text(encoding="utf-8")
        assert "chat-row-skeleton" in text
        assert 'aria-busy="true"' in text
        # The empty state is only reachable AFTER loading is over — and the
        # skeleton covers the WHOLE wait, not just an empty list. Keeping
        # the previous tab's rows on screen under the new tab's heading was
        # the other half of "it came back" (owner, 18 ก.ย. 2569).
        assert "{loadingList ? (" in text

    def test_a_stale_reply_cannot_overwrite_a_newer_one(self):
        """Two requests can be in flight at once — a tab switch and the
        eight-second poll that started just before it. Without a sequence
        number the slower one wins whatever tab it was for, and the first
        `finally` puts the skeleton away while the other is still coming:
        the empty state flashes. That is the symptom that came back."""
        text = CHATS.read_text(encoding="utf-8")
        assert "requestSeq" in text
        assert "const seq = ++requestSeq.current;" in text
        # Guarded after the fetch AND after the body is read, because both
        # are await points.
        assert text.count("if (seq !== requestSeq.current) return;") >= 2

    def test_only_the_newest_request_may_stop_the_spinner(self):
        text = CHATS.read_text(encoding="utf-8")
        assert "if (announce && seq === requestSeq.current) setLoadingList(false);" in text

    def test_the_poll_does_not_raise_it(self):
        """A list that blinks into skeletons every eight seconds is worse
        than one that never says anything."""
        text = CHATS.read_text(encoding="utf-8")
        poll = text[text.index("// The clock."):]
        poll = poll[: poll.index("useEffect", poll.index("useEffect") + 1)]
        assert "loadSessions()" in poll, poll[:300]
        assert "true)" not in poll.replace("loadThread(selectedId)", ""), poll[:300]

    def test_each_tab_says_its_own_empty(self):
        """"ยังไม่มีการสนทนาที่เปิดอยู่" on the live tab must point at the
        other tab; the shop's history did not disappear."""
        text = CHATS.read_text(encoding="utf-8")
        assert "copy.emptyLive" in text and "copy.emptyAll" in text
        th = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
        assert "ทั้งหมด" in th[th.index("emptyLiveHint"):][:200]

    def test_reduced_motion_is_respected(self):
        css = (ROOT / "presentation/app/globals.css").read_text(encoding="utf-8")
        at = css.index(".chat-row-skeleton")
        assert "prefers-reduced-motion" in css[at: at + 1600]


class TestTheLiveTabDoesNotWaitForAPlatformWidePass:
    """The live list awaited live_chat.sweep() before answering, and the
    screen polls every 8 seconds — so every open dashboard drove a
    cross-tenant write pass (escalations, closures, LINE pushes) eight
    times a minute, and the list waited for all of it."""

    def test_the_sweep_is_throttled_and_not_awaited(self):
        src = (ROOT / "application/chann_app/routers_phase2.py").read_text(encoding="utf-8")
        assert "_SWEEP_EVERY_S" in src
        assert "def _sweep_soon(" in src
        listing = src[src.index("async def list_chat_sessions("):]
        listing = listing[: listing.index("@router.post")]
        assert "_sweep_soon(client)" in listing
        assert "await live_chat.sweep(" not in listing, "the list still waits for the sweep"

    def test_the_sweep_serialises_by_licence_not_per_row(self):
        src = (ROOT / "data/chann_data/routers/internal.py").read_text(encoding="utf-8")
        body = src[src.index("def sweep_chat_sessions("):]
        body = body[: body.index("# ===")]
        assert "_grouped(" in body
        assert "for row in overdue:\n            session.refresh(row)\n            escalated_out.extend" not in body


# ------------------------------------------------ saving VAT, and one setting row

from decimal import Decimal  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from chann_app import routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

LICENSE_ID = "11111111-1111-1111-1111-111111111111"


class _Profile:
    """Only what the company-profile write touches."""

    def __init__(self):
        self.sent: list[dict] = []

    async def aclose(self):
        pass

    async def update_company_profile(self, license_id, payload, actor_id=None):
        # The real client hands this straight to httpx `json=`, which is
        # where a Decimal becomes a 500 all over again — so refuse one here
        # the way json.dumps would.
        import json

        json.dumps(payload)
        self.sent.append(payload)
        return {**payload, "license_id": license_id}


def _http(client, keys):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="admin", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestSavingVat:
    """Owner, 18 ก.ย. 2569: "กดบันทึก ภาษีมูลค่าเพิ่มไม่ได้ (500)".

    Round 20d put `mode="json"` on all fifteen model_dump() calls so no
    Decimal could reach the JSON encoder. This is the one site that then
    did ARITHMETIC on the dumped value, and a string will not divide:
    every VAT save since has raised
    `unsupported operand type(s) for /: 'str' and 'decimal.Decimal'`.

    Nothing covered this route end to end, which is why a 500 shipped.
    """

    def test_seven_percent_saves_and_is_stored_as_a_rate(self):
        client = _Profile()
        r = _http(client, ["setting.manage"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"vat_rate_percent": 7},
        )
        assert r.status_code == 200, r.text
        assert client.sent and Decimal(client.sent[0]["vat_rate"]) == Decimal("0.07")

    def test_a_fractional_rate_keeps_its_value(self):
        client = _Profile()
        _http(client, ["setting.manage"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"vat_rate_percent": 7.5},
        )
        assert Decimal(client.sent[0]["vat_rate"]) == Decimal("0.075")

    def test_deregistering_clears_it_rather_than_writing_zero(self):
        """null means "no longer VAT-registered" — 0% is a different claim."""
        client = _Profile()
        _http(client, ["setting.manage"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"vat_rate_percent": None},
        )
        assert client.sent[0]["vat_rate"] is None

    def test_not_mentioning_vat_leaves_it_alone(self):
        client = _Profile()
        _http(client, ["setting.manage"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"legal_name": "ร้านทดสอบ"},
        )
        assert "vat_rate" not in client.sent[0]

    def test_what_goes_out_survives_the_json_encoder(self):
        """The half the 20d sweep was protecting. Both halves, one test."""
        import json

        client = _Profile()
        _http(client, ["setting.manage"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"vat_rate_percent": 7, "legal_name": "ร้านทดสอบ"},
        )
        json.dumps(client.sent[0])

    def test_it_still_takes_setting_manage(self):
        denied = _http(_Profile(), ["quote.create"]).patch(
            f"/api/v1/licenses/{LICENSE_ID}/company-profile",
            json={"vat_rate_percent": 7},
        )
        assert denied.status_code == 403


class TestTheAutoAcceptRowLooksLikeASetting:
    """Owner, 18 ก.ย. 2569: "check รับลูกค้าอัติโนมัติ UI ยังแปลกๆ".

    It wore `.field`, which is the CAPTION style for a form label — 13.5px,
    weight 500, muted — so the setting's own name read as a disabled hint,
    and the checkbox beside it was the browser default against the 44px
    touch target the rest of the app uses.
    """

    PAGE = ROOT / "presentation/app/liff/sales/company/CompanyProfile.tsx"

    def test_it_no_longer_wears_the_caption_class(self):
        text = self.PAGE.read_text(encoding="utf-8")
        at = text.index("saveAutoAccept(e.target.checked)")
        around = text[at - 900: at + 400]
        assert 'className="setting-row"' in around
        assert 'className="field"' not in around

    def test_the_control_meets_the_touch_target(self):
        css = (ROOT / "presentation/app/globals.css").read_text(encoding="utf-8")
        block = css[css.index(".setting-row {"): css.index(".chat-row-skeleton")]
        assert "min-height: 44px" in block
        assert "width: 22px" in block

    def test_the_state_is_in_words_not_only_in_the_tick(self):
        text = self.PAGE.read_text(encoding="utf-8")
        assert "autoAcceptOn" in text and "autoAcceptOff" in text
        th = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
        assert "เปิดอยู่" in th and "ปิดอยู่" in th

    def test_the_hint_is_tied_to_the_control(self):
        text = self.PAGE.read_text(encoding="utf-8")
        assert 'aria-describedby="auto-accept-hint"' in text
        assert 'id="auto-accept-hint"' in text

    def test_the_section_has_a_heading_like_its_neighbours(self):
        text = self.PAGE.read_text(encoding="utf-8")
        at = text.index("saveAutoAccept(e.target.checked)")
        assert "section-head" in text[at - 1200: at]
