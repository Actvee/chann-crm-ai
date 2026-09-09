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


# The left navigation (owner, 8 Sep 2026). What can be asserted without a
# browser: that the icon-only controls still have names and states, that
# the touch targets are declared large enough, and that the one thing on
# the page which moves can be told to stop.
NAV = ROOT / "presentation" / "app" / "liff" / "_nav.tsx"
NAV_MODEL = ROOT / "presentation" / "app" / "liff" / "_nav-model.tsx"
NAV_STATE = ROOT / "presentation" / "lib" / "nav-state.ts"
ADMIN_NAV = ROOT / "presentation" / "app" / "admin" / "_nav.tsx"
ADMIN_TOGGLE = ROOT / "presentation" / "app" / "admin" / "_rail-toggle.tsx"


def _size(css: str, selector: str, prop: str) -> float:
    """The px a selector's own rule declares — the last one, which is the
    one the cascade lands on."""
    blocks = re.findall(
        rf"(?:^|[}},])[ \t]*{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.S | re.M,
    )
    values = [
        float(found.group(1))
        for block in blocks
        if (found := re.search(rf"{prop}:\s*([0-9.]+)px", block))
    ]
    assert values, f"{selector} declares no {prop} in globals.css"
    return values[-1]


class TestLeftNavigation:
    def test_collapsed_links_keep_an_accessible_name(self):
        """Collapsed, the label is off screen; aria-label is all a screen
        reader has and title is all a pointer has."""
        rail = NAV.read_text(encoding="utf-8")
        assert 'aria-label={entry.label}' in rail
        assert 'title={entry.label}' in rail
        admin = ADMIN_NAV.read_text(encoding="utf-8")
        assert "aria-label={item.label}" in admin
        assert "title={item.label}" in admin

    def test_every_toggle_announces_whether_it_is_open(self):
        rail = NAV.read_text(encoding="utf-8")
        # The collapse control on the permanent rail, and the menu button
        # that opens the drawer on a phone.
        assert rail.count("aria-expanded=") >= 2
        assert "aria-controls={railId}" in rail
        assert "aria-expanded={!collapsed}" in ADMIN_TOGGLE.read_text(encoding="utf-8")

    def test_the_current_page_is_marked_as_current(self):
        for path in (NAV, ADMIN_NAV):
            assert 'aria-current={' in path.read_text(encoding="utf-8"), path.name

    def test_navigation_targets_are_at_least_44px(self):
        css = CSS.read_text(encoding="utf-8")
        assert _size(css, ".rail-link", "min-height") >= 44
        assert _size(css, ".rail-btn", "width") >= 44
        assert _size(css, ".rail-btn", "height") >= 44
        assert _size(css, ".navtoggle", "width") >= 44
        assert _size(css, ".navtoggle", "height") >= 44
        assert _size(css, ".backlink", "height") >= 44

    def test_the_rail_stops_moving_when_asked_to(self):
        css = CSS.read_text(encoding="utf-8")
        blocks = re.findall(
            r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S,
        )
        assert any(".rail" in block and "transition: none" in block for block in blocks)

    def test_the_navigation_draws_icons_rather_than_emoji(self):
        """Skill rule and pro-rules alike: an emoji is whatever the phone
        decides it is, and cannot take the OA's colour."""
        emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
        for path in (NAV, NAV_MODEL, ADMIN_NAV):
            hits = emoji.findall(path.read_text(encoding="utf-8"))
            assert not hits, f"{path.name} uses emoji as icons: {hits}"

    def test_a_broken_storage_cannot_break_the_page(self):
        """localStorage throws outright in some in-app browsers; every
        read and write of the rail's state is wrapped."""
        state = NAV_STATE.read_text(encoding="utf-8")
        for call in re.finditer(r"localStorage\.(getItem|setItem)", state):
            before = state[:call.start()]
            assert before.count("try {") > before.count("} catch"), call.group(0)
