"""Phase 13.4/13.5 through chat and the approval hook.

"ออกรายงาน SR-…" hands back the link; an unapproved report is refused
with the reason; final approval produces the document and puts the link
in the CS reply. The renderer and the store are faked at the seam
`report_issue.issue_for_report` — the document pipeline itself is tested
in test_report_issue.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import approval as approval_service  # noqa: E402
from chann_app.services import report_issue  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402

TECH_KEYS = ["ticket.read", "ticket.update", "service_report.create", "service_report.read"]


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    # The signed document link needs the real token secret; the link's
    # shape is what these tests read, so it is faked at the seam.
    from chann_app.services import chat as chat_module

    monkeypatch.setattr(
        chat_module, "document_download_url",
        lambda license_id, document_id: f"https://app.example/api/v1/documents/{document_id}",
    )


def _tech():
    return _ctx(primary_role="technician", oa="technician")


def _client(status="approved", document_id=None):
    client = FakeDataClient(role="technician", permission_keys=TECH_KEYS)
    client._reports = [{
        "id": "sr-1", "report_id": "SR-2026-0001", "ticket_id": "t1", "technician_member_id": "member-1",
        "status": status, "report_data": {"found_issue": "รั่ว", "work_done": "เปลี่ยน"},
        "generated_document_id": document_id,
    }]
    return client


class TestChat:
    async def test_an_existing_document_is_linked_without_re_rendering(self, monkeypatch):
        called = []
        monkeypatch.setattr(report_issue, "issue_for_report", lambda *a, **k: called.append(1))
        client = _client(document_id="doc-9")
        reply = await handle_chat_message(client, message="ออกรายงาน SR-2026-0001", ctx=_tech())
        assert "https://app.example/api/v1/documents/" in reply.text
        assert not called

    async def test_an_approved_report_without_a_document_gets_one(self, monkeypatch):
        async def fake_issue(client, *, license_id, report_id, actor_id=None, allow_reissue=False):
            return {"id": "doc-1", "sha256": "abc"}

        monkeypatch.setattr(report_issue, "issue_for_report", fake_issue)
        client = _client()
        reply = await handle_chat_message(client, message="ออกรายงาน SR-2026-0001", ctx=_tech())
        assert "SR-2026-0001" in reply.text and "/api/v1/documents/" in reply.text
        assert any(send.startswith("ออกรายงานใหม่") for _l, send in reply.quick_replies)

    async def test_not_approved_says_why(self, monkeypatch):
        async def fake_issue(client, **kw):
            raise report_issue.ReportNotApproved("not yet")

        monkeypatch.setattr(report_issue, "issue_for_report", fake_issue)
        client = _client(status="submitted")
        reply = await handle_chat_message(client, message="ออกรายงาน SR-2026-0001", ctx=_tech())
        assert "ยังไม่ผ่านการอนุมัติ" in reply.text

    async def test_the_only_approved_report_of_mine_needs_no_code(self, monkeypatch):
        client = _client(document_id="doc-9")
        reply = await handle_chat_message(client, message="ขอไฟล์รายงาน", ctx=_tech())
        assert "/api/v1/documents/" in reply.text


class TestApprovalHook:
    async def test_final_approval_produces_the_document(self, monkeypatch):
        produced = []

        async def fake_issue(client, *, license_id, report_id, actor_id=None, allow_reissue=False):
            produced.append(report_id)
            return {"id": "doc-1"}

        monkeypatch.setattr(report_issue, "issue_for_report", fake_issue)
        client = FakeDataClient(permission_keys=["approval.approve"])
        url = await approval_service.issue_report_document(
            client, license_id="lic-1", report={"id": "sr-1", "report_id": "SR-2026-0001"}, actor_id="CHN-1",
        )
        assert produced == ["sr-1"]
        assert url and "/api/v1/documents/" in url

    async def test_a_render_failure_never_undoes_the_approval(self, monkeypatch):
        async def fake_issue(client, **kw):
            raise RuntimeError("SmartBrowz down")

        monkeypatch.setattr(report_issue, "issue_for_report", fake_issue)
        client = FakeDataClient(permission_keys=["approval.approve"])
        url = await approval_service.issue_report_document(
            client, license_id="lic-1", report={"id": "sr-1", "report_id": "SR-2026-0001"}, actor_id="CHN-1",
        )
        assert url is None
