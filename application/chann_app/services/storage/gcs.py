"""Google Cloud Storage implementation of DocumentStore.

The one file in the Application tier that imports the GCS client, for the
same reason `pdf/smartbrowz.py` is the one that imports zcatalyst — see
`base.py` for the seam's rationale.

Authentication is Application Default Credentials: on Cloud Run that is the
attached service account, with no key file anywhere. Nothing here reads or
needs a credential from config.

Uploads are wrapped in `asyncio.to_thread` because the google-cloud-storage
client is synchronous; calling it directly would block the event loop for
the whole upload, stalling every other request this instance is serving.
"""
from __future__ import annotations

import asyncio
import logging

from google.api_core import exceptions as gcs_exceptions
from google.cloud import storage

from ...config import settings
from .base import DocumentStoreError, DocumentStoreNotConfigured, StoredDocument, sha256_hex

log = logging.getLogger(__name__)

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    """Lazy singleton. Building a Client performs credential discovery, so
    doing it once per process rather than once per upload keeps a burst of
    documents from re-resolving ADC every time."""
    global _client
    if _client is None:
        _client = storage.Client(project=settings.gcp_project_id or None)
    return _client


class GcsDocumentStore:
    name = "gcs"

    def _blocking_put(self, *, key: str, content: bytes, content_type: str) -> None:
        bucket = _get_client().bucket(settings.gcs_bucket_name)
        blob = bucket.blob(key)
        # if_generation_match=0 makes this create-only: a second write to the
        # same key fails instead of silently replacing bytes that a
        # generated_documents row already claims are immutable evidence.
        blob.upload_from_string(
            content, content_type=content_type, if_generation_match=0
        )

    async def put(self, *, key: str, content: bytes, content_type: str) -> StoredDocument:
        if not settings.gcs_bucket_name:
            raise DocumentStoreNotConfigured(
                "document storage is not configured — GCS_BUCKET_NAME is unset"
            )
        try:
            await asyncio.to_thread(
                self._blocking_put, key=key, content=content, content_type=content_type
            )
        except gcs_exceptions.PreconditionFailed as exc:
            raise DocumentStoreError(
                f"a document already exists at {key} — refusing to overwrite it"
            ) from exc
        except gcs_exceptions.Forbidden as exc:
            # Distinguished from a generic failure on purpose: this one is
            # fixed by a permission change, not a retry.
            raise DocumentStoreError(
                f"not permitted to write to bucket {settings.gcs_bucket_name}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise DocumentStoreError(f"failed to store document at {key}: {exc}") from exc

        path = f"gs://{settings.gcs_bucket_name}/{key}"
        log.info("stored document at %s (%d bytes)", path, len(content))
        return StoredDocument(path=path, sha256=sha256_hex(content), size=len(content))
