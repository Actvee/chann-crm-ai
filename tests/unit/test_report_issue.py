"""Phase 13.4/13.5 — the service report PDF as a recorded document.

Same seam tests as the quote (test_quote_issue.py): the frozen snapshot
is what gets recorded, storage failure records nothing, the built-in
template registers itself once. Plus the two rules that are the report's
own: nothing renders before approval (the approver's signature is on
it), and an approved report can be produced again only on request.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.documents.report_html import render_service_report_html  # noqa: E402
from chann_app.services.documents.report_snapshot import build_service_report_snapshot  # noqa: E402
from chann_app.services.report_issue import (  # noqa: E402
    BUILTIN_REPORT_TEMPLATE_CODE,
    ReportAlreadyIssued,
    ReportNotApproved,
    issue_for_report,
    issue_service_report_document,
    report_document_key,
)
from chann_app.services.storage.base import sha256_hex  # noqa: E402


class _FakeStore:
    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.puts: list[dict] = []

    async def put(self, *, key, content, content_type):
        if self.fail_with:
            raise self.fail_with
        self.puts.append({"key": key, "content": content, "content_type": content_type})
        from chann_app.services.storage.base import StoredDocument

        return StoredDocument(path=f"gs://bucket/{key}", sha256=sha256_hex(content), size=len(content))

    async def signed_url(self, *, path, expires_seconds):
        return f"https://signed.example/{path}?ttl={expires_seconds}"


class _FakeRenderer:
    name = "smartbrowz"

    def __init__(self, content=b"%PDF-1.4 fake"):
        self.content = content
        self.last_html = ""

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
        self.attached: list[tuple] = []
        self.reports = [{
            "id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t-1",
            "technician_member_id": "m-1", "status": "approved",
            "report_data": {"found_issue": "คอมเพรสเซอร์รั่ว", "work_done": "เปลี่ยนคอมเพรสเซอร์"},
            "generated_document_id": None, "created_at": "2026-09-03T10:00:00",
        }]
        self.steps = [{
            "id": "st-1", "step_order": 1, "status": "approved", "acted_by_member_id": "m-2",
            "approver_ref": "cs", "acted_at": "2026-09-03T11:30:00",
        }]

    async def list_document_templates(self, license_id, document_type=None):
        return [t for t in self.templates if document_type in (None, t.get("document_type"))]

    async def create_document_template(self, license_id, payload, actor_id=None):
        row = {"id": f"tmpl-{len(self.templates) + 1}", **payload}
        self.templates.append(row)
        return row

    async def list_document_template_versions(self, license_id, template_id):
        return [v for v in self.versions if v["template_id"] == template_id]

    async def create_document_template_version(self, license_id, template_id, payload, actor_id=None):
        row = {"id": f"ver-{len(self.versions) + 1}", "template_id": template_id,
               "version": len(self.versions) + 1, **payload}
        self.versions.append(row)
        return row

    async def publish_document_template_version(self, license_id, version_id, actor_id=None):
        self.published.append(version_id)
        return {"id": version_id, "status": "published"}

    async def record_generated_document(self, license_id, payload, actor_id=None):
        row = {"id": f"doc-{len(self.recorded) + 1}", **payload}
        self.recorded.append(row)
        return row

    async def attach_report_document(self, license_id, report_id, *, document_id, pdf_path, actor_id=None):
        self.attached.append((report_id, document_id, pdf_path))
        for r in self.reports:
            if r["id"] == report_id:
                r["generated_document_id"] = document_id
                r["pdf_path"] = pdf_path
        return self.reports[0]

    # ---- what issue_for_report gathers
    async def list_service_reports(self, license_id):
        return [dict(r) for r in self.reports]

    async def get_ticket(self, license_id, ticket_id):
        return {"id": ticket_id, "ticket_number": "T-2026-0001", "customer_name": "สมชาย ใจดี",
                "customer_phone": "0812345678", "service_address": "99/1 ถ.สุขุมวิท",
                "serial_number": "SN12345678", "issue_description": "แอร์ไม่เย็น",
                "scheduled_date": "2026-09-03", "scheduled_time": "10:00:00"}

    async def get_company_profile(self, license_id):
        return {"legal_name": "บริษัท ทดสอบ จำกัด", "company_name": "ร้านทดสอบ",
                "company_address": "1 ถ.ทดสอบ", "company_phone": "021234567", "missing_for_documents": ["tax_id"]}

    async def list_members(self, license_id):
        return [{"id": "m-1", "chann_uid": "CHN-T-000001", "role": "technician"},
                {"id": "m-2", "chann_uid": "CHN-S-000002", "role": "cs"}]

    async def get_profile(self, chann_uid):
        return {"CHN-T-000001": {"first_name": "สมศักดิ์", "last_name": "ช่างดี", "phone": "0899999999"},
                "CHN-S-000002": {"first_name": "สมหญิง", "last_name": "ตรวจดี"}}.get(chann_uid)

    async def identity_signature(self, chann_uid):
        return "signatures/CHN-S-000002.png" if chann_uid == "CHN-S-000002" else None

    async def approval_steps_for_entity(self, license_id, entity_type, entity_id):
        return list(self.steps)

    async def list_ticket_photos(self, license_id, ticket_id):
        return [{"id": "p1", "ticket_id": ticket_id, "photo_url": "documents/lic-1/tickets/t-1/photos/a.jpg",
                 "photo_type": "evidence"}]


def _fixtures(status="approved"):
    return {
        "report": {"id": "sr-1", "report_id": "SR-2026-0001", "status": status,
                   "report_data": {"found_issue": "รั่ว", "work_done": "เปลี่ยน", "parts_changed": "คอม"}},
        "ticket": {"ticket_number": "T-2026-0001", "customer_name": "สมชาย"},
        "company": {"company_name": "ร้านทดสอบ"},
        "technician": {"name": "สมศักดิ์ ช่างดี", "phone": "08"},
        "approvals": [{"name": "สมหญิง", "role": "cs", "acted_at": "2026-09-03 11:30", "signature_url": "https://s/x.png"}],
    }


class TestSnapshotAndHtml:
    def test_the_html_prints_only_the_snapshot(self):
        snap = build_service_report_snapshot(
            issued_at=datetime(2026, 9, 3, tzinfo=timezone.utc), **_fixtures(),
        )
        html = render_service_report_html(snap)
        for needle in ("SR-2026-0001", "T-2026-0001", "สมชาย", "รั่ว", "เปลี่ยน", "คอม", "สมศักดิ์", "สมหญิง", "x.png"):
            assert needle in html, needle
        assert "รายงานการซ่อม" in html and "2026-09-03" in html

    def test_a_shop_without_a_tax_id_still_gets_a_report(self):
        snap = build_service_report_snapshot(**_fixtures())
        assert snap["company"]["tax_id"] == ""
        assert "เลขประจำตัวผู้เสียภาษี" not in render_service_report_html(snap)

    def test_key_is_tenant_prefixed_and_safe(self):
        key = report_document_key(
            license_id="lic-1", report_code="../SR-2026-0001",
            issued_at=datetime(2026, 9, 3, tzinfo=timezone.utc), sha256="a" * 64,
        )
        assert key.startswith("documents/lic-1/service-reports/2026/09/") and ".." not in key


class TestIssue:
    def _patch(self, monkeypatch, store=None, renderer=None):
        import chann_app.services.report_issue as ri

        store = store or _FakeStore()
        renderer = renderer or _FakeRenderer()
        monkeypatch.setattr(ri, "get_document_store", lambda *a, **k: store)
        monkeypatch.setattr(ri, "get_renderer", lambda *a, **k: renderer)
        return store, renderer

    async def test_records_the_snapshot_and_links_the_report(self, monkeypatch):
        store, renderer = self._patch(monkeypatch)
        client = _FakeClient()
        document = await issue_service_report_document(client, license_id="lic-1", actor_id="CHN-1", **_fixtures())
        assert store.puts and document["document_type"] == "service_report"
        assert document["data_snapshot"]["report"]["report_id"] == "SR-2026-0001"
        assert client.attached == [("sr-1", "doc-1", "gs://bucket/" + store.puts[0]["key"])]
        assert any(t["template_code"] == BUILTIN_REPORT_TEMPLATE_CODE for t in client.templates)
        assert len(client.published) == 1

    async def test_nothing_before_approval(self, monkeypatch):
        self._patch(monkeypatch)
        client = _FakeClient()
        with pytest.raises(ReportNotApproved):
            await issue_service_report_document(client, license_id="lic-1", **_fixtures(status="submitted"))
        assert not client.recorded

    async def test_a_second_document_must_be_asked_for(self, monkeypatch):
        self._patch(monkeypatch)
        client = _FakeClient()
        fx = _fixtures()
        fx["report"]["generated_document_id"] = "doc-0"
        with pytest.raises(ReportAlreadyIssued):
            await issue_service_report_document(client, license_id="lic-1", **fx)
        document = await issue_service_report_document(client, license_id="lic-1", allow_reissue=True, **fx)
        assert document["id"] == "doc-1"

    async def test_storage_failure_records_nothing(self, monkeypatch):
        self._patch(monkeypatch, store=_FakeStore(fail_with=RuntimeError("bucket down")))
        client = _FakeClient()
        with pytest.raises(RuntimeError):
            await issue_service_report_document(client, license_id="lic-1", **_fixtures())
        assert not client.recorded and not client.attached

    async def test_issue_for_report_gathers_technician_and_signed_approver(self, monkeypatch):
        store, renderer = self._patch(monkeypatch)
        client = _FakeClient()
        document = await issue_for_report(client, license_id="lic-1", report_id="sr-1", actor_id="CHN-1")
        snap = document["data_snapshot"]
        assert snap["technician"]["name"] == "สมศักดิ์ ช่างดี"
        assert snap["approvals"][0]["name"] == "สมหญิง ตรวจดี"
        assert snap["approvals"][0]["signature_url"].startswith("https://signed.example/signatures/")
        assert "สมหญิง ตรวจดี" in renderer.last_html
        assert client.reports[0]["generated_document_id"] == "doc-1"
        # 13.1: the visit's pictures are on the paper, via a signed link.
        assert snap["photos"] and snap["photos"][0].startswith("https://signed.example/documents/")
        assert 'class="photo"' in renderer.last_html
