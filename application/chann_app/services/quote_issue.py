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
from .documents.fill import fill_template
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


async def _resolve_template(
    client: DataClient, license_id: str, snapshot: dict, *, actor_id: str | None = None,
) -> tuple[str, str]:
    """(template_version_id, html) — the shop's own template, or the
    built-in one.

    A tenant that has published a template for this document type gets
    theirs; everyone else gets the layout in the codebase. Falling back
    rather than failing matters because a shop should not lose the ability
    to issue a quote by uploading a template that turns out to be broken.

    The published version's HTML is filled by simple placeholder
    substitution rather than a template language. Anything richer would be
    a code path a tenant controls, running on our server, on data from
    other tenants' snapshots — the safe version of "upload your own
    design" is one that can only put values into holes.
    """
    try:
        templates = await client.list_document_templates(
            license_id, document_type="quote",
        )
    except Exception:
        log.exception("could not read templates; falling back to the built-in")
        templates = []

    for template in templates:
        if template.get("template_code") == BUILTIN_QUOTE_TEMPLATE_CODE:
            continue
        if not template.get("is_active", True):
            continue
        try:
            versions = await client.list_document_template_versions(
                license_id, str(template["id"]),
            )
        except Exception:
            log.exception("could not read versions for template %s", template.get("id"))
            continue

        published = [v for v in versions if v.get("status") == "published"]
        if not published:
            continue
        # Highest version number, so republishing supersedes rather than
        # having to unpublish the old one first.
        newest = max(published, key=lambda v: int(v.get("version") or 0))
        compiled = str(newest.get("compiled_template_path") or "")
        if not compiled or compiled.startswith("builtin://"):
            continue

        try:
            from .storage.base import get_document_store

            raw = await get_document_store().get(path=compiled)
            html = fill_template(raw.decode("utf-8"), snapshot)
        except Exception:
            # A tenant's template that cannot be loaded or filled must not
            # stop them issuing quotes. Their layout is lost for this
            # document; their ability to do business is not.
            log.exception(
                "tenant template %s could not be used; using the built-in",
                newest.get("id"),
            )
            break

        return str(newest["id"]), html

    version_id = await _ensure_builtin_template_version(
        client, license_id, actor_id=actor_id,
    )
    return version_id, render_quote_html(snapshot)


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


class QuoteAlreadyIssued(RuntimeError):
    """This quote already has a generated document.

    Raised rather than silently producing a second one: two documents with
    different digests for the same quote makes "which file did the customer
    receive" unanswerable, which is the exact question the audit trail
    exists to answer.
    """


async def issue_quote_document(
    client: DataClient, *, license_id: str, quote: dict, deal: dict, customer: dict,
    company: dict, actor_id: str | None = None, allow_reissue: bool = False,
) -> dict:
    """Render, store and record. Returns the generated_documents row.

    Raises the same typed errors the callers already translate:
    QuoteNotRenderable (tenant data incomplete), SmartBrowzNotConfigured /
    SmartBrowzRenderError (provider), DocumentStoreNotConfigured /
    DocumentStoreError (storage). Nothing is caught and softened here — a
    document that could not be produced must never look like one that was.
    """
    if quote.get("generated_document_id") and not allow_reissue:
        # Not a hard error — re-issuing after a genuine correction is
        # legitimate — but never the silent default. Two documents with
        # different digests for one quote is exactly the ambiguity
        # generated_documents exists to prevent, so the second one has to
        # be asked for explicitly.
        raise QuoteAlreadyIssued(
            f"quote {quote.get('quote_id')} already has an issued document"
        )

    issued_at = datetime.now(timezone.utc)
    snapshot = build_quote_snapshot(
        quote=quote, deal=deal, customer=customer, company=company, issued_at=issued_at,
    )

    # A shop's own template if it has published one, the built-in
    # otherwise. Chosen BEFORE rendering, not recorded afterwards: the
    # template version was previously resolved only to store its id
    # alongside the finished PDF, which meant every tenant got the
    # built-in layout no matter what they had uploaded.
    template_version_id, html = await _resolve_template(
        client, license_id, snapshot, actor_id=actor_id,
    )

    renderer = get_renderer("smartbrowz")
    result = await renderer.render(
        html, PdfOptions(),
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

    document = await client.record_generated_document(
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

    # Link the quote to its document and move it out of draft. Done after
    # the record exists, so a failure here leaves a complete, findable
    # document rather than a quote claiming a document that was never
    # written. Failures are logged and swallowed for the same reason: the
    # document IS issued at this point, and telling the caller it failed
    # would invite a re-issue that duplicates a real customer-facing file.
    try:
        await client.link_quote_document(
            license_id, str(quote["id"]), str(document["id"]), actor_id=actor_id
        )
    except Exception:
        log.exception(
            "document %s was issued for quote %s but linking it back failed",
            document.get("id"), quote.get("quote_id"),
        )

    if str(quote.get("status") or "").lower() == "draft":
        try:
            await client.transition_quote_status(
                license_id, str(quote["id"]), "sent", actor_id=actor_id
            )
        except Exception:
            log.exception(
                "document issued for quote %s but the status transition failed",
                quote.get("quote_id"),
            )

    return document
