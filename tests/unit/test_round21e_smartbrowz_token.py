"""Round 21E addendum — receipts failed on DEV with INVALID_TOKEN (401).

The owner, DEV, 24 ก.ย. 2569 07:30Z: `SmartBrowzRenderError: … {'code':
'INVALID_TOKEN', 'message': 'invalid oauth token', 'status_code': 401}` —
while the same revision rendered at 05:17Z and 05:33Z, and the same
credentials render from a fresh process.

Root cause (zcatalyst-sdk 1.4.0, credentials.py `RefreshTokenCredential.
token()`): it stores `expires_in = now + expires_in * 1000` — seconds times
1000 compared against `time()` in seconds — so a token Zoho kills after an
hour is believed for ~41 days. `_get_or_init_app()` caches the app (and so
that credential) for the process: the first render more than an hour after
the first one gets 401 until the instance restarts.

The fix lives in smartbrowz.py (the SDK is not patched): our own clock
says a cached token older than 50 minutes is expired, and an INVALID_TOKEN
answer clears the token and retries the call once.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from zcatalyst_sdk.exceptions import CatalystAPIError  # noqa: E402

from chann_app.services.pdf import smartbrowz  # noqa: E402
from chann_app.services.pdf.base import PdfOptions  # noqa: E402


class FakeCredential:
    """The SDK credential's bookkeeping, and nothing else: `token()` hands
    out the cached token, or fetches a new one when there is none."""

    def __init__(self):
        self._cached_token = None
        self.fetched = 0

    def token(self):
        if not self._cached_token:
            self.fetched += 1
            self._cached_token = {"access_token": f"tok-{self.fetched}", "expires_in": 10**12}
        return self._cached_token["access_token"]


class FakeSmartBrowz:
    def __init__(self, credential, answers):
        self.credential = credential
        self.answers = list(answers)
        self.tokens_used = []

    def _call(self):
        self.tokens_used.append(self.credential.token())
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def convert_to_pdf(self, html, options):
        return self._call()

    def take_screenshot(self, html):
        return self._call()


class FakeApp:
    def __init__(self, credential, browz):
        self.credential = credential
        self._browz = browz

    def smart_browz(self):
        return self._browz


def _invalid_token():
    return CatalystAPIError("INVALID_TOKEN", "invalid oauth token", http_status_code=401)


@pytest.fixture
def world(monkeypatch):
    credential = FakeCredential()
    clock = {"now": 1_000_000.0}
    state = {}

    def make(answers):
        browz = FakeSmartBrowz(credential, answers)
        app = FakeApp(credential, browz)
        monkeypatch.setattr(smartbrowz, "_require_config", lambda: None)
        monkeypatch.setattr(smartbrowz, "_get_or_init_app", lambda: app)
        monkeypatch.setattr(smartbrowz, "_monotonic", lambda: clock["now"])
        smartbrowz._TOKEN_BORN.clear()
        state["browz"] = browz
        return browz
    return credential, clock, make


class TestTheTokenIsRefreshedByOurOwnClock:
    @pytest.mark.asyncio
    async def test_a_token_older_than_50_minutes_is_refreshed_before_the_call(self, world, caplog):
        credential, clock, make = world
        browz = make([b"%PDF-1", b"%PDF-2", b"%PDF-3"])
        renderer = smartbrowz.SmartBrowzPdfRenderer()
        await renderer.render("<p>1</p>", PdfOptions(), "k1")
        clock["now"] += 30 * 60
        await renderer.render("<p>2</p>", PdfOptions(), "k2")
        assert browz.tokens_used == ["tok-1", "tok-1"]          # 30 min: still good
        clock["now"] += 21 * 60                                   # 51 min after it was fetched
        with caplog.at_level(logging.INFO):
            await renderer.render("<p>3</p>", PdfOptions(), "k3")
        assert browz.tokens_used[-1] == "tok-2"
        assert any("smartbrowz: access token refreshed" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_the_preview_follows_the_same_clock(self, world):
        credential, clock, make = world
        browz = make([b"png-1", b"png-2"])
        renderer = smartbrowz.SmartBrowzPdfRenderer()
        await renderer.preview_image("<p>1</p>", PdfOptions())
        clock["now"] += 55 * 60
        await renderer.preview_image("<p>2</p>", PdfOptions())
        assert browz.tokens_used == ["tok-1", "tok-2"]


class TestAnInvalidTokenIsRetriedOnce:
    @pytest.mark.asyncio
    async def test_a_401_invalid_token_retries_with_a_fresh_token(self, world):
        credential, clock, make = world
        browz = make([b"%PDF-warm", _invalid_token(), b"%PDF-ok"])
        renderer = smartbrowz.SmartBrowzPdfRenderer()
        await renderer.render("<p>warm</p>", PdfOptions(), "k0")
        out = await renderer.render("<p>receipt</p>", PdfOptions(), "k1")
        assert out.content == b"%PDF-ok"
        assert browz.tokens_used == ["tok-1", "tok-1", "tok-2"]

    @pytest.mark.asyncio
    async def test_a_second_401_raises(self, world):
        credential, clock, make = world
        browz = make([_invalid_token(), _invalid_token(), b"never"])
        renderer = smartbrowz.SmartBrowzPdfRenderer()
        with pytest.raises(smartbrowz.SmartBrowzRenderError) as exc:
            await renderer.render("<p>receipt</p>", PdfOptions(), "k1")
        assert "INVALID_TOKEN" in str(exc.value)
        assert len(browz.tokens_used) == 2                      # one retry, not more

    @pytest.mark.asyncio
    async def test_another_rejection_is_not_retried(self, world):
        credential, clock, make = world
        browz = make([CatalystAPIError("INVALID_INPUT", "bad html", http_status_code=400), b"never"])
        renderer = smartbrowz.SmartBrowzPdfRenderer()
        with pytest.raises(smartbrowz.SmartBrowzRenderError):
            await renderer.render("<p>x</p>", PdfOptions(), "k1")
        assert len(browz.tokens_used) == 1

    @pytest.mark.asyncio
    async def test_the_preview_retries_an_invalid_token_too(self, world):
        credential, clock, make = world
        browz = make([_invalid_token(), b"png"])
        out = await smartbrowz.SmartBrowzPdfRenderer().preview_image("<p>x</p>", PdfOptions())
        assert out.content == b"png"
        assert browz.tokens_used == ["tok-1", "tok-2"]


class TestTheRealSdkCredentialHasTheBookkeepingWeTouch:
    """The fake above mirrors zcatalyst-sdk 1.4.0; this pins that the real
    credential still keeps its token where we clear it, so an SDK upgrade
    that renames it fails here instead of silently never refreshing."""

    def test_the_cached_token_is_where_we_clear_it(self):
        from zcatalyst_sdk.credentials import RefreshTokenCredential

        credential = RefreshTokenCredential({"refresh_token": "r", "client_id": "c", "client_secret": "s"})
        assert hasattr(credential, "_cached_token")
        credential._cached_token = {"access_token": "old", "expires_in": 10**12}
        app = type("App", (), {"credential": credential})()
        smartbrowz._drop_token(app, "test")
        assert credential._cached_token is None
