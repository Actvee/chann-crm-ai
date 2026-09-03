"""Phase 13 — service report snapshot to HTML.

The built-in template, in the same shape as `html.py`'s quote: one
self-contained document, inline CSS, the Thai font fetched by the
renderer rather than assumed. It prints only what the frozen snapshot
holds — no lookups, no arithmetic.

The approval block (13.5) shows each approver's name, role and time, and
their signature image when one is on file. A missing signature leaves a
labelled line rather than a broken image: the document must not look
tampered with because a person never uploaded a signature.
"""
from __future__ import annotations

from html import escape

from .html import _FONT_IMPORT, _FONT_STACK


def _row(label: str, value: str) -> str:
    return (
        f'<div class="row"><div class="k">{escape(label)}</div>'
        f'<div class="v">{escape(value) if value else "—"}</div></div>'
    )


def _para(label: str, value: str) -> str:
    body = escape(value).replace("\n", "<br>") if value else "—"
    return f'<section class="block"><h2>{escape(label)}</h2><p>{body}</p></section>'


def _approvals(rows: list[dict]) -> str:
    if not rows:
        return '<div class="sign"><div class="line">ผู้ตรวจ / ผู้อนุมัติ</div></div>'
    cells = []
    for a in rows:
        signature = (
            f'<img class="signature" src="{escape(a["signature_url"], quote=True)}" alt="">'
            if a.get("signature_url") else '<div class="signature blank"></div>'
        )
        who = escape(a.get("name") or "")
        role = escape(a.get("role") or "")
        when = escape(a.get("acted_at") or "")
        cells.append(
            f'<div class="sign">{signature}<div class="line">{who}'
            f'{" · " + role if role else ""}<br><span class="muted">{when}</span></div></div>'
        )
    return "".join(cells)


def _photos(urls: list[str]) -> str:
    if not urls:
        return ""
    cells = "".join(
        f'<img class="photo" src="{escape(u, quote=True)}" alt="">' for u in urls[:4]
    )
    return f'<section class="block"><h2>รูปหน้างาน</h2><div class="photos">{cells}</div></section>'


def render_service_report_html(snapshot: dict) -> str:
    company = snapshot["company"]
    report = snapshot["report"]
    ticket = snapshot["ticket"]
    technician = snapshot["technician"]

    contact_bits = [b for b in (company.get("phone"), company.get("email")) if b]
    company_contact = escape(" · ".join(contact_bits)) if contact_bits else ""
    tax_line = (
        f'<div class="muted">เลขประจำตัวผู้เสียภาษี {escape(company["tax_id"])}</div>'
        if company.get("tax_id") else ""
    )
    when = " ".join(b for b in (ticket.get("scheduled_date"), ticket.get("scheduled_time")) if b)

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>รายงานการซ่อม {escape(report["report_id"])}</title>
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
  .grid {{ display: flex; gap: 24px; margin: 18px 0; }}
  .grid section {{ flex: 1; }}
  .grid h2, .block h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
                          color: #555; margin: 0 0 6px; }}
  .row {{ display: flex; gap: 8px; padding: 3px 0; border-bottom: 1px solid #eee; }}
  .row .k {{ width: 34%; color: #555; }}
  .row .v {{ flex: 1; }}
  .block {{ margin: 14px 0; }}
  .block p {{ margin: 0; padding: 10px 12px; border: 1px solid #bbb; min-height: 44px;
              white-space: pre-wrap; }}
  .photos {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .photos .photo {{ width: 23%; height: 150px; object-fit: cover; border: 1px solid #bbb; }}
  footer {{ margin-top: 36px; display: flex; justify-content: space-between; gap: 16px;
            flex-wrap: wrap; }}
  .sign {{ width: 45%; text-align: center; }}
  .sign .signature {{ height: 56px; max-width: 100%; object-fit: contain; display: block;
                      margin: 0 auto; }}
  .sign .signature.blank {{ height: 56px; }}
  .sign .line {{ border-top: 1px solid #555; padding-top: 5px; color: #333; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <div>
    <div class="company-name">{escape(company["name"]) or "—"}</div>
    <div class="muted">{escape(company["address"])}</div>
    {tax_line}
    <div class="muted">{company_contact}</div>
  </div>
  <div>
    <div class="doc-title">รายงานการซ่อม</div>
    <div class="meta muted">
      เลขที่ {escape(report["report_id"])}<br>
      วันที่ออก {escape(snapshot["issued_on"])}<br>
      งาน {escape(ticket["ticket_number"])}
    </div>
  </div>
</header>

<div class="grid">
  <section>
    <h2>ลูกค้า</h2>
    {_row("ชื่อ", ticket["customer_name"])}
    {_row("โทร", ticket["customer_phone"])}
    {_row("ที่อยู่", ticket["service_address"])}
    {_row("หมายเลขเครื่อง", ticket["serial_number"])}
  </section>
  <section>
    <h2>งาน</h2>
    {_row("อาการที่แจ้ง", ticket["issue_description"])}
    {_row("นัดหมาย", when)}
    {_row("ช่าง", technician["name"])}
    {_row("โทรช่าง", technician["phone"])}
  </section>
</div>

{_para("ปัญหาที่พบ", report["found_issue"])}
{_para("สิ่งที่แก้ไข", report["work_done"])}
{_para("อะไหล่ที่เปลี่ยน", report["parts_changed"])}
{_para("หมายเหตุ", report["notes"]) if report["notes"] else ""}

{_photos(snapshot.get("photos") or [])}

<footer>
  <div class="sign"><div class="signature blank"></div><div class="line">ช่างผู้ปฏิบัติงาน<br><span class="muted">{escape(technician["name"])}</span></div></div>
  {_approvals(snapshot["approvals"])}
</footer>
</body>
</html>"""
