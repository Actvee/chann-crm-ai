"""Round 20d — the two 500s, the slow tab, and saying what was deleted.

Owner, 17 ก.ย. 2569:
  · "Dashboard deal, Quote แก้ไขข้อมูลแล้วขึ้น Deal : ทำรายการไม่สำเร็จ (500),
     Quote : ทำรายการไม่สำเร็จ (502)"
  · "แชทที่คุยกับลูกค้า บางทีกดทั้งหมดแล้วมันโหลดแชทที่ปิดไปแล้วขึ้นมาช้า"
  · "รายการที่ลบ ควรมีการบอกว่าลบอะไรไปบ้าง และสถานะการณ์ลบว่าสำเร็จ"

Both errors were one class: a Python value the JSON encoder cannot take.
The Application handed Decimal and date straight to httpx (`json=`), and
the Data tier put a date into an audit row's JSONB column. Every audited
write carrying a date or an amount had the same latent fault.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest


class TestNothingUnserialisableReachesJson:
    def test_an_amount_keeps_its_exact_value(self):
        from chann_data.repositories.audit import jsonable

        assert jsonable(Decimal("1234.50")) == "1234.50", "float would round it"

    @pytest.mark.parametrize("value,expected", [
        (date(2026, 9, 30), "2026-09-30"),
        (datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc), "2026-09-30T14:00:00+00:00"),
    ])
    def test_dates_become_iso_strings(self, value, expected):
        from chann_data.repositories.audit import jsonable

        assert jsonable(value) == expected

    def test_it_reaches_inside_dicts_and_lists(self):
        from chann_data.repositories.audit import jsonable

        out = jsonable({"rows": [{"amount": Decimal("10"), "on": date(2026, 1, 2)}]})
        assert out == {"rows": [{"amount": "10", "on": "2026-01-02"}]}

    def test_a_deal_diff_with_a_close_date_can_be_serialised(self):
        """The exact shape that returned 500 on DEV."""
        import json

        from chann_data.repositories.audit import diff_fields

        changed = diff_fields(
            {"amount": Decimal("100"), "expected_close_date": date(2026, 9, 1)},
            {"amount": Decimal("250.75"), "expected_close_date": date(2026, 9, 30)},
        )
        json.dumps(changed)  # must not raise
        assert changed["amount"]["new"] == "250.75"
        assert changed["expected_close_date"]["new"] == "2026-09-30"

    def test_a_quote_valid_until_can_be_serialised(self):
        """The shape behind the 502: the Data tier's own audit write."""
        import json

        from chann_data.repositories.audit import diff_fields

        json.dumps(diff_fields({"valid_until": None}, {"valid_until": date(2026, 12, 31)}))


class TestEveryRoutePayloadIsJsonNative:
    """One missing mode="json" is one endpoint that 500s the first time a
    shop types an amount or a date into it, so the rule is pinned for all
    of them rather than for the two that were reported."""

    def test_no_route_hands_raw_python_to_the_data_tier(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "application/chann_app/routers_phase2.py").read_text(encoding="utf-8")
        bad = [
            line.strip() for line in source.split("\n")
            if "model_dump(" in line and 'mode="json"' not in line
        ]
        assert bad == [], bad


class TestTheClosedChatTabDoesNotWaitForTheSweep:
    def test_only_the_live_view_sweeps(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "application/chann_app/routers_phase2.py").read_text(encoding="utf-8")
        start = source.index("async def list_chat_sessions")
        body = source[start:start + 2400]
        assert 'if wanted == "live":' in body, body[:600]
        assert body.index('if wanted == "live":') < body.index("live_chat.sweep")


class TestTheDeleteDialogSaysWhatAndWhether:
    """The dashboard asked with window.confirm — English OK/Cancel on a Thai
    screen, no code, no consequence, no busy state, no focus management."""

    def _source(self, relative: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")

    def test_the_dialog_is_an_alertdialog_with_its_text_wired_up(self):
        source = self._source("presentation/app/liff/sales/_confirm.tsx")
        for needed in ('role="alertdialog"', "aria-labelledby", "aria-describedby", 'aria-modal="true"'):
            assert needed in source, needed

    def test_focus_starts_on_the_safe_choice(self):
        source = self._source("presentation/app/liff/sales/_confirm.tsx")
        assert "cancelRef.current?.focus()" in source

    def test_escape_cancels_and_tab_is_trapped(self):
        source = self._source("presentation/app/liff/sales/_confirm.tsx")
        assert 'event.key === "Escape"' in source
        assert 'event.key !== "Tab"' in source

    def test_the_customer_delete_no_longer_uses_window_confirm(self):
        source = self._source("presentation/app/liff/sales/customers/CustomerList.tsx")
        code = "\n".join(
            line for line in source.split("\n") if not line.strip().startswith("//")
        )
        assert "window.confirm" not in code
        assert "useConfirm" in code

    def test_the_confirm_names_the_record_and_what_survives(self):
        source = self._source("presentation/app/liff/sales/customers/CustomerList.tsx")
        assert "code: customer.customer_id" in source
        assert "archiveAlsoDeals" in source and "archiveKeeps" in source

    def test_the_success_names_what_went(self):
        source = self._source("presentation/app/liff/sales/customers/CustomerList.tsx")
        assert "archivedNamed" in source, "a generic 'saved' tells nobody what happened"

    @pytest.mark.parametrize("key", ["archivedNamed", "archiveKeeps", "confirmCancel", "confirmPermanent"])
    def test_both_languages_carry_the_new_words(self, key):
        for path in ("presentation/lib/i18n/th.ts", "presentation/lib/i18n/en.ts"):
            assert f"{key}:" in self._source(path), (key, path)
