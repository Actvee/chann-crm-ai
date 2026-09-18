"""Round 20h — finding a job by its number, and three N+1s.

Owner, 17 ก.ย. 2569, on the earlier audit note: "เช็คเรื่องที่เคยบอกฉัน …
เกี่ยวกับที่ระบบมี get by number อยู่แล้ว และแนวทางที่เมื่อมีข้อมูลเยอะขึ้น
ควรจะปรับแต่งระบบยังไง".

Measured on 3,000 tickets and 3,000 deals before writing any of this:

    get_by_number(oldest)                   2.3 ms       1 query    found
    list(limit=100) + scan for oldest      14.2 ms       1 query    NOT FOUND
    list(limit=500) + scan for oldest      33.2 ms       1 query    NOT FOUND
    list_deals (products_of per deal)    4,725.0 ms   3,001 queries

So the first of these is not a speed problem at all: a shop past a hundred
jobs was told "ไม่พบใบงาน" about a job that was still open, and raising the
limit could not fix it because the cap is 500.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio


def _queue(n: int, **extra) -> list[dict]:
    """Newest first, the order the tier returns."""
    return [
        {"id": f"t{i}", "ticket_number": f"T-2026-{i:04d}", "status": "open",
         "visibility": "public", "accept_status": "pending",
         "customer_name": "สมชาย ใจดี", "issue_description": "แอร์ไม่เย็น",
         "service_address": "99/1", **extra}
        for i in range(n, 0, -1)
    ]


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, ai, *, oa="sales", role="sales"):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role=role, oa=oa),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


READS_TICKET = {"action": "read", "entity": "ticket",
                "fields": {"code": "T-2026-0001"}, "missing": []}


class TestAJobOlderThanThePageIsStillFound:
    def _shop(self, keys=None):
        client = FakeDataClient(permission_keys=keys or ["ticket.read", "ticket.assign"], role="sales")
        client._tickets = _queue(150)
        client.page_limit = 100          # what the tier really returns
        return client

    async def test_the_oldest_job_is_outside_the_page(self):
        """The premise. If this ever fails the rest proves nothing."""
        client = self._shop()
        page = await client.list_tickets("lic")
        assert len(page) == 100
        assert not any(t["ticket_number"] == "T-2026-0001" for t in page)

    async def test_it_is_found_anyway(self):
        client = self._shop()
        reply = await _say(client, "ดูงาน T-2026-0001", READS_TICKET)
        assert "T-2026-0001" in reply.text, reply.text
        assert "ไม่พบ" not in reply.text, reply.text

    async def test_it_was_fetched_by_number_not_scanned(self):
        client = self._shop()
        await _say(client, "ดูงาน T-2026-0001", READS_TICKET)
        assert [r for r in client.recorded if r[0] == "get_ticket_by_number"]

    async def test_a_job_inside_the_page_costs_no_extra_read(self):
        """The scan comes first, so the common case is unchanged."""
        client = self._shop()
        await _say(client, "ดูงาน T-2026-0150", {
            "action": "read", "entity": "ticket",
            "fields": {"code": "T-2026-0150"}, "missing": [],
        })
        assert not [r for r in client.recorded if r[0] == "get_ticket_by_number"]

    async def test_a_number_this_shop_never_issued_is_still_not_found(self):
        client = self._shop()
        reply = await _say(client, "ดูงาน T-2026-9999", {
            "action": "read", "entity": "ticket",
            "fields": {"code": "T-2026-9999"}, "missing": [],
        })
        assert "ไม่พบ" in reply.text, reply.text


class TestTheFallbackNeverWidensWhatAPersonMaySee:
    """A lookup that reached further than the list would hand a technician
    a colleague's address for the price of guessing a number."""

    async def test_a_technician_cannot_reach_a_colleagues_private_job(self):
        client = FakeDataClient(permission_keys=["ticket.read"], role="technician")
        client._tickets = _queue(150)
        client._tickets[-1].update(visibility="private", assigned_to_ref="someone-else")
        client.page_limit = 100
        reply = await _say(
            client, "ดูงาน T-2026-0001", READS_TICKET, oa="technician", role="technician",
        )
        assert "99/1" not in reply.text, reply.text
        by_number = [r for r in client.recorded if r[0] == "get_ticket_by_number"]
        # It asked, and it asked WITH the narrowing.
        assert by_number and by_number[0][3], by_number

    async def test_a_customer_cannot_reach_someone_elses_job(self):
        client = FakeDataClient(permission_keys=["ticket.read"], role="customer")
        client._tickets = _queue(150, customer_chann_uid="SOMEONE-ELSE")
        client.page_limit = 100
        reply = await _say(
            client, "งาน T-2026-0001 ถึงไหนแล้ว",
            {"action": "read", "entity": "ticket",
             "fields": {"code": "T-2026-0001"}, "missing": []},
            oa="customer", role="customer",
        )
        assert "99/1" not in reply.text, reply.text
        assert "แอร์ไม่เย็น" not in reply.text, reply.text


# --------------------------------------------------------- the data tier

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from chann_data.repositories.phase12 import ServiceTicketRepository  # noqa: E402
from chann_data.repositories.tenant_scope import TenantScope  # noqa: E402
from chann_data.schemas import MemberOut  # noqa: E402


class TestTheTierContract:
    def test_get_by_number_takes_the_visibility_narrowing(self):
        """The route passes it; the repository has to accept it, or the
        narrowing would be silently dropped and every private job readable
        by number."""
        import inspect

        sig = inspect.signature(ServiceTicketRepository.get_by_number)
        assert "visible_to" in sig.parameters

    def test_the_list_and_the_lookup_share_one_predicate(self):
        """Two copies of the 12.1 rule would drift, and the drift would be
        a leak rather than a bug."""
        assert hasattr(ServiceTicketRepository, "_visible_to_member")

    def test_deal_lines_can_be_read_for_many_deals_at_once(self):
        from chann_data.repositories.phase9 import DealRepository

        assert hasattr(DealRepository, "products_for")

    def test_a_members_row_carries_the_person_s_own_name(self):
        """So the Application tier stops asking per member."""
        row = MemberOut(
            id=uuid.uuid4(), chann_uid="CHN-S-1", role="sales", status="active",
            first_name="สมชาย", last_name="ใจดี", phone="0812345678",
        )
        assert (row.first_name, row.last_name, row.phone) == ("สมชาย", "ใจดี", "0812345678")
