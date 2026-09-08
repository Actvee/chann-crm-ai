"""Per-OA persona separation on the Application tier (owner, 8 Sep 2026).

The OAs are separate registrations that share only the person: a sales
member — even the owner — is a stranger to the Technician OA until they
redeem a technician invite; a code for the other OA is refused; the
principal, chat and ticket actions read the row of the OA in use; the
members page removes, reactivates, re-roles and resets per channel.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import authorization, registration  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.identity import member_channel  # noqa: E402
from chann_app.services.registration import (  # noqa: E402
    INVITE_WRONG_OA, JOINED, KNOWN_ELSEWHERE, LINKED, WELCOME, WELCOME_TECHNICIAN, handle_registration,
)
from test_phase65_registration import FakeRegClient, _ctx  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


class _Known(FakeRegClient):
    """Someone the platform knows from ANOTHER OA."""

    def __init__(self, rows=None, **kw):
        super().__init__(**kw)
        self.rows = rows if rows is not None else [
            {"license_id": "lic-1", "company_name": "ร้านสมชาย", "role": "owner", "channel": "sales"},
        ]
        self.redeem_kwargs: dict = {}

    async def memberships_of(self, chann_uid, oa=None):
        return [r for r in self.rows if oa is None or r["channel"] == oa]

    async def redeem_invite(self, **kw):
        self.redeem_kwargs = kw
        return await super().redeem_invite(**kw)


class TestRegistrationIsPerOA:
    async def test_the_owner_on_the_technician_oa_is_told_to_register_not_linked(self):
        client = _Known()
        reply = await handle_registration(client, message="สวัสดี", ctx=_ctx(oa="technician"), audience="technician")
        assert reply.startswith(KNOWN_ELSEWHERE["th"].split("{", 1)[0])
        assert "(สมชาย)" in reply, "the shared personal data is named"
        assert reply.endswith(WELCOME_TECHNICIAN["th"])
        for never in (LINKED["th"].split("{", 1)[0], JOINED["th"].split("{", 1)[0], "ผูกกับร้าน"):
            assert never not in reply

    async def test_a_technician_on_the_sales_oa_is_unregistered_there(self):
        client = _Known(rows=[{"license_id": "lic-1", "company_name": "ร้านสมชาย", "role": "technician", "channel": "technician"}])
        reply = await handle_registration(client, message="", ctx=_ctx(oa="sales"), audience="sales")
        assert reply.startswith(KNOWN_ELSEWHERE["th"].split("{", 1)[0])
        assert reply.endswith(WELCOME["th"])

    async def test_a_stranger_gets_the_plain_welcome(self):
        client = _Known(rows=[])
        reply = await handle_registration(client, message="", ctx=_ctx(oa="technician"), audience="technician")
        assert reply == WELCOME_TECHNICIAN["th"]

    async def test_a_lookup_failure_degrades_to_the_plain_welcome(self):
        reply = await handle_registration(FakeRegClient(), message="", ctx=_ctx(oa="technician"), audience="technician")
        assert reply == WELCOME_TECHNICIAN["th"]

    async def test_english_reader(self):
        reply = await handle_registration(_Known(), message="", ctx=_ctx(oa="technician"), audience="technician", language="en")
        assert reply.startswith("Your personal details are on file (สมชาย)")
        assert reply.endswith(WELCOME_TECHNICIAN["en"])

    @pytest.mark.parametrize("oa", ["sales", "technician"])
    async def test_the_oa_the_code_was_typed_on_travels_with_the_redeem(self, oa):
        client = _Known(member={"company_name": "ร้านสมชาย", "role": "technician" if oa == "technician" else "cs"})
        reply = await handle_registration(client, message="ABC234XY7Z", ctx=_ctx(oa=oa), audience=oa)
        assert client.redeem_kwargs["oa"] == oa
        assert reply.startswith(JOINED["th"].split("{", 1)[0])

    async def test_a_technician_code_on_the_sales_oa_points_at_the_technician_line(self):
        client = _Known(raises=DataTierError(409, "invite is for the technician OA, not the sales OA"))
        reply = await handle_registration(client, message="ABC234XY7Z", ctx=_ctx(oa="sales"), audience="sales")
        assert reply == INVITE_WRONG_OA["th"].format(target="LINE ช่าง")

    async def test_a_staff_code_on_the_technician_oa_points_at_the_sales_line(self):
        client = _Known(raises=DataTierError(409, "invite is for the sales OA, not the technician OA"))
        reply = await handle_registration(client, message="ABC234XY7Z", ctx=_ctx(oa="technician"), audience="technician", language="en")
        assert reply == INVITE_WRONG_OA["en"].format(target="the sales / CS LINE")

    async def test_other_conflicts_are_still_a_bad_code(self):
        client = _Known(raises=DataTierError(409, "invite code has expired"))
        reply = await handle_registration(client, message="ABC234XY7Z", ctx=_ctx(oa="technician"), audience="technician")
        assert "ไม่พบรหัสนี้" in reply


class TestMemberChannel:
    def test_the_oa_to_channel_rule(self):
        assert member_channel("technician") == "technician"
        assert member_channel("sales") == "sales"
        assert member_channel("customer") == "sales"
        assert member_channel(None) == "sales"


class _Recording(FakeDataClient):
    """Records which channel every membership lookup asked for."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.channels: list[tuple[str, str | None]] = []
        self.status_calls: list[dict] = []
        self.reset_calls: list[dict] = []
        self.role_calls: list[dict] = []
        self._status_error: Exception | None = None
        self._unassigned: list[dict] = []

    async def resolve_identity(self, line_user_id, primary_role, display_name=None):
        return {"chann_uid": "CHN-S-000001", "primary_role": primary_role}

    async def memberships_of(self, chann_uid, oa=None):
        return [{"license_id": LICENSE_ID, "license_code": "TESTCO", "company_name": "บริษัททดสอบ",
                 "license_status": "active", "channel": oa}]

    async def authorization_context(self, license_id, chann_uid, channel="sales"):
        self.channels.append(("authorization_context", channel))
        return await super().authorization_context(license_id, chann_uid, channel)

    async def get_member(self, license_id, chann_uid, channel=None):
        self.channels.append(("get_member", channel))
        return await super().get_member(license_id, chann_uid, channel)

    async def set_member_status(self, license_id, chann_uid, *, status, channel="sales", actor_id=None):
        self.status_calls.append({"chann_uid": chann_uid, "status": status, "channel": channel, "actor_id": actor_id})
        if self._status_error:
            raise self._status_error
        return {"id": "m-9", "chann_uid": chann_uid, "role": "technician", "status": status, "channel": channel,
                "is_owner": False, "joined_at": "2026-09-01T00:00:00+00:00", "display_name": None,
                "unassigned_tickets": list(self._unassigned)}

    async def reset_member(self, license_id, chann_uid, *, channel="sales", actor_id=None):
        self.reset_calls.append({"chann_uid": chann_uid, "channel": channel, "actor_id": actor_id})
        return {"id": "m-9", "chann_uid": chann_uid, "role": "cs", "status": "active", "channel": channel,
                "is_owner": False, "joined_at": None, "display_name": "สมหญิง"}

    async def set_member_role(self, license_id, chann_uid, role_name, actor_id=None, channel="sales"):
        self.role_calls.append({"chann_uid": chann_uid, "role": role_name, "channel": channel})
        return {"id": "m-9", "chann_uid": chann_uid, "role": role_name, "status": "active", "channel": channel,
                "is_owner": False}

    async def aclose(self):
        pass


@pytest.fixture
def verified(monkeypatch):
    async def fake_verify(token, audience):
        return {"sub": "U1", "name": "x"}

    monkeypatch.setattr(authorization, "verify_id_token", fake_verify)


class TestThePrincipalReadsTheRowOfItsOA:
    @pytest.mark.parametrize("audience,channel", [("sales", "sales"), ("technician", "technician")])
    async def test_permissions_come_from_the_channel_in_use(self, verified, audience, channel):
        client = _Recording(permission_keys=["ticket.read"])
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience=audience, x_license_id="", method="GET",
        )
        assert principal.audience == audience
        assert ("authorization_context", channel) in client.channels


class TestChatActsThroughTheRowOfItsOA:
    async def test_technician_flows_read_the_technician_row(self):
        client = _Recording(permission_keys=["ticket.read", "ticket.update"], role="technician")
        from test_phase6_chat import _ctx as chat_ctx

        await handle_chat_message(client, message="งานของฉัน", ctx=chat_ctx(oa="technician", primary_role="technician"))
        assert ("authorization_context", "technician") in client.channels
        assert ("get_member", "technician") in client.channels
        assert all(ch == "technician" for _what, ch in client.channels)

    async def test_sales_flows_read_the_sales_row(self):
        client = _Recording(permission_keys=["ticket.read", "ticket.update", "customer.read"])
        from test_phase6_chat import _ctx as chat_ctx

        await handle_chat_message(client, message="รายการงาน", ctx=chat_ctx())
        assert ("authorization_context", "sales") in client.channels
        assert all(ch == "sales" for _what, ch in client.channels)


def _app(client, *, keys, audience="sales", is_owner=False):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="owner" if is_owner else "cs",
            is_owner=is_owner, permission_keys=frozenset(keys), audience=audience,
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTicketActionsUseTheRowOfTheApp:
    @pytest.mark.parametrize("audience,channel", [("sales", "sales"), ("technician", "technician")])
    def test_member_of_asks_for_the_audiences_channel(self, audience, channel):
        client = _Recording(permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned"}]
        http = _app(client, keys=client._permission_keys, audience=audience)
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/tickets/t1/claim", json={}).status_code == 200
        assert ("get_member", channel) in client.channels


class TestMembersPage:
    def _members(self):
        return [
            {"id": "m-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "channel": "sales",
             "is_owner": True, "joined_at": "2026-09-01T00:00:00+00:00", "display_name": "สมชาย ใจดี"},
            {"id": "m-2", "chann_uid": "CHN-S-000001", "role": "technician", "status": "active", "channel": "technician",
             "is_owner": False, "joined_at": "2026-09-02T00:00:00+00:00", "display_name": "สมชาย ใจดี"},
            {"id": "m-3", "chann_uid": "CHN-T-000002", "role": "technician", "status": "removed", "channel": "technician",
             "is_owner": False, "joined_at": "2026-09-03T00:00:00+00:00", "display_name": None},
        ]

    def test_the_list_is_one_item_per_person_and_channel(self):
        client = _Recording(permission_keys=["member.manage"])
        client._members = self._members()
        http = _app(client, keys=["member.manage"])
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/members").json()
        assert [(r["chann_uid"], r["channel"]) for r in rows] == [("CHN-S-000001", "sales"), ("CHN-S-000001", "technician")]
        owner = rows[0]
        assert owner["is_owner"] is True and owner["status"] == "active" and owner["joined_at"]
        assert owner["display_name"] == "สมชาย ใจดี" and "phone" in owner
        assert rows[1]["is_owner"] is False

    def test_include_removed_adds_the_removed_rows(self):
        client = _Recording(permission_keys=["member.manage"])
        client._members = self._members()
        http = _app(client, keys=["member.manage"])
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/members", params={"include_removed": "1"}).json()
        assert [r["status"] for r in rows] == ["active", "active", "removed"]
        assert rows[2]["display_name"] == "CHN-T-000002", "a nameless row shows its uid"

    def test_role_accepts_role_or_role_name_and_a_channel(self):
        client = _Recording(permission_keys=["member.manage"])
        http = _app(client, keys=["member.manage"])
        base = f"/api/v1/licenses/{LICENSE_ID}/members/CHN-T-000002/role"
        assert http.patch(base, json={"role": "cs", "channel": "technician", "extra": 1}).status_code == 200
        assert http.patch(base, json={"role_name": "cs"}).status_code == 200
        assert client.role_calls == [
            {"chann_uid": "CHN-T-000002", "role": "cs", "channel": "technician"},
            {"chann_uid": "CHN-T-000002", "role": "cs", "channel": "sales"},
        ]
        assert http.patch(base, json={"channel": "sales"}).status_code == 422
        assert http.patch(base, json={"role": "cs", "channel": "customer"}).status_code == 422

    def test_status_needs_member_manage(self):
        client = _Recording(permission_keys=["ticket.read"])
        http = _app(client, keys=["ticket.read"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-T-000002/status",
                              json={"status": "removed", "channel": "technician"})
        assert response.status_code == 403 and client.status_calls == []

    def test_status_removes_per_channel_as_the_caller(self):
        client = _Recording(permission_keys=["member.manage"])
        http = _app(client, keys=["member.manage"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-T-000002/status",
                              json={"status": "removed", "channel": "technician"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "removed" and body["channel"] == "technician" and "unassigned_tickets" not in body
        assert client.status_calls == [{"chann_uid": "CHN-T-000002", "status": "removed", "channel": "technician",
                                        "actor_id": "CHN-S-000001"}]
        assert http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-T-000002/status",
                          json={"status": "gone", "channel": "technician"}).status_code == 422

    def test_the_owner_is_protected_with_a_reason_code(self):
        client = _Recording(permission_keys=["member.manage"])
        client._status_error = DataTierError(409, "the owner cannot be removed or demoted")
        http = _app(client, keys=["member.manage"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-S-000001/status",
                              json={"status": "removed", "channel": "sales"})
        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "owner_protected"

    def test_a_row_missing_on_that_channel_is_404_with_a_reason_code(self):
        client = _Recording(permission_keys=["member.manage"])
        client._status_error = DataTierError(404, "member not found on this channel")
        http = _app(client, keys=["member.manage"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-S-000001/status",
                              json={"status": "removed", "channel": "technician"})
        assert response.status_code == 404
        assert response.json()["detail"]["reason_code"] == "member_not_on_channel"

    async def test_removing_a_technician_tells_the_dispatchers(self, monkeypatch):
        from chann_app.services import notify

        sent: list[dict] = []

        async def fake_send(client, **kw):
            sent.append(kw)
            return {}

        monkeypatch.setattr(notify, "send_notification", fake_send)
        client = _Recording(permission_keys=["ticket.assign", "member.manage"])
        client._unassigned = [{"id": "11111111-1111-1111-1111-111111111112", "ticket_number": "T-2026-0007"}]
        client._members = [
            {"id": "m-1", "chann_uid": "CHN-S-000001", "role": "owner", "status": "active", "channel": "sales"},
            {"id": "m-4", "chann_uid": "CHN-S-000004", "role": "cs", "status": "active", "channel": "sales"},
            {"id": "m-5", "chann_uid": "CHN-T-000005", "role": "technician", "status": "active", "channel": "technician"},
            {"id": "m-2", "chann_uid": "CHN-T-000002", "role": "technician", "status": "active", "channel": "technician"},
        ]
        http = _app(client, keys=["member.manage"])
        response = http.patch(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-T-000002/status",
                              json={"status": "removed", "channel": "technician"})
        assert response.status_code == 200, response.text
        # Everyone on the sales side holding ticket.assign (the fake grants
        # every member the same keys); technician rows are not dispatchers.
        assert sorted(s["target_chann_uid"] for s in sent) == ["CHN-S-000001", "CHN-S-000004"]
        assert all(s["type"] == "ticket_unassigned" and "T-2026-0007" in s["message"] for s in sent)
        assert sent[0]["entity_id"] == "11111111-1111-1111-1111-111111111112"

    def test_reset_clears_one_channel_as_the_caller(self):
        client = _Recording(permission_keys=["member.manage"])
        http = _app(client, keys=["member.manage"])
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-S-000004/reset", json={"channel": "technician"})
        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "สมหญิง"
        assert client.reset_calls == [{"chann_uid": "CHN-S-000004", "channel": "technician", "actor_id": "CHN-S-000001"}]
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-S-000004/reset", json={}).status_code == 200
        assert client.reset_calls[-1]["channel"] == "sales"

    def test_reset_needs_member_manage(self):
        client = _Recording(permission_keys=["ticket.read"])
        http = _app(client, keys=["ticket.read"])
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/members/CHN-S-000004/reset", json={"channel": "sales"}).status_code == 403

    def test_technicians_are_the_technician_channel_rows(self):
        client = _Recording(permission_keys=["ticket.read"])
        client._members = self._members()
        client._roles = [{"role_name": "technician", "permission_keys": ["ticket.read", "ticket.update", "service_report.create"]}]
        http = _app(client, keys=["ticket.read"])
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/technicians").json()
        assert [(r["chann_uid"], r["channel"]) for r in rows] == [("CHN-S-000001", "technician")]
