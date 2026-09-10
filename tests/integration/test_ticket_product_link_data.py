"""A fault filed in chat is stored against the registered unit.

Owner, 10 ก.ย. 2569: "เวลาลูกค้าแจ้งเสียควรจะมีผูกกับสินค้าที่ลงทะเบียนเอาไว้".

`tests/unit/test_ticket_product_link.py` proves the chat handler builds the
right payload, against a fake Data client. That is not the claim that
matters: `service_tickets.product_id` is a real foreign key into
`products`, and the value travelling through `TicketIn` has to survive
pydantic, the repository and the column before it is a link. Two of this
project's worst bugs were exactly that gap — a field the schema did not
declare, dropped without a word, invisible from the tier being looked at.

So this walks the whole way through both tiers over HTTP against a real
migrated database: the shop lists a product, registers the unit it sold,
the customer claims it, then reports a fault in LINE — and the ticket is
read back **out of the database**, not out of the reply, to check it points
at that product row, that serial and that contact.

The negative case is here for the same reason: a customer with nothing
registered files a fault that stores a NULL product, and the row is still
a perfectly good ticket. Nothing is backfilled (rule 4), so NULL has to
keep working forever.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))

from .test_http_journey import _api, shop  # noqa: E402,F401  (fixture)

SERIAL = "SN-TPL-0001"
CUSTOMER_UID = "CHN-TPL-CUST-1"


def _customer_ctx(license_id: str, chann_uid: str = CUSTOMER_UID):
    from chann_app.services.identity import ResolvedContext, TenantResolution

    return ResolvedContext(
        chann_uid=chann_uid,
        display_name="ลูกค้าทดสอบ",
        resolution=TenantResolution.SINGLE,
        memberships=[{
            "license_id": license_id, "license_code": "TPL",
            "company_name": "ร้านทดสอบ", "chann_uid": chann_uid,
            "role": "customer", "status": "active",
        }],
        oa="customer",
        primary_role="customer",
    )


def _chat_client():
    from chann_app.main import app as application_app
    from chann_app.routers_admin import get_data_client

    # The same wired DataClient the HTTP routes got, so chat and the
    # dashboard are talking to one Data tier.
    return application_app.dependency_overrides[get_data_client]()


def _ticket_row(engine, license_id: str, ticket_number: str):
    from sqlalchemy.orm import Session

    from chann_data.models import ServiceTicket

    with Session(engine) as session:
        return session.query(ServiceTicket).filter(
            ServiceTicket.license_id == uuid.UUID(license_id),
            ServiceTicket.ticket_number == ticket_number,
        ).one()


class TestAFaultFiledInChatIsStoredAgainstTheRegisteredUnit:
    async def test_the_ticket_row_points_at_the_product_the_customer_registered(
        self, shop, migrated_db, memory_cache,
    ):
        from chann_app.services.chat import handle_chat_message

        client, license_id = shop

        # ---- 1. the catalogue row the unit was sold as
        response = client.put(
            _api(license_id, "/products/AC12K"),
            json={
                "product_id": "AC12K", "product_name": "แอร์ 12000 BTU",
                "unit_price": "15900.00",
            },
        )
        assert response.status_code in (200, 201), response.text
        product_id = response.json()["id"]

        # ---- 2. the contact, and the LINE identity they message from.
        # customers.customer_chann_uid is a real foreign key, so the
        # identity has to exist before a contact can point at it.
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity

        with Session(migrated_db) as session:
            session.add(ChannIdentity(
                chann_uid=CUSTOMER_UID, line_user_id="line-tpl-cust-1",
                primary_role="customer",
            ))
            session.commit()

        response = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"},
        )
        assert response.status_code == 201, response.text
        contact_id = response.json()["id"]
        # The LINE identity attached to that contact — the same call the
        # webhook makes when a customer links themselves by phone.
        data = _chat_client()
        linked = await data.link_customer_identity(
            license_id, phone="0812345678", customer_chann_uid=CUSTOMER_UID,
        )
        assert linked and str(linked["id"]) == contact_id

        # ---- 3. the shop records the unit it sold, against that product
        response = client.post(
            _api(license_id, "/warranties"),
            json={
                "serial_number": SERIAL, "product_id": product_id,
                "product_name": "แอร์ 12000 BTU",
                "warranty_start": "2026-01-01", "warranty_months": 24,
            },
        )
        assert response.status_code == 201, response.text

        # The link is only usable if the Data tier actually SENDS the
        # product id back — it did not until 10 ก.ย. 2569, and the
        # application cannot link what it never sees (tier-seam rule).
        book = client.get(_api(license_id, "/warranties")).json()
        assert [w["product_id"] for w in book] == [product_id]

        # ---- 4. the customer claims it, then reports a fault in LINE
        ctx = _customer_ctx(license_id)
        claimed = await handle_chat_message(
            data, message=f"ลงทะเบียนสินค้า {SERIAL}", ctx=ctx, language="th",
        )
        assert SERIAL in claimed.text, claimed.text

        reply = await handle_chat_message(
            data, message="แอร์ไม่เย็น มีน้ำหยด", ctx=ctx, language="th",
        )
        assert "แอร์ 12000 BTU" in reply.text, reply.text
        assert "อยู่ในประกันถึง" in reply.text, reply.text

        # ---- 5. read the ticket back OUT OF THE DATABASE
        code = next(w for w in reply.text.split() if w.startswith("T-"))
        row = _ticket_row(migrated_db, license_id, code)
        assert str(row.product_id) == product_id
        assert row.serial_number == SERIAL
        assert str(row.contact_id) == contact_id
        assert row.customer_chann_uid == CUSTOMER_UID
        assert row.issue_description == "แอร์ไม่เย็น มีน้ำหยด"

        # ---- 6. and the dashboard's own list names the machine, so the
        # link is not something only chat can see.
        listed = client.get(_api(license_id, "/tickets")).json()
        mine = next(t for t in listed if t["ticket_number"] == code)
        assert mine["product_id"] == product_id
        assert mine["product_name"] == "แอร์ 12000 BTU"
        assert mine["warranty_status"] == "active"

    async def test_a_customer_with_nothing_registered_still_files_a_usable_ticket(
        self, shop, migrated_db, memory_cache,
    ):
        from chann_app.services.chat import handle_chat_message

        _client, license_id = shop
        data = _chat_client()
        ctx = _customer_ctx(license_id, chann_uid="CHN-TPL-CUST-2")

        # Nothing registered: the shop asks for a serial first (owner rule,
        # 3 ก.ย.) and "ไม่มีหมายเลขเครื่อง" is the way through.
        held = await handle_chat_message(
            data, message="พัดลมไม่หมุน", ctx=ctx, language="th",
        )
        assert "ลงทะเบียน" in held.text
        reply = await handle_chat_message(
            data, message="ไม่มีหมายเลขเครื่อง", ctx=ctx, language="th",
        )
        assert "ยังไม่ได้ผูกกับเครื่องที่ลงทะเบียน" in reply.text

        code = next(w for w in reply.text.split() if w.startswith("T-"))
        row = _ticket_row(migrated_db, license_id, code)
        assert row.product_id is None
        assert row.serial_number is None
        assert row.issue_description == "พัดลมไม่หมุน"
