"""Phase 10 — snapshot to HTML.

Takes an already-frozen snapshot (`snapshot.py`) and produces the HTML that
goes to SmartBrowz. It performs no arithmetic and reads no database: every
number it prints was computed and frozen beforehand, so a change here can
never alter a total.

The markup is one self-contained document with inline CSS. SmartBrowz
renders a page it fetches or is handed directly; an external stylesheet
would be one more thing that has to be reachable, cacheable and unchanged
for an old document to re-render identically. Inline CSS makes the HTML
itself the complete artifact.

This is the built-in default template. Spec 10.4/10.5's tenant-authored
DOCX-derived templates are a separate, later mechanism — but they render
through this same snapshot contract, which is why the snapshot shape is
defined independently of this file.
"""
from __future__ import annotations

from decimal import Decimal
from html import escape

# A PDF renderer without a Thai font produces boxes, and the failure is
# silent: the render "succeeds", the bytes are a valid PDF, and only a
# human looking at the document would ever notice. Relying on the renderer
# having a Thai font installed is therefore not good enough — the exact
# same class of bug was caught by eye in scripts/setup-richmenu.py.
#
# So the font is fetched by the renderer rather than assumed. Noto Sans
# Thai is served by Google Fonts, which the SmartBrowz browser can reach.
# The local names stay in the stack ahead of it so an environment that
# does have the font skips the download entirely.
_FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2"
    "?family=Noto+Sans+Thai:wght@400;600;700&display=swap');"
)
_FONT_STACK = (
    "'Noto Sans Thai', 'Sarabun', 'IBM Plex Sans Thai', "
    "'Garuda', 'Leelawadee UI', 'Tahoma', sans-serif"
)


def _fmt_money(value: str | None) -> str:
    """Thousands separators and exactly two decimals.

    Formatting happens only here, at the very edge. The snapshot stores
    plain decimal strings so the stored value never depends on display
    choices — a locale or separator change must not make an old document's
    stored data look different from what it rendered.
    """
    if value is None:
        return ""
    return f"{Decimal(value):,.2f}"


def _rows(line_items: list[dict]) -> str:
    if not line_items:
        # An empty quote is legitimate (a deal with no line items yet) and
        # must render as an obviously-empty table rather than a broken one.
        return (
            '<tr><td colspan="5" class="empty">ไม่มีรายการสินค้า</td></tr>'
        )
    out = []
    for item in line_items:
        notes = item.get("notes")
        name = escape(item["product_name"])
        if notes:
            name += f'<div class="note">{escape(notes)}</div>'
        out.append(
            "<tr>"
            f'<td class="num">{item["line_no"]}</td>'
            f"<td>{name}</td>"
            f'<td class="num">{item["qty"]}</td>'
            f'<td class="num">{_fmt_money(item["unit_price"])}</td>'
            f'<td class="num">{_fmt_money(item["line_total"])}</td>'
            "</tr>"
        )
    return "".join(out)


def _totals_rows(totals: dict) -> str:
    rows = [
        '<tr><th>รวมเป็นเงิน</th>'
        f'<td class="num">{_fmt_money(totals["subtotal"])}</td></tr>'
    ]
    # No VAT line at all when the tenant is not registered — see
    # compute_totals for why this is not the same as printing 0.00.
    if totals.get("vat_applicable"):
        rows.append(
            f'<tr><th>ภาษีมูลค่าเพิ่ม {escape(str(totals["vat_rate_percent"]))}%</th>'
            f'<td class="num">{_fmt_money(totals["vat_amount"])}</td></tr>'
        )
    rows.append(
        '<tr class="grand"><th>จำนวนเงินรวมทั้งสิ้น</th>'
        f'<td class="num">{_fmt_money(totals["grand_total"])}</td></tr>'
    )
    return "".join(rows)


def render_quote_html(snapshot: dict) -> str:
    company = snapshot["company"]
    customer = snapshot["customer"]
    quote = snapshot["quote"]

    contact_bits = [b for b in (company.get("phone"), company.get("email")) if b]
    company_contact = escape(" · ".join(contact_bits)) if contact_bits else ""

    customer_bits = [
        b for b in (customer.get("phone"), customer.get("email")) if b
    ]
    customer_contact = escape(" · ".join(customer_bits)) if customer_bits else ""

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>ใบเสนอราคา {escape(quote["quote_id"])}</title>
<style>
  {_FONT_IMPORT}
  @page {{ size: A4; margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: {_FONT_STACK}; font-size: 13px; color: #111; margin: 0; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-start;
            border-bottom: 2px solid #111; padding-bottom: 12px; }}
  .company-name {{ font-size: 18px; font-weight: 700; }}
  .muted {{ color: #555; }}
  .doc-title {{ font-size: 22px; font-weight: 700; text-align: right; }}
  .meta {{ text-align: right; margin-top: 6px; }}
  .parties {{ display: flex; gap: 24px; margin: 18px 0; }}
  .parties section {{ flex: 1; }}
  .parties h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
                 color: #555; margin: 0 0 4px; }}
  table.items {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  table.items th, table.items td {{ border: 1px solid #bbb; padding: 7px 8px;
                                    vertical-align: top; }}
  table.items thead th {{ background: #f2f2f2; font-size: 12px; }}
  td.num, th.num {{ text-align: right; white-space: nowrap; }}
  td.empty {{ text-align: center; color: #777; padding: 18px; }}
  .note {{ color: #666; font-size: 11px; margin-top: 3px; }}
  table.totals {{ margin-left: auto; margin-top: 12px; border-collapse: collapse;
                  min-width: 260px; }}
  table.totals th, table.totals td {{ padding: 6px 10px; border-bottom: 1px solid #ddd; }}
  table.totals th {{ text-align: left; font-weight: 500; }}
  table.totals tr.grand th, table.totals tr.grand td {{ font-weight: 700;
                                                        border-bottom: 2px solid #111; }}
  footer {{ margin-top: 36px; display: flex; justify-content: space-between; }}
  .sign {{ width: 45%; text-align: center; }}
  .sign .line {{ margin-top: 46px; border-top: 1px solid #555; padding-top: 5px;
                 color: #555; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <div>
    <div class="company-name">{escape(company["name"])}</div>
    <div class="muted">{escape(company["address"])}</div>
    <div class="muted">เลขประจำตัวผู้เสียภาษี {escape(company["tax_id"])}</div>
    <div class="muted">{company_contact}</div>
  </div>
  <div>
    <div class="doc-title">ใบเสนอราคา</div>
    <div class="meta muted">
      เลขที่ {escape(quote["quote_id"])}<br>
      วันที่ {escape(snapshot["issued_on"])}<br>
      อ้างอิงดีล {escape(snapshot["deal"]["deal_id"])}
    </div>
  </div>
</header>

<div class="parties">
  <section>
    <h2>เสนอราคาให้</h2>
    <div><strong>{escape(customer["name"]) or "-"}</strong></div>
    <div class="muted">{escape(customer["address"])}</div>
    <div class="muted">{customer_contact}</div>
  </section>
</div>

<table class="items">
  <thead>
    <tr>
      <th class="num" style="width:6%">#</th>
      <th>รายการ</th>
      <th class="num" style="width:10%">จำนวน</th>
      <th class="num" style="width:18%">ราคา/หน่วย</th>
      <th class="num" style="width:20%">จำนวนเงิน</th>
    </tr>
  </thead>
  <tbody>{_rows(snapshot["line_items"])}</tbody>
</table>

<table class="totals">{_totals_rows(snapshot["totals"])}</table>

<footer>
  <div class="sign"><div class="line">ผู้เสนอราคา</div></div>
  <div class="sign"><div class="line">ผู้อนุมัติ / ลูกค้า</div></div>
</footer>
</body>
</html>"""
