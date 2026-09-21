"""Master Spec 10.6 — the concrete SmartBrowz PdfRenderer adapter
(application/chann_app/services/pdf/smartbrowz.py).

These tests call the REAL Zoho OAuth endpoint (accounts.zoho.com) with
intentionally-fake credentials, rather than mocking the zcatalyst-sdk's
internal HTTP client — that client uses `requests`, not this project's
own httpx-based DataClient, so a clean mock-transport injection (the
pattern test_data_client.py uses) isn't
available here without patching library internals. A real network round
trip with fake credentials is fast (Zoho rejects immediately) and proves
the actual error-handling paths this module depends on, consistent with
this project's established preference for validating against the real
thing wherever practical.

Genuinely successful rendering (real credentials, a real PDF coming
back) is intentionally NOT covered here — that needs the owner's actual
SmartBrowz credentials configured in the deployed environment, which is
exactly what the /platform/smartbrowz/verify-connection endpoint (behind
platform-admin auth) is for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402
from chann_app.services.pdf.base import PdfOptions  # noqa: E402
from chann_app.services.pdf.smartbrowz import (  # noqa: E402
    SmartBrowzNotConfigured,
    SmartBrowzPdfRenderer,
    SmartBrowzRenderError,
    verify_connection,
)


@pytest.fixture(autouse=True)
def _smartbrowz_settings():
    """Isolates SmartBrowz/Catalyst config per test — this is process-
    global pydantic-settings state."""
    original = (
        settings.smartbrowz_client_id, settings.smartbrowz_client_secret,
        settings.smartbrowz_refresh_token, settings.catalyst_project_id,
        settings.catalyst_zaid,
    )
    yield
    (
        settings.smartbrowz_client_id, settings.smartbrowz_client_secret,
        settings.smartbrowz_refresh_token, settings.catalyst_project_id,
        settings.catalyst_zaid,
    ) = original


def _set_fake_credentials():
    settings.smartbrowz_client_id = "fake-client-id"
    settings.smartbrowz_client_secret = "fake-client-secret"
    settings.smartbrowz_refresh_token = "fake-refresh-token"
    settings.catalyst_project_id = "12345"
    settings.catalyst_zaid = "67890"


def _clear_credentials():
    settings.smartbrowz_client_id = ""
    settings.smartbrowz_client_secret = ""
    settings.smartbrowz_refresh_token = ""
    settings.catalyst_project_id = ""
    settings.catalyst_zaid = ""


class TestSmartBrowzPdfRenderer:
    async def test_missing_config_raises_a_clear_error_before_any_network_call(self):
        """10.6: a provider outage (or here, simply not being configured
        yet) must surface as a clear render failure, never something that
        could be mistaken for AI fabricating a document."""
        _clear_credentials()
        renderer = SmartBrowzPdfRenderer()
        with pytest.raises(SmartBrowzNotConfigured, match="missing"):
            await renderer.render("<html></html>", PdfOptions(), "test-key")

    async def test_fake_credentials_are_rejected_by_the_real_zoho_endpoint(self):
        """Proves the real network path (Application tier -> Zoho OAuth)
        actually works end to end and fails cleanly on bad credentials —
        the exact scenario found live: a misconfigured/missing credential
        must never look like a successful render."""
        _set_fake_credentials()
        renderer = SmartBrowzPdfRenderer()
        with pytest.raises(SmartBrowzRenderError):
            await renderer.render("<html><body>test</body></html>", PdfOptions(), "test-key")

    async def test_preview_image_also_requires_config(self):
        _clear_credentials()
        renderer = SmartBrowzPdfRenderer()
        with pytest.raises(SmartBrowzNotConfigured):
            await renderer.preview_image("<html></html>", PdfOptions())

    async def test_verify_connection_surfaces_the_same_clear_errors(self):
        """The /platform/smartbrowz/verify-connection endpoint calls this
        directly — it must raise the same typed errors the endpoint
        translates into 503 (not configured) vs 502 (provider rejected),
        not some third, undistinguished shape."""
        _clear_credentials()
        with pytest.raises(SmartBrowzNotConfigured):
            await verify_connection()

        _set_fake_credentials()
        with pytest.raises(SmartBrowzRenderError):
            await verify_connection()

    async def test_get_renderer_smartbrowz_returns_the_adapter(self):
        """get_renderer("smartbrowz") is how the rest of the application
        is meant to reach this adapter — never by importing this module
        directly (see the boundary test's exception, which exists only
        for this one file)."""
        from chann_app.services.pdf.base import get_renderer

        renderer = get_renderer("smartbrowz")
        assert isinstance(renderer, SmartBrowzPdfRenderer)
        assert renderer.name == "smartbrowz"


class TestDuplicateSlashWorkaround:
    """zcatalyst-sdk==1.4.0 has a real, confirmed bug: every request it
    makes joins its own base_url with a path literal that already starts
    with "/" (e.g. credentials.py's RefreshTokenCredential.token() calls
    requester.request(path='/oauth/v2/token', ...)), and
    _http_client.py's HttpClient.request() unconditionally inserts
    another "/" between them — producing a doubled slash right after the
    host on every single call (e.g.
    https://accounts.zoho.com//oauth/v2/token). Confirmed directly
    against the real endpoint, not guessed: a single-slash request
    returns 200 (Zoho's normal invalid_client rejection for a bad
    token); the exact doubled-slash request the SDK actually sends
    returns 404 with a generic "Zoho Accounts" HTML roadblock page,
    which DefaultHttpResponse.response_json then fails to parse as
    JSON — surfacing in our own code as a confusing
    SmartBrowzRenderError("... UNPARSABLE_RESPONSE ...") that looks
    like a provider outage or bad credentials but is neither. No newer
    zcatalyst-sdk release fixing this exists on PyPI as of this
    writing (1.4.0 is latest), so smartbrowz.py works around it at the
    actual network boundary instead of waiting on an upstream fix.
    """

    def test_collapse_duplicate_path_slashes_fixes_the_known_sdk_bug(self):
        from chann_app.services.pdf.smartbrowz import _collapse_duplicate_path_slashes

        assert (
            _collapse_duplicate_path_slashes("https://accounts.zoho.com//oauth/v2/token")
            == "https://accounts.zoho.com/oauth/v2/token"
        )

    def test_already_single_slash_urls_pass_through_unchanged(self):
        from chann_app.services.pdf.smartbrowz import _collapse_duplicate_path_slashes

        url = "https://api.catalyst.zoho.com/browser360/v1/project/123/convert"
        assert _collapse_duplicate_path_slashes(url) == url

    def test_the_https_scheme_separator_itself_is_never_touched(self):
        from chann_app.services.pdf.smartbrowz import _collapse_duplicate_path_slashes

        assert _collapse_duplicate_path_slashes("https://example.com") == "https://example.com"

    def test_requests_session_is_actually_patched(self):
        """Not just that the pure helper function works in isolation —
        this confirms the monkeypatch is really installed on
        requests.Session, which is the part that actually protects
        every real zcatalyst-sdk call made through this module."""
        import requests

        assert getattr(requests.Session.request, "_chann_dedup_slash_patch", False) is True


class _SlowThenFine:
    """A SmartBrowz that times out N times, then answers."""

    def __init__(self, failures, exc=None):
        self.calls = 0
        self.failures = failures
        self.exc = exc or requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='api.catalyst.zoho.com', port=443): Read timed out. (read timeout=20)"
        )

    def convert_to_pdf(self, html, options):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return b"%PDF-1.4 fine"


def _wire(monkeypatch, browz):
    import chann_app.services.pdf.smartbrowz as module

    class _App:
        def smart_browz(self):
            return browz

    monkeypatch.setattr(module, "_require_config", lambda: None)
    monkeypatch.setattr(module, "_get_or_init_app", lambda: _App())
    monkeypatch.setattr(module, "_RETRY_PAUSE_S", 0)


class TestATransientTimeoutIsRetried:
    """Owner, 21 ก.ย. 2569 16:04: "ออกเอกสารไม่สำเร็จ: Unexpected error calling
    SmartBrowz: … Read timed out. (read timeout=30)". Zoho answered the
    very next attempt in 3.5 s. One slow answer must not be the person's
    problem, and when it is, the words must say what to do."""

    async def test_one_slow_answer_is_tried_again_and_succeeds(self, monkeypatch):
        browz = _SlowThenFine(failures=1)
        _wire(monkeypatch, browz)
        result = await SmartBrowzPdfRenderer().render("<p>x</p>", PdfOptions(), idempotency_key="k")
        assert result.content == b"%PDF-1.4 fine"
        assert browz.calls == 2

    async def test_two_slow_answers_are_said_in_words_not_a_stack_trace(self, monkeypatch):
        from chann_app.services.pdf.base import RendererUnavailable
        from chann_app.services.pdf.smartbrowz import SmartBrowzUnavailable

        browz = _SlowThenFine(failures=5)
        _wire(monkeypatch, browz)
        with pytest.raises(SmartBrowzUnavailable) as caught:
            await SmartBrowzPdfRenderer().render("<p>x</p>", PdfOptions(), idempotency_key="k")
        assert browz.calls == 2, "one retry, not a storm"
        assert isinstance(caught.value, RendererUnavailable)
        assert isinstance(caught.value, SmartBrowzRenderError), "callers catching the old name still catch it"
        assert "HTTPSConnectionPool" not in str(caught.value)
        assert "try again" in str(caught.value)

    async def test_a_connection_error_is_retried_too(self, monkeypatch):
        browz = _SlowThenFine(failures=1, exc=requests.exceptions.ConnectionError("reset by peer"))
        _wire(monkeypatch, browz)
        result = await SmartBrowzPdfRenderer().render("<p>x</p>", PdfOptions(), idempotency_key="k")
        assert result.content and browz.calls == 2

    async def test_a_rejection_by_zoho_is_not_retried(self, monkeypatch):
        from zcatalyst_sdk.exceptions import CatalystError

        browz = _SlowThenFine(failures=5, exc=CatalystError("INVALID_HTML", "bad html"))
        _wire(monkeypatch, browz)
        with pytest.raises(SmartBrowzRenderError, match="rejected"):
            await SmartBrowzPdfRenderer().render("<p>x</p>", PdfOptions(), idempotency_key="k")
        assert browz.calls == 1

    def test_only_zoho_calls_get_the_shorter_read_timeout(self, monkeypatch):
        """The session patch is process-wide (GCS goes through requests too);
        the tighter timeout must reach Zoho only."""
        import chann_app.services.pdf.smartbrowz as module

        seen = []

        def fake_original(self, method, url, *args, **kwargs):
            seen.append((url, kwargs.get("timeout")))
            return "ok"

        monkeypatch.setattr(module, "_ORIGINAL_SESSION_REQUEST", fake_original)
        session = requests.Session()
        module._patched_session_request(session, "POST", "https://api.catalyst.zoho.com/baas//v1/x", timeout=(60, 30))
        module._patched_session_request(session, "GET", "https://storage.googleapis.com/b/o", timeout=(60, 30))
        assert seen[0] == ("https://api.catalyst.zoho.com/baas/v1/x", module.ZOHO_TIMEOUT)
        assert seen[1] == ("https://storage.googleapis.com/b/o", (60, 30))
