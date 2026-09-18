#!/usr/bin/env python3
"""`mode="json"` turns a Decimal into a STRING. Do not then do sums on it.

Round 20d put `mode="json"` on all fifteen `model_dump()` calls in
routers_phase2.py so no Decimal could reach the JSON encoder — that was the
cause of the Deal 500 and the Quote 502. It fixed one side of the seam and
opened the other at the single site that did ARITHMETIC on the dumped
value:

    body = payload.model_dump(mode="json", exclude_unset=True)
    percent = body.pop("vat_rate_percent")
    body["vat_rate"] = percent / Decimal(100)      # '7' / Decimal → TypeError

Every VAT save answered 500 from then until 18 ก.ย. 2569, and nothing
caught it: no test called that route end to end, and the failure needs a
real request to appear.

What this refuses: a value taken out of a `model_dump(mode="json")` result
and used in arithmetic WITHOUT being converted back first. What it allows:
the same arithmetic wrapped in an explicit conversion —
`Decimal(str(percent))`, `int(n)`, `float(x)` — because that is the fix.

    python3 scripts/dev/check-dumped-arithmetic.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIERS = ("application", "data")

#: `x = something.model_dump(mode="json" …)` — the name the dumped dict takes.
DUMP = re.compile(r'^\s*(\w+)\s*=\s*[\w.]+\.model_dump\(\s*mode="json"', re.M)
#: How far after the dump a use still counts as "this dump's value".
WINDOW = 2000
#: The conversion that makes arithmetic safe again — and it must wrap THE
#: VARIABLE. A first version matched any `Decimal(` on the line, so the bug
#: this file exists for slipped straight through: the offending statement
#: was `percent / Decimal(100)`, whose `Decimal(100)` is the DIVISOR, not a
#: conversion of `percent`. A checker that passes on its own worked example
#: is worse than none, so this is pinned by a test (18 ก.ย. 2569).
def _safe_for(var: str) -> re.Pattern[str]:
    return re.compile(
        rf'(?:Decimal|int|float)\s*\(\s*(?:str\s*\(\s*)?{re.escape(var)}\b'
    )


def _without_comments(src: str) -> str:
    """The same text with comment bodies blanked, line count preserved.

    A first version scanned the raw source and found nothing, because the
    explanation written ABOVE the fixed line filled the whole look-ahead
    window and pushed the statement out of it. Comments are exactly what a
    well-explained fix has most of, so they are removed before looking
    rather than compensated for with a bigger window.
    """
    out = []
    for line in src.splitlines():
        hash_at = line.find("#")
        if hash_at >= 0 and line[:hash_at].count('"') % 2 == 0 and line[:hash_at].count("'") % 2 == 0:
            line = line[:hash_at]
        out.append(line)
    return "\n".join(out)


def offenders_in(src: str, label: str) -> list[str]:
    """Every offending site in one source text. `label` names it in the message."""
    found: list[str] = []
    src = _without_comments(src)
    for dump in DUMP.finditer(src):
        name = dump.group(1)
        region = src[dump.end(): dump.end() + WINDOW]
        # A value pulled out of the dumped dict…
        for taken in re.finditer(
            rf'(\w+)\s*=\s*{re.escape(name)}(?:\.pop|\.get|\[)\s*\(?["\']([\w_]+)',
            region,
        ):
            var, key = taken.group(1), taken.group(2)
            after = region[taken.end(): taken.end() + 400]
            # …then used in arithmetic.
            for use in re.finditer(
                rf'{re.escape(var)}\s*[-+*/]|[-+*/]\s*{re.escape(var)}\b', after
            ):
                nl = after.find("\n", use.end())
                stmt = after[after.rfind("\n", 0, use.start()) + 1: nl if nl > 0 else None]
                if _safe_for(var).search(stmt or ""):
                    continue
                at = src[: dump.end()].count("\n") + 1
                found.append(
                    f"{label}:{at} — '{key}' comes out of "
                    f"{name}.model_dump(mode=\"json\") as a STRING, then "
                    f"{(stmt or '').strip()[:60]!r} does arithmetic on it"
                )
                break
    return found


def offenders() -> list[str]:
    found: list[str] = []
    for tier in TIERS:
        for path in sorted((ROOT / tier).rglob("*.py")):
            found += offenders_in(
                path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
            )
    return found


def main() -> int:
    bad = offenders()
    for line in bad:
        print(f"  {line}")
    if bad:
        print(
            f"\n{len(bad)} place(s) doing sums on a value that mode=\"json\" already "
            "turned into a string.\nConvert it back first — Decimal(str(x)) keeps the "
            "exact value and never goes near a float."
        )
        return 1
    print("no arithmetic on a value that mode=\"json\" stringified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
