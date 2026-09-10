"""A fault report carries the machine it is about.

Owner, 10 ก.ย. 2569: "เวลาลูกค้าแจ้งเสียควรจะมีผูกกับสินค้าที่ลงทะเบียนเอาไว้"
— a fault report should be tied to the product the customer registered.

`service_tickets.product_id` has existed since Phase 12 and `TicketIn` has
always accepted it; nothing ever sent one. The customer flow asked which
registered unit the fault was about and then passed only the serial, and
the staff flow ("เปิดงานให้ สมชาย แอร์ไม่เย็น") asked nothing at all and
sent a `customer_id` key `TicketIn` does not declare, which pydantic drops
without a word — so a job opened by hand pointed at no customer either.

What is asserted here, in the order it matters:

1. **the payload** — the serial the customer chose brings the warranty's
   `product_id` with it; no serial links nothing; a serial another
   customer holds is still refused before any ticket exists;
2. **the staff path** — one registered unit is linked and named, several
   are asked about with the customer flow's own device-choice buttons,
   none leaves the flow exactly as it was;
3. **the warranty line** — "อยู่ในประกันถึง …" appears only when the cover
   is genuinely live, and an expired unit says so instead;
4. **the displays** — the ticket detail, the new-fault card and the
   dispatch card all name the machine, and all render fine without one.

The read-back is the payload the Data tier was handed, not the reply text:
a reply that says the right thing about a ticket that stored nothing is
precisely the failure this exists to catch.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import ticket_machine  # noqa: E402
from chann_app.services.chat import (  # noqa: E402
    _handle_staff_ticket_create,
    _notify_assigned_ticket,
    _notify_new_ticket,
    handle_chat_message,
)
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

# asyncio_mode = auto (pytest.ini): async tests need no marker, and a
# blanket one would be applied to the plain ones below too.
STAFF_KEYS = ["ticket.create", "ticket.read", "ticket.assign", "customer.read"]
CUSTOMER = _ctx(oa="customer").chann_uid


def _unit(serial, name, *, product_id, end="2027-01-01", status="active", owner=CUSTOMER):
    return {
        "id": f"w-{serial}", "warranty_number": f"W-2026-{serial[-4:]}",
        "serial_number": serial, "product_name": name, "product_id": product_id,
        "warranty_start": "2026-01-01", "warranty_end": end, "status": status,
        "customer_chann_uid": owner,
    }


def _created(client):
    return [r[2] for r in client.recorded if r[0] == "create_ticket"]


class TestTheCustomerPathLinksTheUnitTheFaultIsAbout:
    async def test_the_chosen_serial_brings_its_product_with_it(self):
        client = FakeDataClient()
        client._warranties = [
            _unit("SN0001", "แอร์ 12000 BTU", product_id="prod-air"),
            _unit("SN0002", "ตู้เย็น", product_id="prod-fridge"),
        ]
        ctx = _ctx(oa="customer")

        asked = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=ctx)
        # Two units: the question, not a guess (rule 3).
        assert [payload for _, payload in asked.quick_replies][:2] == ["SN0001", "SN0002"]
        assert not _created(client)

        await handle_chat_message(client, message="SN0002", ctx=ctx)
        payload = _created(client)[0]
        assert payload["serial_number"] == "SN0002"
        assert payload["product_id"] == "prod-fridge"

    async def test_one_registered_unit_is_linked_without_being_asked_about(self):
        client = FakeDataClient()
        client._warranties = [_unit("ONLY0001", "แอร์", product_id="prod-air")]
        reply = await handle_chat_message(
            client, message="แอร์ไม่เย็น มีน้ำหยด", ctx=_ctx(oa="customer"),
        )
        payload = _created(client)[0]
        assert payload["product_id"] == "prod-air"
        assert payload["serial_number"] == "ONLY0001"
        # And the customer is told which machine it went against, so the
        # link is visible rather than merely stored.
        assert "แอร์" in reply.text and "ONLY0001" in reply.text

    async def test_the_ticket_points_at_the_shops_contact_row_when_there_is_one(self):
        client = FakeDataClient()
        client._warranties = [_unit("ONLY0001", "แอร์", product_id="prod-air")]
        client._customers = [{
            "id": "cu-7", "customer_id": "C-2026-0007", "first_name": "สมชาย",
            "customer_chann_uid": CUSTOMER,
        }]
        await handle_chat_message(
            client, message="แอร์ไม่เย็น", ctx=_ctx(oa="customer"),
        )
        assert _created(client)[0]["contact_id"] == "cu-7"

    async def test_a_customer_the_shop_has_never_recorded_still_gets_a_ticket(self):
        client = FakeDataClient()
        client._warranties = [_unit("ONLY0001", "แอร์", product_id="prod-air")]
        client._customers = []
        await handle_chat_message(
            client, message="แอร์ไม่เย็น", ctx=_ctx(oa="customer"),
        )
        payload = _created(client)[0]
        assert "contact_id" not in payload
        assert payload["product_id"] == "prod-air"

    async def test_no_serial_links_nothing_and_says_so(self):
        client = FakeDataClient()
        client._warranties = []
        ctx = _ctx(oa="customer")
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=ctx)
        reply = await handle_chat_message(client, message="ไม่มีหมายเลขเครื่อง", ctx=ctx)

        payload = _created(client)[0]
        assert "product_id" not in payload
        assert "serial_number" not in payload
        # Rule 4: no raw key, and no silence either — the customer is told
        # the job carries no machine.
        assert "ยังไม่ได้ผูกกับเครื่องที่ลงทะเบียน" in reply.text

    async def test_a_fault_reported_while_a_live_chat_runs_still_links_the_unit(self):
        # Phase 15 routes free text into an open conversation with the
        # shop, and the fault flow reaches _handle_customer_report by a
        # different door (the "แจ้งซ่อม" tile). It must not be the door
        # where the machine question is skipped.
        client = FakeDataClient()
        client._warranties = [_unit("ONLY0001", "แอร์", product_id="prod-air")]

        async def _one_live_session(license_id, status=None, customer_chann_uid=None, limit=100):
            return [{
                "id": "cs-1", "status": "live",
                "customer_chann_uid": customer_chann_uid or CUSTOMER,
            }]

        client.list_chat_sessions = _one_live_session
        ctx = _ctx(oa="customer")

        await handle_chat_message(client, message="แจ้งซ่อม", ctx=ctx)
        reply = await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=ctx)

        payload = _created(client)[0]
        assert payload["product_id"] == "prod-air"
        assert payload["serial_number"] == "ONLY0001"
        assert "แอร์" in reply.text

    async def test_a_serial_another_customer_holds_is_still_refused(self):
        client = FakeDataClient()
        client._warranties = [
            _unit("SN0009", "แอร์", product_id="prod-air", owner="CHN-SOMEONE-ELSE"),
        ]
        ctx = _ctx(oa="customer")
        await handle_chat_message(client, message="แอร์ไม่เย็น", ctx=ctx)
        reply = await handle_chat_message(client, message="SN0009", ctx=ctx)

        assert not _created(client), "a claimed unit must not open a job for someone else"
        assert "SN0009" in reply.text


class TestTheStaffPathLinksTheCustomersUnit:
    def _shop(self, units):
        client = FakeDataClient(permission_keys=STAFF_KEYS)
        client._customers = [{
            "id": "cu-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
            "last_name": "ใจดี", "phone": "0812345678", "address": "99/1",
            "customer_chann_uid": "CHN-CUST-1",
        }]
        client._warranties = units
        return client

    async def _open(self, client, fields=None):
        return await _handle_staff_ticket_create(
            client, ctx=_ctx(oa="sales"), license_id=LICENSE_ID,
            fields={"target_name": "สมชาย", "issue_description": "แอร์ไม่เย็น", **(fields or {})},
            permission_keys=STAFF_KEYS, language="th",
        )

    async def test_one_unit_is_linked_and_named(self):
        client = self._shop([
            _unit("SN0001", "แอร์ 12000 BTU", product_id="prod-air", owner="CHN-CUST-1"),
        ])
        reply = await self._open(client)
        payload = _created(client)[0]
        assert payload["product_id"] == "prod-air"
        assert payload["serial_number"] == "SN0001"
        # The link to the customer, under the name TicketIn actually
        # declares — `customer_id` was being dropped by pydantic.
        assert payload["contact_id"] == "cu-1"
        assert "customer_id" not in payload
        assert "แอร์ 12000 BTU" in reply.text and "SN0001" in reply.text

    async def test_several_units_are_asked_about_never_guessed(self):
        client = self._shop([
            _unit("SN0001", "แอร์", product_id="prod-air", owner="CHN-CUST-1"),
            _unit("SN0002", "ตู้เย็น", product_id="prod-fridge", owner="CHN-CUST-1"),
        ])
        reply = await self._open(client)
        assert not _created(client)
        assert [payload for _, payload in reply.quick_replies][:2] == ["SN0001", "SN0002"]

        # The tap finishes the ORIGINAL sentence: same issue, same
        # customer, now with the machine that was chosen.
        chosen = await handle_chat_message(client, message="SN0002", ctx=_ctx(oa="sales"))
        payload = _created(client)[0]
        assert payload["product_id"] == "prod-fridge"
        assert payload["issue_description"] == "แอร์ไม่เย็น"
        assert payload["contact_id"] == "cu-1"
        assert "ตู้เย็น" in chosen.text

    async def test_the_way_out_opens_the_job_with_no_machine(self):
        client = self._shop([
            _unit("SN0001", "แอร์", product_id="prod-air", owner="CHN-CUST-1"),
            _unit("SN0002", "ตู้เย็น", product_id="prod-fridge", owner="CHN-CUST-1"),
        ])
        await self._open(client)
        await handle_chat_message(client, message="ไม่ระบุเครื่อง", ctx=_ctx(oa="sales"))
        payload = _created(client)[0]
        assert "product_id" not in payload
        assert "serial_number" not in payload

    async def test_a_customer_with_no_registered_unit_is_unchanged(self):
        client = self._shop([])
        reply = await self._open(client)
        payload = _created(client)[0]
        assert "product_id" not in payload
        assert "serial_number" not in payload
        assert reply.text.startswith("เปิดงานซ่อม")
        assert "เครื่อง:" not in reply.text


class TestTheWarrantyLineOnlyAppearsWhenTheCoverIsLive:
    def _ticket(self, status, end="2027-01-01"):
        return {
            "ticket_number": "T-2026-0001", "serial_number": "SN0001",
            "product_name": "แอร์ 12000 BTU", "warranty_end": end,
            "warranty_status": status,
        }

    def test_a_live_warranty_says_so_with_its_end_date(self):
        line = ticket_machine.machine_line(self._ticket("active"), "th")
        assert "อยู่ในประกันถึง" in line
        assert "1 ม.ค. 2570" in line

    def test_an_expired_warranty_never_claims_cover(self):
        line = ticket_machine.machine_line(self._ticket("expired", "2020-01-01"), "th")
        assert "อยู่ในประกันถึง" not in line
        assert "หมดประกันแล้ว" in line
        assert "แอร์ 12000 BTU" in line

    def test_a_void_warranty_is_treated_as_no_cover(self):
        assert "อยู่ในประกันถึง" not in ticket_machine.machine_line(self._ticket("void"), "th")

    def test_the_state_is_the_data_tiers_derived_status_not_a_date_sum(self):
        # `WarrantyRepository.effective_status` already decides this on the
        # Bangkok calendar and every warranty leaves the Data tier through
        # it. A row still cached as "expired" with a future end date must
        # not be talked back into cover here.
        assert not ticket_machine.in_warranty({"status": "expired", "warranty_end": "2099-01-01"})
        assert ticket_machine.in_warranty({"status": "active", "warranty_end": "2027-01-01"})

    def test_a_ticket_with_no_machine_renders_as_nothing_at_all(self):
        assert ticket_machine.machine_line({"ticket_number": "T-2026-0001"}, "th") == ""
        assert ticket_machine.describe({}, "th") == ""


class TestEveryoneWhoNeedsTheMachineIsToldAboutIt:
    def _shop(self):
        client = FakeDataClient(permission_keys=STAFF_KEYS + ["ticket.update"])
        client._warranties = [_unit("SN0001", "แอร์ 12000 BTU", product_id="prod-air")]
        client._tickets = [{
            "id": "tk-1", "ticket_number": "T-2026-0001", "status": "open",
            "customer_name": "สมชาย", "customer_phone": "0812345678",
            "service_address": "99/1", "issue_description": "แอร์ไม่เย็น",
            "serial_number": "SN0001", "product_id": "prod-air",
            "scheduled_date": "2026-09-12", "scheduled_time": "09:00:00",
        }]
        client._members = [{
            "id": "m-1", "chann_uid": "CHN-TECH-1", "role": "technician", "status": "active",
        }]
        client._profiles = {"CHN-TECH-1": {"first_name": "ช่าง", "last_name": "หนึ่ง"}}
        return client

    async def test_the_ticket_detail_in_chat_names_the_machine(self):
        client = self._shop()
        reply = await handle_chat_message(
            client, message="ข้อมูลงาน T-2026-0001", ctx=_ctx(oa="sales"),
        )
        assert "แอร์ 12000 BTU" in reply.text
        assert "SN0001" in reply.text
        assert "อยู่ในประกันถึง" in reply.text

    async def test_a_ticket_with_no_link_still_renders(self):
        client = self._shop()
        client._tickets[0].pop("serial_number")
        client._tickets[0].pop("product_id")
        reply = await handle_chat_message(
            client, message="ข้อมูลงาน T-2026-0001", ctx=_ctx(oa="sales"),
        )
        assert "T-2026-0001" in reply.text
        assert "เครื่อง:" not in reply.text

    async def test_the_new_fault_card_carries_the_machine(self):
        client = self._shop()
        client._members.append({
            "id": "m-2", "chann_uid": "CHN-CS-1", "role": "cs", "status": "active",
            "permission_keys": ["ticket.assign"],
        })
        await _notify_new_ticket(client, LICENSE_ID, "tk-1", "th")
        sent = [r for r in client.recorded if r[0] == "create_notification"]
        assert sent, "the shop must hear about a new fault"
        assert any("แอร์ 12000 BTU" in str(r) and "อยู่ในประกันถึง" in str(r) for r in sent)

    async def test_the_dispatch_card_tells_the_technician_what_they_are_going_to(self):
        client = self._shop()
        ticket = {
            **client._tickets[0],
            "assigned_target_type": "technician", "assigned_to_ref": "m-1",
        }
        await _notify_assigned_ticket(client, LICENSE_ID, ticket, "ช่างหนึ่ง", "th")
        sent = [r for r in client.recorded if r[0] == "create_notification"]
        assert any("แอร์ 12000 BTU" in str(r) for r in sent)
        assert any("อยู่ในประกันถึง" in str(r) for r in sent)
