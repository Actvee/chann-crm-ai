"""Phase 7.5 / 16 — warranties and the cross-company serial lookup.

The lookup is the only query in this system that deliberately crosses the
tenant boundary, so it gets the most attention here. Everything else in
the codebase refuses to leave a tenant; this one has to, because "my
thing is broken, who do I talk to" cannot be answered inside one.

What it must never do is leak. The tests below check the shape of what
comes back as carefully as they check that it finds anything at all: a
lookup that returned another shop's customer, price or history would be a
privacy failure dressed as a feature.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories.phase16 import (  # noqa: E402
    DisplayPreferenceRepository,
    WarrantyConflict,
    WarrantyRepository,
)
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402


def _shop(migrated_db, name):
    from sqlalchemy.orm import Session

    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-W-{suffix}", line_user_id=f"line-w-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=name, created_by_chann_uid=f"CHN-W-{suffix}",
        )
        session.commit()
        return TenantScope(license_id=lic.id)


@pytest.fixture
def shops(migrated_db):
    from sqlalchemy.orm import Session

    return {
        "a": _shop(migrated_db, "ร้านแอร์ดีเซอร์วิส"),
        "b": _shop(migrated_db, "ACME Cooling"),
        "session": lambda: Session(migrated_db),
    }


class TestRegistration:
    def test_a_serial_is_enough_to_register(self, shops):
        """A customer knows the sticker on the back of the machine, not
        what the shop calls the model. Refusing over that would lose the
        record everything afterwards depends on."""
        with shops["session"]() as session:
            row = WarrantyRepository(session).register(
                shops["a"], serial_number="ABC123",
            )
            session.commit()
            assert row.warranty_number.startswith("W-")
            assert row.status == "active"
            # A year by default, which is the common floor in Thai retail.
            assert (row.warranty_end - row.warranty_start).days >= 364

    def test_an_empty_serial_is_refused(self, shops):
        with shops["session"]() as session:
            with pytest.raises(WarrantyConflict):
                WarrantyRepository(session).register(
                    shops["a"], serial_number="   ",
                )

    def test_the_same_serial_cannot_be_registered_twice(self, shops):
        """Two registrations means two different expiry dates for one
        machine, and nobody can say which is right."""
        with shops["session"]() as session:
            WarrantyRepository(session).register(shops["a"], serial_number="DUP1")
            session.commit()
        with shops["session"]() as session:
            with pytest.raises(WarrantyConflict):
                WarrantyRepository(session).register(shops["a"], serial_number="DUP1")

    def test_two_shops_may_hold_the_same_serial(self, shops):
        """Manufacturers reuse serials. A global constraint would let one
        shop's registration block another's."""
        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            repo.register(shops["a"], serial_number="SHARED1")
            repo.register(shops["b"], serial_number="SHARED1")
            session.commit()

        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            assert repo.by_serial(shops["a"], "SHARED1") is not None
            assert repo.by_serial(shops["b"], "SHARED1") is not None

    def test_a_custom_cover_length_is_honoured(self, shops):
        with shops["session"]() as session:
            row = WarrantyRepository(session).register(
                shops["a"], serial_number="LONG1",
                warranty_start=date(2026, 1, 31), warranty_months=1,
            )
            session.commit()
            # 31 Jan + 1 month clamps to the end of February rather than
            # raising on a day that does not exist.
            assert row.warranty_end == date(2026, 2, 28)


class TestCrossCompanyLookup:
    """Master Spec 16.6 test_cross_company_serial."""

    def test_a_serial_in_one_shop_names_that_shop(self, shops):
        with shops["session"]() as session:
            WarrantyRepository(session).register(
                shops["a"], serial_number="ONE1", product_name="แอร์ 12000 BTU",
            )
            session.commit()

        with shops["session"]() as session:
            matches = WarrantyRepository(session).find_shops_by_serial("ONE1")
            assert len(matches) == 1
            assert matches[0]["company_name"] == "ร้านแอร์ดีเซอร์วิส"

    def test_a_serial_in_two_shops_returns_both_to_be_chosen_between(self, shops):
        """16.4 case 3: ambiguous, so the customer picks. Guessing would
        send a repair request to a shop that never sold the thing."""
        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            repo.register(shops["a"], serial_number="BOTH1")
            repo.register(shops["b"], serial_number="BOTH1")
            session.commit()

        with shops["session"]() as session:
            matches = WarrantyRepository(session).find_shops_by_serial("BOTH1")
            assert {m["company_name"] for m in matches} == {
                "ร้านแอร์ดีเซอร์วิส", "ACME Cooling",
            }

    def test_an_unknown_serial_returns_nothing_rather_than_a_guess(self, shops):
        with shops["session"]() as session:
            assert WarrantyRepository(session).find_shops_by_serial("NOPE") == []

    def test_a_partial_serial_matches_nothing(self, shops):
        """Exact match only. A prefix search here would let anyone
        enumerate another company's inventory one keystroke at a time."""
        with shops["session"]() as session:
            WarrantyRepository(session).register(shops["a"], serial_number="ABCDEF123")
            session.commit()
        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            assert repo.find_shops_by_serial("ABC") == []
            assert repo.find_shops_by_serial("ABCDEF") == []

    def test_the_result_identifies_a_shop_and_nothing_more(self, shops):
        """The shape matters as much as the content: this crosses a tenant
        boundary, so anything beyond "which shop" is a leak."""
        from sqlalchemy.orm import Session

        from chann_data.repositories.phase9 import CustomerRepository

        with shops["session"]() as session:
            customer = CustomerRepository(session).create(
                shops["a"], first_name="ความลับ", last_name="ห้ามหลุด",
                phone="0812345678",
            )
            WarrantyRepository(session).register(
                shops["a"], serial_number="SHAPE1",
                contact_id=customer.id, customer_chann_uid="CHN-PRIVATE",
                product_name="แอร์",
            )
            session.commit()

        with shops["session"]() as session:
            match = WarrantyRepository(session).find_shops_by_serial("SHAPE1")[0]

        allowed = {
            "license_id", "company_name", "company_code",
            "warranty_number", "product_name", "warranty_end", "status",
        }
        assert set(match) == allowed, f"unexpected fields leaked: {set(match) - allowed}"
        serialised = str(match)
        assert "ความลับ" not in serialised
        assert "0812345678" not in serialised
        assert "CHN-PRIVATE" not in serialised

    def test_a_voided_warranty_is_not_routable(self, shops):
        """A voided registration should not send anyone anywhere."""
        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            row = repo.register(shops["a"], serial_number="VOID1")
            session.flush()
            repo.set_status(shops["a"], row.id, status="void")
            session.commit()

        with shops["session"]() as session:
            assert WarrantyRepository(session).find_shops_by_serial("VOID1") == []


class TestTenantIsolation:
    """Master Spec 16.6 test_cross_tenant_audit — the scoped reads must
    stay scoped even though one query on the same repository does not."""

    def test_a_warranty_is_invisible_to_another_tenant(self, shops):
        with shops["session"]() as session:
            row = WarrantyRepository(session).register(
                shops["a"], serial_number="ISO1",
            )
            session.commit()
            warranty_id = row.id

        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            assert repo.get(shops["b"], warranty_id) is None
            assert repo.by_serial(shops["b"], "ISO1") is None
            assert repo.list_for_license(shops["b"]) == []

    def test_numbering_restarts_per_tenant(self, shops):
        """A new shop's first warranty is W-YYYY-0001, not whatever the
        platform-wide count happens to be — same reasoning as customer,
        deal, quote and ticket codes."""
        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            first = repo.register(shops["a"], serial_number="NUM-A1")
            session.commit()
            assert first.warranty_number.endswith("0001")

        with shops["session"]() as session:
            other = WarrantyRepository(session).register(
                shops["b"], serial_number="NUM-B1",
            )
            session.commit()
            assert other.warranty_number.endswith("0001")


class TestExpiry:
    def test_overdue_cover_is_marked_expired(self, shops):
        with shops["session"]() as session:
            WarrantyRepository(session).register(
                shops["a"], serial_number="OLD1",
                warranty_start=date(2020, 1, 1), warranty_months=12,
            )
            session.commit()

        with shops["session"]() as session:
            repo = WarrantyRepository(session)
            assert repo.expire_overdue(shops["a"]) == 1
            session.commit()

        with shops["session"]() as session:
            assert WarrantyRepository(session).by_serial(
                shops["a"], "OLD1",
            ).status == "expired"

    def test_current_cover_is_left_alone(self, shops):
        with shops["session"]() as session:
            WarrantyRepository(session).register(shops["a"], serial_number="NEW1")
            session.commit()
        with shops["session"]() as session:
            assert WarrantyRepository(session).expire_overdue(shops["a"]) == 0


class TestDisplayPreferences:
    """Master Spec 16.6 test_display_preference."""

    def test_defaults_are_returned_for_someone_with_no_row(self, shops, migrated_db):
        from sqlalchemy.orm import Session

        with Session(migrated_db) as session:
            pref = DisplayPreferenceRepository(session).get("CHN-NOBODY")
        # Defaults rather than None: making every caller handle absence is
        # how one of them ends up rendering "None" to a person.
        assert pref["language"] == "th"
        assert pref["timezone"] == "Asia/Bangkok"

    def test_a_preference_follows_the_person(self, shops, migrated_db):
        """16.5: keyed on the identity, so someone who reads English at one
        shop reads English at all of them."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid="CHN-PREF-1", line_user_id="line-pref-1",
                primary_role="customer",
            ))
            session.commit()

        with Session(migrated_db) as session:
            DisplayPreferenceRepository(session).upsert(
                "CHN-PREF-1", {"language": "en", "date_format": "mm/dd/yyyy"},
            )
            session.commit()

        with Session(migrated_db) as session:
            pref = DisplayPreferenceRepository(session).get("CHN-PREF-1")
        assert pref["language"] == "en"
        assert pref["date_format"] == "mm/dd/yyyy"
        # Untouched fields keep their defaults rather than being blanked.
        assert pref["timezone"] == "Asia/Bangkok"
