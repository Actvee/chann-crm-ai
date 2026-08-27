"""Regression for a live production bug: EVERY call to
set_pending_intent/clear_pending_intent/set_last_customer_ref crashed,
silently, because _unwrap() called .json() unconditionally on the response
— including a bare 204 No Content, whose body is empty by definition.

This had been broken since pending_intent was first built; it only became
visible once the webhook-level exception logging (added in the same patch
as this fix) started logging the JSONDecodeError instead of the request
just dying with no trace at all. The stricter last_name+phone validation
rule made it manifest constantly, since far more messages now hit the
"missing fields -> set_pending_intent" path than before.

FakeDataClient (used throughout tests/unit/test_phase6_chat.py) never
exercises the real DataClient._unwrap at all — it's a hand-written stand-in
that returns plain Python objects directly, never touching httpx. These
tests exist specifically to cover the real HTTP-unwrapping code path that
the fake bypasses entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import settings  # noqa: E402
from chann_app.data_client import DataClient, DataTierError  # noqa: E402

settings.admin_secret = "test-secret"


def _client_for(handler) -> DataClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://data-tier.test")
    return DataClient(base_url="http://data-tier.test", secret="test-secret", client=http_client)


class TestUnwrapHandles204NoContent:
    async def test_a_bare_204_response_does_not_raise(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)  # no body at all, by HTTP definition

        client = _client_for(handler)
        # None of these three should raise — this is the exact crash from
        # the production log (json.decoder.JSONDecodeError: Expecting
        # value: line 1 column 1 (char 0)).
        await client.set_pending_intent(
            "CHN-S-000001", "sales", action="create", entity="customer",
            fields={}, missing=["last_name"],
        )
        await client.clear_pending_intent("CHN-S-000001", "sales")
        await client.set_last_customer_ref(
            "CHN-S-000001", "sales", customer_id="CUST-1", name="สมชาย",
        )

    async def test_a_204_with_empty_but_present_content_also_does_not_raise(self):
        """Some HTTP stacks send a zero-length body rather than omitting
        it entirely — resp.content == b"" rather than None. Both must be
        treated identically."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204, content=b"")

        client = _client_for(handler)
        await client.clear_pending_intent("CHN-S-000001", "sales")

    async def test_a_normal_200_json_response_still_unwraps_correctly(self):
        """The fix must not break the ordinary case — only 204/empty-body
        responses are special-cased."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"action": "create", "entity": "customer",
                                              "fields": {}, "missing": []})

        client = _client_for(handler)
        result = await client.get_pending_intent("CHN-S-000001", "sales")
        assert result == {"action": "create", "entity": "customer",
                          "fields": {}, "missing": []}

    async def test_an_error_response_still_raises_with_the_real_detail(self):
        """The fix must not swallow real errors — only success responses
        with no body are affected."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "already exists"})

        client = _client_for(handler)
        with pytest.raises(DataTierError) as exc_info:
            await client.set_last_customer_ref(
                "CHN-S-000001", "sales", customer_id="CUST-1", name="สมชาย",
            )
        assert exc_info.value.status_code == 409
