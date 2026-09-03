"""Phase 20.5 — the accessibility checks that can be made without a
browser: every form control on every LIFF page has a label, and the
palette's text pairs meet WCAG AA contrast (4.5:1) in all three OA
themes. The rest of 20.5 (keyboard navigation) is on the owner's
walk-through list (docs/TEST_CASES_3OA.md U-02).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "presentation" / "app" / "globals.css"
CONTROL = re.compile(r"<(input|select|textarea)\b")
LABELLED = re.compile(r"\(id\) =>|id=\{id\}|<label|aria-label|htmlFor=|aria-labelledby")


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


def _theme(css: str, name: str) -> dict[str, str]:
    start = css.index(f'[data-theme="{name}"]')
    return _tokens(css[start:css.index("}", start)])


class TestEveryControlHasALabel:
    def test_inputs_selects_and_textareas_are_labelled(self):
        offenders = []
        for path in sorted((ROOT / "presentation" / "app").rglob("*.tsx")):
            if "node_modules" in path.parts:
                continue
            lines = path.read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                if not CONTROL.search(line) or 'type="checkbox"' in line or 'type="file"' in line and "id=" in line:
                    continue
                window = "\n".join(lines[max(0, i - 14):i + 4])
                if not LABELLED.search(window):
                    offenders.append(f"{path.relative_to(ROOT)}:{i + 1}")
        assert not offenders, "\n".join(["form controls without a label:", *offenders])


class TestPaletteContrast:
    PAIRS = (
        ("ink", "surface"), ("ink-soft", "surface"),
        ("ok-ink", "ok-soft"), ("danger", "danger-soft"), ("danger-ink", "danger-soft"),
    )

    def test_neutral_text_pairs_meet_aa(self):
        tokens = _tokens(CSS.read_text(encoding="utf-8"))
        weak = [
            f"{a} on {b}: {contrast(tokens[a], tokens[b]):.2f}"
            for a, b in self.PAIRS if contrast(tokens[a], tokens[b]) < 4.5
        ]
        assert not weak, weak

    def test_every_theme_keeps_its_accent_text_readable(self):
        css = CSS.read_text(encoding="utf-8")
        root = _tokens(css[css.index(":root"):css.index("}", css.index(":root"))])
        for name in ("sales", "technician", "customer"):
            theme = root if name == "sales" else {**root, **_theme(css, name)}
            # Accent text on the accent tint (chips, guide link) and white on
            # the deep accent (primary buttons, the shop's chat bubble).
            assert contrast(theme["accent-ink"], theme["accent-soft"]) >= 4.5, name
            assert contrast("#ffffff", theme["accent-deep"]) >= 4.5, name

    def test_the_raw_accent_is_never_body_text(self):
        """The raw accent (orange especially) is 3:1 on white — fine for a
        border or an icon, not for words. Text takes accent-ink."""
        css = CSS.read_text(encoding="utf-8")
        assert not re.search(r"^\s*color:\s*var\(--accent\)\s*;", css, re.M)
