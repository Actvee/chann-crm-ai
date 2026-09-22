"""Round 20Z — someone who registers a unit is a customer, not a lead.

Owner, 22 ก.ย. 2569: "ลูกค้าที่เข้ามาผ่านการลงทะเบียนรับประกันสินค้า ควรจะ
กลายเป็นลูกค้าเลย ไม่ใช่ลูกค้ามุ่งหวัง".

Until now a warranty claim only wrote the LINE identity onto the unit.
The shop's own customer row — when there was one at all — stayed
`lead`, the stage that means "someone who might buy", although the
person was holding a machine the shop had sold and recorded. The row
also never learned which customer record the unit belonged to.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402


class _Fake(FakeDataClient):
    """The Data Tier's side of a claim, as it answers from round 20Z on:
    the unit carries the customer record it now belongs to, and says
    whether the shop gained a new one."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The shop's staff, so the announcement has somebody to reach.
        self._members = [
            {"id": "m-owner", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"},
            {"id": "m-tech", "chann_uid": "CHN-TECH", "role": "technician", "status": "active"},
        ]
        self._line_targets = {"CHN-OWNER": "line-owner", "CHN-TECH": "line-tech"}
        self._claim_answer: dict = {
            "id": "W1", "warranty_number": "WR-2026-0001", "serial_number": "SN123",
            "product_name": "แอร์", "customer_chann_uid": "CHN-S-000001",
            "contact_id": "CUST-1", "contact_code": "C-2026-0001", "contact_name": "สมชาย ใจดี",
            "warranty_start": "2026-09-22", "warranty_end": "2027-09-22", "status": "active",
            "customer_created": True,
        }

    async def claim_warranty(self, license_id, payload, actor_id=None):
        self.recorded.append(("claim_warranty", license_id, payload.get("serial_number")))
        return dict(self._claim_answer)


@pytest.fixture
def told(monkeypatch):
    """Every notification the shop is sent, at the one seam they go through."""
    from chann_app.services import notify

    sent: list[tuple] = []

    async def fake_push(oa, to, text, client=None, quick_reply=None):
        sent.append((oa, to, text))
        return ["mid"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    return sent


class TestTheShopHearsAboutTheNewCustomer:
    async def test_a_claim_that_created_a_record_is_announced(self, told):
        client = _Fake(role="customer", permission_keys=["warranty.create"])
        reply = await handle_chat_message(
            client, message="ลงทะเบียนสินค้า SN123", ctx=_ctx(primary_role="customer", oa="customer"),
        )
        assert "SN123" in reply.text or "แอร์" in reply.text
        assert any(r[0] == "claim_warranty" for r in client.recorded)
        announcements = [t for t in told if "ลงทะเบียนสินค้า" in t[2]]
        assert announcements, told
        # The owner hears; the technician is not the one who keeps the list.
        assert [t[1] for t in announcements] == ["line-owner"]
        said = announcements[0][2]
        assert "สมชาย ใจดี" in said and "C-2026-0001" in said and "SN123" in said
        assert "ลูกค้า" in said

    async def test_nothing_is_announced_when_the_record_was_already_there(self, told):
        client = _Fake(role="customer", permission_keys=["warranty.create"])
        client._claim_answer["customer_created"] = False
        await handle_chat_message(
            client, message="ลงทะเบียนสินค้า SN123", ctx=_ctx(primary_role="customer", oa="customer"),
        )
        assert not [t for t in told if "ลงทะเบียนสินค้า" in t[2]]

    async def test_the_customer_is_still_answered_when_the_announcement_fails(self, monkeypatch):
        """The claim is the point; telling the shop is a courtesy."""
        from chann_app.services import onboarding

        async def boom(*a, **k):
            raise RuntimeError("no members")

        monkeypatch.setattr(onboarding, "_tell_the_shop", boom)
        client = _Fake(role="customer", permission_keys=["warranty.create"])
        reply = await handle_chat_message(
            client, message="ลงทะเบียนสินค้า SN123", ctx=_ctx(primary_role="customer", oa="customer"),
        )
        assert reply.text and "WR-2026-0001" in reply.text
