"""Phase 10 — where a generated document is stored.

Mirrors the `PdfRenderer` seam (ADR-021's pattern) for the same reason:
`generated_documents.output_path` is the audit trail's only link to the
actual bytes a customer received, and the thing holding those bytes should
be swappable without touching anything that records or reads that link.

Two implementations:

  * `GcsDocumentStore` — the real one.
  * `NullDocumentStore` — refuses loudly. It exists so an environment with
    no bucket configured fails with a clear "storage is not configured"
    rather than silently skipping the write and leaving a
    `generated_documents` row pointing at nothing. A recorded document that
    cannot be produced on demand is worse than no record, because later it
    still looks authoritative.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


class DocumentStoreNotConfigured(RuntimeError):
    """No object store is wired up — a deploy/config problem, not an outage."""


class DocumentStoreError(RuntimeError):
    """The store rejected or failed the write. Per 10.6's principle, this
    surfaces as a clear failure; nothing ever pretends a document was
    stored when it was not."""


@dataclass
class StoredDocument:
    path: str
    sha256: str
    size: int


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class DocumentStore(Protocol):
    async def put(self, *, key: str, content: bytes, content_type: str) -> StoredDocument:
        ...

    async def signed_url(self, *, path: str, expires_seconds: int) -> str:
        ...

    async def get(self, *, path: str) -> bytes:
        ...

    async def delete(self, *, path: str) -> None:
        ...


class NullDocumentStore:
    name = "null"

    async def put(self, *, key: str, content: bytes, content_type: str) -> StoredDocument:
        raise DocumentStoreNotConfigured(
            "document storage is not configured — GCS_BUCKET_NAME is unset"
        )

    async def signed_url(self, *, path: str, expires_seconds: int) -> str:
        raise DocumentStoreNotConfigured(
            "document storage is not configured — GCS_BUCKET_NAME is unset"
        )

    async def get(self, *, path: str) -> bytes:
        raise DocumentStoreNotConfigured(
            "document storage is not configured — GCS_BUCKET_NAME is unset"
        )

    async def delete(self, *, path: str) -> None:
        raise DocumentStoreNotConfigured(
            "document storage is not configured — GCS_BUCKET_NAME is unset"
        )


def get_document_store(name: str | None = None):
    """Factory. Imports the GCS adapter locally so this module never pulls
    in the vendor library, exactly as `pdf/base.py` does for zcatalyst."""
    from ...config import settings

    chosen = name or ("gcs" if settings.gcs_bucket_name else "null")
    if chosen == "null":
        return NullDocumentStore()
    if chosen == "gcs":
        from .gcs import GcsDocumentStore

        return GcsDocumentStore()
    raise ValueError(f"unknown document store: {chosen!r}")
