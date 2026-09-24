"""Round 21C — a report filter may name more than one value.

The prompt has promised this since round 20V ("งานค้าง = status open or
assigned or in_progress", "ยอดค้าง = issued or partially_paid") while the
engine could only ever compare to one, so both answers silently
undercounted (diagnosis §1, "two more defects").
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))

from chann_app.services import reports_ai  # noqa: E402
from chann_data.repositories.phase17 import ReportSpecInvalid, validate_spec  # noqa: E402


class TestTheDataTierValidator:
    def test_a_list_of_statuses_is_kept_as_a_list(self):
        spec = validate_spec({"entity": "tickets", "metric": "count",
                              "filter": {"status": ["open", "assigned", "in_progress"]}})
        assert spec["filter"] == {"status": ["open", "assigned", "in_progress"]}

    def test_one_value_is_still_a_scalar(self):
        spec = validate_spec({"entity": "tickets", "metric": "count",
                              "filter": {"status": "open"}})
        assert spec["filter"] == {"status": "open"}

    def test_duplicates_collapse_and_order_is_kept(self):
        spec = validate_spec({"entity": "invoices", "metric": "sum", "field": "outstanding",
                              "filter": {"status": ["issued", "issued", "partially_paid"]}})
        assert spec["filter"] == {"status": ["issued", "partially_paid"]}

    def test_a_bad_value_inside_a_good_list_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="is not a valid status"):
            validate_spec({"entity": "tickets", "metric": "count",
                           "filter": {"status": ["open", "จบแล้ว"]}})

    def test_an_empty_list_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="at least one value"):
            validate_spec({"entity": "tickets", "metric": "count", "filter": {"status": []}})

    def test_more_than_ten_values_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="at most 10"):
            validate_spec({"entity": "tickets", "metric": "count",
                           "filter": {"status": ["open"] * 11}})


class TestTheApplicationValidatorAgrees:
    @pytest.mark.parametrize("spec", [
        {"entity": "tickets", "metric": "count", "filter": {"status": ["open", "assigned", "in_progress"]}},
        {"entity": "invoices", "metric": "sum", "field": "outstanding",
         "filter": {"status": ["issued", "partially_paid"]}},
    ])
    def test_both_validators_accept_the_same_spec(self, spec):
        assert reports_ai.validate_query_spec(dict(spec))["filter"] == validate_spec(dict(spec))["filter"]

    def test_the_prompt_tells_the_model_it_may_send_a_list(self):
        prompt = reports_ai.build_system_prompt()
        assert '["issued","partially_paid"]' in prompt.replace(" ", "")
        assert '["open","assigned","in_progress"]' in prompt.replace(" ", "")

    def test_the_header_names_every_value_that_was_counted(self):
        words = reports_ai._filter_words("status", ["issued", "partially_paid"], "th")
        assert "·" in words and "ออกแล้ว" in words
