"""Round 21D — the two checkers know the plan (spec §10). Each is proven
in both directions: clean on the real tree, and failing on a file that
uses a key outside the ten (round 20K: a checker that cannot go red
proves nothing)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"scripts/dev/{script}"], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def test_check_perms_is_clean():
    out = _run("check-perms.py")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "every plan key in use is one of the ten · every permission key is classified" in out.stdout


def test_check_perms_goes_red_on_an_unknown_plan_key():
    probe = ROOT / "presentation/app/liff/_round21d_probe.tsx"
    probe.write_text('export const x = (p: unknown) => planHas(p as never, "feature.gold");\n', encoding="utf-8")
    try:
        out = _run("check-perms.py")
    finally:
        probe.unlink()
    assert out.returncode == 1 and "feature.gold" in out.stdout


def test_check_parity_has_a_clean_plan_section():
    out = _run("check-parity.py")
    assert "every plan-gated capability is gated the same on both surfaces" in out.stdout, out.stdout[-1500:]
    assert "every capability is reachable from both surfaces" in out.stdout
