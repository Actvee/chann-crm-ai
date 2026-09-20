"""Occasional work belongs behind a button.

Owner, 20 ก.ย. 2569: "พวกเมนูที่เป็นการเพิ่มทีละหลายรายการหรือการ import
ข้อมูลเข้า คิดว่าซ่อนเป็นปุ่มให้กดแล้วค่อยขึ้นการทำงานแบบนั้นจะดีกว่า".

A sample table, a file picker, a paste box and the errors from the last
run were sitting open on three list pages — in front of everyone, every
visit, for a job most shops do once when they arrive.

These read the source: the behaviour is structural (which component holds
what, which handlers exist) and there is no browser in this suite. A test
that rendered nothing would pass while the panel was nailed open.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIFF = ROOT / "presentation/app/liff"
SHEET = LIFF / "_sheet.tsx"
TOOLS = {
    "sales/_csv-import.tsx": "CsvImport",
    "sales/_bulk-paste.tsx": "BulkPaste",
}
PAGES = (
    "sales/customers/CustomerList.tsx",
    "sales/products/ProductList.tsx",
    "sales/warranties/SalesWarranties.tsx",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheToolsOpenFromAButton:
    def test_each_one_starts_closed(self):
        for name in TOOLS:
            src = read(LIFF / name)
            assert "useState(false)" in src, name
            assert "<Sheet open={open}" in src, name

    def test_each_one_has_a_button_that_opens_it(self):
        for name in TOOLS:
            src = read(LIFF / name)
            assert "onClick={() => setOpen(true)}" in src, name

    def test_the_sample_is_fetched_when_it_is_needed(self):
        """Fetched on mount, the sample cost every visitor a request for a
        file only the panel ever shows."""
        src = read(LIFF / "sales/_csv-import.tsx")
        body = src[src.index("useEffect(() => {"):]
        body = body[: body.index("async function")]
        assert "if (!open || sample) return;" in body


class TestTheSheetBehavesLikeADialog:
    def test_it_is_a_modal_dialog(self):
        src = read(SHEET)
        assert 'role="dialog"' in src
        assert 'aria-modal="true"' in src
        assert "aria-labelledby" in src

    def test_escape_and_the_veil_both_close_it(self):
        src = read(SHEET)
        assert '"Escape"' in src
        assert 'className="sheet-veil" onClick={close}' in src

    def test_a_click_inside_does_not_close_it(self):
        assert "event.stopPropagation()" in read(SHEET)

    def test_tab_stays_inside(self):
        src = read(SHEET)
        assert "event.shiftKey" in src
        assert "last.focus()" in src and "first.focus()" in src

    def test_it_traps_form_fields_not_only_buttons(self):
        """ConfirmDialog traps `button` alone, which is right for a yes/no
        question and wrong for a panel holding a file picker and a
        textarea — Tab would walk straight out of it."""
        src = read(SHEET)
        focusable = src[src.index("const FOCUSABLE"):]
        focusable = focusable[: focusable.index(";")]
        for control in ("input", "textarea", "select", "a[href]"):
            assert control in focusable, control

    def test_focus_goes_in_and_comes_back(self):
        src = read(SHEET)
        assert "returnTo.current = document.activeElement" in src
        assert "returnTo.current?.focus()" in src

    def test_the_page_behind_does_not_scroll(self):
        src = read(SHEET)
        assert 'document.body.style.overflow = "hidden"' in src
        # ...and is put back exactly as it was, not blanked.
        assert "document.body.style.overflow = previous" in src

    def test_it_is_not_a_second_copy_of_the_confirm_dialog(self):
        """They look alike on purpose and do different jobs; what must not
        happen is one of them quietly becoming the other."""
        confirm = read(LIFF / "_confirm.tsx")
        assert 'role="alertdialog"' in confirm
        assert 'role="alertdialog"' not in read(SHEET)


class TestThePagesOnlyShowTheDoor:
    def test_no_page_renders_the_tool_open(self):
        for name in PAGES:
            src = read(LIFF / name)
            # The tools are still mounted — they hold the button — but the
            # page must not be wrapping them in a section of its own.
            assert "<CsvImport" in src or "<BulkPaste" in src, name
            assert '<section className="section">\n        <CsvImport' not in src, name

    def test_the_buttons_share_one_row(self):
        src = read(LIFF / "sales/customers/CustomerList.tsx")
        row = src[src.index("<BulkPaste") - 200: src.index("<CsvImport") + 200]
        assert 'className="actions"' in row

    def test_every_tool_is_still_gated_on_the_permission_that_writes(self):
        for name, gate in (
            ("sales/customers/CustomerList.tsx", 'can("customer.create")'),
            ("sales/products/ProductList.tsx", "canManage"),
            ("sales/warranties/SalesWarranties.tsx", "canCreate"),
        ):
            src = read(LIFF / name)
            spot = src.index("<CsvImport")
            assert gate in src[spot - 260: spot], name


class TestTheSheetFitsAPhone:
    def test_it_never_grows_past_the_screen(self):
        css = read(ROOT / "presentation/app/globals.css")
        panel = css[css.index(".sheet-panel {"):]
        panel = panel[: panel.index("}")]
        assert "max-height" in panel

    def test_its_body_scrolls_rather_than_the_panel(self):
        css = read(ROOT / "presentation/app/globals.css")
        body = css[css.index(".sheet-body {"):]
        body = body[: body.index("}")]
        assert "overflow-y: auto" in body

    def test_it_clears_the_home_indicator(self):
        css = read(ROOT / "presentation/app/globals.css")
        veil = css[css.index(".sheet-veil {"):]
        veil = veil[: veil.index("}")]
        assert "safe-area-inset-bottom" in veil

    def test_the_close_control_is_a_real_target(self):
        css = read(ROOT / "presentation/app/globals.css")
        close = css[css.index(".sheet-close {"):]
        close = close[: close.index("}")]
        assert "min-width: 44px" in close and "min-height: 44px" in close
