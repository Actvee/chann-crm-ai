"""The designed template as a Word file the shop can keep editing.

The product loop `docs/SMARTBROWZ_DOCUMENT_ENGINE.md` §1 describes is
"DOCX upload -> compilation -> preview -> publish". A template the AI drew
in chat starts as HTML instead, which left the shop at a dead end: they
could publish it or discard it, but they could not open it in Word, move a
column, and upload it back — the thing `samples.py` was written to enable
for the starter files.

So the draft goes the other way too. This is a deliberately small
converter: it handles exactly the markup `design.py`'s sanitiser allows
through — headings, paragraphs, tables, and inline emphasis — and it reuses
`samples.py`'s OOXML writer rather than adding python-docx to the
Application tier, which keeps the dependency list at the one Word library
that earns its place (mammoth, for files we did not write).

It is a convenience, not a contract. The published template is always the
HTML; this file is a copy to edit. Anything the converter cannot express
is dropped rather than approximated, because a Word file that silently
disagrees with the published template is worse than a simpler one.
"""
from __future__ import annotations

from html.parser import HTMLParser

from .samples import _package, _para, _table

# Word's built-in heading styles, as far as `samples._STYLES` defines them.
_HEADING_STYLE = {
    "h1": "Heading1", "h2": "Heading2", "h3": "Heading2",
    "h4": "Heading2", "h5": "Heading2", "h6": "Heading2",
}

_BLOCK_TAGS = frozenset({
    "p", "div", "section", "header", "footer", "main", "article", "aside",
    "nav", "blockquote", "pre", "li", "dt", "dd", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6",
})


class _ToDocx(HTMLParser):
    """Flatten the document into Word blocks, in order.

    A printed business form is a sequence of paragraphs and tables, so
    that is what this produces. Nesting beyond a table's own cells carries
    no meaning worth preserving here — a <div> wrapping three paragraphs
    is a styling hook, not structure — and flattening it keeps the OOXML
    valid, which nesting mistakes very quickly do not.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._text: list[str] = []
        self._style = ""
        # Table state. `_table_stack` exists only so a stray nested table
        # cannot corrupt the row being built.
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._header = False
        self._seen_th = False
        # Text sitting between cells — which is exactly where
        # `{{#line_items}}` and `{{/line_items}}` land, since the sanitiser
        # keeps them wrapped around the <tr> rather than inside a cell.
        self._loose: list[str] = []

    # -- paragraph accumulation

    def _flush(self) -> None:
        text = " ".join("".join(self._text).split())
        if text:
            self.blocks.append(_para(text, style=self._style))
        self._text = []
        self._style = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._flush()
            self._rows, self._row, self._cell = [], None, None
            self._seen_th = False
            return
        if self._rows is not None:
            if tag == "tr":
                self._row = []
                # Whatever preceded this row belongs to its first cell —
                # `samples.py` writes the open marker there too.
                self._prefix = "".join(self._loose).strip()
                self._loose = []
            elif tag in ("td", "th"):
                self._cell = []
                if tag == "th":
                    self._seen_th = True
            elif tag == "br" and self._cell is not None:
                self._cell.append(" ")
            return
        if tag == "br":
            self._text.append(" ")
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            self._style = _HEADING_STYLE.get(tag, "")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            # A close marker trails the row it closes; it belongs on that
            # row's last cell, again matching how the samples are written.
            trailing = "".join(self._loose).strip()
            if trailing and self._rows and self._rows[-1]:
                self._rows[-1][-1] = f"{self._rows[-1][-1]}{trailing}"
            self._loose = []
            if self._rows:
                # `header=` bolds the first row. Only claim it when the
                # markup actually said <th>: bolding a data row because a
                # table happened to start with one is a visible wrong.
                self.blocks.append(_table(self._rows, header=self._seen_th))
            self._rows, self._row, self._cell = None, None, None
            return
        if self._rows is not None:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                prefix = getattr(self, "_prefix", "")
                if prefix and self._row:
                    self._row[0] = f"{prefix}{self._row[0]}"
                self._prefix = ""
                self._rows.append(self._row)
                self._row = None
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._rows is not None:
            # Text loose between cells — where the repeat markers land.
            # Dropping it would produce a Word file whose rows do not
            # repeat, so it is folded into the neighbouring row instead.
            if data.strip():
                self._loose.append(data.strip())
        else:
            self._text.append(data)

    def finish(self) -> list[str]:
        self._flush()
        return self.blocks


def html_to_docx(html: str) -> bytes:
    """A .docx carrying the same text, placeholders and tables as `html`.

    Takes the whole document; everything outside <body> contributes no
    text, so the <style> block and the head fall away on their own.
    """
    parser = _ToDocx()
    parser.feed(html or "")
    parser.close()
    blocks = parser.finish()
    if not blocks:
        blocks = [_para("")]
    return _package("".join(blocks))
