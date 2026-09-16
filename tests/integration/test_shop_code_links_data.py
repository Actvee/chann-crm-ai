"""Round 19v — the code a shop hands out is the code that links.

Owner's transcript, 16 ก.ย. 2569, 19:59:

    M.A.C      COV9URCZ
    CS(Dev)    ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง
    M.A.C      COV9URCZ
    CS(Dev)    ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง
    M.A.C      ร้านทดสอบ
    CS(Dev)    ผูกกับร้าน "ร้านทดสอบ" เรียบร้อย

— and the shop's own card then printed "รหัสร้าน: COV9URCZ" for that very
shop. A licence carries two codes: `company_code`, which link_customer
looked up, and `license_code`, which the shop's card calls "รหัสร้าน" and
tells the shop to give to customers. Shops handed out the one the lookup
refused.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session


@pytest.mark.usefixtures("migrated_db")
class TestEitherCodeFindsTheShop:
    def _identity(self, migrated_db, chann_uid, role="customer"):
        from chann_data.models import ChannIdentity

        with Session(migrated_db) as session:
            if session.get(ChannIdentity, chann_uid) is None:
                session.add(ChannIdentity(
                    chann_uid=chann_uid,
                    line_user_id=f"line-{chann_uid.lower()}",
                    primary_role=role,
                ))
                session.commit()

    def _a_shop(self, migrated_db, who="OWNER"):
        """A fresh shop: one account may only ever create one company, so each
        test brings its own owner."""
        from chann_data.models import License
        from chann_data.repositories.phase65 import RegistrationRepository

        owner = f"CHN-SHOPCODE-{who}"
        self._identity(migrated_db, owner, role="sales")
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            created = repo.create_license(
                company_name="ร้านทดสอบรหัส", created_by_chann_uid=owner,
            )
            session.commit()
            row = session.get(License, created.id)
            return str(row.id), row.license_code, row.company_code

    def test_the_two_codes_are_different_shapes(self, migrated_db):
        _lid, license_code, company_code = self._a_shop(migrated_db, "SHAPES")
        assert license_code.startswith("CO") and len(license_code) == 8
        assert company_code and len(company_code) == 8
        assert license_code != company_code, "the card and the lookup would agree by luck"

    def test_the_customer_facing_code_links(self, migrated_db):
        from chann_data.repositories.phase65 import RegistrationRepository

        lid, _license_code, company_code = self._a_shop(migrated_db, "O1")
        self._identity(migrated_db, "CHN-SHOPCODE-C1")
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            repo.link_customer(chann_uid="CHN-SHOPCODE-C1", company_code=company_code)
            session.commit()
        with Session(migrated_db) as session:
            shops = RegistrationRepository(session).my_shops("CHN-SHOPCODE-C1")
            assert [str(s.id) for s in shops] == [lid]

    def test_the_code_on_the_shops_own_card_links_too(self, migrated_db):
        """The one the owner typed."""
        from chann_data.repositories.phase65 import RegistrationRepository

        lid, license_code, _company_code = self._a_shop(migrated_db, "O2")
        self._identity(migrated_db, "CHN-SHOPCODE-C2")
        with Session(migrated_db) as session:
            repo = RegistrationRepository(session)
            repo.link_customer(chann_uid="CHN-SHOPCODE-C2", company_code=license_code)
            session.commit()
        with Session(migrated_db) as session:
            shops = RegistrationRepository(session).my_shops("CHN-SHOPCODE-C2")
            assert [str(s.id) for s in shops] == [lid]

    def test_lower_case_is_the_same_code(self, migrated_db):
        from chann_data.repositories.phase65 import RegistrationRepository

        lid, license_code, _ = self._a_shop(migrated_db, "O3")
        self._identity(migrated_db, "CHN-SHOPCODE-C3")
        with Session(migrated_db) as session:
            RegistrationRepository(session).link_customer(
                chann_uid="CHN-SHOPCODE-C3", company_code=license_code.lower(),
            )
            session.commit()
        with Session(migrated_db) as session:
            shops = RegistrationRepository(session).my_shops("CHN-SHOPCODE-C3")
            assert [str(s.id) for s in shops] == [lid]

    def test_a_code_nobody_has_is_still_refused(self, migrated_db):
        from chann_data.repositories.phase65 import RegistrationNotFound, RegistrationRepository

        self._identity(migrated_db, "CHN-SHOPCODE-C4")
        with Session(migrated_db) as session:
            with pytest.raises(RegistrationNotFound):
                RegistrationRepository(session).link_customer(
                    chann_uid="CHN-SHOPCODE-C4", company_code="COZZZZZZ",
                )
