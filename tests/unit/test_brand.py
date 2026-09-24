"""Round 21E — the brand a person can see must read "Chann1", never bare
"Chann" (owner: "...ยังมีเขียนว่าเป็น Chann เฉยๆอยู่ ต้องเป็น Chann1 ทั้งหมดทุกส่วน").

scripts/dev/check-brand.py does the scanning; this just runs it as a gate.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/dev/check-brand.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_brand", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_bare_chann_in_any_user_visible_source():
    module = _load()
    offenders: list[str] = []
    for pattern in module.PY_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            offenders += module.scan_py(path)
    for pattern in module.TEXT_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            offenders += module.scan_text(path)
    assert offenders == []
