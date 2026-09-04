"""Phase 16.5 — PDPA on the Application side: consent before any
registration step, "ขอข้อมูลของฉัน" as a page the person can keep,
"ขอลบข้อมูล" confirmed then anonymised everywhere with the pictures gone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import pdpa  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from chann_app.services.registration import handle_registration  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402
from test_phase65_registration import FakeRegClient  # noqa: E402
from test_phase65_registration import _ctx as _reg_ctx  # noqa: E402


class PdpaFake(FakeDataClient):
    def __init__(self, *, consented=True, **kwargs):
        super().__init__(**kwargs)
        self._consent = {"chann_uid": "CHN-S-000001", "consent_accepted_at": "2026-09-01T00:00:00+00:00" if consented else None,
                         "consent_version": "2026-09-04" if consented else None, "anonymized_at": None}
        self._pdpa_result = {
            "request_type": "export",
            "bundle": {"request_id": "r1", "exported_at": "2026-09-04T10:00:00+00:00",
                       "identity": {"chann_uid": "CHN-S-000001", "display_name": "สมชาย", "first_name": "สมชาย", "phone": "0812345678"},
                       "companies": [{"license_id": "L1", "company_name": "ร้านเย็นสบาย", "roles": [], "customer": {"customer_id": "C-1", "first_name": "สมชาย"},
                                      "tickets": [{"ticket_number": "T-2026-0001", "status": "completed"}], "warranties": [], "deals": [], "chat_messages": []}]},
        }
        self._erase_result = {"request_type": "erasure", "tenants": 2, "customers": 2, "tickets": 3, "photos": 1, "chat_messages": 4,
                              "storage_paths": ["gs://b/documents/x/photo.jpg", "gs://b/signatures/CHN-S-000001/s.png"], "request_id": "r2"}
        self._shops = [{"license_id": "L1", "company_name": "ร้านเย็นสบาย", "company_code": "ABC123"}]

    async def get_consent(self, chann_uid):
        return dict(self._consent)

    async def put_consent(self, chann_uid, version):
        self.recorded.append(("put_consent", chann_uid, version))
        self._consent.update({"consent_accepted_at": "2026-09-04T10:00:00+00:00", "consent_version": version})
        return dict(self._consent)

    async def create_pdpa_request(self, *, chann_uid, request_type, requested_via):
        self.recorded.append(("create_pdpa_request", chann_uid, request_type, requested_via))
        return {"id": "r-" + request_type, "chann_uid": chann_uid, "request_type": request_type, "status": "pending"}

    async def process_pdpa_request(self, request_id, processed_by=None):
        self.recorded.append(("process_pdpa_request", request_id))
        return dict(self._erase_result if request_id == "r-erasure" else self._pdpa_result)


class _Store:
    def __init__(self, configured=True):
        self.configured = configured
        self.puts: list[str] = []
        self.deleted: list[str] = []

    async def put(self, *, key, content, content_type):
        from chann_app.services.storage.base import DocumentStoreNotConfigured, StoredDocument, sha256_hex

        if not self.configured:
            raise DocumentStoreNotConfigured("no bucket")
        self.puts.append(key)
        return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content), size=len(content))

    async def signed_url(self, *, path, expires_seconds):
        return f"https://signed/{path}?ttl={expires_seconds}"

    async def delete(self, *, path):
        self.deleted.append(path)


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    from chann_app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "test-model")


def _unregistered(oa="customer"):
    return _reg_ctx(oa=oa, primary_role=oa)


def _reg(consented):
    client = FakeRegClient(
        shops=[{"license_id": "lic-1", "company_code": "DEV001", "company_name": "Dev Company"}],
        link={"company_name": "Dev Company", "company_code": "DEV001", "license_id": "lic-1"},
    )
    client.consented = consented
    return client


class TestConsentAtRegistration:
    async def test_the_first_step_asks_for_consent_and_holds_the_message(self):
        client = _reg(consented=False)
        reply = await handle_registration(client, message="ร้าน dev company", ctx=_unregistered(), audience="customer")
        text = reply if isinstance(reply, str) else reply.text
        assert "PDPA" in text and "ยอมรับ" in text
        assert "link_customer" not in client.calls
        assert client.pending and client.pending["entity"] == "consent" and client.pending["fields"]["message"] == "ร้าน dev company"

    async def test_accepting_records_consent_and_continues_with_what_was_held(self):
        client = _reg(consented=False)
        await handle_registration(client, message="ร้าน dev company", ctx=_unregistered(), audience="customer")
        reply = await handle_registration(client, message="ยอมรับ", ctx=_unregistered(), audience="customer")
        text = reply if isinstance(reply, str) else reply.text
        assert "put_consent" in client.calls
        assert "บันทึกความยินยอมแล้ว" in text and "Dev Company" in text
        assert "link_customer" in client.calls, "the held shop name was not used"

    async def test_declining_stops_registration(self):
        client = _reg(consented=False)
        await handle_registration(client, message="ร้าน dev company", ctx=_unregistered(), audience="customer")
        reply = await handle_registration(client, message="ไม่ยอมรับ", ctx=_unregistered(), audience="customer")
        text = reply if isinstance(reply, str) else reply.text
        assert "ยังลงทะเบียนให้ไม่ได้" in text
        assert "link_customer" not in client.calls and "put_consent" not in client.calls
        assert client.pending is None

    async def test_a_consented_person_is_not_asked_again(self):
        client = _reg(consented=True)
        await handle_registration(client, message="ร้าน dev company", ctx=_unregistered(), audience="customer")
        assert "link_customer" in client.calls and "put_consent" not in client.calls

    async def test_staff_registration_is_gated_too(self):
        client = _reg(consented=False)
        reply = await handle_registration(client, message="สร้างบริษัท ร้านใหม่", ctx=_unregistered("sales"), audience="sales")
        assert "PDPA" in (reply if isinstance(reply, str) else reply.text)
        assert "create_license" not in client.calls


class TestExport:
    async def test_a_copy_becomes_a_page_with_a_day_long_link(self, monkeypatch):
        store = _Store()
        monkeypatch.setattr(pdpa, "get_document_store", lambda *a, **k: store)
        client = PdpaFake()
        out = await pdpa.export_my_data(client, chann_uid="CHN-S-000001", via="chat", language="th")
        assert out["url"].startswith("https://signed/gs://b/pdpa/CHN-S-000001/")
        assert "ttl=86400" in out["url"] and "1 ร้าน" in out["text"]
        assert ("create_pdpa_request", "CHN-S-000001", "export", "chat") in client.recorded
        html = pdpa._render_export_html(out["bundle"])
        assert "ร้านเย็นสบาย" in html and "T-2026-0001" in html and "0812345678" in html

    async def test_without_storage_the_summary_is_inline(self, monkeypatch):
        monkeypatch.setattr(pdpa, "get_document_store", lambda *a, **k: _Store(configured=False))
        client = PdpaFake()
        out = await pdpa.export_my_data(client, chann_uid="CHN-S-000001", via="liff", language="th")
        assert out["url"] is None and "ร้านเย็นสบาย" in out["text"]

    async def test_from_chat_on_any_oa(self, monkeypatch):
        monkeypatch.setattr(pdpa, "get_document_store", lambda *a, **k: _Store(configured=False))
        for oa in ("customer", "technician", "sales"):
            client = PdpaFake(permission_keys=[])
            reply = await handle_chat_message(client, message="ขอข้อมูลของฉัน", ctx=_ctx(primary_role=oa, oa=oa))
            assert "สำเนาข้อมูล" in reply.text, oa


class TestErasure:
    async def test_needs_a_confirmation_then_deletes_the_pictures(self, monkeypatch):
        store = _Store()
        monkeypatch.setattr(pdpa, "get_document_store", lambda *a, **k: store)
        client = PdpaFake(permission_keys=[])
        first = await handle_chat_message(client, message="ขอลบข้อมูล", ctx=_ctx(primary_role="customer", oa="customer"))
        assert "ยืนยันลบข้อมูล" in first.text
        assert not [r for r in client.recorded if r[0] == "create_pdpa_request"]
        done = await handle_chat_message(client, message="ยืนยันลบข้อมูล", ctx=_ctx(primary_role="customer", oa="customer"))
        assert ("create_pdpa_request", "CHN-S-000001", "erasure", "chat") in client.recorded
        assert "2 ร้าน" in done.text
        assert store.deleted == ["gs://b/documents/x/photo.jpg", "gs://b/signatures/CHN-S-000001/s.png"]

    async def test_a_confirmation_out_of_the_blue_does_nothing(self):
        client = PdpaFake(permission_keys=[])
        reply = await handle_chat_message(client, message="ยืนยันลบข้อมูล", ctx=_ctx(primary_role="customer", oa="customer"))
        assert not [r for r in client.recorded if r[0] == "create_pdpa_request"]
        assert "ขอลบข้อมูล" in reply.text
