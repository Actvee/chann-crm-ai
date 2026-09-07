"""Phase 13.1 photos and 13.5 signatures — the storage seam and the chat
path (a picture sent in LINE lands on the job the technician is on).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import photos  # noqa: E402
from chann_app.services.chat import handle_incoming_image  # noqa: E402
from chann_app.services.storage.base import sha256_hex  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _FakeStore:
    def __init__(self):
        self.puts = []

    async def put(self, *, key, content, content_type):
        from chann_app.services.storage.base import StoredDocument

        self.puts.append((key, content_type))
        return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content), size=len(content))



@pytest.fixture
def store(monkeypatch):
    from chann_app.config import settings

    fake = _FakeStore()
    monkeypatch.setattr(photos, "get_document_store", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    return fake


class TestStore:
    async def test_a_photo_is_stored_under_the_ticket_then_recorded(self, store):
        client = FakeDataClient(role="technician", permission_keys=["ticket.update"])
        row = await photos.store_ticket_photo(
            client, license_id="lic-1", ticket_id="t1", content=PNG, content_type="image/png",
            photo_type="checkin", uploaded_by_member_id="member-1",
        )
        assert store.puts and store.puts[0][0].startswith("documents/lic-1/tickets/t1/photos/")
        assert store.puts[0][0].endswith(".png")
        recorded = [r for r in client.recorded if r[0] == "add_ticket_photo"]
        assert recorded and recorded[0][3]["photo_type"] == "checkin"
        assert row["photo_url"].startswith("gs://")

    async def test_not_an_image_is_refused_before_storing(self, store):
        client = FakeDataClient()
        with pytest.raises(photos.PhotoRefused):
            await photos.store_ticket_photo(
                client, license_id="lic-1", ticket_id="t1", content=b"hello", content_type="text/plain",
            )
        assert not store.puts

    async def test_links_are_asset_tokens_served_by_this_tier(self, store):
        from chann_app.auth.document_link import decode_asset_token

        client = FakeDataClient()
        client._photos = [{"id": "p1", "ticket_id": "t1", "photo_url": "gs://b/documents/x.jpg", "photo_type": "evidence"}]
        rows = await photos.photo_links(client, license_id="lic-1", ticket_id="t1")
        assert rows[0]["url"].startswith("https://app.example/api/v1/assets/")
        path, content_type, _ = decode_asset_token(rows[0]["url"].rsplit("/", 1)[-1])
        assert path == "gs://b/documents/x.jpg" and content_type == "image/jpeg"

    async def test_a_request_base_wins_over_the_setting(self, store):
        client = FakeDataClient()
        client._photos = [{"id": "p1", "ticket_id": "t1", "photo_url": "gs://b/documents/x.jpg", "photo_type": "evidence"}]
        rows = await photos.photo_links(client, license_id="lic-1", ticket_id="t1", base_url="https://req.example/")
        assert rows[0]["url"].startswith("https://req.example/api/v1/assets/")

    async def test_no_base_url_means_no_link_not_a_broken_one(self, store, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "public_base_url", "")
        client = FakeDataClient()
        client._photos = [{"id": "p1", "ticket_id": "t1", "photo_url": "gs://b/documents/x.jpg", "photo_type": "evidence"}]
        rows = await photos.photo_links(client, license_id="lic-1", ticket_id="t1")
        assert rows[0]["url"] == ""

    async def test_a_signature_link_is_an_asset_token(self, store):
        client = FakeDataClient()
        client._signatures = {"CHN-S-000001": "gs://b/signatures/CHN-S-000001/x.png"}
        url = await photos.signature_link(client, chann_uid="CHN-S-000001")
        assert url and url.startswith("https://app.example/api/v1/assets/")

    async def test_a_signature_is_kept_against_the_identity(self, store):
        client = FakeDataClient()
        path = await photos.store_signature(client, chann_uid="CHN-S-000001", content=PNG)
        assert path.startswith("gs://b/signatures/CHN-S-000001/")
        assert ("set_identity_signature", "CHN-S-000001", path) in client.recorded


class TestPictureInChat:
    @pytest.fixture(autouse=True)
    def _ai(self, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
        monkeypatch.setattr(settings, "openrouter_model", "test-model")

    async def test_a_technicians_picture_lands_on_the_job_in_progress(self, store, monkeypatch):
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return PNG, "image/png"

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        client._tickets = [{"id": "t1", "ticket_number": "T-2026-0001", "status": "in_progress",
                            "accept_status": "accepted", "assigned_to_ref": "member-1"}]
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="technician", oa="technician"), oa="technician",
            message_id="m1", language="th",
        )
        assert "T-2026-0001" in reply.text
        assert any(r[0] == "add_ticket_photo" for r in client.recorded)

    async def test_with_no_current_job_the_picture_is_not_stored(self, store, monkeypatch):
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return PNG, "image/png"

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        client._tickets = []
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="technician", oa="technician"), oa="technician",
            message_id="m1", language="th",
        )
        assert not any(r[0] == "add_ticket_photo" for r in client.recorded)
        assert "ยังไม่มีงาน" in reply.text or "งาน" in reply.text

    async def test_a_customers_picture_goes_on_their_open_repair(self, store, monkeypatch):
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return PNG, "image/jpeg"

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        client = FakeDataClient(role="customer", permission_keys=[])
        client._tickets = [{"id": "t9", "ticket_number": "T-2026-0009", "status": "open",
                            "accept_status": "pending", "customer_chann_uid": "CHN-S-000001"}]
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="customer", oa="customer"), oa="customer",
            message_id="m2", language="th",
        )
        assert "T-2026-0009" in reply.text
        recorded = [r for r in client.recorded if r[0] == "add_ticket_photo"]
        assert recorded and recorded[0][2] == "t9"
