"""Word (.docx) as a source for a shop's own document template.

The owner's report, 9 Sep 2026: "ไฟล์ที่ควรอัพเข้าไม่ใช่ html แต่ควรรองรับ
เป็น word เพื่อให้รับมาเป็น template ได้". Nobody in a shop writes HTML.
They already have the quotation they use, in Word.

So the upload takes .docx and this module turns it into the HTML the
fill engine (`fill.py`) already understands. What it does NOT do is make
the DOCX the runtime template — `docs/SMARTBROWZ_DOCUMENT_ENGINE.md` §2
is explicit that business history must not be coupled to a mutable Word
file. The .docx is kept as uploaded evidence; the compiled HTML is what
renders, immutably, per published version.

Conversion is mammoth's: it reads only the document body and produces
plain semantic HTML — no VBA, no field codes, no embedded objects, no
external references. That property is why it is used rather than a
general converter: whatever a tenant puts in the file, what comes out is
paragraphs, runs, lists, tables and images, and nothing that executes.
"""
from __future__ import annotations

import io
import re
import zipfile

from .html import _FONT_IMPORT, _FONT_STACK

# 2 MB of Word. A quotation layout with a letterhead logo is well under
# it; anything larger is a document with photographs in it, and it has to
# survive being base64'd through a JSON body as well.
MAX_DOCX_BYTES = 2_000_000

# The compiled HTML shares the upload route's existing cap, so a .docx and
# a .html template are held to the same limit on the thing that actually
# renders.
MAX_COMPILED_CHARS = 512_000

# An OLE2 compound file. Both the old binary .doc AND a password-protected
# .docx look like this — a real .docx is a zip ("PK"). The two are told
# apart by the filename, because the bytes genuinely cannot tell you.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Inline tags Word's conversion can leave INSIDE a placeholder. Word splits
# a typed run wherever it feels like it — a spell-check mark, a language
# switch between Thai and Latin, an accidental bold on one character — and
# each fragment comes back as its own element. `{{customer.name}}` typed as
# one word can therefore arrive as `{{cus<strong>tomer</strong>.name}}`.
_INLINE_TAGS = "strong|em|b|i|u|sup|sub|span|a|br"

# A placeholder, tolerating inline markup inside it. Deliberately refuses
# to cross a block boundary (`<p>`, `<td>`, `<tr>`) or another brace, so a
# stray "{{" in prose cannot swallow the rest of the document.
_SPLIT_PLACEHOLDER = re.compile(
    r"\{\{(?:</?(?:" + _INLINE_TAGS + r")\b[^>]*>|[^<>{}])*?\}\}",
)
_ANY_TAG = re.compile(r"<[^>]+>")

# `{{#line_items}}` typed into the first cell of a Word table row. See
# `polish_converted_html` for why it has to move.
_ROW_WITH_MARKERS = re.compile(
    r"<tr>(?P<body>(?:(?!</?tr\b).)*?)</tr>", re.DOTALL,
)


class DocxConversionError(Exception):
    """A Word file this shop cannot use, with a sentence they can act on.

    Carries both languages because the message is shown verbatim in the
    dashboard and the shop may be running it in either — and because a
    conversion failure is exactly the moment when "something went wrong"
    is the least useful thing to say.
    """

    def __init__(self, message_th: str, message_en: str):
        self.message_th = message_th
        self.message_en = message_en
        super().__init__(f"{message_th} / {message_en}")

    @property
    def detail(self) -> str:
        return f"{self.message_th} ({self.message_en})"


def _reject(message_th: str, message_en: str) -> DocxConversionError:
    return DocxConversionError(message_th, message_en)


def check_docx_bytes(data: bytes, *, filename: str = "") -> None:
    """Everything that can be decided before asking mammoth to parse.

    Separated from the conversion so the caller can reject a 40 MB body
    or an old .doc without ever handing it to a parser.
    """
    name = (filename or "").lower().strip()

    if name.endswith(".doc"):
        raise _reject(
            "ไฟล์ .doc เป็นรูปแบบเก่า เปิดใน Word แล้วเลือก บันทึกเป็น (Save As) "
            "ชนิด Word Document (.docx) แล้วอัปโหลดใหม่",
            "the old .doc format is not supported — open it in Word and "
            "save as Word Document (.docx), then upload again",
        )
    if name and not name.endswith((".docx", ".html", ".htm")):
        raise _reject(
            "รองรับเฉพาะไฟล์ Word (.docx) และ HTML (.html) เท่านั้น",
            "only Word (.docx) and HTML (.html) files are supported",
        )
    if not data:
        raise _reject("ไฟล์ว่าง ไม่มีข้อมูลในไฟล์", "the file is empty")
    if len(data) > MAX_DOCX_BYTES:
        raise _reject(
            f"ไฟล์ใหญ่เกินไป ({len(data) // 1024} KB) "
            f"ต้องไม่เกิน {MAX_DOCX_BYTES // 1024} KB — "
            "ถ้ามีรูปในเอกสาร ลองย่อรูปหรือลบรูปที่ไม่จำเป็นออก",
            f"the file is too large ({len(data) // 1024} KB, "
            f"limit {MAX_DOCX_BYTES // 1024} KB) — shrink or remove images",
        )
    if data[:8] == _OLE_MAGIC:
        # A .docx that is an OLE container is an encrypted one: Word wraps
        # the real zip inside a compound file when you set a password.
        raise _reject(
            "ไฟล์นี้ตั้งรหัสผ่านไว้ ระบบเปิดไม่ได้ "
            "เปิดใน Word แล้วเอารหัสผ่านออก (File > Info > Protect Document) "
            "แล้วบันทึกใหม่",
            "this file is password-protected — remove the password in Word "
            "(File > Info > Protect Document) and save it again",
        )
    if data[:2] != b"PK":
        raise _reject(
            "ไฟล์นี้ไม่ใช่ไฟล์ Word (.docx) ที่ถูกต้อง หรือไฟล์เสียหาย "
            "ลองเปิดใน Word แล้วบันทึกเป็น .docx ใหม่อีกครั้ง",
            "this is not a valid Word (.docx) file, or it is corrupt — "
            "open it in Word and save it as .docx again",
        )

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            broken = archive.testzip()
    except zipfile.BadZipFile:
        raise _reject(
            "ไฟล์เสียหาย เปิดไม่ได้ ลองเปิดใน Word แล้วบันทึกเป็น .docx ใหม่",
            "the file is corrupt and cannot be opened — open it in Word "
            "and save it as .docx again",
        )
    if broken is not None:
        raise _reject(
            "ไฟล์เสียหาย (ข้อมูลภายในไม่ครบ) ลองบันทึกจาก Word ใหม่อีกครั้ง",
            "the file is corrupt (a part of it failed its checksum) — "
            "save it from Word again",
        )
    if "word/document.xml" not in names:
        # A .xlsx or a .zip renamed to .docx: also a PK zip, also opens,
        # and produces a blank template rather than an error if let past.
        raise _reject(
            "ไฟล์นี้เป็นไฟล์บีบอัดแต่ไม่ใช่เอกสาร Word "
            "(ไม่พบเนื้อหาเอกสารข้างใน) ตรวจสอบว่าอัปโหลดไฟล์ถูกไฟล์",
            "this is a zip but not a Word document (no document body "
            "inside) — check that you uploaded the right file",
        )


def convert_docx_to_html(data: bytes, *, filename: str = "") -> str:
    """A shop's Word file as the HTML the fill engine renders.

    The returned string is a complete standalone document — the same
    contract as `html.py`'s built-in template, because it goes to the
    same place: `fill_template` puts values into it and SmartBrowz turns
    the result into a PDF. An HTML fragment would render as an unstyled
    page with no Thai font, which fails silently as boxes on paper.
    """
    check_docx_bytes(data, filename=filename)

    import mammoth

    try:
        result = mammoth.convert_to_html(io.BytesIO(data))
    except Exception as exc:  # mammoth raises its own, and zipfile's
        raise _reject(
            "อ่านไฟล์ Word นี้ไม่สำเร็จ ไฟล์อาจเสียหายหรือถูกสร้างจากโปรแกรมอื่น "
            "ลองเปิดใน Word แล้วบันทึกเป็น .docx ใหม่",
            f"this Word file could not be read ({type(exc).__name__}) — "
            "open it in Word and save it as .docx again",
        )

    body = result.value or ""
    if not _ANY_TAG.sub("", body).strip() and "<img" not in body:
        raise _reject(
            "เอกสารนี้ไม่มีข้อความอยู่เลย ตรวจสอบว่าอัปโหลดไฟล์ถูกไฟล์",
            "this document has no text in it — check that you uploaded "
            "the right file",
        )

    html = polish_converted_html(body)
    if len(html) > MAX_COMPILED_CHARS:
        raise _reject(
            "เอกสารนี้ใหญ่เกินไปหลังแปลง มักเกิดจากรูปภาพความละเอียดสูงในเอกสาร "
            "ลองย่อรูปหรือลบรูปที่ไม่จำเป็นออกแล้วอัปโหลดใหม่",
            "the converted document is too large — this is almost always "
            "high-resolution images; shrink or remove them and try again",
        )
    return html


def polish_converted_html(body: str) -> str:
    """The single place mammoth's output is adapted to the fill engine.

    Three repairs, all of them for things Word does that HTML authors
    never do by hand:

    1. **Rejoin split placeholders.** Word breaks a typed word into runs
       whenever anything about it changes — a proofing mark, a Thai/Latin
       language switch, one bolded character. `{{customer.name}}` comes
       back as `{{cus<strong>tomer</strong>.name}}` and `fill.py`'s regex,
       correctly, does not match it. So inline markup INSIDE a `{{...}}`
       is stripped. Only inline tags, and only within one placeholder, so
       nothing outside the braces is touched.

    2. **Hoist the line-item markers out of the table row.** A person
       building a quotation in Word puts `{{#line_items}}` in the first
       cell of the row that should repeat and `{{/line_items}}` in the
       last. Left where they are, the block repeats the cell CONTENTS and
       every quote prints exactly one row. Moved to wrap `<tr>...</tr>`,
       the row itself repeats, which is what the person meant and what
       they would have written in HTML.

    3. **Frame it as a printable document.** mammoth returns a fragment
       with no font, no page size and no table borders. The Thai font is
       fetched rather than assumed for the reason `html.py` gives: a
       renderer without one produces boxes, and the failure is silent.

    Deliberately NOT done here: sanitising. mammoth's output vocabulary is
    already limited to text markup, and `fill_template` escapes every
    value it substitutes, so nothing tenant-supplied becomes executable
    by passing through this function.
    """
    html = _rejoin_split_placeholders(body)
    html = _hoist_row_markers(html)
    return _frame_document(html)


def _rejoin_split_placeholders(html: str) -> str:
    def _strip(match: re.Match) -> str:
        return _ANY_TAG.sub("", match.group(0))

    return _SPLIT_PLACEHOLDER.sub(_strip, html)


def _hoist_row_markers(html: str) -> str:
    open_marker = "{{#line_items}}"
    close_marker = "{{/line_items}}"

    def _move(match: re.Match) -> str:
        row = match.group(0)
        body = match.group("body")
        if open_marker not in body or close_marker not in body:
            return row
        cleaned = body.replace(open_marker, "").replace(close_marker, "")
        return f"{open_marker}<tr>{cleaned}</tr>{close_marker}"

    return _ROW_WITH_MARKERS.sub(_move, html)


def _frame_document(body: str) -> str:
    """The fragment as a complete A4 document.

    The CSS is deliberately plain: this is a shop's own layout, and the
    less opinion is imposed on it the closer the PDF looks to the Word
    file they uploaded. Borders on tables are the exception, because Word
    tables almost always have them and mammoth drops the formatting.
    """
    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>เอกสาร</title>
<style>
  {_FONT_IMPORT}
  @page {{ size: A4; margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: {_FONT_STACK}; font-size: 13px; color: #111; margin: 0;
          line-height: 1.6; }}
  h1 {{ font-size: 21px; margin: 0 0 10px; }}
  h2 {{ font-size: 16px; margin: 16px 0 6px; }}
  h3 {{ font-size: 14px; margin: 14px 0 6px; }}
  p {{ margin: 0 0 8px; }}
  img {{ max-width: 100%; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  table td, table th {{ border: 1px solid #bbb; padding: 6px 8px;
                        vertical-align: top; }}
  table th {{ background: #f2f2f2; text-align: left; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
