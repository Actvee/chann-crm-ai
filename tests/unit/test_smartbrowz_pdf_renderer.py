"""Master Spec 10.6 — the concrete SmartBrowz PdfRenderer adapter
(application/chann_app/services/pdf/smartbrowz.py).

These tests call the REAL Zoho OAuth endpoint (accounts.zoho.com) with
intentionally-fake credentials, rather than mocking the zcatalyst-sdk's
internal HTTP client — that client uses `requests`, not this project's
own httpx-based DataClient, so a clean mock-transport injection (the
pattern test_smartbrowz_auth.py and test_data_client.py use) isn't
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
