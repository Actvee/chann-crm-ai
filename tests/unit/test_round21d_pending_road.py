"""Round 21D final fix (Task 10) — a typed question while a pending action
is held is READ by the model, not answered by a keyword.

Before: with any pending held, `_deterministic_reason` skipped the model
(road=pending) and the sales price arm (`_asks_price`, "เท่าไหร่") answered
"เหลือเครดิตรายงาน AI เท่าไหร่" with the product list. A word may decline,
never act (docs/MODEL_FIRST.md): the arm now runs only after the model had
its read, and the held sentence reaches the tail's model road, which reads
it with the pending in view and leaves the pending alive.

Readings (scripts/dev/ask-model.py, and parse_intent with the pending in
view, DEV's model google/gemini-3.1-flash-lite, 24 ก.ย. 2569 — recorded in
final-fix-report.md):
  เหลือเครดิตรายงาน AI เท่าไหร่ -> {"action": "read", "entity": "plan"}
  ราคาแอร์เท่าไหร่            -> {"action": "read", "entity": "product", "fields": {"target_name": "แอร์"}}
"""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from chann_data.permissions import PERMISSION_KEYS
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ctx

CREDIT = "เหลือเครดิตรายงาน AI เท่าไหร่"
READ_PLAN = {"action": "read", "entity": "plan", "fields": {}, "missing": []}
PRICE = "ราคาแอร์เท่าไหร่"
READ_PRODUCT = {"action": "read", "entity": "product", "fields": {"target_name": "แอร์"}, "missing": []}

HELD = {
    # What the Pro scenario leaked across OAs in the fake (task-10 report).
    "a customer's message": {"action": "report", "entity": "pending_customer_message",
                             "fields": {"text": "แอร์ไม่เย็น"}, "missing": []},
    # The same OA, a form waiting for a phone.
    "a half-made customer": {"action": "create", "entity": "customer",
                             "fields": {"full_name": "สมชาย"}, "missing": ["phone"]},
}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


#: Two products, so the model road's reading (target_name แอร์: the one
#: product) can be told from the price arm's catalogue (both).
PRODUCTS = [
    {"id": "p1", "product_id": "P-0001", "product_name": "แอร์ Daikin 12000 BTU", "unit_price": "15900.00",
     "category": "แอร์", "is_active": True},
    {"id": "p2", "product_id": "P-0002", "product_name": "พัดลมตั้งพื้น", "unit_price": "990.00",
     "category": "พัดลม", "is_active": True},
]


async def _say(message: str, reading: dict, *, pending: dict | None, products: list | None = None):
    client = FakeDataClient(permission_keys=sorted(PERMISSION_KEYS), role="owner",
                            pending_intent=dict(pending) if pending else None)
    client._is_owner = True
    if products is not None:
        client._products = [dict(p) for p in products]
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("pro")
    calls: list[int] = []

    def model(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(reading)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    reply = await handle_chat_message(client, message=message, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))
    return reply, client, calls


@pytest.mark.parametrize("held", sorted(HELD))
async def test_the_credit_question_is_the_plan_reply_and_the_pending_stays(held):
    reply, client, calls = await _say(CREDIT, READ_PLAN, pending=HELD[held])
    assert calls, "the model must read a typed question even while a pending is held"
    assert reply.text.startswith("แพ็กเกจของร้าน: Pro"), reply.text
    assert "เครดิตรายงาน AI เดือนนี้ " in reply.text
    assert "ยังไม่มีสินค้า" not in reply.text                  # not the product list
    assert reply.intent == {"action": "read", "entity": "plan"}
    assert client._pending == HELD[held]                      # kept alive, untouched


@pytest.mark.parametrize("held", sorted(HELD))
async def test_a_price_question_is_read_too(held):
    """The price arm no longer answers past the model: the same words reach
    the model road and its product reading; the pending stays."""
    reply, client, calls = await _say(PRICE, READ_PRODUCT, pending=HELD[held], products=PRODUCTS)
    assert calls
    # The model road's answer to read/product "แอร์": that one product —
    # not the price arm's whole catalogue (fix round 1, review Minor 3).
    assert "แอร์ Daikin 12000 BTU" in reply.text and "15,900.00" in reply.text, reply.text
    assert "พัดลมตั้งพื้น" not in reply.text, reply.text
    assert client._pending == HELD[held]


SHRUG = {"action": "suggest", "suggestions": [], "entity": None, "fields": {}, "missing": []}
HELP_MENU = {"action": "help", "entity": "help_menu", "fields": {}, "missing": []}


@pytest.mark.parametrize("pending", [None, HELP_MENU], ids=["nothing held", "a help menu held"])
async def test_after_the_model_shrugs_the_price_arm_still_answers(pending):
    """Unchanged road: when the model HAD its read and shrugged (suggest),
    the table may answer — the catalogue with prices. A help menu does not
    skip the model (`_deterministic_reason`), so it behaves as nothing held
    (fix round 1, review Minor 1: the arm keys on the model's read, not on
    whether a pending exists)."""
    reply, _client, calls = await _say(PRICE, SHRUG, pending=pending, products=PRODUCTS)
    assert calls
    assert "แอร์ Daikin 12000 BTU" in reply.text and "พัดลมตั้งพื้น" in reply.text, reply.text


async def test_with_nothing_in_the_catalogue_the_arm_says_so():
    reply, _client, calls = await _say(PRICE, SHRUG, pending=None)
    assert calls and reply.text == "ยังไม่มีสินค้าในระบบ"
