"""docs/RUNTIME_CONFIG_CONTRACT.md and application/chann_app/config.py
must name the same variables — the doc drifted for a year (review E12,
6 Sep 2026: CRON_SECRET, SMARTBROWZ_RENDER_MODE and two SMARTBROWZ_CATALYST_*
names nothing read; six real settings missing)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.config import Settings  # noqa: E402

COMMON = {"APP_ENV", "PLATFORM_VERSION", "GIT_COMMIT"}


def _documented() -> set[str]:
    text = (ROOT / "docs" / "RUNTIME_CONFIG_CONTRACT.md").read_text(encoding="utf-8")
    section = text[text.index("## Application"):text.index("## Data")]
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        names |= set(re.findall(r"`([A-Z0-9_]+)`", first_cell))
    return names


def test_the_contract_names_exactly_the_settings_the_code_reads():
    configured = {name.upper() for name in Settings.model_fields} - COMMON
    documented = _documented()
    assert documented - configured == set(), f"documented but not read by config.py: {sorted(documented - configured)}"
    assert configured - documented == set(), f"read by config.py but undocumented: {sorted(configured - documented)}"


def test_the_dead_names_stay_dead():
    text = (ROOT / "docs" / "RUNTIME_CONFIG_CONTRACT.md").read_text(encoding="utf-8")
    table_rows = [l for l in text.splitlines() if l.startswith("| `")]
    for dead in ("CRON_SECRET", "SMARTBROWZ_RENDER_MODE", "SMARTBROWZ_CATALYST_PROJECT_ID", "SMARTBROWZ_CATALYST_ORG_ID", "PDF_RENDERER"):
        assert not any(f"`{dead}`" in row for row in table_rows), dead
    assert "pdf_renderer" not in Settings.model_fields
