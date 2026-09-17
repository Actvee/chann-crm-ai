"""Round 20d — a number freed by a deletion can be used again.

Owner, 17 ก.ย. 2569: "ลบลูกค้าไปแล้ว พอจะเพิ่มลูกค้าใหม่ที่เป็นเบอร์เดิมแล้ว
เพิ่มไม่ได้".

Deleting a customer here means archiving: the row stays for the history
that points at it. The duplicate rule is what the shop experiences, so it
has to agree — an archived record must not hold a phone number hostage.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session


@pytest.mark.usefixtures("migrated_db")
class TestTheNumberIsFreeAgain:
    def _repo(self, session):
        from chann_data.repositories.phase9 import CustomerRepository
        from chann_data.repositories.tenant_scope import TenantScope

        return CustomerRepository(session), TenantScope(license_id=self.license_id)

    _n = [0]

    @pytest.fixture(autouse=True)
    def _a_shop(self, migrated_db):
        # One account may only ever create one company, so each test brings
        # its own owner.
        self._n[0] += 1
        who = f"CHN-READD-{self._n[0]:03d}"
        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        with Session(migrated_db) as session:
            if session.get(ChannIdentity, who) is None:
                session.add(ChannIdentity(
                    chann_uid=who, line_user_id=f"line-{who.lower()}",
                    primary_role="sales",
                ))
                session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="ร้านทดสอบเบอร์ซ้ำ", created_by_chann_uid=who,
            )
            session.commit()
            self.license_id = lic.id

    def test_the_same_phone_can_be_used_after_the_first_is_deleted(self, migrated_db):
        with Session(migrated_db) as session:
            repo, scope = self._repo(session)
            first = repo.create(
                scope, first_name="สมชาย", last_name="ใจดี", phone="0812345678",
            )
            session.commit()
            first_id = first.id

        with Session(migrated_db) as session:
            repo, scope = self._repo(session)
            repo.archive(scope, first_id)
            session.commit()

        with Session(migrated_db) as session:
            repo, scope = self._repo(session)
            again = repo.create(
                scope, first_name="สมหญิง", last_name="รักดี", phone="0812345678",
            )
            session.commit()
            assert again.phone == "0812345678"
            assert again.first_name == "สมหญิง", "it must be the NEW person, not the old row"

    def test_a_live_customer_still_holds_the_number(self, migrated_db):
        from chann_data.repositories.phase9 import Phase9Duplicate

        with Session(migrated_db) as session:
            repo, scope = self._repo(session)
            repo.create(scope, first_name="สมปอง", last_name="ดี", phone="0899999999")
            session.commit()

        with Session(migrated_db) as session:
            repo, scope = self._repo(session)
            with pytest.raises(Phase9Duplicate):
                repo.create(scope, first_name="คนอื่น", last_name="ใคร", phone="0899999999")


@pytest.mark.usefixtures("migrated_db")
class TestOpeningADealConfirmsTheCustomer:
    """Owner, 17 ก.ย. 2569: "ถ้าลูกค้าตกลงสร้าง Deal จะต้องกลายเป็น contact
    auto ไปเลย" — opening a deal for someone IS confirming them, so the
    lead should not sit at "lead" waiting for a second command."""

    _n = [100]

    @pytest.fixture(autouse=True)
    def _a_shop(self, migrated_db):
        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        self._n[0] += 1
        who = f"CHN-DEALSTAGE-{self._n[0]:03d}"
        with Session(migrated_db) as session:
            if session.get(ChannIdentity, who) is None:
                session.add(ChannIdentity(
                    chann_uid=who, line_user_id=f"line-{who.lower()}", primary_role="sales",
                ))
                session.commit()
        with Session(migrated_db) as session:
            lic = RegistrationRepository(session).create_license(
                company_name="ร้านทดสอบดีล", created_by_chann_uid=who,
            )
            session.commit()
            self.license_id = lic.id

    def _repos(self, session):
        from chann_data.repositories.phase9 import CustomerRepository, DealRepository
        from chann_data.repositories.tenant_scope import TenantScope

        scope = TenantScope(license_id=self.license_id)
        return CustomerRepository(session), DealRepository(session), scope

    def test_a_lead_becomes_a_contact(self, migrated_db):
        from chann_data.models import Customer

        with Session(migrated_db) as session:
            customers, deals, scope = self._repos(session)
            lead = customers.create(
                scope, first_name="สมชาย", last_name="ใจดี", phone="0810000001",
            )
            session.commit()
            assert lead.stage == "lead"
            lead_id = lead.id

        with Session(migrated_db) as session:
            _customers, deals, scope = self._repos(session)
            deals.create(scope, contact_id=lead_id)
            session.commit()

        with Session(migrated_db) as session:
            assert session.get(Customer, lead_id).stage == "contact"

    def test_someone_already_a_contact_is_left_alone(self, migrated_db):
        from chann_data.models import Customer

        with Session(migrated_db) as session:
            customers, _deals, scope = self._repos(session)
            row = customers.create(
                scope, first_name="สมหญิง", last_name="ดี", phone="0810000002",
                stage="contact",
            )
            session.commit()
            row_id = row.id

        with Session(migrated_db) as session:
            _customers, deals, scope = self._repos(session)
            deals.create(scope, contact_id=row_id)
            session.commit()

        with Session(migrated_db) as session:
            assert session.get(Customer, row_id).stage == "contact"
