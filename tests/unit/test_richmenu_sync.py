"""Phase 19 / review E10 — per-user rich menu linking: alias → richMenuId
(cached), then user → menu; the sync helper is best-effort; registration
triggers it once, on success only."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.config import settings  # noqa: E402
from chann_app.line import client as line  # noqa: E402
from chann_app.services import registration, richmenu  # noqa: E402
from test_phase65_registration import FakeRegClient  # noqa: E402
from test_phase65_registration import _ctx as _reg_ctx  # noqa: E402


class _Line:
    """A fake LINE: aliases resolve to ids, links are recorded."""

    def __init__(self, aliases=None, fail_link=False):
        self.aliases = aliases if aliases is not None else {"chann-sales-main": "rm-th", "chann-sales-main-en": "rm-en"}
        self.fail_link = fail_link
        self.lookups: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sales-token"
        path = request.url.path
        if request.method == "GET" and path.startswith("/v2/bot/richmenu/alias/"):
            alias = path.rsplit("/", 1)[-1]
            self.lookups.append(alias)
            if alias not in self.aliases:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={"richMenuAliasId": alias, "richMenuId": self.aliases[alias]})
        if request.method == "POST" and "/richmenu/" in path and path.startswith("/v2/bot/user/"):
            _, _, _, _, user_id, _, rich_menu_id = path.split("/")
            if self.fail_link or rich_menu_id not in self.aliases.values():
                return httpx.Response(404, json={"message": "richmenu not found"})
            self.links.append((user_id, rich_menu_id))
            return httpx.Response(200, json={})
        return httpx.Response(500, text="unexpected")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(settings, "line_sales_channel_access_token", "sales-token")
    line.forget_rich_menu_aliases()
    yield
    line.forget_rich_menu_aliases()


class TestLinkRichMenu:
    async def test_alias_is_resolved_then_the_user_is_linked(self):
        fake = _Line()
        rich_menu_id = await line.link_rich_menu("sales", "Uabc", "chann-sales-main-en", client=fake.client())
        assert rich_menu_id == "rm-en"
        assert fake.lookups == ["chann-sales-main-en"] and fake.links == [("Uabc", "rm-en")]

    async def test_the_alias_lookup_is_cached_per_oa_and_alias(self):
        fake = _Line()
        for user in ("U1", "U2", "U3"):
            await line.link_rich_menu("sales", user, "chann-sales-main", client=fake.client())
        assert fake.lookups == ["chann-sales-main"]
        assert [u for u, _ in fake.links] == ["U1", "U2", "U3"]

    async def test_a_stale_cached_id_is_refreshed_once(self):
        fake = _Line()
        await line.link_rich_menu("sales", "U1", "chann-sales-main", client=fake.client())
        # The apply script re-created the menus: the alias now points elsewhere.
        fake.aliases["chann-sales-main"] = "rm-th-v2"
        assert await line.link_rich_menu("sales", "U2", "chann-sales-main", client=fake.client()) == "rm-th-v2"
        assert fake.links[-1] == ("U2", "rm-th-v2") and fake.lookups == ["chann-sales-main", "chann-sales-main"]

    async def test_an_unknown_alias_is_an_error_not_a_silent_no_op(self):
        fake = _Line()
        with pytest.raises(line.LineReplyError):
            await line.link_rich_menu("sales", "U1", "chann-sales-nothing", client=fake.client())

    async def test_no_token_is_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(settings, "line_sales_channel_access_token", "")
        with pytest.raises(line.LineReplyError, match="REQUIRED_NOT_CONFIGURED"):
            await line.link_rich_menu("sales", "U1", "chann-sales-main", client=_Line().client())


class TestAliasNaming:
    def test_pages_and_languages(self):
        assert richmenu.rich_menu_alias("sales", "th") == "chann-sales-main"
        assert richmenu.rich_menu_alias("sales", "en") == "chann-sales-main-en"
        assert richmenu.rich_menu_alias("technician", "en-US", page="more") == "chann-technician-more-en"
        assert richmenu.rich_menu_alias("customer", None) == "chann-customer-main"
        with pytest.raises(ValueError):
            richmenu.rich_menu_alias("sales", "th", page="third")


class _Client:
    def __init__(self, line_uid="Uline", language="th"):
        self._line_uid = line_uid
        self._language = language
        self.calls: list[str] = []

    async def line_target_of(self, chann_uid):
        self.calls.append("line_target_of")
        return self._line_uid

    async def get_display_preferences(self, chann_uid):
        self.calls.append("get_display_preferences")
        return {"language": self._language}


class TestSyncHelper:
    async def test_links_the_page_for_the_given_language(self):
        fake = _Line()
        ok = await richmenu.sync_rich_menu(_Client(), oa="sales", chann_uid="CHN-1", language="en", http_client=fake.client())
        assert ok is True and fake.links == [("Uline", "rm-en")]

    async def test_reads_the_preference_when_no_language_is_given(self):
        fake = _Line()
        client = _Client(language="en")
        assert await richmenu.sync_rich_menu(client, oa="sales", chann_uid="CHN-1", http_client=fake.client())
        assert "get_display_preferences" in client.calls and fake.links == [("Uline", "rm-en")]

    async def test_uses_a_supplied_line_user_id_without_a_lookup(self):
        fake = _Line()
        client = _Client()
        assert await richmenu.sync_rich_menu(client, oa="sales", chann_uid="CHN-1", language="th", line_user_id="Ugiven", http_client=fake.client())
        assert "line_target_of" not in client.calls and fake.links == [("Ugiven", "rm-th")]

    async def test_failures_are_logged_never_raised(self, caplog):
        fake = _Line(fail_link=True)
        assert await richmenu.sync_rich_menu(_Client(), oa="sales", chann_uid="CHN-1", language="th", http_client=fake.client()) is False
        assert "rich menu sync failed" in caplog.text
        assert await richmenu.sync_rich_menu(_Client(line_uid=None), oa="sales", chann_uid="CHN-1", language="th") is False
        assert await richmenu.sync_rich_menu(_Client(), oa="mystery", chann_uid="CHN-1", language="th") is False

        class Broken(_Client):
            async def line_target_of(self, chann_uid):
                raise RuntimeError("data tier down")

        assert await richmenu.sync_rich_menu(Broken(), oa="sales", chann_uid="CHN-1", language="th") is False


@pytest.fixture
def synced(monkeypatch):
    seen = []

    async def fake_sync(client, *, oa, chann_uid, language=None, line_user_id=None, http_client=None):
        seen.append((oa, chann_uid, language))
        return True

    monkeypatch.setattr(registration, "sync_rich_menu", fake_sync)
    return seen


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


class TestRegistrationCallSite:
    async def test_a_customer_link_syncs_the_menu(self, synced):
        client = FakeRegClient(
            shops=[{"license_id": "lic-1", "company_code": "DEV001", "company_name": "Dev Company"}],
            link={"company_name": "Dev Company", "company_code": "DEV001"},
        )
        await registration.handle_registration(
            client, message="ร้าน dev company", ctx=_reg_ctx(oa="customer", primary_role="customer"), audience="customer", language="en",
        )
        assert "link_customer" in client.calls
        assert synced == [("customer", _reg_ctx(oa="customer", primary_role="customer").chann_uid, "en")]

    async def test_a_staff_invite_syncs_the_menu(self, synced):
        client = FakeRegClient(member={"company_name": "ร้านเอ", "role": "technician"})
        await registration.handle_registration(
            client, message="ABCDEFGH23", ctx=_reg_ctx(oa="technician", primary_role="technician"), audience="technician",
        )
        assert "redeem_invite" in client.calls
        assert synced and synced[0][0] == "technician"

    async def test_a_bad_code_does_not(self, synced):
        client = FakeRegClient(raises=RuntimeError("404 not found"))
        reply = await registration.handle_registration(
            client, message="ABCDEFGH23", ctx=_reg_ctx(oa="technician", primary_role="technician"), audience="technician",
        )
        assert "ไม่พบรหัส" in str(getattr(reply, "text", reply))
        assert synced == []
