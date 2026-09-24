"""Concrete PdfRenderer implementation for Zoho Catalyst SmartBrowz
(Master Spec 10.6, ADR-021).

This is the ONE file in the Application tier allowed to import
zcatalyst_sdk directly — see tests/boundary/test_tier_boundaries.py's
explicit, narrowly-scoped exception for this exact path. Every other
module in this tier depends on the PdfRenderer protocol (base.py)
instead, so the next renderer swap (should SmartBrowz itself ever need
replacing, the way ADR-021 already replaced Carbone) stays a one-class
change.

Uses the official zcatalyst-sdk Python package, initialized in
"third-party application" mode (Zoho's own documented pattern for an app
deployed outside Catalyst) — confirmed correct after determining that
Catalyst does not publicly document a raw REST endpoint for SmartBrowz's
PDF & Screenshot component specifically (see docs/SESSION_HANDOFF.md for
the full story of how this was established, including a live token
response confirming the granted OAuth scope).

Token handling is the zcatalyst-sdk's own `RefreshTokenCredential`,
which refreshes and caches an access token per process. A project-side
token manager with a Data-tier Redis cache (smartbrowz_auth.py and three
/chat/smartbrowz-token routes) existed alongside it, unused; it was
removed on 6 Sep 2026 (review E12) — a handful of Cloud Run instances
refreshing independently, at most once per ~55 minutes each, is nowhere
near Zoho's documented limit of 10 access tokens per refresh_token per
10 minutes, so a shared cache bought nothing.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import requests
import zcatalyst_sdk
from zcatalyst_sdk import _constants as sdk_constants
from zcatalyst_sdk import credentials
from zcatalyst_sdk.exceptions import CatalystAppError, CatalystError
from zcatalyst_sdk.types import ICatalystOptions

from ...config import settings
from .base import PdfOptions, PdfResult, RendererUnavailable

log = logging.getLogger(__name__)

_APP_NAME = "ChannCRMSmartBrowz"

# --- Workaround for a genuine zcatalyst-sdk==1.4.0 bug, confirmed directly ---
# Every request the SDK makes joins its own base_url with a path literal that
# already starts with "/" (e.g. credentials.py's RefreshTokenCredential.token()
# calls requester.request(path='/oauth/v2/token', ...)), and _http_client.py's
# HttpClient.request() unconditionally inserts another "/" between base_url
# and path: `url = url or (self._base_url + URL_SEPARATOR + path)`. The result
# is always a doubled slash right after the host, e.g.
# "https://accounts.zoho.com//oauth/v2/token" — regardless of what any of our
# own env vars are set to. No newer zcatalyst-sdk release exists on PyPI as of
# this writing (1.4.0 is latest) that fixes this.
#
# Confirmed directly, not guessed: curl to .../oauth/v2/token (single slash)
# -> 200 (Zoho's normal invalid_client rejection for a bad token); curl to
# .../ /oauth/v2/token (double slash, exactly what the SDK sends) -> 404 with
# a generic "Zoho Accounts" HTML roadblock page, which is what
# DefaultHttpResponse.response_json then fails to parse as JSON — surfacing
# in our own code as SmartBrowzRenderError("... UNPARSABLE_RESPONSE ...").
#
# Fixed at the actual network boundary (requests.Session.request) rather than
# by re-implementing any of the SDK's own internal URL-building logic: this
# way the fix holds regardless of which internal code path inside the SDK
# produced the doubled slash, and survives any zcatalyst-sdk point release
# that changes those internals without changing this outward symptom. Scoped
# to collapsing repeated slashes only in the path portion of the URL, never
# the "://" scheme separator.
_ORIGINAL_SESSION_REQUEST = requests.Session.request


def _collapse_duplicate_path_slashes(url: str) -> str:
    match = re.match(r"^(https?://[^/]+)(/.*)$", url)
    if not match:
        return url
    host, rest = match.groups()
    return host + re.sub(r"/{2,}", "/", rest)


#: (connect, read) seconds for a call to Zoho. The SDK's own default is
#: (60, 30). A render normally answers in 3–4 s; on 21 ก.ย. 2569 one took
#: longer than 30 and the next, 23 s later, took 3.5. A shorter read and
#: one retry (render() below) recover that case in well under the time the
#: SDK's single attempt spent failing, which matters inside a LINE webhook.
ZOHO_TIMEOUT = (10, 20)


def _host_of(url: str) -> str:
    match = re.match(r"^https?://([^/:]+)", url or "")
    return (match.group(1) if match else "").lower()


#: Where the SDK refreshes its token (`credentials.py` builds its client on
#: `_constants.ACCOUNTS_URL`). Its default is `accounts.localzoho.com`,
#: which is not under "zoho.com", and the env override can name anything —
#: so the host is read from the SDK itself rather than guessed, and a
#: misconfigured accounts URL is bounded like every other Zoho call
#: instead of running on the SDK's own (60, 30).
SDK_ACCOUNTS_HOST = _host_of(getattr(sdk_constants, "ACCOUNTS_URL", "")) or "accounts.localzoho.com"
_ZOHO_HOSTS = tuple(dict.fromkeys((
    "zoho.com", "zohoapis.com", "catalyst.zoho.com", "localzoho.com", SDK_ACCOUNTS_HOST,
)))
#: Between the two attempts. Patched to 0 in tests.
_RETRY_PAUSE_S = 1.0
_RENDER_ATTEMPTS = 2


def _is_zoho(url: str) -> bool:
    host = _host_of(url)
    return bool(host) and any(host == h or host.endswith("." + h) for h in _ZOHO_HOSTS)


def _patched_session_request(self, method, url, *args, **kwargs):
    # The timeout is set here, at the same boundary, for the same reason:
    # the SDK passes its own default explicitly, so nothing short of the
    # session sees ours. Zoho only — google-cloud-storage rides the same
    # requests library and keeps its own limits.
    if _is_zoho(url):
        kwargs["timeout"] = ZOHO_TIMEOUT
    return _ORIGINAL_SESSION_REQUEST(
        self, method, _collapse_duplicate_path_slashes(url), *args, **kwargs
    )


if not getattr(requests.Session.request, "_chann_dedup_slash_patch", False):
    _patched_session_request._chann_dedup_slash_patch = True  # guard against double-patching
    requests.Session.request = _patched_session_request


class SmartBrowzNotConfigured(RuntimeError):
    """Raised when required SmartBrowz/Catalyst config is missing — a
    deploy/config problem, not a provider outage."""


class SmartBrowzRenderError(RuntimeError):
    """Raised when SmartBrowz itself rejects or fails a render request —
    a genuine provider-side failure. Per 10.6, this must always surface
    as a clear failure; nothing in this module ever falls back to
    fabricating a document."""


class SmartBrowzUnavailable(SmartBrowzRenderError, RendererUnavailable):
    """Zoho did not answer in time, twice. Both names on purpose: routes
    that catch SmartBrowzRenderError keep working, and services that only
    know the renderer seam catch RendererUnavailable."""



def _require_config() -> None:
    missing = [
        name for name, value in (
            ("SMARTBROWZ_CLIENT_ID", settings.smartbrowz_client_id),
            ("SMARTBROWZ_CLIENT_SECRET", settings.smartbrowz_client_secret),
            ("SMARTBROWZ_REFRESH_TOKEN", settings.smartbrowz_refresh_token),
            ("CATALYST_PROJECT_ID", settings.catalyst_project_id),
            ("CATALYST_ZAID", settings.catalyst_zaid),
        ) if not value
    ]
    if missing:
        raise SmartBrowzNotConfigured(
            "SmartBrowz/Catalyst is not fully configured — missing: "
            + ", ".join(missing)
        )


def _get_or_init_app():
    """Lazy-init-once. initialize_app() raises CatalystAppError on a
    second call with the same name (the SDK's own bookkeeping is
    thread-scoped) — safe here because Uvicorn runs this app's async
    request handling on a single thread's event loop per worker process,
    so "already initialized in this thread" correctly means "already
    initialized for this whole process"."""
    try:
        return zcatalyst_sdk.get_app(_APP_NAME)
    except CatalystAppError:
        pass

    _require_config()
    catalyst_credential = credentials.RefreshTokenCredential({
        "refresh_token": settings.smartbrowz_refresh_token,
        "client_id": settings.smartbrowz_client_id,
        "client_secret": settings.smartbrowz_client_secret,
    })
    catalyst_options = ICatalystOptions(
        project_id=settings.catalyst_project_id,
        project_key=settings.catalyst_zaid,
        project_domain=settings.catalyst_api_domain,
        environment=settings.catalyst_environment,
    )
    return zcatalyst_sdk.initialize_app(
        credential=catalyst_credential, options=catalyst_options, name=_APP_NAME,
    )


# ------------------------------------------------ the access token's real age
#
# DEV, 24 ก.ย. 2569 07:30Z: receipts failed with INVALID_TOKEN (401) while
# the same revision rendered at 05:17Z/05:33Z. zcatalyst-sdk 1.4.0's
# `RefreshTokenCredential.token()` stores `expires_in = now + expires_in *
# 1000` — seconds times 1000, compared against `time()` in seconds — so a
# token Zoho kills after one hour is believed for ~41 days, and the app
# (with its credential) is cached for the process. The SDK is not patched;
# our own clock decides instead: a token we have held for 50 minutes is
# dropped before the call, and an INVALID_TOKEN answer drops it and retries
# the call once.

_TOKEN_MAX_AGE_S = 50 * 60
_monotonic = time.monotonic
#: id(credential) -> (access token, when WE first saw it).
_TOKEN_BORN: dict[int, tuple[str, float]] = {}


def _credential_of(app):
    return getattr(app, "credential", None)


def _cached_access_token(credential) -> str | None:
    cached = getattr(credential, "_cached_token", None) or {}
    return cached.get("access_token") if isinstance(cached, dict) else None


def _drop_token(app, why: str) -> None:
    credential = _credential_of(app)
    if credential is None or not hasattr(credential, "_cached_token"):
        return
    credential._cached_token = None
    _TOKEN_BORN.pop(id(credential), None)
    log.info("smartbrowz: access token refreshed (%s)", why)


def _expire_if_old(app) -> None:
    credential = _credential_of(app)
    token = _cached_access_token(credential)
    if not token:
        return
    seen = _TOKEN_BORN.get(id(credential))
    if seen is None or seen[0] != token:
        _TOKEN_BORN[id(credential)] = (token, _monotonic())
        return
    if _monotonic() - seen[1] >= _TOKEN_MAX_AGE_S:
        _drop_token(app, "older than 50 minutes")


def _note_token(app) -> None:
    credential = _credential_of(app)
    token = _cached_access_token(credential)
    if token and (_TOKEN_BORN.get(id(credential)) or ("",))[0] != token:
        _TOKEN_BORN[id(credential)] = (token, _monotonic())


def _is_invalid_token(exc: Exception) -> bool:
    return str(getattr(exc, "code", "") or "") == "INVALID_TOKEN" or "invalid oauth token" in str(exc).lower()


async def _call_with_fresh_token(app, fn, *args):
    """One SmartBrowz call with a token our clock trusts; an INVALID_TOKEN
    answer drops the token and retries ONCE — a second one is raised."""
    _expire_if_old(app)
    try:
        result = await asyncio.to_thread(fn, *args)
    except CatalystError as exc:
        if not _is_invalid_token(exc):
            raise
        _drop_token(app, "INVALID_TOKEN from Zoho")
        result = await asyncio.to_thread(fn, *args)
    _note_token(app)
    return result


def _to_sdk_pdf_options(options: PdfOptions) -> dict:
    sdk_options = {
        "format": options.page_format,
        "landscape": options.landscape,
        "print_background": options.print_background,
    }
    if options.password:
        sdk_options["password"] = options.password
    sdk_options.update(options.extra)
    return sdk_options


class SmartBrowzPdfRenderer:
    """The PdfRenderer protocol implementation get_renderer("smartbrowz")
    returns. idempotency_key is accepted (protocol compliance) but not
    yet used — SmartBrowz's own API has no idempotency-key concept to
    forward it to; de-duplication, if ever needed, belongs at the
    generated_documents layer instead (Phase 10's own audit-trail table),
    not here.
    """

    name = "smartbrowz"

    async def render(self, html: str, options: PdfOptions, idempotency_key: str) -> PdfResult:
        _require_config()
        app = _get_or_init_app()
        smart_browz = app.smart_browz()
        # Rendering has no side effect on our side (the store and the row
        # come after), so a second attempt after a timeout costs nothing
        # but time. One retry: a slow answer recovers, an outage is said
        # in words within ~45 s rather than a stack trace after 30.
        last: Exception | None = None
        for attempt in range(1, _RENDER_ATTEMPTS + 1):
            try:
                result = await _call_with_fresh_token(
                    app, smart_browz.convert_to_pdf, html, _to_sdk_pdf_options(options),
                )
                break
            except CatalystError as exc:
                raise SmartBrowzRenderError(f"SmartBrowz/Zoho rejected the render: {exc}") from exc
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last = exc
                log.warning("SmartBrowz did not answer (attempt %d/%d): %s", attempt, _RENDER_ATTEMPTS, exc)
                if attempt < _RENDER_ATTEMPTS and _RETRY_PAUSE_S:
                    await asyncio.sleep(_RETRY_PAUSE_S)
            except Exception as exc:  # noqa: BLE001
                raise SmartBrowzRenderError(f"Unexpected error calling SmartBrowz: {exc}") from exc
        else:
            raise SmartBrowzUnavailable(
                "SmartBrowz (Zoho) did not answer in time — try again in a moment"
            ) from last
        content = getattr(result, "content", None) or result
        return PdfResult(content=content, url=None, renderer=self.name)

    async def preview_image(self, html: str, options: PdfOptions) -> PdfResult:
        _require_config()
        app = _get_or_init_app()
        smart_browz = app.smart_browz()
        try:
            result = await _call_with_fresh_token(app, smart_browz.take_screenshot, html)
        except CatalystError as exc:
            raise SmartBrowzRenderError(f"SmartBrowz/Zoho rejected the preview: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise SmartBrowzRenderError(f"Unexpected error calling SmartBrowz: {exc}") from exc
        content = getattr(result, "content", None) or result
        return PdfResult(content=content, url=None, renderer=self.name)


async def verify_connection() -> dict:
    """Master Spec 10.6's own requirement: verify the actual auth path
    works from the deployed Application environment before claiming
    readiness — this is that verification, not a step towards generating
    a real quote PDF yet. Converts one trivial, fixed HTML snippet.
    Returns a small dict on success (never the PDF bytes themselves —
    this proves connectivity, it isn't the render adapter's real job).
    """
    renderer = SmartBrowzPdfRenderer()
    result = await renderer.render(
        "<html><body><p>chann-crm-ai SmartBrowz connectivity check</p></body></html>",
        PdfOptions(),
        idempotency_key="connectivity-check",
    )
    size = len(result.content) if result.content is not None else None
    log.info("SmartBrowz connectivity check succeeded (output size: %s)", size)
    return {"ok": True, "output_size": size}
