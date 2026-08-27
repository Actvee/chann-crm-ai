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

Deliberately does NOT use this project's own SmartBrowzTokenManager
(smartbrowz_auth.py, a Data-tier-cached token manager built before this
adapter existed) — the zcatalyst-sdk's own `RefreshTokenCredential`
already refreshes and caches an access token internally per-process, and
duplicating that against the Data-tier cache as well would just be two
caches disagreeing with each other for no benefit at this project's
scale (a handful of Cloud Run instances refreshing independently, at
most once per ~55 minutes each, is nowhere near Zoho's documented rate
limit of 10 access tokens per refresh_token per 10 minutes).
SmartBrowzTokenManager is kept as-is, unused by this module, in case a
future scale-up ever makes the shared-cache benefit worth the added
complexity.
"""
from __future__ import annotations

import asyncio
import logging

import zcatalyst_sdk
from zcatalyst_sdk import credentials
from zcatalyst_sdk.exceptions import CatalystAppError, CatalystError
from zcatalyst_sdk.types import ICatalystOptions

from ...config import settings
from .base import PdfOptions, PdfResult

log = logging.getLogger(__name__)

_APP_NAME = "ChannCRMSmartBrowz"


class SmartBrowzNotConfigured(RuntimeError):
    """Raised when required SmartBrowz/Catalyst config is missing — a
    deploy/config problem, not a provider outage."""


class SmartBrowzRenderError(RuntimeError):
    """Raised when SmartBrowz itself rejects or fails a render request —
    a genuine provider-side failure. Per 10.6, this must always surface
    as a clear failure; nothing in this module ever falls back to
    fabricating a document."""


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
        try:
            result = await asyncio.to_thread(
                smart_browz.convert_to_pdf, html, _to_sdk_pdf_options(options),
            )
        except CatalystError as exc:
            raise SmartBrowzRenderError(f"SmartBrowz/Zoho rejected the render: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise SmartBrowzRenderError(f"Unexpected error calling SmartBrowz: {exc}") from exc
        content = getattr(result, "content", None) or result
        return PdfResult(content=content, url=None, renderer=self.name)

    async def preview_image(self, html: str, options: PdfOptions) -> PdfResult:
        _require_config()
        app = _get_or_init_app()
        smart_browz = app.smart_browz()
        try:
            result = await asyncio.to_thread(smart_browz.take_screenshot, html)
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
