"""The guides stay true (owner, 3 Sep: "อัพเดตขั้นตอนวิธีใช้ตลอด").

Three ways a how-to goes stale, each caught here: a step tells the person
to type something chat no longer recognises; the printable handout in
docs/guides/ drifts from the data; the two image maps (application and
presentation) disagree.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from chann_app.services.guides import GUIDES, guide_images, render_help_text  # noqa: E402
from chann_app.services.chat import handle_chat_message  # noqa: E402
from test_phase6_chat import FakeDataClient, _ctx  # noqa: E402


def _all_phrases() -> set[str]:
    phrases: set[str] = set()
    for value in vars(chat).values():
        if isinstance(value, (tuple, list)) and value and all(isinstance(x, str) for x in value):
            phrases.update(value)
        elif isinstance(value, dict):
            for inner in value.values():
                if isinstance(inner, (tuple, list)) and inner and all(isinstance(x, str) for x in inner):
                    phrases.update(inner)
    return phrases


class TestCommandsAreReal:
    @pytest.mark.parametrize(
        "oa,key,command",
        [(oa, step["key"], cmd) for oa, guide in GUIDES.items() for step in guide["steps"] for cmd in step["commands"]],
    )
    def test_every_command_in_the_guide_is_a_chat_trigger(self, oa, key, command):
        phrases = _all_phrases()
        assert command in phrases or any(
            len(p) >= 3 and p in command for p in phrases
        ), f"{oa}/{key}: '{command}' matches no trigger — the guide is ahead of (or behind) the chat"


class TestHandoutsAreCurrent:
    def test_docs_guides_match_the_source(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dev" / "render-guides.py"), "--check"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_two_image_maps_agree(self):
        app = json.loads((ROOT / "application" / "chann_app" / "help_images.json").read_text(encoding="utf-8"))
        web = json.loads((ROOT / "presentation" / "lib" / "help-images.json").read_text(encoding="utf-8"))
        assert app.get("images") == web.get("images")

    def test_every_slot_has_a_place_in_the_map(self):
        app = json.loads((ROOT / "application" / "chann_app" / "help_images.json").read_text(encoding="utf-8"))
        slots = {step["image"] for guide in GUIDES.values() for step in guide["steps"]}
        assert slots <= set((app.get("images") or {}).keys())


class TestHelpInChat:
    @pytest.fixture(autouse=True)
    def _ai(self, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
        monkeypatch.setattr(settings, "openrouter_model", "test-model")

    def test_help_text_is_numbered_steps_with_something_to_type(self):
        text = render_help_text("technician", "th")
        assert "1. " in text and "4. " in text
        assert "▸ พิมพ์:" in text

    async def test_customer_help_comes_from_the_guide(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await handle_chat_message(
            client, message="วิธีใช้", ctx=_ctx(primary_role="customer", oa="customer"),
        )
        assert "ลงทะเบียนสินค้า" in reply.text and "แจ้งซ่อม" in reply.text
        assert reply.text.count("\n1. ") == 1

    async def test_technician_help_comes_from_the_guide(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        reply = await handle_chat_message(
            client, message="วิธีใช้", ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "เช็คอิน" in reply.text and "รับงาน" in reply.text  # the topics
        full = await handle_chat_message(
            client, message="วิธีใช้ทั้งหมด", ctx=_ctx(primary_role="technician", oa="technician"),
        )
        assert "ปฏิเสธงาน" in full.text and "ออกรายงาน" in full.text

    def test_no_image_means_no_image_message(self):
        assert guide_images("customer") == [] or all(u.startswith("https://") for u in guide_images("customer"))


class TestPermissionReplies:
    def test_the_no_permission_lead_says_who_to_ask(self):
        text = chat.SUGGEST_NO_PERMISSION_LEAD["th"]
        assert "เจ้าของร้าน" in text or "แอดมิน" in text
        assert "วิธีใช้" in text

    def test_the_refusal_points_at_the_guide_instead_of_listing_permissions(self):
        catalog = [
            {"key": "customer.read", "group": "customer", "label": {"th": "ดูลูกค้า"}},
            {"key": "customer.create", "group": "customer", "label": {"th": "สร้างลูกค้า"}},
            {"key": "ticket.read", "group": "ticket", "label": {"th": "ดูงานซ่อม"}},
        ]
        text = chat.suggest_what_you_can_do(["customer.read", "customer.create", "ticket.read"], catalog, "th")
        assert "• ดูลูกค้า" not in text and "สร้างลูกค้า" not in text and "📋" not in text
        assert "วิธีใช้" in text
