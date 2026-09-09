"""Starter Word templates, and the sample data every template is checked against.

Two jobs that have to live together, because separating them is how the
bug this module was written to kill got in.

**The sample data.** `sample_snapshot()` builds a REAL snapshot — through
`build_quote_snapshot` and `build_service_report_snapshot`, not by hand —
from representative business values. Anything that resolves against it
resolves against a real document, by construction. The previous
hand-written sample in `routers_phase2.py` had drifted from the snapshot
it was meant to imitate (`company.legal_name` and `item.name` are not
snapshot keys; `company.name` and `item.product_name` are), so the upload
told shops their placeholders were fine and their quotes printed a blank
company name. A sample that is not built by the real builder cannot say
anything true about the real builder.

**The starter files.** The owner's report, 9 Sep 2026: "ไม่มีตัวอย่างที่
เป็นไฟล์ให้ดาวน์โหลดไปดู". A shop needs a .docx it can open in Word, edit,
and upload back. Those are generated here, from the same placeholder
table `LEGEND` describes, rather than committed as binaries — a checked-in
.docx cannot be reviewed in a diff, drifts from the vocabulary the moment
a snapshot key changes, and would have carried exactly the two wrong
placeholders above forever. `tests/unit/test_template_samples.py` asserts
every placeholder in every generated sample resolves against
`sample_snapshot()`, which is a promise a binary cannot make.

The .docx is written directly as a minimal OOXML package rather than with
python-docx: the samples need paragraphs, bold, and a table, the format
for that is a few hundred bytes of XML, and it keeps the Application
tier's dependency list at the one thing that actually earns its place
(mammoth, which reads Word files we did not write).
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from .report_snapshot import build_service_report_snapshot
from .snapshot import build_quote_snapshot

SAMPLE_DOCUMENT_TYPES = ("quote", "service_report")

# Frozen, so the generated sample is byte-identical run to run and a
# download can be cached. A moving date would also make the tests that
# assert on the sample's content depend on the day they run.
_SAMPLE_AT = datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc)

_SAMPLE_COMPANY = {
    "legal_name": "บริษัท ชาญ แอร์ เซอร์วิส จำกัด",
    "company_name": "ชาญแอร์",
    "tax_id": "0105560000000",
    "company_address": "99/1 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110",
    "company_phone": "02-123-4567",
    "company_email": "sales@channair.co.th",
    # Set, so {{totals.vat_amount}} and {{totals.vat_rate_percent}} are
    # real values in the sample rather than the None a non-registered
    # shop gets — a placeholder demonstrated with nothing behind it is
    # indistinguishable from a placeholder that does not work.
    "vat_rate": "0.07",
    "missing_for_documents": [],
}


def sample_quote_snapshot() -> dict:
    """A representative quote, frozen through the real builder."""
    return build_quote_snapshot(
        quote={
            "quote_id": "QT-2026-0042",
            "status": "sent",
            "valid_until": "2026-09-30",
            "discount_amount": "500.00",
            "products": [
                {
                    "product_name": "เครื่องปรับอากาศ 12,000 BTU (Inverter)",
                    "qty": 2,
                    "quoted_unit_price": "18500.00",
                    "notes": "รับประกันคอมเพรสเซอร์ 5 ปี",
                },
                {
                    "product_name": "ค่าติดตั้งพร้อมท่อน้ำยา 4 เมตร",
                    "qty": 2,
                    "quoted_unit_price": "3500.00",
                    "notes": "ราคารวมค่าแรงและอุปกรณ์มาตรฐาน",
                },
            ],
        },
        deal={"deal_id": "DL-2026-0100", "products": []},
        customer={
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "phone": "0812345678",
            "email": "somchai@example.com",
            "address": "45/7 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540",
        },
        company=_SAMPLE_COMPANY,
        issued_at=_SAMPLE_AT,
    )


def sample_service_report_snapshot() -> dict:
    """A representative service report, frozen through the real builder."""
    return build_service_report_snapshot(
        report={
            "report_id": "SR-2026-0088",
            "status": "approved",
            "created_at": "2026-09-01",
            "report_data": {
                "found_issue": "คอยล์เย็นอุดตัน น้ำยาแอร์ต่ำกว่ามาตรฐาน",
                "work_done": "ล้างคอยล์เย็นและคอยล์ร้อน เติมน้ำยา R32 ตรวจรั่ว",
                "parts_changed": "ฟิลเตอร์อากาศ 1 ชุด",
                "notes": "แนะนำให้ล้างแอร์ทุก 6 เดือน",
            },
        },
        ticket={
            "ticket_number": "TK-2026-0311",
            "customer_name": "สมชาย ใจดี",
            "customer_phone": "0812345678",
            "service_address": "45/7 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540",
            "serial_number": "SN-AC-2024-8891",
            "issue_description": "แอร์ไม่เย็น มีน้ำหยดจากตัวเครื่อง",
            "scheduled_date": "2026-09-01",
            "scheduled_time": "09:00",
        },
        company=_SAMPLE_COMPANY,
        technician={"name": "อนุชา ช่างเก่ง", "phone": "0898765432"},
        approvals=[],
        issued_at=_SAMPLE_AT,
    )


def sample_snapshot(document_type: str = "quote") -> dict:
    """The representative snapshot for a document type.

    Used in three places that must agree: the upload's "these
    placeholders will come out blank" warning, the preview render, and
    the sample .docx files. Three different sample shapes was the bug.
    """
    if document_type == "service_report":
        return sample_service_report_snapshot()
    return sample_quote_snapshot()


# ---------------------------------------------------------------- legend
#
# (placeholder, what it becomes, in Thai). This IS the tenant-facing
# vocabulary: it drives the legend printed at the top of every sample
# .docx, and a test asserts every entry resolves against
# `sample_snapshot()`. A placeholder that stops resolving fails a test
# here rather than printing a gap on a customer's quotation.

LEGEND: dict[str, list[tuple[str, str]]] = {
    "quote": [
        ("company.name", "ชื่อบริษัทตามหนังสือรับรอง"),
        ("company.trading_name", "ชื่อร้าน / ชื่อที่ใช้ค้าขาย"),
        ("company.tax_id", "เลขประจำตัวผู้เสียภาษี"),
        ("company.address", "ที่อยู่บริษัท"),
        ("company.phone", "เบอร์โทรบริษัท"),
        ("company.email", "อีเมลบริษัท"),
        ("customer.name", "ชื่อลูกค้า"),
        ("customer.phone", "เบอร์โทรลูกค้า"),
        ("customer.email", "อีเมลลูกค้า"),
        ("customer.address", "ที่อยู่ลูกค้า"),
        ("quote.quote_id", "เลขที่ใบเสนอราคา"),
        ("quote.valid_until", "ยืนราคาถึงวันที่"),
        ("quote.status", "สถานะใบเสนอราคา"),
        ("deal.deal_id", "เลขที่ดีลอ้างอิง"),
        ("issued_on", "วันที่ออกเอกสาร"),
        ("totals.subtotal", "รวมเป็นเงิน (ก่อนส่วนลด)"),
        ("totals.discount_amount", "ส่วนลด"),
        ("totals.net_total", "ยอดหลังหักส่วนลด"),
        ("totals.vat_rate_percent", "อัตราภาษีมูลค่าเพิ่ม (%)"),
        ("totals.vat_amount", "ภาษีมูลค่าเพิ่ม"),
        ("totals.grand_total", "จำนวนเงินรวมทั้งสิ้น"),
    ],
    "service_report": [
        ("company.name", "ชื่อบริษัทตามหนังสือรับรอง"),
        ("company.trading_name", "ชื่อร้าน / ชื่อที่ใช้ค้าขาย"),
        ("company.tax_id", "เลขประจำตัวผู้เสียภาษี"),
        ("company.address", "ที่อยู่บริษัท"),
        ("company.phone", "เบอร์โทรบริษัท"),
        ("report.report_id", "เลขที่ใบรายงาน"),
        ("report.status", "สถานะใบรายงาน"),
        ("report.found_issue", "อาการ / ปัญหาที่พบ"),
        ("report.work_done", "งานที่ทำ"),
        ("report.parts_changed", "อะไหล่ที่เปลี่ยน"),
        ("report.notes", "หมายเหตุ"),
        ("ticket.ticket_number", "เลขที่งานซ่อม"),
        ("ticket.customer_name", "ชื่อลูกค้า"),
        ("ticket.customer_phone", "เบอร์โทรลูกค้า"),
        ("ticket.service_address", "สถานที่เข้าบริการ"),
        ("ticket.serial_number", "หมายเลขเครื่อง (S/N)"),
        ("ticket.issue_description", "อาการที่ลูกค้าแจ้ง"),
        ("ticket.scheduled_date", "วันที่นัดหมาย"),
        ("ticket.scheduled_time", "เวลานัดหมาย"),
        ("technician.name", "ชื่อช่างผู้ปฏิบัติงาน"),
        ("technician.phone", "เบอร์โทรช่าง"),
        ("issued_on", "วันที่ออกเอกสาร"),
    ],
}

# The repeating row inside `{{#line_items}}`, which only the quote has.
LINE_ITEM_LEGEND: list[tuple[str, str]] = [
    ("item.index", "ลำดับที่"),
    ("item.product_name", "ชื่อสินค้า / รายการ"),
    ("item.qty", "จำนวน"),
    ("item.unit_price", "ราคาต่อหน่วย"),
    ("item.line_total", "รวมเงินบรรทัดนี้"),
    ("item.notes", "หมายเหตุของรายการ"),
]


def placeholders_used_by_samples(document_type: str) -> list[str]:
    """Every placeholder a generated sample puts in the document."""
    names = [name for name, _ in LEGEND[document_type]]
    if document_type == "quote":
        names += [name for name, _ in LINE_ITEM_LEGEND]
    return names


# ------------------------------------------------------- minimal OOXML

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# Heading styles carry `w:name` values mammoth recognises, so a heading
# typed in the sample comes back as <h1>/<h2> rather than a bold <p>.
_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
<w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
</w:styles>"""


def _x(text: str) -> str:
    """XML-escape. `&` first, or the escapes escape each other."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _run(text: str, *, bold: bool = False) -> str:
    props = "<w:b/>" if bold else ""
    # xml:space="preserve" or Word eats the leading/trailing spaces that
    # separate a placeholder from its label.
    return (
        f"<w:r><w:rPr>{props}<w:rFonts w:ascii=\"Calibri\" w:hAnsi=\"Calibri\" "
        f"w:cs=\"Cordia New\"/><w:szCs w:val=\"28\"/></w:rPr>"
        f'<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'
    )


def _para(text: str = "", *, bold: bool = False, style: str = "") -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    body = _run(text, bold=bold) if text else ""
    return f"<w:p>{props}{body}</w:p>"


def _cell(text: str, *, bold: bool = False) -> str:
    return (
        "<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>"
        f"{_para(text, bold=bold)}</w:tc>"
    )


def _table(rows: list[list[str]], *, header: bool = True) -> str:
    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="6" w:space="0" w:color="999999"/>'
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    out = [
        f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
        f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr>"
    ]
    for index, row in enumerate(rows):
        cells = "".join(_cell(c, bold=header and index == 0) for c in row)
        out.append(f"<w:tr>{cells}</w:tr>")
    out.append("</w:tbl>")
    # A table must be followed by a paragraph or Word reports the document
    # as needing repair when two tables meet or a table ends the body.
    return "".join(out) + _para()


def _package(body_xml: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body_xml}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
        "</w:sectPr></w:body></w:document>"
    )
    buffer = io.BytesIO()
    # Deflate, no timestamps that vary: the same call produces the same
    # bytes every time, so the download is cacheable and testable.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", _CONTENT_TYPES),
            ("_rels/.rels", _ROOT_RELS),
            ("word/_rels/document.xml.rels", _DOC_RELS),
            ("word/styles.xml", _STYLES),
            ("word/document.xml", document),
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


# ------------------------------------------------------ the sample files

def _intro_th(example: str) -> str:
    """The opening paragraph, with an example placeholder from THIS
    document type's own vocabulary. A quote placeholder in the service
    report sample resolved to nothing, which made the sample warn about
    itself the moment it was uploaded."""
    return (
        "เอกสารนี้คือ “ตัวอย่างแบบฟอร์ม” สำหรับให้ร้านนำไปแก้เป็นแบบของตัวเอง "
        f"ข้อความในวงเล็บปีกกาสองชั้น เช่น {{{{{example}}}}} คือช่องที่ระบบจะแทนที่ด้วย "
        "ข้อมูลจริงตอนออกเอกสาร ส่วนข้อความอื่นจะพิมพ์ออกมาตามที่พิมพ์ไว้"
    )
_INTRO_HOWTO_TH = (
    "วิธีใช้: แก้ไขหน้าตา สี โลโก้ และข้อความได้ตามต้องการ แต่ให้คงตัวอักษรในวงเล็บ "
    "ปีกกาไว้ให้ครบ (พิมพ์ติดกันไม่มีเว้นวรรค) แล้วบันทึกเป็นไฟล์ .docx "
    "จากนั้นอัปโหลดกลับเข้าหน้า “แบบฟอร์มเอกสาร” กด “ดูตัวอย่าง” เพื่อดูผลก่อน "
    "แล้วจึงกด “เผยแพร่”"
)
_LEGEND_HEAD_TH = "ตารางอธิบายช่องข้อมูล (ลบตารางนี้ออกได้เมื่อแก้เสร็จ)"


def _legend_block(document_type: str) -> str:
    rows = [["ช่องข้อมูล", "จะกลายเป็น"]]
    rows += [[f"{{{{{name}}}}}", label] for name, label in LEGEND[document_type]]
    if document_type == "quote":
        rows += [
            ["{{#line_items}} ... {{/line_items}}", "บล็อกรายการสินค้า (ทำซ้ำต่อ 1 รายการ)"],
        ]
        rows += [[f"{{{{{name}}}}}", label] for name, label in LINE_ITEM_LEGEND]
    return (
        _para(_LEGEND_HEAD_TH, style="Heading2")
        + _table(rows)
    )


def _quote_sample_body() -> str:
    return "".join([
        _para("ตัวอย่างแบบฟอร์มใบเสนอราคา", style="Heading1"),
        _para(_intro_th("customer.name")),
        _para(_INTRO_HOWTO_TH),
        _legend_block("quote"),
        _para("— ตัดตั้งแต่บรรทัดนี้ลงไปคือตัวแบบฟอร์มจริง —"),
        _para(),
        _para("{{company.name}}", bold=True, style="Heading1"),
        _para("{{company.address}}"),
        _para("โทร {{company.phone}}   อีเมล {{company.email}}"),
        _para("เลขประจำตัวผู้เสียภาษี {{company.tax_id}}"),
        _para(),
        _para("ใบเสนอราคา", style="Heading1"),
        _table([
            ["เลขที่", "{{quote.quote_id}}", "วันที่", "{{issued_on}}"],
            ["ยืนราคาถึง", "{{quote.valid_until}}", "อ้างอิงดีล", "{{deal.deal_id}}"],
        ], header=False),
        _para("เรียน / เสนอราคาให้", style="Heading2"),
        _para("{{customer.name}}"),
        _para("{{customer.address}}"),
        _para("โทร {{customer.phone}}   อีเมล {{customer.email}}"),
        _para(),
        _para("รายการสินค้าและบริการ", style="Heading2"),
        _table([
            ["ลำดับ", "รายการ", "จำนวน", "ราคา/หน่วย", "จำนวนเงิน"],
            # One row, wrapped in the repeat markers. `docx.py`'s
            # polish step lifts the markers out of the cells so the ROW
            # repeats — put them anywhere in the row you want repeated.
            [
                "{{#line_items}}{{item.index}}",
                "{{item.product_name}} {{item.notes}}",
                "{{item.qty}}",
                "{{item.unit_price}}",
                "{{item.line_total}}{{/line_items}}",
            ],
        ]),
        _table([
            ["รวมเป็นเงิน", "{{totals.subtotal}}"],
            ["ส่วนลด", "{{totals.discount_amount}}"],
            ["ยอดหลังหักส่วนลด", "{{totals.net_total}}"],
            ["ภาษีมูลค่าเพิ่ม {{totals.vat_rate_percent}}%", "{{totals.vat_amount}}"],
            ["จำนวนเงินรวมทั้งสิ้น", "{{totals.grand_total}}"],
        ], header=False),
        _para("สถานะเอกสาร: {{quote.status}}"),
        _para(),
        _para("ผู้เสนอราคา ..............................        "
              "ผู้อนุมัติ / ลูกค้า .............................."),
    ])


def _service_report_sample_body() -> str:
    return "".join([
        _para("ตัวอย่างแบบฟอร์มรายงานการซ่อม (Service Report)", style="Heading1"),
        _para(_intro_th("ticket.customer_name")),
        _para(_INTRO_HOWTO_TH),
        _legend_block("service_report"),
        _para("— ตัดตั้งแต่บรรทัดนี้ลงไปคือตัวแบบฟอร์มจริง —"),
        _para(),
        _para("{{company.name}}", bold=True, style="Heading1"),
        _para("{{company.address}}"),
        _para("โทร {{company.phone}}   เลขประจำตัวผู้เสียภาษี {{company.tax_id}}"),
        _para(),
        _para("รายงานการซ่อม / ใบแจ้งผลการบริการ", style="Heading1"),
        _table([
            ["เลขที่ใบรายงาน", "{{report.report_id}}", "วันที่", "{{issued_on}}"],
            ["เลขที่งานซ่อม", "{{ticket.ticket_number}}", "สถานะ", "{{report.status}}"],
            ["วันที่นัดหมาย", "{{ticket.scheduled_date}}", "เวลา", "{{ticket.scheduled_time}}"],
        ], header=False),
        _para("ข้อมูลลูกค้าและเครื่อง", style="Heading2"),
        _table([
            ["ลูกค้า", "{{ticket.customer_name}}"],
            ["โทร", "{{ticket.customer_phone}}"],
            ["สถานที่เข้าบริการ", "{{ticket.service_address}}"],
            ["หมายเลขเครื่อง (S/N)", "{{ticket.serial_number}}"],
            ["อาการที่ลูกค้าแจ้ง", "{{ticket.issue_description}}"],
        ], header=False),
        _para("ผลการตรวจและงานที่ทำ", style="Heading2"),
        _table([
            ["อาการ / ปัญหาที่พบ", "{{report.found_issue}}"],
            ["งานที่ทำ", "{{report.work_done}}"],
            ["อะไหล่ที่เปลี่ยน", "{{report.parts_changed}}"],
            ["หมายเหตุ", "{{report.notes}}"],
        ], header=False),
        _para(),
        _para("ช่างผู้ปฏิบัติงาน: {{technician.name}}   โทร {{technician.phone}}"),
        _para(),
        _para("ลงชื่อช่าง ..............................        "
              "ลงชื่อลูกค้า .............................."),
    ])


_SAMPLE_FILENAMES = {
    "quote": "chann-template-quotation.docx",
    "service_report": "chann-template-service-report.docx",
}


def sample_docx_filename(document_type: str) -> str:
    return _SAMPLE_FILENAMES[document_type]


def build_sample_docx(document_type: str) -> bytes:
    """The starter Word file for a document type, generated on the spot."""
    if document_type == "quote":
        return _package(_quote_sample_body())
    if document_type == "service_report":
        return _package(_service_report_sample_body())
    raise KeyError(document_type)
