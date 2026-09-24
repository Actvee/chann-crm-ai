"""Round 21E — the record pages' boxes, and one "ออกใบแจ้งหนี้" everywhere.

The owner, on DEV (449819e), 24 ก.ย. 2569: "หน้า UI สถานะใบเสนอราคา เอกสาร
ใบแจ้งหนี้ใน Quote และ สถานะดีล กับเอกสารแจ้งหนี้ในดีล ปุ่มก็ชิดขอบออกแบบ
ไม่ดี และในหน้าใบแจ้งหนี้ ปุ่มสร้างใบแจ้งหนี้ก็ไม่เหมือนในใบเสนอราคา".

Why the buttons touched the edge: `.section` has no padding of its own
(its head and its `.fields` carry 16px each), and round 21A's
`StatusSection` / `RecordActions` put their `.actions` rows straight into
the section — so the buttons sat on the box's left edge and its bottom.
The fix is in the shared pattern (`_record.tsx` + globals.css), not per
page: every box body is a `.section-body` with the same 16px gutter as the
head above it (ui-ux-pro-max `spacing-scale`).

Source assertions, like tests/unit/test_round21c_invoice_ui.py: they pin
the structure and the classes. Whether it LOOKS right is the ui-ux pass
and a person looking at a phone.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "presentation/app/liff/sales"
CSS = (ROOT / "presentation/app/globals.css").read_text(encoding="utf-8")
RECORD = (APP / "_record.tsx").read_text(encoding="utf-8")
QUOTE = (APP / "quotes/[id]/QuoteDetail.tsx").read_text(encoding="utf-8")
DEAL = (APP / "deals/[id]/DealDetail.tsx").read_text(encoding="utf-8")
CUSTOMER = (APP / "customers/[id]/CustomerDetail.tsx").read_text(encoding="utf-8")
INVOICES = (APP / "invoices/InvoiceList.tsx").read_text(encoding="utf-8")
BUTTON = APP / "invoices/_create-button.tsx"
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
EN = (ROOT / "presentation/lib/i18n/en.ts").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """The body of the first CSS rule whose selector list is exactly this."""
    found = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert found, f"no CSS rule for {selector}"
    return found.group(1)


def _component(name: str) -> str:
    start = RECORD.index(f"export function {name}(")
    end = RECORD.find("\nexport function ", start + 1)
    return RECORD[start:end if end != -1 else None]


class TestTheBoxesHaveAGutter:
    def test_a_box_body_is_padded_like_its_head(self):
        body = _rule(".section-body")
        assert re.search(r"padding:\s*12px 16px 16px", body), body
        # The head above it is 16px at the sides; the body matches it.
        assert re.search(r"padding:\s*12px 16px", _rule(".section-head"))

    def test_the_status_box_puts_its_buttons_inside_the_gutter(self):
        status = _component("StatusSection")
        assert 'className="section-body"' in status
        # The moves and the note live inside the body, never beside it.
        body = status[status.index('className="section-body"'):]
        assert "record-status-moves" in body and "record-status-note" in body

    def test_the_documents_box_puts_its_buttons_inside_the_gutter(self):
        actions = _component("RecordActions")
        assert 'className="section-body"' in actions
        body = actions[actions.index('className="section-body"'):]
        for part in ("record-primary", "record-secondary", "record-danger"):
            assert part in body, part

    def test_a_row_inside_the_body_does_not_add_its_own_top_margin(self):
        # `.actions` carries margin-top: 14px for rows under a form; inside
        # a padded body that doubled the space above the first row.
        assert re.search(r"margin-top:\s*0", _rule(".section-body > .actions"))


class TestTheButtonRow:
    def test_rows_wrap_with_an_8px_gap(self):
        # ui-ux-pro-max touch-spacing: 8px minimum between targets.
        row = _rule(".actions")
        assert "flex-wrap: wrap" in row and re.search(r"gap:\s*8px", row)

    def test_secondary_buttons_share_a_phone_row_evenly(self):
        secondary = _rule(".record-secondary .btn")
        assert re.search(r"flex:\s*1 1 \d+px", secondary), secondary

    def test_touch_targets_stay_44px(self):
        assert re.search(r"min-height:\s*44px", _rule(".btn"))

    def test_a_status_move_that_ends_the_record_goes_last(self):
        # "ปฏิเสธ" / "แพ้" sat between the other moves; it now closes the row.
        assert re.search(r"order:\s*1", _rule('.record-status-moves .btn[data-variant="danger"]'))


class TestOnePrimaryPerBox:
    def test_the_documents_box_takes_its_primary_apart(self):
        actions = _component("RecordActions")
        assert "primary?: ReactNode" in actions
        # The primary has a row of its own; nothing else shares it.
        assert '"actions record-primary"' in actions

    def test_the_quote_page_names_one_primary(self):
        block = QUOTE[QUOTE.index("<RecordActions"):QUOTE.index("</RecordActions>")]
        assert "primary={" in block
        # The buttons passed as children (the secondary row) never decide
        # for themselves that they are primary.
        children = block[block.index("\n            >\n"):]
        assert 'data-variant="primary"' not in children
        assert "? \"primary\"" not in children and "? 'primary'" not in children
        assert "primary={false}" in children

    def test_the_deal_page_names_one_primary(self):
        block = DEAL[DEAL.index("<RecordActions"):DEAL.index("</RecordActions>")]
        assert "primary={" in block
        children = block[block.index("\n            >\n"):]
        assert 'data-variant="primary"' not in children

    def test_the_invoice_sheet_has_one_primary_row(self):
        sheet = INVOICES[INVOICES.index("<Sheet open={Boolean(open)}"):]
        assert '"actions record-primary"' in sheet
        assert '"actions record-secondary"' in sheet


class TestOneCreateInvoiceButton:
    """The quote page's button is the model: the verb chat uses ("ออก
    ใบแจ้งหนี้ Q-…", "ออกใบแจ้งหนี้ให้ดีล D-…"), the verb the deal and
    customer pages already used, a primary a thumb can find (full width
    on a phone). The invoices page said "สร้างใบแจ้งหนี้" in a compact
    button tucked into the count line."""

    def test_one_component_draws_it(self):
        source = BUTTON.read_text(encoding="utf-8")
        assert "export function CreateInvoiceButton" in source
        assert 'className="btn"' in source

    def test_every_page_that_starts_a_bill_uses_it(self):
        for name, page in (("quote", QUOTE), ("deal", DEAL), ("customer", CUSTOMER), ("invoices", INVOICES)):
            assert "<CreateInvoiceButton" in page, name

    def test_the_invoices_page_puts_it_where_the_other_lists_put_create(self):
        # Above the list in its own action row, like InlineCreateForm on the
        # customer and deal lists — not inside the count line's tools.
        tools = INVOICES[INVOICES.index('<div className="list-tools">'):]
        tools = tools[:tools.index("</div>")]
        assert "CreateInvoiceButton" not in tools
        assert 'className="actions record-create"' in INVOICES

    def test_one_label_in_both_languages(self):
        assert 'create: "ออกใบแจ้งหนี้"' in TH
        assert 'createTitle: "ออกใบแจ้งหนี้"' in TH
        assert 'create: "Issue invoice"' in EN
        for gone in ("forThisDeal:", "forThisCustomer:", "      fromQuote:"):
            assert gone not in TH and gone not in EN, gone
