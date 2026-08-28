"""Phase 10 — storing and recording an issued quote document.

The GCS client itself is not exercised here (that needs real credentials
and a real bucket, and is proven by the deployed environment, the same way
SmartBrowz connectivity was). What IS tested is everything around it: the
seam's refusal behaviour, the object key, and the order of operations that
decides whether a bad outcome leaves an orphan or a lie.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.quote_issue import (  # noqa: E402
    BUILTIN_QUOTE_TEMPLATE_CODE,
    document_key,
    issue_quote_document,
)
from chann_app.services.storage.base import (  # noqa: E402
    DocumentStoreNotConfigured,
    NullDocumentStore,
    sha256_hex,
)


class TestDocumentKey:
    def test_key_is_tenant_prefixed_and_date_partitioned(self):
        key = document_key(
            license_id="lic-1", quote_code="Q-2026-0001",
            issued_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            sha256="abcdef0123456789" * 4,
        )
        assert key.startswith("documents/lic-1/quotes/2026/08/")
        assert key.endswith(".pdf")

    def test_key_includes_the_content_digest(self):
        """Re-issuing after a real change must land on a new key rather than
        colliding with the create-only upload guard."""
        common = dict(
            license_id="lic-1", quote_code="Q-2026-0001",
            issued_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        a = document_key(**common, sha256="a" * 64)
        b = document_key(**common, sha256="b" * 64)
        assert a != b

    def test_unsafe_characters_in_a_quote_code_cannot_escape_the_prefix(self):
        key = document_key(
            license_id="lic-1", quote_code="../../etc/passwd",
            issued_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            sha256="c" * 64,
        )
        assert ".." not in key
        assert key.startswith("documents/lic-1/quotes/")


class TestNullStore:
    async def test_refuses_loudly_rather_than_silently_skipping(self):
        """The whole point of the null store: an unconfigured environment
        must fail visibly, never let a generated_documents row be written
        with nowhere to point."""
        with pytest.raises(DocumentStoreNotConfigured):
            await NullDocumentStore().put(
                key="k", content=b"x", content_type="application/pdf"
            )


class _FakeStore:
    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.puts: list[dict] = []

    async def put(self, *, key, content, content_type):
        if self.fail_with:
            raise self.fail_with
        self.puts.append({"key": key, "content": content, "content_type": content_type})
        from chann_app.services.storage.base import StoredDocument

        return StoredDocument(
            path=f"gs://bucket/{key}", sha256=sha256_hex(content), size=len(content)
        )


class _FakeRenderer:
    name = "smartbrowz"

    def __init__(self, content=b"%PDF-1.4 fake"):
        self.content = content

    async def render(self, html, options, idempotency_key):
        from chann_app.services.pdf.base import PdfResult

        self.last_html = html
        return PdfResult(content=self.content, url=None, renderer=self.name)


class _FakeClient:
    def __init__(self):
        self.templates: list[dict] = []
        self.versions: list[dict] = []
        self.recorded: list[dict] = []
        self.published: list[str] = []

    async def list_document_templates(self, license_id, document_type=None):
        return list(self.templates)

    async def create_document_template(self, license_id, payload, actor_id=None):
        row = {"id": f"tmpl-{len(self.templates) + 1}", **payload}
        self.templates.append(row)
        return row

    async def list_document_template_versions(self, license_id, template_id):
        return [v for v in self.versions if v["template_id"] == template_id]

    async def create_document_template_version(
        self, license_id, template_id, payload, actor_id=None
    ):
        row = {
            "id": f"ver-{len(self.versions) + 1}",
            "template_id": template_id,
            "version": len(self.versions) + 1,
            **payload,
        }
        self.versions.append(row)
        return row

    async def publish_document_template_version(self, license_id, version_id, actor_id=None):
        self.published.append(version_id)
        return {"id": version_id, "status": "published"}

    async def record_generated_document(self, license_id, payload, actor_id=None):
        row = {"id": f"doc-{len(self.recorded) + 1}", **payload}
        self.recorded.append(row)
        return row


def _fixtures():
    return {
        "quote": {"id": "q-1", "quote_id": "Q-2026-0001", "status": "draft", "deal_id": "d-1"},
        "deal": {
            "deal_id": "D-2026-0001", "contact_id": "c-1",
            "products": [
                {"product_name": "พัดลม", "qty": 2, "quoted_unit_price": "100.00"},
            ],
        },
        "customer": {"first_name": "สมชาย", "last_name": "ใจดี", "address": "1 ถนน"},
        "company": {
            "legal_name": "บริษัท ทดสอบ จำกัด", "company_name": "ร้านทดสอบ",
            "tax_id": "0105558123456", "company_address": "99/1",
            "company_phone": "021234567", "company_email": "a@b.com",
            "vat_rate": "0.07", "missing_for_documents": [],
        },
    }


class TestIssueQuoteDocument:
    async def _issue(self, monkeypatch, *, store=None, renderer=None):
        import chann_app.services.quote_issue as qi

        store = store or _FakeStore()
        renderer = renderer or _FakeRenderer()
        monkeypatch.setattr(qi, "get_document_store", lambda *a, **k: store)
        monkeypatch.setattr(qi, "get_renderer", lambda *a, **k: renderer)
        client = _FakeClient()
        document = await issue_quote_document(
            client, license_id="lic-1", actor_id="CHN-1", **_fixtures()
        )
        return client, store, renderer, document

    async def test_records_the_snapshot_digest_and_stored_path(self, monkeypatch):
        client, store, _, document = await self._issue(monkeypatch)
        assert len(client.recorded) == 1
        recorded = client.recorded[0]
        assert recorded["output_path"] == store.puts[0]["key"].join(
            ["gs://bucket/", ""]
        ) or recorded["output_path"].startswith("gs://bucket/")
        assert recorded["sha256"] == sha256_hex(b"%PDF-1.4 fake")
        assert recorded["source_entity_type"] == "quote"
        assert recorded["source_entity_id"] == "q-1"
        assert document["id"] == "doc-1"

    async def test_the_frozen_snapshot_is_what_gets_recorded(self, monkeypatch):
        """Not a reference to live rows: the recorded snapshot has to carry
        the numbers as they were, so the document can be reproduced after a
        price or VAT-rate change."""
        client, _, _, _ = await self._issue(monkeypatch)
        snapshot = client.recorded[0]["data_snapshot"]
        assert snapshot["totals"]["subtotal"] == "200.00"
        assert snapshot["totals"]["vat_rate"] == "0.07"
        assert snapshot["company"]["tax_id"] == "0105558123456"

    async def test_the_builtin_template_is_registered_and_published_once(self, monkeypatch):
        """template_version_id is NOT NULL for a reason — which template
        produced a document must always be answerable — so the built-in
        template becomes a real, published version row rather than a
        special case carved out of the schema."""
        client, _, _, _ = await self._issue(monkeypatch)
        assert client.templates[0]["template_code"] == BUILTIN_QUOTE_TEMPLATE_CODE
        assert len(client.versions) == 1
        assert client.published == [client.versions[0]["id"]]
        assert client.recorded[0]["template_version_id"] == client.versions[0]["id"]

    async def test_nothing_is_recorded_when_storage_fails(self, monkeypatch):
        """Store first, record second. An orphaned object is findable and
        harmless; a row pointing at nothing is a lie that looks
        authoritative forever."""
        from chann_app.services.storage.base import DocumentStoreError

        import chann_app.services.quote_issue as qi

        store = _FakeStore(fail_with=DocumentStoreError("bucket exploded"))
        monkeypatch.setattr(qi, "get_document_store", lambda *a, **k: store)
        monkeypatch.setattr(qi, "get_renderer", lambda *a, **k: _FakeRenderer())
        client = _FakeClient()
        with pytest.raises(DocumentStoreError):
            await issue_quote_document(client, license_id="lic-1", **_fixtures())
        assert client.recorded == []

    async def test_an_empty_render_is_refused_before_anything_is_stored(self, monkeypatch):
        import chann_app.services.quote_issue as qi

        store = _FakeStore()
        monkeypatch.setattr(qi, "get_document_store", lambda *a, **k: store)
        monkeypatch.setattr(qi, "get_renderer", lambda *a, **k: _FakeRenderer(content=b""))
        client = _FakeClient()
        with pytest.raises(RuntimeError):
            await issue_quote_document(client, license_id="lic-1", **_fixtures())
        assert store.puts == []
        assert client.recorded == []

    async def test_an_incomplete_company_refuses_before_rendering(self, monkeypatch):
        from chann_app.services.documents.snapshot import QuoteNotRenderable

        import chann_app.services.quote_issue as qi

        store, renderer = _FakeStore(), _FakeRenderer()
        monkeypatch.setattr(qi, "get_document_store", lambda *a, **k: store)
        monkeypatch.setattr(qi, "get_renderer", lambda *a, **k: renderer)
        fixtures = _fixtures()
        fixtures["company"]["missing_for_documents"] = ["tax_id"]
        with pytest.raises(QuoteNotRenderable):
            await issue_quote_document(_FakeClient(), license_id="lic-1", **fixtures)
        assert store.puts == []
