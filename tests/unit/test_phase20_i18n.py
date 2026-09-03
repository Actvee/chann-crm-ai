"""Phase 20 (PLAN_3OA B8) — the i18n pass: a LINE push reads in the
RECIPIENT's language, not the sender's; every notification the three-OA
flows raise carries an English variant; no page hardcodes Thai.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import live_chat, notify, onboarding, storefront  # noqa: E402
from chann_app.services.notify import send_notification  # noqa: E402
from test_live_chat import ChatFake  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


class Capturing(ChatFake):
    """Records message_en too — the base fake drops it."""

    async def create_notification(self, license_id, *, target_chann_uid, type, message,
                                  message_en=None, entity_type=None, entity_id=None,
                                  delivery_line=True, delivery_dashboard=True):
        self.recorded.append(("notification_en", target_chann_uid, type, message_en))
        return await super().create_notification(
            license_id, target_chann_uid=target_chann_uid, type=type, message=message,
            message_en=message_en, entity_type=entity_type, entity_id=entity_id,
            delivery_line=delivery_line, delivery_dashboard=delivery_dashboard,
        )


@pytest.fixture
def pushed(monkeypatch):
    sent: list[tuple] = []

    async def fake_push(oa, to, text, client=None):
        sent.append((oa, to, text))
        return ["mid"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    monkeypatch.setattr(live_chat, "push_text", fake_push)
    return sent


class TestRecipientLanguage:
    async def test_an_english_reader_gets_the_english_line(self, pushed):
        client = FakeDataClient()
        client._prefs = {"CHN-EN": {"language": "en"}}
        await send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-EN", target_line_user_id="line-en",
            type="ticket_created", message="แจ้งซ่อมใหม่ T-1", message_en="New repair request T-1",
            language="th",
        )
        assert pushed[-1][2] == "New repair request T-1"

    async def test_a_thai_reader_gets_thai_even_when_the_sender_reads_english(self, pushed):
        client = FakeDataClient()
        client._prefs = {"CHN-TH": {"language": "th"}}
        await send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-TH", target_line_user_id="line-th",
            type="ticket_created", message="แจ้งซ่อมใหม่ T-1", message_en="New repair request T-1",
            language="en",
        )
        assert pushed[-1][2] == "แจ้งซ่อมใหม่ T-1"

    async def test_without_an_english_variant_the_thai_text_stands(self, pushed):
        client = FakeDataClient()
        client._prefs = {"CHN-EN": {"language": "en"}}
        await send_notification(
            client, license_id=LICENSE_ID, target_chann_uid="CHN-EN", target_line_user_id="line-en",
            type="x", message="ไทยเท่านั้น", language="th",
        )
        assert pushed[-1][2] == "ไทยเท่านั้น"


class TestEveryFlowCarriesEnglish:
    async def test_live_chat_announcements(self, pushed):
        client = Capturing(role="customer", permission_keys=[])
        session, _, _ = await live_chat.start_session(
            client, license_id=LICENSE_ID, chann_uid="CHN-S-000001", display_name="Somchai",
        )
        session = await client.assign_chat_session(LICENSE_ID, session["id"], "m-cs")
        await live_chat.close_session(
            client, license_id=LICENSE_ID, session=session, by="customer", actor_chann_uid="CHN-S-000001",
        )
        client._sweep_result = {
            "escalated": [{"id": "cs-9", "license_id": LICENSE_ID, "customer_chann_uid": "CHN-S-000001",
                           "customer_name": "สมชาย", "assigned_to": "m-cs", "sla_deadline": None}],
            "timed_out": [],
        }
        await live_chat.sweep(client)
        rows = [r for r in client.recorded if r[0] == "notification_en"]
        assert rows and all(r[3] for r in rows), rows

    async def test_the_customers_own_pushes_follow_their_language(self, pushed):
        client = Capturing(role="customer", permission_keys=[])
        client._prefs = {"CHN-S-000001": {"language": "en"}}
        session, _, _ = await live_chat.start_session(client, license_id=LICENSE_ID, chann_uid="CHN-S-000001")
        await live_chat.agent_reply(
            client, license_id=LICENSE_ID, session=session, agent_chann_uid="CHN-CS", member_id="m-cs",
            text="15,900 baht",
        )
        assert pushed and "15,900 baht" in pushed[-1][2] and "end chat" in pushed[-1][2]

    async def test_onboarding_and_storefront(self, pushed):
        client = Capturing()
        client._members = [{"id": "m-1", "chann_uid": "CHN-OWNER", "role": "owner", "status": "active"}]
        client._profiles = {"CHN-C-1": {"first_name": "สมชาย", "phone": "0812345678"}}
        await onboarding.after_customer_linked(client, license_id=LICENSE_ID, chann_uid="CHN-C-1", display_name=None)
        await storefront.record_interest(
            client, chann_uid="CHN-C-1", license_id=LICENSE_ID, product_name="พัดลม", company_name="ร้าน",
        )
        rows = [r for r in client.recorded if r[0] == "notification_en"]
        assert len(rows) == 2 and all(r[3] for r in rows), rows


class TestNoHardcodedThaiInPages:
    THAI = re.compile(r"[฀-๿]")

    def test_every_page_reads_its_words_from_the_dictionary(self):
        offenders = []
        for path in sorted((ROOT / "presentation" / "app").rglob("*.tsx")):
            if "node_modules" in path.parts:
                continue
            in_block = False
            for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                s = line.strip()
                if in_block:
                    if "*/" in s:
                        in_block = False
                    continue
                if s.startswith("/*") or s.startswith("{/*"):
                    if "*/" not in s:
                        in_block = True
                    continue
                if s.startswith("//") or s.startswith("*"):
                    continue
                code = s.split("//", 1)[0]
                if self.THAI.search(code):
                    offenders.append(f"{path.relative_to(ROOT)}:{n}")
        assert not offenders, "\n".join(["hardcoded Thai outside the dictionary:", *offenders])
