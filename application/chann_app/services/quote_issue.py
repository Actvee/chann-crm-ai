"""Phase 10 — issuing a quote as a recorded document.

This is the step that makes a rendered PDF *evidence* rather than a
preview: the bytes go to object storage, and a `generated_documents` row
records exactly which template version and which frozen data snapshot
produced them, plus the SHA-256 of the file itself.

Order matters and is deliberate: store first, record second. A stored
object with no row is an orphan taking up space — annoying, harmless,
and findable. A row with no object is a lie that looks authoritative
forever. So the write that can be wrong is done first, and the record is
only made once the bytes are provably somewhere.

The built-in template registers itself as a real `document_templates` +
`document_template_versions` row rather than being special-cased out of
the schema. `template_version_id` is NOT NULL for a good reason — "which
template produced this document" must always be answerable — and a
built-in template is still a template. Bumping BUILTIN_QUOTE_TEMPLATE_VERSION
when the HTML changes creates a new version row, so documents issued
before the change keep pointing at the version that actually rendered
them.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..data_client import DataClient
from .documents.html import render_quote_html
from .documents.snapshot import build_quote_snapshot
from .pdf.base import PdfOptions, get_renderer
from .storage.base import get_document_store, sha256_hex

log = logging.getLogger(__name__)

BUILTIN_QUOTE_TEMPLATE_CODE = "BUILTIN-QUOTE"
BUILTIN_QUOTE_TEMPLATE_NAME = "ใบเสนอราคา (แบบมาตรฐานของระบบ)"
# Bump this whenever render_quote_html's output changes in a way that would
# make an old document re-render differently.
BUILTIN_QUOTE_TEMPLATE_VERSION = 1

# No dots: a quote code never contains one, and allowing them lets a
# malformed code leave ".." sequences in an object name. GCS keys are flat
# so that is not traversal, but it confuses tooling that treats keys as
# paths, and there is nothing to gain by permitting it.
_SAFE_KEY = re.compile(r"[^A-Za-z0-9_-]+")


def document_key(*, license_id: str, quote_code: str, issued_at: datetime, sha256: str) -> str:
    """Object key for a generated quote.

    Tenant-prefixed so one tenant's documents are never interleaved with
    another's, and suffixed with the content digest so re-issuing after a
    real change lands on a new key instead of colliding with the
    create-only upload guard.
    """
    safe_code = _SAFE_KEY.sub("-", quote_code or "quote")
    return (
        f"documents/{license_id}/quotes/"
        f"{issued_at:%Y/%m}/{safe_code}-{sha256[:12]}.pdf"
    )


async def _ensure_builtin_template_version(
    client: DataClient, license_id: str, *, actor_id: str | None = None,
) -> str:
    """Returns the id of the published built-in template version for this
    tenant, creating the template and version on first use.

    Idempotent by business key (template_code + version number), so
    concurrent first-issues converge on the same row rather than racing to
    create duplicates.
    """
    templates = await client.list_document_templates(license_id, document_type="quote")
    template = next(
        (t for t in templates if t.get("template_code") == BUILTIN_QUOTE_TEMPLATE_CODE), None
    )
    if template is None:
        template = await client.create_document_template(
            license_id,
            {
                "document_type": "quote",
                "template_code": BUILTIN_QUOTE_TEMPLATE_CODE,
                "template_name": BUILTIN_QUOTE_TEMPLATE_NAME,
            },
            actor_id=actor_id,
        )

    template_id = str(template["id"])
    versions = await client.list_document_template_versions(license_id, template_id)
    existing = next(
        (v for v in versions if v.get("version") == BUILTIN_QUOTE_TEMPLATE_VERSION), None
    )
    if existing is not None:
        return str(existing["id"])

    version = await client.create_document_template_version(
        license_id,
        template_id,
        {
            # The built-in template has no uploaded DOCX and no compiled
            # artifact on disk: it is code. Recording that explicitly with a
            # `builtin://` scheme is more honest than inventing a GCS path
            # that nothing will ever be stored at — a later reader can tell
            # at a glance that this version came from the codebase, not from
            # a tenant upload.
            "source_docx_path": "builtin://none",
            "intermediate_model": {
                "kind": "builtin",
                "module": "chann_app.services.documents.html:render_quote_html",
                "version": BUILTIN_QUOTE_TEMPLATE_VERSION,
            },
            "mapping_schema": {"kind": "builtin"},
            "compiled_template_path": (
                f"builtin://quote/v{BUILTIN_QUOTE_TEMPLATE_VERSION}"
            ),
        },
        actor_id=actor_id,
    )
    version_id = str(version["id"])
    # Published immediately: a built-in template needs no human review step,
    # and an unpublished version cannot legitimately be referenced by a
    # generated document.
    await client.publish_document_template_version(
        license_id, version_id, actor_id=actor_id
    )
    return version_id


async def issue_quote_document(
    client: DataClient, *, license_id: str, quote: dict, deal: dict, customer: dict,
    company: dict, actor_id: str | None = None,
) -> dict:
    """Render, store and record. Returns the generated_documents row.

    Raises the same typed errors the callers already translate:
    QuoteNotRenderable (tenant data incomplete), SmartBrowzNotConfigured /
    SmartBrowzRenderError (provider), DocumentStoreNotConfigured /
    DocumentStoreError (storage). Nothing is caught and softened here — a
    document that could not be produced must never look like one that was.
    """
    issued_at = datetime.now(timezone.utc)
    snapshot = build_quote_snapshot(
        quote=quote, deal=deal, customer=customer, company=company, issued_at=issued_at,
    )

    renderer = get_renderer("smartbrowz")
    result = await renderer.render(
        render_quote_html(snapshot), PdfOptions(),
        idempotency_key=f"quote:{license_id}:{quote.get('id')}",
    )
    if not result.content:
        # A "successful" render with no bytes must fail rather than record
        # a zero-length document as evidence.
        raise RuntimeError("renderer returned no document content")

    digest = sha256_hex(result.content)
    key = document_key(
        license_id=license_id,
        quote_code=str(quote.get("quote_id") or ""),
        issued_at=issued_at,
        sha256=digest,
    )

    store = get_document_store()
    stored = await store.put(key=key, content=result.content, content_type="application/pdf")

    template_version_id = await _ensure_builtin_template_version(
        client, license_id, actor_id=actor_id
    )

    return await client.record_generated_document(
        license_id,
        {
            "document_type": "quote",
            "source_entity_type": "quote",
            "source_entity_id": str(quote["id"]),
            "template_version_id": template_version_id,
            "data_snapshot": snapshot,
            "output_path": stored.path,
            "sha256": stored.sha256,
            "renderer": result.renderer,
        },
        actor_id=actor_id,
    )
