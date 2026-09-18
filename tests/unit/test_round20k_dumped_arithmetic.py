"""The VAT-500 checker must fail on the bug it was written for.

18 ก.ย. 2569 — `scripts/dev/check-dumped-arithmetic.py` was written to stop
the Deal/Quote/VAT class of 500 from coming back, and its first two versions
both reported "clean" when handed the exact line that caused the outage:
once because it accepted `Decimal(100)` (the DIVISOR) as proof of a
conversion, once because the explanatory comment above the fixed line filled
the look-ahead window. A checker that passes on its own worked example is
worse than none, so both directions are pinned here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_dumped_arithmetic", ROOT / "scripts" / "dev" / "check-dumped-arithmetic.py"
)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


#: The shape of the line that answered 500 to every VAT save.
BUGGY = '''
async def update_company(payload):
    body = payload.model_dump(mode="json", exclude_unset=True)
    percent = body.pop("vat_rate_percent", None)
    body["vat_rate"] = None if percent is None else percent / Decimal(100)
    return body
'''

#: The same route after the fix.
FIXED = '''
async def update_company(payload):
    body = payload.model_dump(mode="json", exclude_unset=True)
    percent = body.pop("vat_rate_percent", None)
    body["vat_rate"] = (
        None if percent is None else str(Decimal(str(percent)) / Decimal(100))
    )
    return body
'''

#: The fix as it is actually written — with the explanation above it.
FIXED_WITH_THE_COMMENT_THAT_HID_IT = '''
async def update_company(payload):
    body = payload.model_dump(mode="json", exclude_unset=True)
    percent = body.pop("vat_rate_percent", None)
    # `mode="json"` hands a Decimal back as a STRING. Round 20d added that to
    # all fifteen dumps to stop Decimals reaching the JSON encoder, which is
    # right, but this is the one site that then does arithmetic on the value,
    # so it has to be converted back before the division or every save 500s.
    # Decimal(str(x)) keeps the exact value and never goes near a float.
    body["vat_rate"] = (
        None if percent is None else str(Decimal(str(percent)) / Decimal(100))
    )
    return body
'''

#: Same comment wall, but the bug is still there underneath it.
BUGGY_BEHIND_A_COMMENT_WALL = FIXED_WITH_THE_COMMENT_THAT_HID_IT.replace(
    '        None if percent is None else str(Decimal(str(percent)) / Decimal(100))',
    '        None if percent is None else percent / Decimal(100)',
)


def test_it_fails_on_the_line_that_caused_the_outage():
    found = checker.offenders_in(BUGGY, "routers_phase2.py")
    assert found, "the checker reported clean on the exact VAT-500 line"
    assert "vat_rate_percent" in found[0]


def test_decimal_as_a_divisor_is_not_a_conversion():
    # `Decimal(100)` sits on the same line; it converts the 100, not the value.
    assert checker.offenders_in(BUGGY, "x.py")


def test_a_comment_wall_does_not_hide_the_bug():
    assert checker.offenders_in(BUGGY_BEHIND_A_COMMENT_WALL, "x.py")


def test_the_fix_passes():
    assert checker.offenders_in(FIXED, "routers_phase2.py") == []


def test_the_fix_passes_with_its_explanation_above_it():
    assert checker.offenders_in(FIXED_WITH_THE_COMMENT_THAT_HID_IT, "x.py") == []


def test_a_dump_without_mode_json_is_not_this_bug():
    plain = BUGGY.replace('model_dump(mode="json", exclude_unset=True)',
                          "model_dump(exclude_unset=True)")
    assert checker.offenders_in(plain, "x.py") == []


def test_the_repository_is_clean_right_now():
    assert checker.offenders() == []
