"""Which of a shop's templates a document is rendered from.

The owner's third report, 9 Sep 2026: nobody chooses. A shop with two
published quote templates got whichever one came first out of the list —
`is_active` was read by the issue path and written by nothing, so the
answer was an ordering accident.

The rule these tests pin down:

  the shop's own template for that document type that is active and has
  a published version compiled to a real stored file; the built-in
  layout when there is none.

with two properties that matter as much as the rule itself:

  * **nothing changes for a shop that already published one.** When more
    than one template is still active — which is every shop that never
    made a choice — the most recently created wins, exactly as "first in
    the list" did, because the Data tier lists templates created_at DESC.
  * **switching off never breaks issuing.** A deactivated template, or one
    whose only published version was archived, falls back to the built-in
    layout rather than failing. `quote_issue._resolve_template` says why:
    a shop must not lose the ability to do business over a template.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.documents.selection import (  # noqa: E402
    TEMPLATE_DOCUMENT_TYPES,
    choosable_templates,
    is_builtin_template,
    resolve_tenant_template,
    usable_version,
)
from chann_app.services.quote_issue import issue_quote_document  # noqa: E402
from chann_app.services.storage.base import StoredDocument, sha256_hex  # noqa: E402

LICENSE_ID = "lic-1"


# ------------------------------------------------------------- doubles

class _Store:
    """Holds template files by their gs:// path, like the real store's
    contract (see test_document_templates_docx._Store for why the fake
    keeps the scheme honest)."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})

    async def put(self, *, key, content, content_type=None):
        self.objects[f"gs://test-bucket/{key}"] = content
        return StoredDocument(
            path=f"gs://test-bucket/{key}", sha256=sha256_hex(content),
            size=len(content),
        )

    async def get(self, *, path):
        from chann_app.services.storage.base import DocumentStoreError

        if path not in self.objects:
            raise DocumentStoreError(f"no stored document at {path}")
        return self.objects[path]


class _Renderer:
    name = "smartbrowz"
    last_html = ""

    async def render(self, html, options, idempotency_key):
        from chann_app.services.pdf.base import PdfResult

        self.last_html = html
        return PdfResult(content=b"%PDF-1.4 fake", url=None, renderer=self.name)


class _Client:
    def __init__(self, templates=None, versions=None):
        self.templates = list(templates or [])
        self.versions = list(versions or [])
        self.recorded: list[dict] = []
        self.published: list[str] = []

    async def list_document_templates(self, license_id, document_type=None):
        return [
            t for t in self.templates
            if document_type is None or t["document_type"] == document_type
        ]

    async def create_document_template(self, license_id, payload, actor_id=None):
        row = {"id": f"tmpl-{len(self.templates) + 1}", "is_active": True, **payload}
        self.templates.append(row)
        return row

    async def list_document_template_versions(self, license_id, template_id):
        return [v for v in self.versions if str(v["template_id"]) == str(template_id)]

    async def create_document_template_version(
        self, license_id, template_id, payload, actor_id=None,
    ):
        row = {
            "id": f"ver-{len(self.versions) + 1}", "template_id": template_id,
            "version": len(self.versions) + 1, "status": "draft", **payload,
        }
        self.versions.append(row)
        return row

    async def publish_document_template_version(self, license_id, version_id, actor_id=None):
        self.published.append(version_id)
        row = next(v for v in self.versions if v["id"] == version_id)
        row["status"] = "published"
        return dict(row)

    async def record_generated_document(self, license_id, payload, actor_id=None):
        row = {"id": f"doc-{len(self.recorded) + 1}", **payload}
        self.recorded.append(row)
        return row

    async def link_quote_document(self, license_id, quote_id, document_id, actor_id=None):
        return {}

    async def transition_quote_status(self, license_id, quote_id, status, actor_id=None):
        return {}


def _template(number: int, *, is_active: bool = True, document_type: str = "quote") -> dict:
    return {
        "id": f"tmpl-{number}", "template_code": f"tenant-{number}",
        "template_name": f"แบบที่ {number}", "document_type": document_type,
        "is_active": is_active,
    }


def _version(number: int, template_number: int, *, status: str = "published") -> dict:
    return {
        "id": f"ver-{number}", "template_id": f"tmpl-{template_number}",
        "version": 1, "status": status,
        "compiled_template_path": f"gs://test-bucket/tmpl-{template_number}.html",
        "source_docx_path": "upload://html",
        "intermediate_model": {"kind": "html_upload"},
    }


def _files(*template_numbers: int) -> dict[str, bytes]:
    """One recognisable file per template, so the rendered HTML says which
    template produced it."""
    return {
        f"gs://test-bucket/tmpl-{n}.html":
            f"<html><body>TEMPLATE-{n} {{{{quote.quote_id}}}}</body></html>".encode()
        for n in template_numbers
    }


def _fixtures():
    return {
        "quote": {"id": "q-1", "quote_id": "Q-2026-0001", "status": "draft", "deal_id": "d-1"},
        "deal": {
            "deal_id": "D-2026-0001", "contact_id": "c-1",
            "products": [{"product_name": "พัดลม", "qty": 1, "quoted_unit_price": "100.00"}],
        },
        "customer": {"first_name": "สมชาย", "last_name": "ใจดี", "address": "1 ถนน"},
        "company": {
            "legal_name": "บริษัท ทดสอบ จำกัด", "company_name": "ร้านทดสอบ",
            "tax_id": "0105558123456", "company_address": "99/1",
            "company_phone": "021234567", "company_email": "a@b.com",
            "vat_rate": "0.07", "missing_for_documents": [],
        },
    }


async def _issue(monkeypatch, client, store):
    import chann_app.services.quote_issue as qi

    renderer = _Renderer()
    monkeypatch.setattr(qi, "get_document_store", lambda *a, **k: store)
    monkeypatch.setattr(qi, "get_renderer", lambda *a, **k: renderer)
    await issue_quote_document(client, license_id=LICENSE_ID, actor_id="CHN-1", **_fixtures())
    return renderer


# --------------------------------------------------------- the rule alone

class TestTheRuleOnItsOwn:
    def test_only_the_two_types_something_actually_renders_are_offered(self):
        """A template slot for a document nothing issues is a file a shop
        would maintain for nothing."""
        assert TEMPLATE_DOCUMENT_TYPES == ("quote", "service_report")

    def test_the_builtin_is_recognised_whatever_its_case(self):
        """The codes are BUILTIN-QUOTE and BUILTIN-SERVICE-REPORT; the
        dashboard's lowercase `startsWith("builtin")` matched neither, so
        the built-in was listed as if a shop could edit it."""
        assert is_builtin_template({"template_code": "BUILTIN-QUOTE"})
        assert is_builtin_template({"template_code": "builtin-quote"})
        assert not is_builtin_template({"template_code": "tenant-abc"})

    def test_an_inactive_template_is_not_a_candidate(self):
        rows = choosable_templates([_template(1, is_active=False), _template(2)])
        assert [t["id"] for t in rows] == ["tmpl-2"]

    def test_list_order_is_the_tie_break(self):
        """Two still-active templates is what every shop that never chose
        looks like. The Data tier orders created_at DESC, so keeping the
        order keeps today's winner."""
        rows = choosable_templates([_template(1), _template(2)])
        assert [t["id"] for t in rows] == ["tmpl-1", "tmpl-2"]

    def test_the_highest_published_version_wins(self):
        versions = [
            dict(_version(1, 1), version=1),
            dict(_version(2, 1), version=2),
            dict(_version(3, 1), version=3, status="draft"),
        ]
        assert usable_version(versions)["id"] == "ver-2"

    def test_a_template_with_no_published_version_has_nothing_usable(self):
        assert usable_version([_version(1, 1, status="draft")]) is None
        assert usable_version([_version(1, 1, status="archived")]) is None
        assert usable_version([]) is None

    def test_a_builtin_compiled_path_is_not_a_file(self):
        version = dict(_version(1, 1), compiled_template_path="builtin://quote/v1")
        assert usable_version([version]) is None


class TestResolvingAgainstTheDataTier:
    async def test_the_active_template_is_returned_with_its_version(self):
        client = _Client(
            templates=[_template(1, is_active=False), _template(2)],
            versions=[_version(1, 1), _version(2, 2)],
        )
        template, version = await resolve_tenant_template(client, LICENSE_ID, "quote")
        assert template["id"] == "tmpl-2"
        assert version["id"] == "ver-2"

    async def test_a_data_tier_failure_falls_back_rather_than_raising(self):
        class _Broken(_Client):
            async def list_document_templates(self, license_id, document_type=None):
                raise RuntimeError("data tier is down")

        assert await resolve_tenant_template(_Broken(), LICENSE_ID, "quote") == (None, None)

    async def test_a_service_report_choice_is_separate_from_a_quote_one(self):
        client = _Client(
            templates=[_template(1), _template(2, document_type="service_report")],
            versions=[_version(1, 1), _version(2, 2)],
        )
        quote_template, _ = await resolve_tenant_template(client, LICENSE_ID, "quote")
        report_template, _ = await resolve_tenant_template(
            client, LICENSE_ID, "service_report",
        )
        assert quote_template["id"] == "tmpl-1"
        assert report_template["id"] == "tmpl-2"


# ------------------------------------------------- through a real issue

class TestIssuingUsesTheChosenTemplate:
    async def test_two_published_templates_and_the_chosen_one_wins(self, monkeypatch):
        """The bug, directly: two published quote templates. The one the
        shop switched on is the one that renders — not the first row."""
        client = _Client(
            templates=[_template(1), _template(2)],
            versions=[_version(1, 1), _version(2, 2)],
        )
        # tmpl-1 is first in the list (newest); the shop chose tmpl-2.
        client.templates[0]["is_active"] = False
        renderer = await _issue(monkeypatch, client, _Store(_files(1, 2)))
        assert "TEMPLATE-2" in renderer.last_html
        assert "TEMPLATE-1" not in renderer.last_html
        assert client.recorded[0]["template_version_id"] == "ver-2"

    async def test_a_shop_that_never_chose_keeps_the_document_it_had(self, monkeypatch):
        """Both still active — every shop that published before this
        change. The first in the Data tier's order is what rendered
        before and what must render now."""
        client = _Client(
            templates=[_template(1), _template(2)],
            versions=[_version(1, 1), _version(2, 2)],
        )
        renderer = await _issue(monkeypatch, client, _Store(_files(1, 2)))
        assert "TEMPLATE-1" in renderer.last_html
        assert client.recorded[0]["template_version_id"] == "ver-1"

    async def test_one_published_template_renders_exactly_as_before(self, monkeypatch):
        """The single-template shop, which is most of them: nothing about
        this change may alter the document they get."""
        client = _Client(templates=[_template(1)], versions=[_version(1, 1)])
        renderer = await _issue(monkeypatch, client, _Store(_files(1)))
        assert "TEMPLATE-1" in renderer.last_html
        assert "Q-2026-0001" in renderer.last_html  # the placeholder was filled
        assert client.recorded[0]["template_version_id"] == "ver-1"

    async def test_deactivating_the_chosen_template_falls_back_to_the_builtin(
        self, monkeypatch,
    ):
        """Documented behaviour, not an accident: switching a template off
        puts the shop back on the standard layout. Failing instead would
        take away their ability to issue a quote."""
        client = _Client(
            templates=[_template(1, is_active=False)], versions=[_version(1, 1)],
        )
        renderer = await _issue(monkeypatch, client, _Store(_files(1)))
        assert "TEMPLATE-1" not in renderer.last_html
        # The built-in registered itself as a real published version row.
        builtin = next(t for t in client.templates if t["template_code"] == "BUILTIN-QUOTE")
        assert builtin is not None
        assert client.recorded[0]["template_version_id"] in client.published

    async def test_archiving_the_only_published_version_falls_back_too(self, monkeypatch):
        client = _Client(
            templates=[_template(1)], versions=[_version(1, 1, status="archived")],
        )
        renderer = await _issue(monkeypatch, client, _Store(_files(1)))
        assert "TEMPLATE-1" not in renderer.last_html
        assert client.recorded[0]["template_version_id"] in client.published

    async def test_no_tenant_template_at_all_uses_the_builtin(self, monkeypatch):
        client = _Client()
        renderer = await _issue(monkeypatch, client, _Store())
        assert "TEMPLATE-" not in renderer.last_html
        assert client.templates[0]["template_code"] == "BUILTIN-QUOTE"
        assert client.recorded[0]["template_version_id"] in client.published

    async def test_a_template_file_that_cannot_be_read_falls_back(self, monkeypatch):
        """The stored file is gone. Their layout is lost for this
        document; their ability to do business is not."""
        client = _Client(templates=[_template(1)], versions=[_version(1, 1)])
        renderer = await _issue(monkeypatch, client, _Store())  # no files at all
        assert "TEMPLATE-1" not in renderer.last_html
        assert client.recorded[0]["template_version_id"] in client.published


class TestTheServiceReportUsesTheSameRule:
    async def test_the_chosen_report_template_renders(self, monkeypatch):
        import chann_app.services.report_issue as ri

        client = _Client(
            templates=[
                _template(1, is_active=False, document_type="service_report"),
                _template(2, document_type="service_report"),
            ],
            versions=[_version(1, 1), _version(2, 2)],
        )
        store = _Store(_files(1, 2))
        snapshot = {"report": {"report_id": "SR-1"}}
        monkeypatch.setattr(ri, "get_document_store", lambda *a, **k: store)
        version_id, html = await ri._resolve_template(client, LICENSE_ID, snapshot)
        assert version_id == "ver-2"
        assert "TEMPLATE-2" in html


@pytest.mark.parametrize("document_type", TEMPLATE_DOCUMENT_TYPES)
def test_every_offered_type_has_a_sample_to_start_from(document_type):
    """A type the page offers must have a starter file and a snapshot to
    check placeholders against, or the upload advises on nothing."""
    from chann_app.services.documents.samples import SAMPLE_DOCUMENT_TYPES, sample_snapshot

    assert document_type in SAMPLE_DOCUMENT_TYPES
    assert sample_snapshot(document_type)
