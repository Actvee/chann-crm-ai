"""Round 21D — no per-plan rich menu (spec §5.8, owner decision Q6), which is
safe only if every tile, on a Starter shop, either works or refuses on plan.
Walks the REAL tile table, so a tile added later is covered."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

ROOT = Path(__file__).resolve().parents[2]


def _tiles():
    spec = importlib.util.spec_from_file_location("richmenu_generate", ROOT / "scripts/richmenu/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TILES


TILES = _tiles()
SUGGEST = json.dumps({"action": "suggest", "entity": None, "fields": {}, "missing": []})
#: The Sales tiles that ARE the service feature on a Starter shop.
SERVICE_TILES = {"รายการรออนุมัติ", "ทีมช่าง"}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _texts(oa: str) -> list[str]:
    return [tile[3]["text"] for page in ("main", "more") for tile in TILES[oa][page] if tile[3]["type"] == "message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _texts("sales"))
async def test_a_sales_tile_on_starter(text):
    client = FakeDataClient(role="owner", permission_keys=[
        "customer.read", "deal.read", "followup.read", "product.read", "team.manage", "setting.manage",
        "ticket.read", "approval.view"])
    client._is_owner = True
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("starter")
    reply = await handle_chat_message(client, message=text, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=_ai(SUGGEST)))
    if text in SERVICE_TILES:
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง»"), (text, reply.text)
    else:
        assert "🔒" not in reply.text, (text, reply.text)


def test_the_uri_tiles_open_pages_the_nav_model_can_lock():
    """The one Sales URI tile into a locked area (the chat inbox) opens a
    page whose nav entry carries its feature, so the dashboard — not the
    rich menu — draws the lock (Task 12's navState)."""
    nav = (ROOT / "presentation/app/liff/_nav-model.tsx").read_text(encoding="utf-8")
    checked = 0
    for page in ("main", "more"):
        for tile in TILES["sales"][page]:
            action = tile[3]
            if action["type"] == "uri" and action["uri"].endswith("/chats"):
                line = next(l for l in nav.splitlines() if 'key: "chats"' in l and "/liff/sales/" in l)
                assert 'feature: "feature.live_chat"' in line
                checked += 1
    assert checked, "no Sales URI tile opens the chat inbox any more — re-point this test"
