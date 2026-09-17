"""Round 20f — giving a job back, a diary page, and a deal that confirms
the customer.

Owner, 17 ก.ย. 2569:
  · "ถ้ารับงานไปแล้วยังไม่เช็คอิน ควรจะสามารถคืนงานได้เพื่อไม่สะดวก หรือ
     ตอนลูกค้าขอเลื่อนแล้วต้องเปลี่ยนช่างที่ไป"
  · "หน้ารวมนัดหมายที่เสนอมา ทำได้เลย"
  · "ถ้าลูกค้าตกลงสร้าง Deal จะต้องกลายเป็น contact auto ไปเลย"

The Data tier already allowed handing back a job accepted but not yet
checked in — `reject()` refuses only completed, cancelled and in_progress
— and CS could already reassign an accepted one. What was missing was any
way to ASK: chat sent "คืนงาน" to the shop's release handler, which wants
ticket.assign, so a technician was told they lacked a permission they
should never hold; and the technician's screen offered decline only on a
job not yet accepted.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[2]

ACCEPTED = {
    "id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
    "accept_status": "accepted", "assigned_to_ref": "member-1",
    "assigned_target_type": "technician", "customer_name": "สมชาย",
    "issue_description": "แอร์ไม่เย็น", "service_address": "99/1",
    "scheduled_date": "2026-09-20",
}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _tech(**extra) -> FakeDataClient:
    client = FakeDataClient(
        permission_keys=["ticket.read", "ticket.claim", "ticket.update"], role="technician",
    )
    client._tickets = [dict(ACCEPTED)]
    for k, v in extra.items():
        setattr(client, k, v)
    return client


async def _say(client, message, ai):
    return await handle_chat_message(
        client, message=message, ctx=_ctx(primary_role="technician", oa="technician"),
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


#: What the model returns for "คืนงาน T-…" — the same verb the shop uses
#: for "เปิดให้ช่างรับ", which is why it arrives as `release`.
RELEASE = {"action": "release", "entity": "ticket",
           "fields": {"code": "T-2026-0001"}, "missing": []}


class TestATechnicianCanGiveAJobBack:
    async def test_returning_a_job_is_not_a_permission_wall(self):
        reply = await _say(_tech(), "คืนงาน T-2026-0001", RELEASE)
        assert "ไม่มีสิทธิ์" not in reply.text, reply.text
        assert "มอบหมายใบงาน" not in reply.text, reply.text

    async def test_it_asks_why_before_doing_it(self):
        """The shop has to know, and the reason is what they act on."""
        client = _tech()
        reply = await _say(client, "คืนงาน T-2026-0001", RELEASE)
        assert "T-2026-0001" in reply.text, reply.text
        assert [w for w in client.recorded if w[0] == "set_pending_intent"], client.recorded
        assert not [w for w in client.recorded if w[0] == "reject_ticket"], (
            "nothing is handed back until the reason is given"
        )

    async def test_the_shop_still_opens_a_job_to_everyone_from_its_own_line(self):
        """The same word from the SALES line keeps its old meaning."""
        client = FakeDataClient(
            permission_keys=["ticket.read", "ticket.assign", "ticket.update"], role="sales",
        )
        client._tickets = [dict(ACCEPTED, accept_status="pending", assigned_to_ref=None,
                                status="open")]
        reply = await handle_chat_message(
            client, message="เปิดให้ช่างรับ T-2026-0001",
            ctx=_ctx(primary_role="sales", oa="sales"),
            ai_client=httpx.AsyncClient(transport=_ai(json.dumps(RELEASE))),
        )
        assert "ไม่มีสิทธิ์" not in reply.text, reply.text
        assert [w for w in client.recorded if w[0] == "release_ticket"], client.recorded


class TestTheScreenOffersItToo:
    def _source(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_an_accepted_job_has_a_hand_back_button(self):
        source = self._source("presentation/app/liff/technician/TechnicianHome.tsx")
        assert "technician.handBack" in source

    def test_the_reason_form_is_shared_by_both_lists(self):
        """Offered and accepted both need it; two copies drift."""
        source = self._source("presentation/app/liff/technician/TechnicianHome.tsx")
        assert source.count("{declineForm(ticket)}") == 2
        assert "function declineForm(" in source

    def test_it_is_gone_once_the_job_is_under_way(self):
        """After check-in the Data tier refuses, so the button must not be
        offered — a button that always fails is worse than no button."""
        source = self._source("presentation/app/liff/technician/TechnicianHome.tsx")
        start = source.index("technician.handBack")
        before = source[max(0, start - 700):start]
        assert 'ticket.status !== "in_progress"' in before, before[-300:]


class TestTheDiaryPage:
    def _source(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_the_page_exists_and_is_on_the_menu(self):
        assert (ROOT / "presentation/app/liff/sales/appointments/page.tsx").exists()
        nav = self._source("presentation/app/liff/_nav-model.tsx")
        assert "/liff/sales/appointments" in nav

    def test_it_groups_by_when_rather_than_listing_everything(self):
        source = self._source("presentation/app/liff/sales/appointments/Appointments.tsx")
        for key in ("overdue", "today", "ahead"):
            assert f'key: "{key}"' in source, key

    def test_a_row_can_be_settled_from_the_page(self):
        source = self._source("presentation/app/liff/sales/appointments/Appointments.tsx")
        assert "status_value=" in source
        assert "markDone" in source and "markCancelled" in source

    def test_a_row_links_to_the_record_it_belongs_to(self):
        source = self._source("presentation/app/liff/sales/appointments/Appointments.tsx")
        assert "/liff/sales/deals/" in source and "/liff/sales/customers/" in source

    @pytest.mark.parametrize("key", ["title", "overdue", "markDone", "cancelKeeps"])
    def test_both_languages_carry_its_words(self, key):
        for path in ("presentation/lib/i18n/th.ts", "presentation/lib/i18n/en.ts"):
            assert f"{key}:" in self._source(path), (key, path)
