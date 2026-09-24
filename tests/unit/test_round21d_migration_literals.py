"""Round 21D — migration 0039 freezes the plan codes and member limits (it
must not import the Data tier), so a test keeps the copies equal; and the
member-limit assertion is a pure function, tested here without a database."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from chann_data import plans

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "database/alembic/versions/0039_license_plan.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0039", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ids():
    m = _migration()
    assert m.revision == "0039_license_plan" and m.down_revision == "0038_deal_closed_at"


def test_the_frozen_copies_equal_the_matrix():
    m = _migration()
    assert m.PLAN_CODES == plans.PLAN_CODES
    assert m.MEMBER_LIMITS == tuple((code, plans.PLANS[code].members) for code in plans.PLAN_CODES)


def test_a_shop_within_its_limit_is_left_alone():
    assert _migration().plan_moves([("L1", "pro", 15), ("L2", "enterprise", 50)]) == []


def test_a_shop_over_its_limit_moves_to_the_smallest_plan_that_fits():
    moves = _migration().plan_moves([("L1", "pro", 16), ("L2", "pro", 51), ("L3", "starter", 6)])
    assert moves == [("L1", "pro", 16, "enterprise"), ("L2", "pro", 51, "enterprise_plus"),
                     ("L3", "starter", 6, "pro")]
    for _id, code, people, target in moves:
        assert target == plans.smallest_plan_for(people, at_least=code)


def test_enterprise_plus_is_never_over():
    assert _migration().plan_moves([("L1", "enterprise_plus", 5000)]) == []
