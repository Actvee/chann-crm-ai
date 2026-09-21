"""Round 20S — what LINE calls the person, when the shop has nothing better.

Owner, 21 ก.ย. 2569: "ถ้าไม่มีข้อมูลในระบบเราให้ใช้ชื่อ LINE ลูกค้าได้ไหม".
The identity row had a display_name column since Phase 1 and the fallback
to it existed on the chat page — but the webhook resolved identities
without ever asking LINE, so the column was always empty. The name is
fetched once, the first time a person is seen without one, and stored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.line import client as line  # noqa: E402
from chann_app.services.identity import ResolvedContext, TenantResolution, ensure_display_name  # noqa: E402


def _ctx(display_name=None) -> ResolvedContext:
    return ResolvedContext(
        chann_uid="CHN-C-000002", primary_role="customer", display_name=display_name,
        resolution=TenantResolution.SINGLE, memberships=[], oa="customer",
    )


class _Client:
    def __init__(self, raises=None):
        self.stored: list[tuple[str, str]] = []
        self._raises = raises

    async def set_identity_display_name(self, chann_uid, display_name):
        if self._raises:
            raise self._raises
        self.stored.append((chann_uid, display_name))
        return {"chann_uid": chann_uid, "display_name": display_name}


class TestTheNameIsFetchedOnce:
    async def test_a_nameless_identity_gets_the_line_name_and_it_is_stored(self):
        client = _Client()
        asked: list[tuple[str, str]] = []

        async def fetch(oa, line_user_id):
            asked.append((oa, line_user_id))
            return "สมชาย 🐱"

        ctx = await ensure_display_name(client, _ctx(), oa="customer", line_user_id="U1", fetch=fetch)
        assert ctx.display_name == "สมชาย 🐱"
        assert client.stored == [("CHN-C-000002", "สมชาย 🐱")]
        assert asked == [("customer", "U1")]

    async def test_a_named_identity_is_not_asked_about(self):
        client = _Client()

        async def fetch(oa, line_user_id):
            raise AssertionError("LINE must not be asked when the name is already there")

        ctx = await ensure_display_name(client, _ctx("มีชื่อแล้ว"), oa="customer", line_user_id="U1", fetch=fetch)
        assert ctx.display_name == "มีชื่อแล้ว"
        assert client.stored == []

    async def test_line_saying_nothing_leaves_the_context_alone(self):
        client = _Client()

        async def fetch(oa, line_user_id):
            return None

        ctx = await ensure_display_name(client, _ctx(), oa="customer", line_user_id="U1", fetch=fetch)
        assert ctx.display_name is None
        assert client.stored == []

    async def test_a_data_tier_failure_never_stops_the_message(self):
        client = _Client(raises=RuntimeError("data tier down"))

        async def fetch(oa, line_user_id):
            return "ชื่อ"

        ctx = await ensure_display_name(client, _ctx(), oa="customer", line_user_id="U1", fetch=fetch)
        assert ctx.display_name is None  # not claimed when it was not stored


class TestTheLineCall:
    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setattr(line, "channel_access_token", lambda oa: "test-token")

    def _client(self, status, body=None):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(status, json=body or {})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen

    async def test_the_display_name_comes_back_trimmed(self):
        client, seen = self._client(200, {"displayName": "  สมหญิง  ", "userId": "U1"})
        assert await line.get_profile_name("customer", "U1", client=client) == "สมหญิง"
        assert seen["url"] == "https://api.line.me/v2/bot/profile/U1"
        assert seen["auth"] == "Bearer test-token"

    async def test_not_a_friend_is_none_not_an_error(self):
        client, _ = self._client(404, {"message": "Not found"})
        assert await line.get_profile_name("customer", "U1", client=client) is None

    async def test_a_blank_name_is_none(self):
        client, _ = self._client(200, {"displayName": "   "})
        assert await line.get_profile_name("customer", "U1", client=client) is None

    async def test_no_token_means_no_call(self, monkeypatch):
        monkeypatch.setattr(line, "channel_access_token", lambda oa: "")
        assert await line.get_profile_name("customer", "U1") is None
