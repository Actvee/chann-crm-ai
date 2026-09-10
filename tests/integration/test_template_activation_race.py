"""Review v3, S01: two shops' clicks cannot both win.

`set_template_active` reads the active siblings and then writes them one
at a time. Nothing held the read and the writes together, so two
activations of DIFFERENT templates of the same document type, arriving at
once, could each read "nothing active here" and each write its own row
true — leaving two active templates for one (license, document_type) and
therefore two possible answers to "which layout does a quote print with".

The review asked for this to be proven on real PostgreSQL before anyone
claimed a reproduction, so it is: real threads, real sessions, real
transactions. Without the lock this file's assertion fails on most runs
(7 of 8 measured); with it, the second activation waits, re-reads, and
turns the first off — one active, every time.

What is deliberately NOT claimed: this serialises the path that CHOOSES.
`is_active` still defaults to true when a template is created, so a shop
can have several active without anyone ever choosing — the pre-existing
state `documents/selection.py` resolves by taking the most recently
created one. Zero active stays legal, and the built-in layout is still
what renders when nothing is chosen; both are asserted below.
"""
from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase10 import DocumentTemplateRepository  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402

# Enough runs that a race would have to hide from all of them. Without the
# advisory lock this loop fails on the first or second iteration.
RUNS = 5


@pytest.fixture
def two_templates(migrated_db):
    """A tenant with two quote templates, neither in use yet."""
    from sqlalchemy.orm import Session

    from chann_data.models import DocumentTemplate, License

    def _make():
        suffix = uuid.uuid4().hex[:8]
        license_id = uuid.uuid4()
        ids = (uuid.uuid4(), uuid.uuid4())
        with Session(migrated_db) as session:
            session.add(License(
                id=license_id, license_code=f"S01-{suffix}"[:32],
                company_name=f"S01 {suffix}",
            ))
            session.flush()
            for template_id, code in zip(ids, ("a", "b")):
                session.add(DocumentTemplate(
                    id=template_id, license_id=license_id, document_type="quote",
                    template_code=f"{code}-{suffix}", template_name=code,
                    is_active=False,
                ))
            session.commit()
        return TenantScope(license_id=license_id), license_id, ids

    return _make


def _active_count(engine, license_id, document_type="quote") -> int:
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        return session.execute(
            text(
                "SELECT count(*) FROM document_templates "
                "WHERE license_id = :l AND document_type = :t AND is_active"
            ),
            {"l": license_id, "t": document_type},
        ).scalar_one()


class TestTwoActivationsAtOnce:
    def test_only_one_template_of_a_type_is_left_active(self, migrated_db, two_templates):
        from sqlalchemy.orm import Session

        for _ in range(RUNS):
            scope, license_id, (first, second) = two_templates()
            start = threading.Barrier(2)
            failures: list[str] = []

            def activate(template_id):
                try:
                    with Session(migrated_db) as session:
                        # Both threads reach the repository call together,
                        # which is what the single-writer unit suite can
                        # never arrange.
                        start.wait(timeout=10)
                        DocumentTemplateRepository(session).set_template_active(
                            scope, template_id, active=True,
                        )
                        session.commit()
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{type(exc).__name__}: {exc}")

            threads = [
                threading.Thread(target=activate, args=(template_id,))
                for template_id in (first, second)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert failures == [], failures
            assert _active_count(migrated_db, license_id) == 1, (
                "two templates of the same document type ended up active"
            )

    def test_the_loser_is_the_one_that_committed_first(self, migrated_db, two_templates):
        """Serialised, not refused. Choosing has to work: the person who
        clicked second gets what they clicked, not an error."""
        from sqlalchemy.orm import Session

        scope, license_id, (first, second) = two_templates()
        with Session(migrated_db) as session:
            DocumentTemplateRepository(session).set_template_active(scope, first, active=True)
            session.commit()
        with Session(migrated_db) as session:
            _template, turned_off = DocumentTemplateRepository(session).set_template_active(
                scope, second, active=True,
            )
            # Read before the commit detaches them from the session.
            turned_off_ids = [t.id for t in turned_off]
            session.commit()
        assert turned_off_ids == [first]
        assert _active_count(migrated_db, license_id) == 1

    def test_zero_active_is_still_allowed(self, migrated_db, two_templates):
        """How a shop says "go back to the system's standard layout"."""
        from sqlalchemy.orm import Session

        scope, license_id, (first, _second) = two_templates()
        with Session(migrated_db) as session:
            repository = DocumentTemplateRepository(session)
            repository.set_template_active(scope, first, active=True)
            session.commit()
        with Session(migrated_db) as session:
            DocumentTemplateRepository(session).set_template_active(scope, first, active=False)
            session.commit()
        assert _active_count(migrated_db, license_id) == 0

    def test_another_document_type_is_untouched(self, migrated_db, two_templates):
        """A choice about quotations must not switch off a service-report
        template — the lock is keyed on the pair, not on the tenant."""
        from sqlalchemy.orm import Session

        from chann_data.models import DocumentTemplate

        scope, license_id, (first, _second) = two_templates()
        report_id = uuid.uuid4()
        with Session(migrated_db) as session:
            session.add(DocumentTemplate(
                id=report_id, license_id=license_id, document_type="service_report",
                template_code=f"r-{uuid.uuid4().hex[:8]}", template_name="r",
                is_active=True,
            ))
            session.commit()
        with Session(migrated_db) as session:
            DocumentTemplateRepository(session).set_template_active(scope, first, active=True)
            session.commit()
        assert _active_count(migrated_db, license_id, "service_report") == 1
        assert _active_count(migrated_db, license_id, "quote") == 1
