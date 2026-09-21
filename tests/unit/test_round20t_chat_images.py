"""Round 20T — a picture in the conversation.

Owner, 21 ก.ย. 2569: "เพิ่มเติมฟีเจอร์ส่งรูปให้ลูกค้าได้ไหม". The shop sends
a photo from the chat page and it reaches the customer's LINE as a
picture; a customer's photo while they are talking to the shop lands on
the thread instead of "no job to attach it to". One stored object, one
row with the path, the caption in the text column.
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import chat_images, live_chat  # noqa: E402
from chann_app.services.chat import handle_incoming_image  # noqa: E402
from chann_app.services.photos import PhotoRefused  # noqa: E402
from chann_app.services.storage.base import sha256_hex  # noqa: E402
from test_live_chat import LICENSE_ID, ChatFake, _cs_principal, _customer_principal, _harness  # noqa: E402
from test_phase6_chat import _ctx  # noqa: E402


def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    from PIL import Image

    image = Image.new(mode, (width, height), (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(data)).size


class _FakeStore:
    def __init__(self):
        self.puts = []

    async def put(self, *, key, content, content_type):
        from chann_app.services.storage.base import StoredDocument

        self.puts.append((key, content_type, content))
        return StoredDocument(path=f"gs://b/{key}", sha256=sha256_hex(content), size=len(content))


@pytest.fixture
def store(monkeypatch):
    from chann_app.config import settings

    fake = _FakeStore()
    monkeypatch.setattr(chat_images, "get_document_store", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")
    return fake


@pytest.fixture
def pushed(monkeypatch):
    """Every LINE push, text or with pictures, at the one seam they share."""
    from chann_app.services import notify

    sent: list[dict] = []

    async def fake_text(oa, to, text, client=None, quick_reply=None):
        sent.append({"oa": oa, "to": to, "messages": [{"type": "text", "text": text, "quickReply": quick_reply}]})
        return ["mid"]

    async def fake_messages(oa, to, messages, client=None):
        sent.append({"oa": oa, "to": to, "messages": messages})
        return ["mid"]

    monkeypatch.setattr(notify, "push_text", fake_text)
    monkeypatch.setattr(notify, "push_messages", fake_messages)
    return sent


def _live(client: ChatFake, status: str = "open") -> dict:
    row = {
        "id": "cs-1", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
        "customer_name": "สมชาย", "status": status, "assigned_to": None, "sla_deadline": None,
    }
    client._chat_sessions.append(row)
    return row


class TestEveryPictureIsNormalisedFirst:
    def test_a_camera_photo_is_shrunk_to_a_jpeg_under_the_preview_cap(self):
        data, kind = chat_images.normalise_image(_png(3000, 2000), "image/png")
        assert kind == "image/jpeg"
        assert _size(data) == (1600, 1067)
        assert len(data) <= 1024 * 1024

    def test_a_small_picture_keeps_its_size(self):
        data, _ = chat_images.normalise_image(_png(320, 240), "image/png")
        assert _size(data) == (320, 240)

    def test_transparency_lands_on_white_not_black(self):
        from PIL import Image

        data, _ = chat_images.normalise_image(_png(8, 8, "RGBA"), "image/png")
        r, g, b = Image.open(io.BytesIO(data)).convert("RGB").getpixel((4, 4))
        assert r > 200 and g > 100 and b > 100, "half-transparent red on white is pink, not maroon"

    def test_not_a_picture_is_refused_before_anything_is_stored(self):
        with pytest.raises(PhotoRefused, match="not an image"):
            chat_images.normalise_image(b"%PDF-1.4 not a picture", "application/pdf")
        with pytest.raises(PhotoRefused, match="empty"):
            chat_images.normalise_image(b"", "image/png")

    def test_over_ten_megabytes_is_refused_by_size_alone(self):
        with pytest.raises(PhotoRefused, match="10 MB"):
            chat_images.normalise_image(b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")

    async def test_the_object_goes_under_the_conversation(self, store):
        path = await chat_images.store_chat_image(
            license_id="lic-1", session_id="cs-1", content=_png(10, 10), content_type="image/png",
        )
        assert path.startswith("gs://b/documents/lic-1/chats/cs-1/") and path.endswith(".jpg")
        assert store.puts[0][1] == "image/jpeg"

    def test_the_link_lasts_a_year_and_needs_a_base(self, store, monkeypatch):
        from chann_app.auth.document_link import decode_asset_token
        from chann_app.config import settings

        link = chat_images.chat_image_link("gs://b/documents/x/chats/y/a.jpg")
        assert link and link.startswith("https://app.example/api/v1/assets/")
        path, content_type, _name = decode_asset_token(link.rsplit("/", 1)[-1])
        assert path == "gs://b/documents/x/chats/y/a.jpg" and content_type == "image/jpeg"
        monkeypatch.setattr(settings, "public_base_url", "")
        assert chat_images.chat_image_link("gs://b/a.jpg") is None
        assert chat_images.chat_image_link(None) is None


class TestTheShopSendsAPicture:
    async def test_the_customer_gets_the_picture_then_the_words(self, store, pushed):
        client = ChatFake(role="cs", permission_keys=["chat_session.reply"])
        session = _live(client)
        row = await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS",
            member_id="member-cs", text="ตัวนี้ครับ", image_path="gs://b/p.jpg",
            image_url="https://app.example/api/v1/assets/tok",
        )
        assert row["image_path"] == "gs://b/p.jpg" and row["content"] == "ตัวนี้ครับ"
        to_customer = [p for p in pushed if p["to"] == "line-cust"]
        assert len(to_customer) == 1
        messages = to_customer[0]["messages"]
        assert messages[0] == {
            "type": "image", "originalContentUrl": "https://app.example/api/v1/assets/tok",
            "previewImageUrl": "https://app.example/api/v1/assets/tok",
        }
        assert messages[1]["type"] == "text" and "📷 รูปภาพ ตัวนี้ครับ" in messages[1]["text"]
        # The way out still rides on the last message.
        assert messages[1]["quickReply"], "จบการสนทนา must stay reachable"

    async def test_a_picture_with_no_words_still_says_a_picture_came(self, store, pushed):
        client = ChatFake(role="cs", permission_keys=["chat_session.reply"])
        session = _live(client)
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS",
            member_id="member-cs", text="", image_path="gs://b/p.jpg", image_url="https://x/y",
        )
        stored = [r for r in client.recorded if r[0] == "add_chat_message"]
        assert stored and stored[0][3] == ""
        text = [m for m in pushed[-1]["messages"] if m["type"] == "text"][0]["text"]
        assert "📷 รูปภาพ" in text and "📷 รูปภาพ \n" not in text

    async def test_without_a_link_the_words_go_alone(self, store, pushed):
        """No PUBLIC_BASE_URL and no request base: LINE cannot be given a
        gs:// path, so the words say a picture came and the thread keeps it."""
        client = ChatFake(role="cs", permission_keys=["chat_session.reply"])
        session = _live(client)
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS",
            member_id="member-cs", text="ดูรูป", image_path="gs://b/p.jpg", image_url=None,
        )
        assert [m["type"] for m in pushed[-1]["messages"]] == ["text"]

    async def test_into_a_closed_conversation_the_picture_rides_the_invitation(self, store, pushed):
        client = ChatFake(role="cs", permission_keys=["chat_session.reply"])
        session = _live(client, status="closed")
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS",
            member_id="member-cs", text="", image_path="gs://b/p.jpg", image_url="https://x/y",
        )
        messages = pushed[-1]["messages"]
        assert messages[0]["type"] == "image"
        assert "📷 รูปภาพ" in messages[1]["text"] and "คุยกับร้าน" in messages[1]["text"]


class TestTheCustomerSendsAPicture:
    async def test_while_talking_to_the_shop_it_lands_on_the_thread(self, store, pushed, monkeypatch):
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return _png(40, 30), "image/png"

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        client = ChatFake(role="customer", permission_keys=[])
        _live(client)
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="customer", oa="customer"), oa="customer",
            message_id="m1", language="th",
        )
        assert reply.text == "", "no echo — the person is talking to a person"
        assert reply.entity_type == "chat_session" and reply.entity_id == "cs-1"
        assert store.puts[0][0].startswith(f"documents/{LICENSE_ID}/chats/cs-1/")
        stored = [r for r in client.recorded if r[0] == "add_chat_message"]
        assert stored == [("add_chat_message", "cs-1", "customer", "")]
        assert client._chat_messages[-1]["image_path"].startswith("gs://b/documents/")
        assert not any(r[0] == "add_ticket_photo" for r in client.recorded)
        # The agents are told a picture came, in LINE, as the opening line.
        told = [p for p in pushed if p["to"] != "line-cust"]
        assert told and any("📷 รูปภาพ" in m["text"] for p in told for m in p["messages"] if m["type"] == "text")

    async def test_with_no_conversation_open_the_old_road_answers(self, store, monkeypatch):
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return _png(4, 4), "image/png"

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        client = ChatFake(role="customer", permission_keys=[])
        client._tickets = []
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="customer", oa="customer"), oa="customer",
            message_id="m1", language="th",
        )
        assert reply.text and not store.puts
        assert not any(r[0] == "add_chat_message" for r in client.recorded)

    async def test_a_store_that_refuses_is_said_not_swallowed(self, pushed, monkeypatch):
        from chann_app.config import settings
        from chann_app.services import chat as chat_module

        async def fake_download(oa, message_id):
            return _png(4, 4), "image/png"

        class _Refusing:
            async def put(self, **kw):
                raise RuntimeError("bucket says no")

        monkeypatch.setattr(chat_module, "get_message_content", fake_download)
        monkeypatch.setattr(chat_images, "get_document_store", lambda *a, **k: _Refusing())
        monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
        client = ChatFake(role="customer", permission_keys=[])
        _live(client)
        reply = await handle_incoming_image(
            client, ctx=_ctx(primary_role="customer", oa="customer"), oa="customer",
            message_id="m1", language="th",
        )
        assert reply.text, "a failure answers in words"
        assert not any(r[0] == "add_chat_message" for r in client.recorded)


def _data_url(data: bytes, kind: str = "image/png") -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


class TestTheRoute:
    def test_the_shop_posts_a_picture_and_reads_it_back_as_a_link(self, store, pushed):
        http, client = _harness(_cs_principal())
        _live(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/images",
            json={"image": _data_url(_png(2400, 1200)), "caption": "  ราคาตามนี้  "},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["content"] == "ราคาตามนี้"
        assert body["image_url"].startswith("https://") and "/api/v1/assets/" in body["image_url"]
        assert store.puts[0][0].startswith(f"documents/{LICENSE_ID}/chats/cs-1/")
        assert _size(store.puts[0][2]) == (1600, 800)

        listed = http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/messages")
        assert listed.status_code == 200, listed.text
        rows = listed.json()["messages"]
        assert rows[-1]["image_url"] and rows[-1]["image_url"].startswith("https://")
        assert rows[-1]["image_path"].startswith("gs://")

    def test_a_file_that_is_not_a_picture_is_a_422_in_words(self, store):
        http, client = _harness(_cs_principal())
        _live(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/images",
            json={"image": _data_url(b"hello", "text/plain")},
        )
        assert response.status_code == 422 and "not an image" in response.text
        assert not store.puts
        assert not any(r[0] == "add_chat_message" for r in client.recorded)

    def test_reading_without_the_reply_key_may_not_send(self, store):
        http, client = _harness(_cs_principal(keys=("chat_session.view",)))
        _live(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/images",
            json={"image": _data_url(_png(4, 4))},
        )
        assert response.status_code == 403
        assert not store.puts

    def test_a_customer_may_not_picture_into_a_closed_conversation(self, store):
        http, client = _harness(_customer_principal())
        _live(client, status="closed")
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/images",
            json={"image": _data_url(_png(4, 4))},
        )
        assert response.status_code == 409
        assert not store.puts

    def test_a_text_line_still_carries_no_picture(self, store, pushed):
        http, client = _harness(_cs_principal())
        _live(client)
        http.post(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/messages", json={"content": "สวัสดี"})
        rows = http.get(f"/api/v1/licenses/{LICENSE_ID}/chat-sessions/cs-1/messages").json()["messages"]
        assert rows[-1]["image_url"] is None
