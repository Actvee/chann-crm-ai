"""Round 21D — the external API is an Enterprise feature (spec §5.5). The
key checks stay first: a bad key is 401 whatever the plan, so an attacker
learns nothing about the shop's plan."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from chann_app import routers_ext
from chann_app.routers_admin import get_data_client
from plan_fixtures import plan_payload
from test_phase6_chat import LICENSE_ID, FakeDataClient
from test_round21b_ext_api import RAW, RESOLVED, _ResolvingFake


@pytest.fixture(autouse=True)
def _clean():
    yield
    routers_ext.ext_app.dependency_overrides.clear()


def _ext_with(plan_code: str | None):
    resolved = copy.deepcopy(RESOLVED)
    if plan_code is None:
        resolved.pop("plan", None)
    else:
        resolved["plan"] = plan_payload(plan_code)
    fake = _ResolvingFake(resolved=resolved, role="sales", permission_keys=["customer.read"])

    async def override():
        yield fake

    routers_ext.ext_app.dependency_overrides[get_data_client] = override
    return TestClient(routers_ext.ext_app)


@pytest.mark.parametrize("plan_code", ["starter", "pro"])
def test_below_enterprise_every_route_is_403_plan_required(plan_code):
    http = _ext_with(plan_code)
    for path in ("/me", "/customers?limit=1"):
        out = http.get(path, headers={"Authorization": f"Bearer {RAW}"})
        assert out.status_code == 403, (path, out.text)
        assert out.json()["error"]["code"] == "plan_required"
        assert "Enterprise" in out.json()["error"]["message"]


@pytest.mark.parametrize("plan_code", ["enterprise", "enterprise_plus"])
def test_enterprise_and_up_pass(plan_code):
    out = _ext_with(plan_code).get("/me", headers={"Authorization": f"Bearer {RAW}"})
    assert out.status_code == 200, out.text


def test_an_older_data_tier_with_no_plan_is_pro_and_refused():
    out = _ext_with(None).get("/me", headers={"Authorization": f"Bearer {RAW}"})
    assert out.status_code == 403


def test_a_bad_key_is_401_before_any_plan_is_read():
    http = _ext_with("starter")
    assert http.get("/me").status_code == 401
    assert http.get("/me", headers={"Authorization": "Bearer chann_live_short"}).status_code == 401
