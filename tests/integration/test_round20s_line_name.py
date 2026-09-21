"""Round 20S — the LINE display name lands on the identity and the chat
page uses it when the shop has no record and no registered name."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_database_from_empty import _phase2_tenant  # noqa: E402


class TestTheDisplayNameLands:
    def test_set_and_read_back_through_the_chat_page(self, migrated_db):
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.tenant_scope import IdentityRepository, MemberNotFound, TenantScope
        from chann_data.routers.internal import _chat_sessions_out

        with Session(migrated_db) as session:
            lic, _o, _m = _phase2_tenant(session)
            scope = TenantScope(lic.id)
            repo = IdentityRepository(session)
            person = repo.create(
                chann_uid=f"CHN-C-{uuid.uuid4().hex[:8]}", line_user_id=f"U{uuid.uuid4().hex}",
                primary_role="customer",
            )
            assert person.display_name is None
            chat = ChatSessionRepository(session).open_session(scope, customer_chann_uid=person.chann_uid)[0]
            session.flush()
            (before,) = _chat_sessions_out(session, scope, [chat])
            assert before.customer_name is None, "nothing known: the page falls back to the id"

            repo.set_display_name(person.chann_uid, "  สมชาย 🐱  ")
            session.flush()
            (after,) = _chat_sessions_out(session, scope, [chat])
            assert after.customer_name == "สมชาย 🐱"

            with pytest.raises(MemberNotFound):
                repo.set_display_name("CHN-C-nobody", "x")
            session.rollback()
