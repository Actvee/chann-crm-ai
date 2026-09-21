"""Round 20T — a picture on the thread, on a real database.

The row keeps the stored path; the text column is the caption or empty
(a picture may go without words, words may not); the conversation list
says the newest line is a picture.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


def _customer(session):
    from chann_data.models import ChannIdentity

    row = ChannIdentity(
        chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}", line_user_id=f"U{uuid.uuid4().hex}",
        primary_role="customer", display_name="สมชาย",
    )
    session.add(row)
    session.flush()
    return row


class TestAPictureIsALine:
    def test_stored_listed_and_summarised(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase15 import ChatSessionConflict, ChatSessionRepository
        from chann_data.repositories.tenant_scope import TenantScope
        from chann_data.routers.internal import _chat_sessions_out

        with Session(migrated_db) as session:
            lic, _owner, _member = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            customer = _customer(session)
            repo = ChatSessionRepository(session)
            chat = repo.open_session(scope, customer_chann_uid=customer.chann_uid)[0]
            session.flush()

            # Words may not go without words.
            with pytest.raises(ChatSessionConflict, match="empty"):
                repo.add_message(scope, chat.id, sender_type="agent", content="   ")

            repo.add_message(scope, chat.id, sender_type="customer", content="ดูรูปหน่อย")
            picture = repo.add_message(
                scope, chat.id, sender_type="agent", content="",
                image_path="gs://b/documents/x/chats/y/1.jpg",
            )
            session.flush()
            assert picture.image_path == "gs://b/documents/x/chats/y/1.jpg" and picture.content == ""

            rows = repo.list_messages(scope, chat.id)
            assert [r.image_path for r in rows] == [None, "gs://b/documents/x/chats/y/1.jpg"]

            summary = repo.summaries(scope, [chat.id])[chat.id]
            assert summary["last_message"] is None and summary["last_message_image"] is True
            assert summary["last_sender_type"] == "agent"

            (out,) = _chat_sessions_out(session, scope, [chat])
            assert out.last_message_image is True and out.last_message is None

            # A captioned picture: the caption is the preview, the flag stays.
            repo.add_message(
                scope, chat.id, sender_type="customer", content="ตัวนี้ใช่ไหม",
                image_path="gs://b/documents/x/chats/y/2.jpg",
            )
            session.flush()
            summary = repo.summaries(scope, [chat.id])[chat.id]
            assert summary["last_message"] == "ตัวนี้ใช่ไหม" and summary["last_message_image"] is True
            session.rollback()

    def test_the_migration_adds_exactly_the_column(self):
        path = ROOT / "database" / "alembic" / "versions" / "0034_chat_message_images.py"
        text = path.read_text()
        assert 'op.add_column("chat_messages", sa.Column("image_path", sa.Text(), nullable=True))' in text
        assert 'down_revision = "0033_names_from_identity"' in text
