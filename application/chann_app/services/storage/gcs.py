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
from datetime import timedelta

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

    def _blocking_signed_url(self, *, object_name: str, expires_seconds: int) -> str:
        bucket = _get_client().bucket(settings.gcs_bucket_name)
        return bucket.blob(object_name).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_seconds),
            method="GET",
            # Forces a download with the original filename rather than
            # rendering inline, so a customer who opens the link on a phone
            # gets a file they keep instead of a tab they lose.
            response_disposition=(
                f'attachment; filename="{object_name.rsplit("/", 1)[-1]}"'
            ),
        )

    async def signed_url(self, *, path: str, expires_seconds: int) -> str:
        """A time-limited public link to an object stored under gs://.

        Signed URLs, not public objects: the bucket has
        public_access_prevention enforced, and a quote is commercial
        information that should stop being reachable once it is stale.
        Requires the signing service account to have token-creator rights
        on itself; on Cloud Run with the default compute SA and
        roles/editor this is satisfied, but the failure is surfaced rather
        than swallowed so a misconfiguration is visible immediately.
        """
        if not settings.gcs_bucket_name:
            raise DocumentStoreNotConfigured(
                "document storage is not configured — GCS_BUCKET_NAME is unset"
            )
        prefix = f"gs://{settings.gcs_bucket_name}/"
        if not path.startswith(prefix):
            # Refuses cross-bucket paths outright: a stored path that does
            # not belong to this deployment's bucket is a data problem, and
            # signing it anyway would hand out a link to someone else's
            # object.
            raise DocumentStoreError(
                f"stored path {path!r} does not belong to bucket "
                f"{settings.gcs_bucket_name}"
            )
        object_name = path[len(prefix):]
        try:
            return await asyncio.to_thread(
                self._blocking_signed_url,
                object_name=object_name, expires_seconds=expires_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise DocumentStoreError(f"failed to sign a URL for {path}: {exc}") from exc

    def _blocking_get(self, *, object_name: str) -> bytes:
        bucket = _get_client().bucket(settings.gcs_bucket_name)
        return bucket.blob(object_name).download_as_bytes()

    async def get(self, *, path: str) -> bytes:
        """The stored bytes for a gs:// path.

        Exists because signed URLs need the signing service account to hold
        iam.serviceAccounts.signBlob on itself, which roles/editor does not
        grant and which this project has decided not to add. Serving the
        bytes through an already-authenticated endpoint needs no new IAM at
        all, and keeps the bucket's public_access_prevention intact.
        """
        if not settings.gcs_bucket_name:
            raise DocumentStoreNotConfigured(
                "document storage is not configured — GCS_BUCKET_NAME is unset"
            )
        prefix = f"gs://{settings.gcs_bucket_name}/"
        if not path.startswith(prefix):
            raise DocumentStoreError(
                f"stored path {path!r} does not belong to bucket "
                f"{settings.gcs_bucket_name}"
            )
        try:
            return await asyncio.to_thread(
                self._blocking_get, object_name=path[len(prefix):]
            )
        except gcs_exceptions.NotFound as exc:
            raise DocumentStoreError(f"no stored document at {path}") from exc
        except Exception as exc:  # noqa: BLE001
            raise DocumentStoreError(f"failed to read {path}: {exc}") from exc

    def _blocking_delete(self, *, object_name: str) -> None:
        bucket = _get_client().bucket(settings.gcs_bucket_name)
        bucket.blob(object_name).delete()

    async def delete(self, *, path: str) -> None:
        """Remove an object for good (Phase 16.5 erasure). A path outside
        this deployment's bucket is refused like signed_url does; an
        object already gone is not an error — the goal is absence."""
        if not settings.gcs_bucket_name:
            raise DocumentStoreNotConfigured(
                "document storage is not configured — GCS_BUCKET_NAME is unset"
            )
        prefix = f"gs://{settings.gcs_bucket_name}/"
        if not path.startswith(prefix):
            raise DocumentStoreError(
                f"stored path {path!r} does not belong to bucket {settings.gcs_bucket_name}"
            )
        try:
            await asyncio.to_thread(self._blocking_delete, object_name=path[len(prefix):])
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc) or "Not Found" in str(exc):
                return
            raise DocumentStoreError(f"failed to delete {path}: {exc}") from exc
