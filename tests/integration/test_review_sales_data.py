"""Review batch C (6 Sep 2026) against Postgres: a deal's value, timing
and lost reason survive the round trip through the Data router (C1), a
quote's terms come back (C2), the dispatch gate names its columns (C11),
an ownership transfer can be listed with both parties (E6), and a sales
group's members can be read back (E7).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, LicenseMember
from chann_data.repositories.phase10 import QuoteRepository
from chann_data.repositories.phase12 import DispatchBlocked, ServiceTicketRepository
from chann_data.repositories.phase2 import OwnershipTransferRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.phase7 import SalesGroupRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.tenant_scope import TenantScope
from chann_data.routers.internal import _deal_out, _transfer_out
from chann_data.schemas import QuoteOut


@pytest.fixture
def tenant(migrated_db):
    tag = uuid.uuid4().hex[:6]
    owner_uid = f"CHN-RS{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=owner_uid, line_user_id=f"line-RS{tag}", primary_role="sales"))
        s.commit()
        lic = RegistrationRepository(s).create_license(company_name=f"Review C {tag}", created_by_chann_uid=owner_uid)
        s.commit()
        other = ChannIdentity(chann_uid=f"CHN-RS{tag}-2", line_user_id=f"line-RS{tag}-2", primary_role="sales")
        s.add(other)
        s.flush()
        member = LicenseMember(id=uuid.uuid4(), license_id=lic.id, chann_uid=other.chann_uid, role="member", status="active")
        s.add(member)
        s.commit()
        return {"engine": migrated_db, "scope": TenantScope(license_id=lic.id), "owner_uid": owner_uid,
                "other_uid": other.chann_uid, "other_member_id": member.id}


class TestDealAndQuoteRoundTrip:
    def test_the_router_helper_sends_value_timing_and_lost_reason(self, tenant):
        with Session(tenant["engine"]) as s:
            sc = tenant["scope"]
            customer = CustomerRepository(s).create(sc, first_name="สมชาย", phone="0811111111")
            s.flush()
            deal = DealRepository(s).create(sc, contact_id=customer.id, amount=Decimal("9900.00"),
                                            expected_close_date=date(2026, 10, 1))
            s.flush()
            DealRepository(s).update(sc, deal.id, {"lost_reason": "งบไม่พอ"})
            s.commit()
            out = _deal_out(DealRepository(s).get(sc, deal.id), []).model_dump()
            assert out["amount"] == Decimal("9900.00")
            assert out["expected_close_date"] == date(2026, 10, 1)
            assert out["currency"] == "THB"
            assert out["lost_reason"] == "งบไม่พอ"

    def test_quote_out_carries_the_terms_it_stores(self, tenant):
        with Session(tenant["engine"]) as s:
            sc = tenant["scope"]
            customer = CustomerRepository(s).create(sc, first_name="สมหญิง", phone="0822222222")
            s.flush()
            deal = DealRepository(s).create(sc, contact_id=customer.id)
            s.flush()
            DealRepository(s).add_product(sc, deal.id, product_id=None, product_name="แอร์", quoted_unit_price=Decimal("15000"), qty=1)
            s.flush()
            quote = QuoteRepository(s).create(sc, deal_id=deal.id)
            s.flush()
            QuoteRepository(s).set_terms(sc, quote.id, valid_until=date(2026, 12, 31), discount_percent=Decimal("10"))
            s.commit()
            out = QuoteOut.model_validate(QuoteRepository(s).get(sc, quote.id), from_attributes=True).model_dump()
            assert out["valid_until"] == date(2026, 12, 31)
            assert out["discount_percent"] == Decimal("10.00")
            assert out["discount_amount"] is None


class TestDispatchGateNamesItsColumns:
    def test_missing_fields_accompany_the_labels(self, tenant):
        with Session(tenant["engine"]) as s:
            sc = tenant["scope"]
            repo = ServiceTicketRepository(s)
            row = repo.create(sc, issue_description="แอร์ไม่เย็น", customer_name="ก")
            s.flush()
            assert repo.dispatch_missing_fields(row) == ["customer_phone", "service_address", "scheduled_date", "scheduled_time"]
            with pytest.raises(DispatchBlocked) as exc:
                repo.assign(sc, row.id, target_type="technician", target_ref=tenant["other_member_id"])
            assert exc.value.fields == repo.dispatch_missing_fields(row)
            assert len(exc.value.missing) == len(exc.value.fields)


class TestOwnershipTransferList:
    def test_a_pending_transfer_is_listed_with_both_parties(self, tenant):
        with Session(tenant["engine"]) as s:
            sc = tenant["scope"]
            repo = OwnershipTransferRepository(s)
            assert repo.list(sc) == []
            transfer = repo.request(sc, tenant["owner_uid"], tenant["other_uid"])
            s.commit()
            listed = repo.list(sc)
            assert [t.id for t in listed] == [transfer.id]
            out = _transfer_out(s, listed[0])
            assert out.from_chann_uid == tenant["owner_uid"]
            assert out.to_chann_uid == tenant["other_uid"]
            repo.accept(sc, transfer.id, tenant["other_uid"])
            s.commit()
            assert repo.list(sc) == []
            assert [t.status for t in repo.list(sc, status=None)] == ["accepted"]


class TestSalesGroupMembers:
    def test_members_can_be_read_back(self, tenant):
        with Session(tenant["engine"]) as s:
            sc = tenant["scope"]
            repo = SalesGroupRepository(s)
            group = repo.create(sc, "ทีมเหนือ")
            s.flush()
            repo.add_member(sc, group.id, tenant["other_member_id"])
            s.commit()
            assert [m.member_id for m in repo.members(sc, group.id)] == [tenant["other_member_id"]]
            repo.remove_member(sc, group.id, tenant["other_member_id"])
            s.commit()
            assert repo.members(sc, group.id) == []
