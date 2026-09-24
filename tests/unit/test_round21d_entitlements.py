"""Round 21D — the Application's copies agree with the Data tier's matrix,
and every permission key is classified (spec §4.1, §10)."""
from __future__ import annotations

import pytest

from chann_app.services import entitlements as E
from chann_data import plans
from chann_data.permissions import PERMISSION_KEYS
from plan_fixtures import plan_payload, plan_view


class TestTheCopiesAgree:
    def test_labels_cover_exactly_the_ten_keys(self):
        assert set(E.FEATURE_LABELS) == set(plans.ENTITLEMENT_KEYS)

    def test_the_english_labels_are_the_data_tiers(self):
        assert {k: v["en"] for k, v in E.FEATURE_LABELS.items()} == plans.FEATURE_LABELS_EN

    def test_plan_labels_and_order(self):
        assert E.PLAN_ORDER == plans.PLAN_CODES
        assert E.PLAN_LABELS == {code: plans.PLANS[code].label for code in plans.PLAN_CODES}

    def test_the_assignment_rule_scope_map_is_the_data_tiers(self):
        assert E.ASSIGNMENT_RULE_SCOPE_FEATURE == plans.ASSIGNMENT_RULE_SCOPE_FEATURE

    def test_the_fail_open_plan_is_pro_exactly(self):
        assert E.UNKNOWN_PLAN == plans.resolve("pro")


class TestEveryPermissionKeyIsClassified:
    def test_families_and_always_on_partition_the_catalogue(self):
        gated = {k for k in PERMISSION_KEYS if E.feature_of(k) is not None}
        assert gated.isdisjoint(E.ALWAYS_ON_PERMISSIONS)
        assert gated | E.ALWAYS_ON_PERMISSIONS == set(PERMISSION_KEYS)

    def test_every_gated_family_names_a_real_feature(self):
        assert set(E.PERMISSION_FEATURE.values()) <= set(plans.FEATURE_KEYS)
        assert set(E.NAMED_FEATURE_CHECKS.values()) <= set(plans.FEATURE_KEYS)

    @pytest.mark.parametrize("key,feature", [
        ("ticket.read", "feature.service"), ("service_report.create", "feature.service"),
        ("approval.approve", "feature.service"), ("warranty.create", "feature.warranty"),
        ("chat_session.reply", "feature.live_chat"), ("customer.read", None), ("setting.manage", None),
    ])
    def test_feature_of(self, key, feature):
        assert E.feature_of(key) == feature

    def test_named_checks_win_over_the_key(self):
        assert E.feature_for_intent("create", "api_key", "setting.manage") == "feature.external_api"
        assert E.feature_for_intent("read", "api_key", "setting.manage") is None
        assert E.feature_for_intent("send", "invoice", "invoice.update") == "feature.customer_line_link"
        assert E.feature_for_intent("read", "ticket", "ticket.read") == "feature.service"


class TestPlanView:
    def test_no_payload_behaves_as_pro_and_says_it_is_a_guess(self):
        view = E.PlanView.from_payload(None)
        assert view.code == "pro" and view.known is False
        assert view.has("feature.service") and not view.has("feature.external_api")

    def test_a_payload_without_features_is_pro_not_lock_all(self):
        """Final fix (Task 5): a known code whose feature list is missing is
        an unreadable payload — the documented Pro fallback, not a shop
        with every feature locked."""
        view = E.PlanView.from_payload({"code": "enterprise", "label": "Enterprise"})
        assert view == E.PlanView.unknown()
        assert view.has("feature.service") and view.known is False
        # An EMPTY list is a real answer (Starter's), and is kept.
        assert not E.PlanView.from_payload(plan_payload("starter")).has("feature.service")

    def test_starter(self):
        view = plan_view("starter")
        assert not view.has("feature.service") and not view.has(E.AI_REPORTS)
        assert view.has(E.MEMBERS)
        assert view.min_plan("feature.external_api") == "enterprise" and view.min_label("feature.service") == "Pro"

    def test_an_override_of_zero_is_not_a_plan_lock(self):
        assert E.PlanView.from_payload(plan_payload("pro", ai_override=0)).has(E.AI_REPORTS)

    def test_effective_keys_subtract_what_the_plan_locks(self):
        keys, locked = E.effective_keys({"ticket.read", "customer.read", "warranty.read"}, plan_view("starter"))
        assert keys == {"customer.read"} and locked == {"ticket.read", "warranty.read"}

    def test_a_whole_road_locks_every_key(self):
        keys, locked = E.effective_keys({"ticket.read", "invoice.read"}, plan_view("starter"),
                                        whole_road="feature.customer_line_link")
        assert keys == frozenset() and locked == {"ticket.read", "invoice.read"}

    def test_the_refusal_shape(self):
        exc = E.plan_required("feature.service", plan_view("starter"))
        assert exc.status_code == 403
        assert exc.detail == {
            "error": "plan_required", "feature": "feature.service", "plan": "starter", "min_plan": "pro",
            "message": "Service jobs and technicians: included from the Pro plan. This shop is on Starter.",
        }
        # The Data tier says the same sentence for the same refusal.
        assert exc.detail == plans.PlanFeatureLocked("feature.service", "starter").detail()


class TestSalesContact:
    def test_unset_is_none(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "")
        assert E.sales_contact() is None

    def test_label_and_url(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "LINE @channcrm|https://line.me/R/ti/p/@channcrm")
        assert E.sales_contact() == {"label": "LINE @channcrm", "url": "https://line.me/R/ti/p/@channcrm"}

    def test_a_url_that_is_not_https_is_dropped_but_the_label_stays(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "โทร 02-123-4567|javascript:alert(1)")
        assert E.sales_contact() == {"label": "โทร 02-123-4567", "url": ""}
