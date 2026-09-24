"""Round 21D — the plan matrix IS the owner's table.

The owner uploaded the sales-plan comparison (24 ก.ย. 2569) and asked the
system to enforce it. This test reads that HTML and compares it, cell by
cell, with `chann_data.plans`. If marketing edits the table, this fails
until the code is changed on purpose (spec §11.1).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from chann_data import plans

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/superpowers/specs/2026-09-24-sales-plans-source.html"
COLUMNS = ("starter", "pro", "enterprise", "enterprise_plus")
ROW_RE = re.compile(r'<div class="row[^"]*">.*?</div></div>', re.S)
CELL_RE = re.compile(r'<div class="([^"]*)">(.*?)</div>', re.S)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _table() -> list[tuple[str, tuple[str, str, str, str]]]:
    """(label, four cell texts) per row, in table order. The first cell
    match swallows the category div (its text is the category); the users
    row has an empty description, so its label is the category without
    the emoji."""
    rows = []
    for row in ROW_RE.findall(SOURCE.read_text(encoding="utf-8")):
        cells = CELL_RE.findall(row)
        category, description = _text(cells[0][1]), _text(cells[1][1])
        label = description or category.split(" ", 1)[1]
        rows.append((label, tuple(_text(c[1]) for c in cells[2:6])))
    return rows


TABLE = _table()


def _with(key: str) -> set[str]:
    return {code for code in COLUMNS if key in plans.PLANS[code].features}


class TestEveryRowIsMapped:
    def test_the_table_has_the_41_rows_the_spec_counted(self):
        assert len(TABLE) == 41

    def test_row_map_names_the_same_rows_in_the_same_order(self):
        assert [label for label, _ in TABLE] == [label for label, _ in plans.ROW_MAP]

    def test_every_key_in_the_row_map_is_a_known_key_or_always_on(self):
        for _label, key in plans.ROW_MAP:
            assert key == plans.ALWAYS_ON or key in plans.ENTITLEMENT_KEYS, key

    def test_every_entitlement_key_appears_in_the_table(self):
        mapped = {key for _label, key in plans.ROW_MAP}
        assert set(plans.ENTITLEMENT_KEYS) <= mapped


class TestTheCellsAgree:
    @pytest.mark.parametrize("index", range(41))
    def test_one_row(self, index):
        label, cells = TABLE[index]
        key = plans.ROW_MAP[index][1]
        ticks = {code for code, cell in zip(COLUMNS, cells) if cell == "✓"}
        if key == plans.ALWAYS_ON:
            assert cells == ("✓", "✓", "✓", "✓"), label
        elif key == plans.AI_REPORTS:
            assert cells == ("—", "(30 ครั้ง/เดือน)", "(100 ครั้ง/เดือน)", "(เพิ่มโควต้าได้)")
            assert [plans.PLANS[c].ai_reports_per_month for c in COLUMNS[:3]] == [0, 30, 100]
            plus = plans.PLANS["enterprise_plus"]
            assert plus.ai_reports_per_month == 100 and plus.quota_top_up is True
        elif key == plans.MEMBERS:
            assert cells == ("5 คน", "15 คน", "50 คน", "มากกว่า 50 คน")
            assert [plans.PLANS[c].members for c in COLUMNS] == [5, 15, 50, None]
        elif key == "feature.custom_roles":
            # Row 36, the one mixed row: standard roles are ✓ everywhere,
            # "กำหนดเองได้" is the customising half the key covers.
            assert cells == ("✓", "(กำหนดเองได้)", "(กำหนดเองได้)", "(กำหนดเองได้)")
            assert _with(key) == {"pro", "enterprise", "enterprise_plus"}
        else:
            assert ticks == _with(key), (label, key)


class TestTheOwnersDecisions:
    """Spec §13, 24 ก.ย. 2569 — what the table does not say by itself."""

    def test_the_top_up_is_honoured_on_pro_and_up_and_never_on_starter(self):
        assert [plans.PLANS[c].quota_top_up for c in COLUMNS] == [False, True, True, True]

    def test_an_override_moves_the_allowance_only_where_it_is_honoured(self):
        assert plans.ai_allowance(plans.PLANS["pro"], 45) == 45
        assert plans.ai_allowance(plans.PLANS["pro"], None) == 30
        assert plans.ai_allowance(plans.PLANS["pro"], "") == 30
        assert plans.ai_allowance(plans.PLANS["pro"], 0) == 0      # 0 is a value, not "unset"
        assert plans.ai_allowance(plans.PLANS["starter"], 45) == 0
        assert plans.ai_allowance(plans.PLANS["enterprise_plus"], 400) == 400

    def test_the_default_plan_is_pro(self):
        assert plans.DEFAULT_PLAN == "pro"
        assert plans.plan(None).code == "pro"
        assert plans.plan("nonsense").code == "pro"


class TestResolve:
    def test_min_plan_is_computed_from_rank(self):
        assert plans.min_plan("feature.service") == "pro"
        assert plans.min_plan("feature.external_api") == "enterprise"
        assert plans.min_plan(plans.AI_REPORTS) == "pro"
        assert plans.min_plan(plans.MEMBERS) is None

    def test_starter_payload(self):
        out = plans.resolve("starter")
        assert out["code"] == "starter" and out["label"] == "Starter" and out["rank"] == 0
        assert out["features"] == []
        assert out["locked"][plans.AI_REPORTS] == "pro"
        assert out["locked"]["feature.external_api"] == "enterprise"
        assert out["limits"] == {"members": 5, "ai_reports_per_month": 0}

    def test_pro_payload_is_the_fail_open_shape(self):
        out = plans.resolve("pro")
        assert out["locked"] == {"feature.external_api": "enterprise",
                                 "feature.multi_level_approval": "enterprise"}
        assert out["limits"] == {"members": 15, "ai_reports_per_month": 30}

    def test_an_override_of_zero_on_pro_is_used_up_not_locked(self):
        out = plans.resolve("pro", ai_override=0)
        assert out["limits"]["ai_reports_per_month"] == 0
        assert plans.AI_REPORTS not in out["locked"]

    def test_smallest_plan_for(self):
        assert plans.smallest_plan_for(5) == "starter"
        assert plans.smallest_plan_for(16) == "enterprise"
        assert plans.smallest_plan_for(51) == "enterprise_plus"
        assert plans.smallest_plan_for(3, at_least="pro") == "pro"


class TestTheRefusals:
    def test_plan_required(self):
        body = plans.PlanFeatureLocked("feature.service", "starter").detail()
        assert body == {
            "error": "plan_required", "feature": "feature.service", "plan": "starter",
            "min_plan": "pro",
            "message": "Service jobs and technicians: included from the Pro plan. This shop is on Starter.",
        }

    def test_member_limit(self):
        body = plans.MemberLimitReached(5, "starter", license_id="L1").detail()
        assert body["error"] == "member_limit_reached" and body["limit"] == 5
        assert body["plan"] == "starter" and body["license_id"] == "L1"

    def test_the_downgrade_refusal_says_how_many_to_inactivate(self):
        body = plans.PlanDowngradeRefused(9, 5, "starter").detail()
        assert body == {
            "error": "plan_member_limit", "members": 9, "limit": 5, "inactivate": 4,
            "plan": "starter", "plan_label": "Starter",
            "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter",
        }

    def test_unknown_plan_is_a_value_error(self):
        assert issubclass(plans.UnknownPlan, ValueError)
