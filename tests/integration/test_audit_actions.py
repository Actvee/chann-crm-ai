"""The audit log's allowed verbs, in three places that must agree.

This file exists because they did not, and the consequence was invisible
until a customer-facing document went missing.

An audit entry shares a transaction with the change it records. When the
CHECK constraint rejected "link_document", the rollback took out the row
linking a rendered, stored, recorded PDF to its quote — so the file sat
in GCS, unreachable, while the salesperson was told the quote had not
been issued. Eight verbs were in that state at once, covering most of
Phases 12 and 13.

The failure mode is worth naming: a constraint that guards a WRITE and
shares a transaction with it does not merely reject bad data, it destroys
good work alongside it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.audit_actions import AUDIT_ACTIONS  # noqa: E402


def _constraint_actions() -> set[str]:
    """The verbs the newest migration actually permits."""
    # The newest migration that (re)defines the list wins — 0017 set it,
    # 0023 (PDPA) widened it; each one carries the full ALLOWED tuple.
    candidates = sorted(
        p for p in (ROOT / "database/alembic/versions").glob("*.py")
        if "ALLOWED = (" in p.read_text(encoding="utf-8")
    )
    text = candidates[-1].read_text(encoding="utf-8")
    block = text[text.index("ALLOWED = ("):text.index(")", text.index("ALLOWED = ("))]
    return set(re.findall(r'"([a-z_]+)"', block))


def _actions_written_in_code() -> set[str]:
    """Every action= literal the routers pass to the audit repository."""
    found: set[str] = set()
    for path in (ROOT / "data/chann_data/routers").glob("*.py"):
        found |= set(re.findall(r'action="([a-z_]+)"', path.read_text(encoding="utf-8")))
    return found


class TestAuditVocabulary:
    def test_the_migration_and_the_code_list_agree(self):
        """One list is enforced by Postgres and the other by Python. A verb
        in only one of them fails at flush — inside someone else's
        transaction."""
        assert _constraint_actions() == set(AUDIT_ACTIONS)

    def test_every_action_the_routers_write_is_allowed(self):
        """The check that would have caught this before it shipped.

        Eight verbs were being written that the constraint rejected, and
        nothing anywhere said so until a PDF went missing.
        """
        written = _actions_written_in_code()
        unknown = written - set(AUDIT_ACTIONS)
        assert not unknown, (
            f"these actions are written but not allowed: {sorted(unknown)} — "
            "add them to audit_actions.py AND a migration, or every write "
            "that triggers them will roll back"
        )

    def test_an_unknown_action_is_refused_in_code_not_at_the_database(self):
        """Failing in Python gives a message naming the problem. Failing at
        the constraint gives a CheckViolation the caller reports as an
        opaque 409, three layers from the cause."""
        from chann_data.repositories.audit import AuditRepository

        with pytest.raises(ValueError, match="unknown audit action"):
            AuditRepository(session=None).write(
                entity_type="quote",
                entity_id="00000000-0000-0000-0000-000000000000",
                actor_type="user",
                action="definitely_not_a_verb",
            )


class TestAuditWritesActuallyCommit:
    """The behaviour the constraint mismatch broke, proven end to end."""

    def _license(self, migrated_db):
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        suffix = uuid.uuid4().hex[:6]
        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=f"CHN-AU-{suffix}", line_user_id=f"line-au-{suffix}",
                primary_role="sales",
            ))
            session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name=f"Audit {suffix}", created_by_chann_uid=f"CHN-AU-{suffix}",
            )
            session.commit()
            return lic.id

    @pytest.mark.parametrize("action", sorted(AUDIT_ACTIONS))
    def test_every_allowed_action_survives_a_commit(self, migrated_db, action):
        """Parametrised over the whole vocabulary: a verb that passes the
        Python check but not the database one is exactly the situation this
        file exists to prevent, and only a real commit proves it."""
        import uuid

        from sqlalchemy.orm import Session

        from chann_data.repositories.audit import AuditRepository

        license_id = self._license(migrated_db)
        with Session(migrated_db) as session:
            row = AuditRepository(session).write(
                license_id=license_id,
                entity_type="quote",
                entity_id=uuid.uuid4(),
                actor_type="user",
                action=action,
            )
            session.commit()
            assert row.action == action
