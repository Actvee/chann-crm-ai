"""Round 20g — two things the product could already do, with no way in.

Owner, 17 ก.ย. 2569: "ไล่ตรวจทั้งระบบเลยว่ามีอะไรทำได้แต่ไปไม่ถึงอีก".

The audit found the same shape twice:

  * `audit_log.view` is a permission an owner has held since Phase 2,
    labelled "ดูประวัติการใช้งาน". The Data tier had the route. The
    Application tier added one on 6 ก.ย. 2569 *because* the Data route had
    no caller — and then had no caller either, so the gap moved up a tier
    instead of closing. No screen, no sentence.
  * `list_invites` and `revoke_invite` have existed since Phase 6.5 with
    nothing above them. A shop could issue a key to the shop and could
    neither see which keys were out nor take one back when it leaked.

The intents here are the shapes the DEPLOYED model really returns for
these sentences (ask-model.py, 17 ก.ย. 2569), not shapes invented to suit
the handler.
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services.chat import handle_chat_message
from chann_app.services.thai_datetime import local_today
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

KEYS = ["audit_log.view", "member.manage", "customer.read"]

TODAY = local_today().isoformat()
AUDIT = [
    {"id": "a1", "entity_type": "customer", "entity_id": "c1", "actor_type": "user",
     "actor_id": "U-owner", "action": "delete", "field_changes": None,
     "created_at": f"{TODAY}T14:30:00+00:00"},
    {"id": "a2", "entity_type": "deal", "entity_id": "d1", "actor_type": "user",
     "actor_id": "U-staff", "action": "update",
     "field_changes": {"amount": {"old": "5000", "new": "9000"}},
     "created_at": f"{TODAY}T09:05:00+00:00"},
    {"id": "a3", "entity_type": "quote", "entity_id": "q1", "actor_type": "system",
     "actor_id": None, "action": "create", "field_changes": None,
     "created_at": f"{TODAY}T08:00:00+00:00"},
]


def _shop(keys=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=keys or KEYS, role="sales")
    client._audit = [dict(row) for row in AUDIT]
    client._members = [
        {"id": "m1", "chann_uid": "U-owner", "display_name": "", "role": "owner",
         "channel": "sales", "status": "active", "is_owner": True},
        {"id": "m2", "chann_uid": "U-staff", "display_name": "", "role": "sales",
         "channel": "sales", "status": "active", "is_owner": False},
    ]
    client._profiles = {
        "U-owner": {"first_name": "สมชาย", "last_name": "ใจดี"},
        "U-staff": {"first_name": "สมหญิง", "last_name": "ขยัน"},
    }
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="sales", oa="sales"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


READS_HISTORY = {"action": "read", "entity": "audit_log", "fields": {}, "missing": []}
READS_CUSTOMER_HISTORY = {
    "action": "read", "entity": "audit_log",
    "fields": {"entity_type": "customer"}, "missing": [],
}
READS_INVITES = {"action": "read", "entity": "invite", "fields": {}, "missing": []}


def _cancels(code):
    return {"action": "delete", "entity": "invite",
            "fields": {"invite_code": code}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class TestTheShopCanReadItsOwnHistory:
    async def test_the_owners_question_is_answered_at_all(self):
        reply = await _say(_shop(), "ประวัติการใช้งาน", READS_HISTORY)
        assert "ยังทำรายการนี้ไม่ได้" not in reply.text, reply.text
        assert "ประวัติการใช้งานล่าสุด" in reply.text, reply.text

    async def test_it_names_the_person_not_their_chann_uid(self):
        reply = await _say(_shop(), "ประวัติการใช้งาน", READS_HISTORY)
        assert "สมชาย" in reply.text, reply.text
        assert "U-owner" not in reply.text, reply.text

    async def test_it_says_what_they_did_in_words(self):
        reply = await _say(_shop(), "ประวัติการใช้งาน", READS_HISTORY)
        assert "ลบลูกค้า" in reply.text, reply.text
        assert "แก้ไขดีล" in reply.text, reply.text

    async def test_a_row_nobody_did_is_the_system(self):
        reply = await _say(_shop(), "ประวัติการใช้งาน", READS_HISTORY)
        assert "ระบบ เพิ่มใบเสนอราคา" in reply.text, reply.text

    async def test_the_question_can_narrow_to_one_kind(self):
        """"ใครลบลูกค้ารายนี้" carries entity_type=customer — the deal row
        must not be in the answer."""
        client = _shop()
        reply = await _say(client, "ใครลบลูกค้ารายนี้", READS_CUSTOMER_HISTORY)
        assert "ลบลูกค้า" in reply.text, reply.text
        assert "ดีล" not in reply.text, reply.text
        reads = [r for r in client.recorded if r[0] == "list_audit_log"]
        assert [r[2] for r in reads] == ["customer"], reads

    async def test_an_entity_type_the_table_does_not_know_is_dropped(self):
        """A model that invents entity_type="ทุกอย่าง" must not filter the
        list down to nothing — the read goes out unnarrowed."""
        client = _shop()
        await _say(client, "ประวัติการใช้งาน", {
            "action": "read", "entity": "audit_log",
            "fields": {"entity_type": "ทุกอย่าง"}, "missing": [],
        })
        reads = [r for r in client.recorded if r[0] == "list_audit_log"]
        assert [r[2] for r in reads] == [None], reads

    async def test_an_empty_history_says_so_rather_than_failing(self):
        client = _shop()
        client._audit = []
        reply = await _say(client, "ประวัติการใช้งาน", READS_HISTORY)
        assert "ยังไม่มีประวัติ" in reply.text, reply.text

    async def test_the_bubble_stays_inside_the_line_limit(self):
        client = _shop()
        client._audit = [
            {**AUDIT[0], "id": f"a{n}", "created_at": f"{TODAY}T1{n % 10}:00:00+00:00"}
            for n in range(40)
        ]
        reply = await _say(client, "ประวัติการใช้งาน", READS_HISTORY)
        assert len(reply.text.splitlines()) <= 15, reply.text
        assert len(reply.text) <= 700, len(reply.text)

    async def test_it_says_how_many_it_left_out(self):
        client = _shop()
        client._audit = [
            {**AUDIT[0], "id": f"a{n}", "created_at": f"{TODAY}T1{n % 10}:00:00+00:00"}
            for n in range(40)
        ]
        reply = await _say(client, "ประวัติการใช้งาน", READS_HISTORY)
        assert "อีก" in reply.text, reply.text

    async def test_without_the_permission_it_refuses(self):
        client = _shop(keys=["customer.read"])
        reply = await _say(client, "ประวัติการใช้งาน", READS_HISTORY)
        assert "สิทธิ์" in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] == "list_audit_log"]

    async def test_the_permission_key_is_the_one_the_product_grants(self):
        assert C.ACTION_PERMISSIONS[("read", "audit_log")] == "audit_log.view"


class TestTheCodesAlreadyIssued:
    def _with_invites(self, client):
        client._invites = [
            {"id": "i1", "invite_code": "QK4P2RSTUV", "role": "technician",
             "channel": "technician", "max_uses": 1, "used_count": 0,
             "expires_at": f"{TODAY}T00:00:00+00:00", "revoked_at": None,
             "created_at": f"{TODAY}T08:00:00+00:00"},
            {"id": "i2", "invite_code": "ZZ9TUVWXYB", "role": "sales",
             "channel": "sales", "max_uses": 1, "used_count": 1,
             "expires_at": None, "revoked_at": None,
             "created_at": f"{TODAY}T08:00:00+00:00"},
            {"id": "i3", "invite_code": "AA2BCDEFGH", "role": "sales",
             "channel": "sales", "max_uses": 1, "used_count": 0,
             "expires_at": None, "revoked_at": f"{TODAY}T09:00:00+00:00",
             "created_at": f"{TODAY}T08:00:00+00:00"},
        ]
        return client

    async def test_the_list_answers_at_all(self):
        reply = await _say(self._with_invites(_shop()), "ดูรหัสเชิญ", READS_INVITES)
        assert "ยังทำรายการนี้ไม่ได้" not in reply.text, reply.text
        assert "QK4P2RSTUV" in reply.text, reply.text

    async def test_a_used_up_code_is_not_offered_as_valid(self):
        reply = await _say(self._with_invites(_shop()), "ดูรหัสเชิญ", READS_INVITES)
        assert "ZZ9TUVWXYB" not in reply.text, reply.text

    async def test_a_cancelled_code_is_not_offered_as_valid(self):
        reply = await _say(self._with_invites(_shop()), "ดูรหัสเชิญ", READS_INVITES)
        assert "AA2BCDEFGH" not in reply.text, reply.text

    async def test_no_codes_says_how_to_issue_one(self):
        reply = await _say(_shop(), "ดูรหัสเชิญ", READS_INVITES)
        assert "ขอรหัสเชิญช่าง" in reply.text, reply.text

    async def test_cancelling_calls_the_tier_with_that_codes_id(self):
        client = self._with_invites(_shop())
        reply = await _say(client, "ยกเลิกรหัสเชิญ QK4P2RSTUV", _cancels("QK4P2RSTUV"))
        wrote = [r for r in client.recorded if r[0] == "revoke_invite"]
        assert [r[2] for r in wrote] == ["i1"], wrote
        assert "QK4P2RSTUV" in reply.text, reply.text

    async def test_a_cancelled_code_stops_being_valid(self):
        client = self._with_invites(_shop())
        await _say(client, "ยกเลิกรหัสเชิญ QK4P2RSTUV", _cancels("QK4P2RSTUV"))
        reply = await _say(client, "ดูรหัสเชิญ", READS_INVITES)
        assert "QK4P2RSTUV" not in reply.text, reply.text

    async def test_a_code_this_shop_never_issued_is_not_revoked(self):
        client = self._with_invites(_shop())
        reply = await _say(client, "ยกเลิกรหัสเชิญ NOTMINE99", _cancels("NOTMINE99"))
        assert "ไม่พบ" in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] == "revoke_invite"]

    async def test_cancelling_with_no_code_asks_which(self):
        client = self._with_invites(_shop())
        reply = await _say(client, "ยกเลิกรหัสเชิญ", {
            "action": "delete", "entity": "invite", "fields": {}, "missing": [],
        })
        assert "ไหน" in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] == "revoke_invite"]

    async def test_the_code_can_come_from_the_sentence_when_the_model_missed_it(self):
        """The model returns the code in a field most of the time; when it
        does not, the sentence still has it."""
        client = self._with_invites(_shop())
        await _say(client, "ยกเลิกรหัสเชิญ qk4p2rstuv", {
            "action": "delete", "entity": "invite", "fields": {}, "missing": [],
        })
        assert [r for r in client.recorded if r[0] == "revoke_invite"]

    async def test_without_member_manage_nothing_is_listed(self):
        client = self._with_invites(_shop(keys=["customer.read"]))
        reply = await _say(client, "ดูรหัสเชิญ", READS_INVITES)
        assert "สิทธิ์" in reply.text, reply.text
        assert not [r for r in client.recorded if r[0] == "list_invites"]

    async def test_without_member_manage_nothing_is_cancelled(self):
        client = self._with_invites(_shop(keys=["customer.read"]))
        await _say(client, "ยกเลิกรหัสเชิญ QK4P2RSTUV", _cancels("QK4P2RSTUV"))
        assert not [r for r in client.recorded if r[0] == "revoke_invite"]


class TestAskingToSeeACodeNeverIssuesOne:
    """The bare word "รหัสเชิญ" bypassed the model road entirely, so
    "ยกเลิกรหัสเชิญ QK4P2RSTUV" would have MINTED a code. Caught by asking
    the model before shipping, 17 ก.ย. 2569."""

    def test_issuing_still_works_for_each_kind(self):
        assert C._is_technician_invite_request("ขอรหัสเชิญช่าง")
        assert C._is_sales_invite_request("ขอรหัสเชิญพนักงานขาย")
        assert C._is_ambiguous_invite_request("ขอรหัสเชิญ")
        assert C._is_ambiguous_invite_request("เพิ่มสมาชิก")

    def test_seeing_and_cancelling_are_not_issuing(self):
        for sentence in (
            "ดูรหัสเชิญ", "ยกเลิกรหัสเชิญ QK4P2RSTUV",
            "มีรหัสเชิญอะไรค้างอยู่บ้าง", "รายการรหัสเชิญ",
            "revoke invite QK4P2RSTUV", "list invite code",
        ):
            assert not C._is_technician_invite_request(sentence), sentence
            assert not C._is_sales_invite_request(sentence), sentence
            assert not C._is_ambiguous_invite_request(sentence), sentence

    async def test_asking_to_cancel_does_not_create_a_code(self):
        client = _shop()
        client._invites = [
            {"id": "i1", "invite_code": "QK4P2RSTUV", "role": "technician",
             "channel": "technician", "max_uses": 1, "used_count": 0,
             "expires_at": None, "revoked_at": None,
             "created_at": f"{TODAY}T08:00:00+00:00"},
        ]
        await _say(client, "ยกเลิกรหัสเชิญ QK4P2RSTUV", _cancels("QK4P2RSTUV"))
        assert not [r for r in client.recorded if r[0] == "create_invite"]


class TestEveryPageTheReplyPointsAtCanBeOpened:
    def test_the_two_new_pages_have_a_deep_link(self):
        """A section with no path here comes back None, and the reply says
        "try the dashboard" with nothing to tap."""
        assert C.DASHBOARD_PATHS["history"] == "history"
        assert C.DASHBOARD_PATHS["appointments"] == "appointments"

    def test_the_history_reply_points_at_the_history_page(self):
        section, _label = C.ENTITY_DASHBOARD_PAGE["audit_log"]
        assert section == "history"

    def test_the_invite_reply_points_at_the_members_page(self):
        section, _label = C.ENTITY_DASHBOARD_PAGE["invite"]
        assert section == "members"


# ----------------------------------------------- the routes behind the screens

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

LICENSE_ID = "11111111-1111-1111-1111-111111111111"


class _Routes:
    """Only the four calls these two routes make."""

    def __init__(self, invites=None, audit=None, members=None, profiles=None):
        self.recorded = []
        self._invites = invites if invites is not None else []
        self._audit = audit if audit is not None else []
        self._members = members if members is not None else []
        self._profiles = profiles or {}

    async def aclose(self):
        pass

    async def list_audit_log_with_total(self, license_id, **kwargs):
        """Page and true total, delegating so `recorded` keeps its shape."""
        kwargs.pop("offset", None)
        rows = await self.list_audit_log(license_id, **kwargs)
        return rows, len(rows)

    async def list_audit_log(self, license_id, *, entity_type=None, actor_type=None, limit=100):
        self.recorded.append(("list_audit_log", license_id, entity_type, actor_type, limit))
        return list(self._audit)

    async def list_members(self, license_id):
        self.recorded.append(("list_members", license_id))
        return list(self._members)

    async def get_profile(self, chann_uid):
        return self._profiles.get(chann_uid)

    async def list_invites(self, license_id):
        self.recorded.append(("list_invites", license_id))
        return list(self._invites)

    async def revoke_invite(self, license_id, invite_id, actor_id=None):
        self.recorded.append(("revoke_invite", license_id, invite_id, actor_id))
        row = next((r for r in self._invites if r["id"] == invite_id), None)
        if row is None:
            raise DataTierError(404, "invite not found")
        row["revoked_at"] = f"{TODAY}T10:00:00+00:00"
        return dict(row)


def _http(client, keys, license_id=LICENSE_ID):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=license_id, chann_uid="CHN-S-000001", role="admin", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTheAuditRouteNamesThePerson:
    def test_a_row_carries_the_actors_name(self):
        client = _Routes(
            audit=[{"id": "a-1", "entity_type": "customer", "action": "delete",
                    "actor_type": "user", "actor_id": "U-owner"}],
            members=[{"id": "m1", "chann_uid": "U-owner", "channel": "sales", "status": "active"}],
            profiles={"U-owner": {"first_name": "สมชาย", "last_name": "ใจดี"}},
        )
        body = _http(client, ["audit_log.view"]).get(
            f"/api/v1/licenses/{LICENSE_ID}/audit-log",
        ).json()
        assert body[0]["actor_name"] == "สมชาย ใจดี", body

    def test_an_actor_who_is_no_longer_a_member_leaves_the_name_blank(self):
        client = _Routes(
            audit=[{"id": "a-1", "entity_type": "customer", "action": "delete",
                    "actor_type": "user", "actor_id": "U-gone"}],
        )
        body = _http(client, ["audit_log.view"]).get(
            f"/api/v1/licenses/{LICENSE_ID}/audit-log",
        ).json()
        assert body[0]["actor_name"] == "", body
        assert body[0]["actor_id"] == "U-gone", body


class TestTheInviteRoutes:
    ROWS = [
        {"id": "i1", "invite_code": "QK4P2RSTUV", "role": "technician",
         "max_uses": 1, "used_count": 0, "revoked_at": None,
         "expires_at": "2099-01-01T00:00:00+00:00"},
        {"id": "i2", "invite_code": "ZZ9TUVWXYB", "role": "sales",
         "max_uses": 1, "used_count": 1, "revoked_at": None, "expires_at": None},
        {"id": "i3", "invite_code": "AA2BCDEFGH", "role": "sales",
         "max_uses": 1, "used_count": 0,
         "revoked_at": "2026-09-01T00:00:00+00:00", "expires_at": None},
        {"id": "i4", "invite_code": "BB3CDEFGHJ", "role": "sales",
         "max_uses": 1, "used_count": 0, "revoked_at": None,
         "expires_at": "2020-01-01T00:00:00+00:00"},
    ]

    def _rows(self):
        return [dict(r) for r in self.ROWS]

    def test_each_code_says_whether_it_still_works(self):
        body = _http(_Routes(invites=self._rows()), ["member.manage"]).get(
            f"/api/v1/licenses/{LICENSE_ID}/invites",
        ).json()
        assert {r["invite_code"]: r["status"] for r in body} == {
            "QK4P2RSTUV": "open",
            "ZZ9TUVWXYB": "used",
            "AA2BCDEFGH": "revoked",
            "BB3CDEFGHJ": "expired",
        }, body

    def test_listing_needs_member_manage(self):
        denied = _http(_Routes(), ["customer.read"]).get(
            f"/api/v1/licenses/{LICENSE_ID}/invites",
        )
        assert denied.status_code == 403

    def test_another_shops_invites_are_not_this_shops(self):
        other = _http(_Routes(), ["member.manage"]).get(
            "/api/v1/licenses/22222222-2222-2222-2222-222222222222/invites",
        )
        assert other.status_code == 403

    def test_revoking_marks_the_code_dead(self):
        client = _Routes(invites=self._rows())
        body = _http(client, ["member.manage"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/invites/i1/revoke",
        ).json()
        assert body["status"] == "revoked", body
        assert [r for r in client.recorded if r[0] == "revoke_invite"]

    def test_revoking_records_who_did_it(self):
        client = _Routes(invites=self._rows())
        _http(client, ["member.manage"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/invites/i1/revoke",
        )
        wrote = [r for r in client.recorded if r[0] == "revoke_invite"][0]
        assert wrote[3] == "CHN-S-000001", wrote

    def test_revoking_needs_member_manage(self):
        client = _Routes(invites=self._rows())
        denied = _http(client, ["customer.read"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/invites/i1/revoke",
        )
        assert denied.status_code == 403
        assert not [r for r in client.recorded if r[0] == "revoke_invite"]

    def test_an_id_this_shop_does_not_own_is_a_404_not_a_500(self):
        client = _Routes(invites=self._rows())
        gone = _http(client, ["member.manage"]).post(
            f"/api/v1/licenses/{LICENSE_ID}/invites/i9/revoke",
        )
        assert gone.status_code == 404, gone.text


# ------------------------------------------------ screens with no way in

import re  # noqa: E402
from pathlib import Path  # noqa: E402

LIFF = Path(__file__).resolve().parents[2] / "presentation" / "app" / "liff"


class TestEverySalesScreenIsOnTheMenu:
    """A page that ships, builds and deploys with no link to it is not a
    page anyone has.

    `/liff/sales/signature` was exactly that: the technician and customer
    rails carried a signature entry and the sales rail never did, so a CS
    approving a service report signed it with a blank line and had nowhere
    to fix that (owner, 17 ก.ย. 2569). This walks the directory rather
    than a hand-kept list, so the next page cannot go missing quietly.
    """

    def test_no_sales_page_is_unreachable(self):
        nav = (LIFF / "_nav-model.tsx").read_text(encoding="utf-8")
        missing = [
            d.name for d in sorted((LIFF / "sales").iterdir())
            if d.is_dir() and (d / "page.tsx").is_file()
            # The guide sits at the foot of every rail via guideEntry().
            and d.name != "guide"
            and f'/liff/sales/{d.name}"' not in nav
        ]
        assert missing == [], missing

    def test_the_signature_page_is_on_the_sales_rail(self):
        nav = (LIFF / "_nav-model.tsx").read_text(encoding="utf-8")
        assert '/liff/sales/signature"' in nav

    def test_signing_is_not_gated_on_a_permission(self):
        """Anyone who can approve anything needs to sign it, and approving
        is gated already — a second key here would only lock people out of
        their own signature."""
        nav = (LIFF / "_nav-model.tsx").read_text(encoding="utf-8")
        line = next(
            l for l in nav.splitlines()
            if '/liff/sales/signature"' in l
        )
        assert "needs:" not in line, line
