"""The AI drafting a shop's own document template, from a sentence in chat.

The owner, 9 Sep 2026: "ตอนนี้รองรับให้ผู้ใช้พิมพ์ในแชทเพื่อให้ AI ช่วยออกแบบ
ให้ในแชทแล้วใช่ไหม สำหรับ Sale OA กับคนที่มีสิทธิ์". It did not. A shop could
upload a .docx and could edit HTML in the dashboard, but nobody could say
"ออกแบบใบเสนอราคาให้หน่อย" and get one.

`docs/SMARTBROWZ_DOCUMENT_ENGINE.md` §1 is what makes this legal at all:
"AI is permitted in template authoring only. AI is forbidden from the normal
runtime PDF-generation path." Authoring is this module. Nothing here runs when
a quote is issued: by then the template is a fixed, published string and
`fill.py` only substitutes values into it.

Three things this module refuses to get wrong.

**The vocabulary is derived, never written down here.** `samples.py` says why:
a hand-written placeholder list in `routers_phase2.py` had drifted from the
snapshot builders, so uploads were told their placeholders were fine and
shops printed a blank company name. The list the model is given comes from
`LEGEND`/`LINE_ITEM_LEGEND`, which a test asserts resolves against a real
snapshot. If a snapshot key changes, the prompt changes with it.

**What comes back is sanitised before it is stored, not before it is shown.**
`fill.py`'s docstring gives the reasoning for substitution-only filling: a
tenant-controlled code path, running on our server, with another tenant's
snapshot in scope, is the thing to avoid. Markup a model wrote on a tenant's
instruction is exactly that code path arriving by a different door. So the
draft is parsed against a whitelist and REJECTED — never repaired quietly,
never stored and cleaned later. A template that fails is re-asked for.

**Every placeholder is checked against the engine that fills it.** A model
inventing `{{customer.tax_id}}` is not a hypothetical; it is the single most
likely way this feature prints a gap on a customer's document. The draft is
resolved against the real sample snapshot and the person is told, in words,
which holes would come out blank.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

from ..ai.client import complete
from .fill import unknown_placeholders
from .html import _FONT_IMPORT, _FONT_STACK
from .samples import LEGEND, LINE_ITEM_LEGEND, sample_snapshot

# Only the two the renderer actually has a snapshot builder for. A template
# for anything else would pass every check here and then have nothing to
# fill it at issue time.
DESIGNABLE_DOCUMENT_TYPES = ("quote", "service_report")

DOCUMENT_TYPE_LABELS = {
    "quote": {"th": "ใบเสนอราคา", "en": "quotation"},
    "service_report": {"th": "ใบรายงานการซ่อม", "en": "service report"},
}

# Room for a full A4 layout. The chat tier's 1024 default truncates a
# quotation halfway down the totals table, and a truncated document fails
# the sanitiser for an unclosed tag rather than for anything meaningful.
MAX_DRAFT_TOKENS = 6000

# The same ceiling the upload route enforces, applied before the call rather
# than after, so a draft that could never be stored is never stored.
MAX_TEMPLATE_CHARS = 512_000


class TemplateRejected(Exception):
    """The draft cannot be stored, with the reasons in the shop's own words.

    Carried rather than raised-and-lost because the reply has to say what
    was wrong: "ระบบไม่พร้อม" for a model that produced a `<script>` teaches
    the person nothing and invites them to try the same sentence again.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# --------------------------------------------------------------- vocabulary

def vocabulary_for(document_type: str) -> list[tuple[str, str]]:
    """Every placeholder this document type can use, and what it becomes.

    Derived from `samples.py`, which is derived from the snapshot builders.
    Nothing is added here — a placeholder that is not in the legend is one
    `fill_template` would leave blank.
    """
    rows = list(LEGEND[document_type])
    if document_type == "quote":
        rows += list(LINE_ITEM_LEGEND)
    return rows


def known_placeholders(document_type: str) -> set[str]:
    return {name for name, _ in vocabulary_for(document_type)}


def _vocabulary_block(document_type: str) -> str:
    lines = [f"  {{{{{name}}}}} — {label}" for name, label in vocabulary_for(document_type)]
    if document_type == "quote":
        lines.insert(
            len(LEGEND[document_type]),
            "  {{#line_items}} ... {{/line_items}} — wrap ONE <tr> in these two "
            "markers; that row is repeated once per line item, and only inside "
            "it do the {{item.*}} placeholders below resolve",
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ prompt

_SYSTEM_PROMPT = """You design printable business document templates for a \
Thai appliance shop's CRM.

You return ONE HTML document and nothing else. No explanation, no markdown \
fence, no commentary before or after.

THE DOCUMENT
- It is printed on A4 and read in Thai. Headings and labels are Thai unless \
the shop asked otherwise.
- Lay it out as a real Thai business document: the shop's details at the top, \
the document title and number, who it is for, the body, then a signature line.
- Use <table> for anything tabular. Tables are how these documents are read.

PLACEHOLDERS — the only way real data reaches the page
- Write {{placeholder}} where a value goes. At issue time the system replaces \
each one with the shop's real value.
- You may use ONLY the placeholders listed below. A placeholder you invent \
prints as an empty gap on a customer's document, so inventing one is worse \
than leaving the field out.
- Do not put the shop's actual name, address or tax id in the markup. Use the \
{{company.*}} placeholders, so the template stays right when the shop's \
details change.

WHAT YOU MAY WRITE
- These tags only: html, head, body, title, style, div, section, header, \
footer, main, article, p, span, br, hr, h1, h2, h3, h4, h5, h6, strong, b, \
em, i, u, small, table, thead, tbody, tfoot, tr, td, th, caption, colgroup, \
col, ul, ol, li, dl, dt, dd.
- These attributes only: class, id, style, colspan, rowspan, align, valign, \
width, height, lang, dir, scope.
- Style it with ONE <style> block in the head, or with style="" attributes.

WHAT IS REFUSED — a draft containing any of these is thrown away entirely
- <script>, <iframe>, <object>, <embed>, <form>, <link>, <base>, <svg>, <a>.
- Any on* attribute (onclick, onload, onerror, ...).
- Any javascript: URL.
- Any external reference: no <img>, no src=, no href=, no url(...) in CSS, \
no @import. The document must be complete on its own.

THE HOUSE RULES
- The page frame — A4 size, margins, and the Thai web font — is added by the \
system around what you write. Do not add @page, @import, or a font-family \
for the whole body; style the parts, not the page.
- Keep it to one page unless the shop asked for more."""


def build_user_prompt(
    *,
    document_type: str,
    description: str,
    company: dict | None = None,
    previous_html: str | None = None,
) -> str:
    """Everything the model needs for one draft.

    `previous_html` turns this into the refine call: the instruction is
    applied to the draft that already exists rather than to a blank page,
    which is what makes "ตัดโลโก้ออก" mean anything.
    """
    label = DOCUMENT_TYPE_LABELS[document_type]["th"]
    parts = [
        f"เอกสารที่ต้องออกแบบ: {label} (document_type = {document_type})",
        "",
        "ช่องข้อมูลที่ใช้ได้ทั้งหมด (ห้ามใช้ชื่ออื่นนอกจากนี้):",
        _vocabulary_block(document_type),
    ]

    profile = _company_context(company or {})
    if profile:
        # Context, not content: it tells the model what kind of shop this is
        # (registered for VAT, has an email, trades under a short name) so
        # the layout suits them. The values still go in as placeholders.
        parts += [
            "",
            "ข้อมูลร้าน (ใช้เพื่อให้เข้าใจว่าร้านนี้เป็นแบบไหน — "
            "ในแบบฟอร์มให้ใช้ {{company.*}} ไม่ใช่ค่าจริงเหล่านี้):",
            profile,
        ]

    if previous_html:
        parts += [
            "",
            "แบบฟอร์มเดิมที่ต้องแก้ (แก้จากอันนี้ อย่าเริ่มใหม่ "
            "และอย่าทิ้งส่วนที่ผู้ใช้ไม่ได้สั่งให้แก้):",
            previous_html,
            "",
            f"สิ่งที่ผู้ใช้สั่งให้แก้: {description}",
        ]
    else:
        parts += ["", f"สิ่งที่ผู้ใช้อยากได้: {description}"]

    parts += ["", "ตอบกลับเป็น HTML อย่างเดียว"]
    return "\n".join(parts)


_PROFILE_FIELDS = (
    ("legal_name", "ชื่อตามหนังสือรับรอง"),
    ("company_name", "ชื่อร้าน"),
    ("tax_id", "เลขประจำตัวผู้เสียภาษี"),
    ("company_address", "ที่อยู่"),
    ("company_phone", "โทร"),
    ("company_email", "อีเมล"),
)


def _company_context(company: dict) -> str:
    lines = []
    for key, label in _PROFILE_FIELDS:
        value = str(company.get(key) or "").strip()
        if value:
            lines.append(f"  {label}: {value}")
    rate = str(company.get("vat_rate") or "").strip()
    lines.append(
        "  จดทะเบียนภาษีมูลค่าเพิ่ม: ใช่ (ต้องมีบรรทัด VAT)" if rate and rate != "0"
        else "  จดทะเบียนภาษีมูลค่าเพิ่ม: ไม่ (อย่าใส่บรรทัด VAT)"
    )
    return "\n".join(lines)


# -------------------------------------------------------------- extraction

_FENCE = re.compile(r"```(?:html|HTML)?\s*(.*?)```", re.DOTALL)


def extract_html(raw: str) -> str:
    """The markup out of whatever the model wrapped it in.

    Models fence HTML, and they preface it with a sentence however firmly
    they were told not to. Taking the first fenced block, and otherwise
    everything between the first tag and the last, costs nothing and saves a
    re-ask that would cost the person ten seconds and us a model call.
    """
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("<")
    end = text.rfind(">")
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start : end + 1].strip()


# --------------------------------------------------------------- sanitiser

# Structure the model may emit around the document, which is consumed rather
# than kept: the frame this module puts on is the one that ships.
_SKELETON_TAGS = frozenset({"html", "head", "body", "title", "meta", "style"})

_CONTENT_TAGS = frozenset({
    "div", "section", "header", "footer", "main", "article", "aside", "nav",
    "p", "span", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "small", "sub", "sup", "blockquote", "pre",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "colgroup", "col", "ul", "ol", "li", "dl", "dt", "dd",
})

_VOID_TAGS = frozenset({"br", "hr", "col", "meta"})

_ALLOWED_ATTRS = frozenset({
    "class", "id", "style", "colspan", "rowspan", "align", "valign",
    "width", "height", "lang", "dir", "scope", "span",
})

# Named so the refusal can say which one, in words a shop understands.
_TAG_REASONS = {
    "script": "สคริปต์ (<script>)",
    "iframe": "หน้าต่างซ้อน (<iframe>)",
    "object": "วัตถุฝัง (<object>)",
    "embed": "วัตถุฝัง (<embed>)",
    "applet": "วัตถุฝัง (<applet>)",
    "form": "แบบฟอร์มกรอกข้อมูล (<form>)",
    "input": "ช่องกรอกข้อมูล (<input>)",
    "button": "ปุ่ม (<button>)",
    "link": "ไฟล์ภายนอก (<link>)",
    "base": "<base>",
    "svg": "ภาพเวกเตอร์ (<svg>)",
    "math": "<math>",
    "img": "รูปภาพจากภายนอก (<img>)",
    "a": "ลิงก์ (<a>)",
    "video": "วิดีโอ (<video>)",
    "audio": "เสียง (<audio>)",
    "frame": "<frame>",
    "frameset": "<frameset>",
    "template": "<template>",
}

_URL_IN_CSS = re.compile(r"url\s*\(", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import", re.IGNORECASE)
_DANGEROUS_CSS = re.compile(
    r"expression\s*\(|behaviou?r\s*:|-moz-binding|javascript\s*:|vbscript\s*:",
    re.IGNORECASE,
)
_DANGEROUS_URL = re.compile(r"javascript\s*:|vbscript\s*:|data\s*:", re.IGNORECASE)

# --- reading CSS the way a renderer reads it -------------------------------
#
# Review v3, T03: the three checks above matched literal words, so a
# stylesheet spelling the same thing with CSS escapes walked past all of
# them — `u\72l(...)`, `\75rl(...)` and `@\69mport ...` were each accepted.
# Escapes are ordinary CSS syntax, not a trick a parser might refuse:
# https://www.w3.org/TR/2008/REC-CSS2-20080411/syndata.html#characters says
# a backslash followed by 1-6 hex digits (with one optional trailing
# whitespace character, which is swallowed) stands for that code point, and
# a backslash followed by anything else stands for that character. A
# renderer resolves those before it decides what an identifier says; this
# module has to do the same before it decides whether it likes it.
#
# What we do NOT claim: nothing here was rendered and no URL was fetched, so
# this closes "the filter can be spelled around", not "external fetches are
# impossible". See the module docstring of the tests for the same wording.

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# \<1-6 hex><one optional whitespace> | \<newline> (a string continuation)
# | \<any other single character>
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(\r\n|[\n\r\f])|(.))", re.DOTALL)


def _canonical_css(css: str) -> str:
    """The stylesheet as a renderer would read its identifiers.

    Comments go first (they cannot legally split an identifier, and
    dropping them can only reveal text, never hide it), then every escape
    is resolved to the character it stands for.
    """
    text = _CSS_COMMENT.sub("", css or "")

    def _resolve(match: re.Match) -> str:
        hex_digits, continuation, literal = match.groups()
        if hex_digits is not None:
            code = int(hex_digits, 16)
            # 0 and anything outside Unicode is U+FFFD per CSS; surrogates
            # cannot be built in Python and are not worth a special case.
            if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return "�"
            return chr(code)
        if continuation is not None:
            return ""
        return literal or ""

    return _CSS_ESCAPE.sub(_resolve, text)


def _css_problem(css: str, *, where: str) -> str | None:
    """A reason this stylesheet may not be stored, or None.

    Decided on the canonical form AND on the text as written: an escape
    the canonicaliser got wrong must not become a way through, and a
    construct that is dangerous as typed is dangerous either way.
    """
    canonical = _canonical_css(css)
    for text in (canonical, css or ""):
        if _CSS_IMPORT.search(text):
            return f"ดึงไฟล์จากภายนอกด้วย @import ({where})"
        if _URL_IN_CSS.search(text):
            return f"ดึงไฟล์จากภายนอกด้วย url(...) ({where})"
        if _DANGEROUS_CSS.search(text):
            return f"คำสั่งที่รันโค้ดได้ใน CSS ({where})"
    # The belt: an escape sequence is refused on sight rather than only
    # being seen through. A quotation layout has no use for one, so this
    # is "allow what a document needs" rather than "guess every spelling
    # of what it must not have" — which is the failure T03 describes.
    if canonical != _CSS_COMMENT.sub("", css or ""):
        return f"รหัสหลบการตรวจใน CSS (escape sequence) ({where})"
    return None


# What a link and a picture may be, when the profile allows them at all.
# Only ever reached on the Word path (see `sanitise`): a .docx that a shop
# already uses routinely has hyperlinks in the footer and the shop's logo
# in the header, and mammoth renders the logo as an inline data: image
# because there is nowhere else for the bytes to go.
_WORD_LINK_SCHEMES = ("http://", "https://", "mailto:", "tel:")
_WORD_IMAGE_PREFIX = re.compile(r"^data:image/(?:png|jpe?g|gif|bmp|webp|x-emf|x-wmf);base64,", re.IGNORECASE)


class _Sanitiser(HTMLParser):
    """Rebuilds the document from what is allowed, and records what is not.

    Rebuilding rather than editing the input in place is the point: what
    gets stored is assembled from tags and attributes that were each
    checked, so anything the checks did not understand cannot survive by
    being left alone. A regex pass over the original string has the
    opposite property.

    `word` widens the vocabulary — and only the vocabulary — for the .docx
    ingress: links and inline images are what Word documents are made of,
    and refusing them would mean refusing the shop's own quotation. The
    security rules are the same object either way; nothing that executes
    becomes allowed, and both paths still come out rebuilt rather than
    edited (review v3, T02: "same policy at every ingress").
    """

    def __init__(self, *, word: bool = False):
        super().__init__(convert_charrefs=True)
        self.word = word
        self.content_tags = _CONTENT_TAGS | ({"a", "img"} if word else frozenset())
        self.void_tags = _VOID_TAGS | ({"img"} if word else frozenset())
        self.problems: list[str] = []
        self.body: list[str] = []
        self.styles: list[str] = []
        self._open: list[str] = []
        self._in_style = False

    def _refused_tag(self, tag: str) -> str | None:
        """The reason this tag may not appear, or None if the profile
        allows it. A tag named in _TAG_REASONS is refused unless this
        profile puts it back in the content vocabulary."""
        if tag in self.content_tags:
            return None
        return _TAG_REASONS.get(tag)

    # -- reporting

    def _reject(self, reason: str) -> None:
        if reason not in self.problems:
            self.problems.append(reason)

    # -- tags

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        refused = self._refused_tag(tag)
        if refused:
            self._reject(refused)
            return
        if tag == "style":
            self._in_style = True
            return
        if tag == "meta":
            for name, value in attrs:
                if (name or "").lower() == "http-equiv":
                    self._reject("<meta http-equiv> ที่สั่งให้เปลี่ยนหน้า")
            return
        if tag in _SKELETON_TAGS:
            return
        if tag not in self.content_tags:
            self._reject(f"แท็กที่ไม่รองรับ (<{tag}>)")
            return
        rendered = self._attrs(tag, attrs)
        if tag in self.void_tags:
            self.body.append(f"<{tag}{rendered}>")
            return
        self._open.append(tag)
        self.body.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        refused = self._refused_tag(tag)
        if refused:
            self._reject(refused)
            return
        if tag in _SKELETON_TAGS:
            return
        if tag not in self.content_tags:
            self._reject(f"แท็กที่ไม่รองรับ (<{tag}>)")
            return
        self.body.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "style":
            self._in_style = False
            return
        if tag in _SKELETON_TAGS or tag in self.void_tags:
            return
        if tag not in self.content_tags:
            return
        # Close back to the matching open tag. A model that forgets </td>
        # is a formatting slip, not an attack, and the document should
        # still come out as a document.
        if tag not in self._open:
            return
        while self._open:
            current = self._open.pop()
            self.body.append(f"</{current}>")
            if current == tag:
                break

    def _attrs(self, tag: str, attrs) -> str:
        out = []
        for raw_name, raw_value in attrs:
            name = (raw_name or "").lower()
            value = raw_value or ""
            if name.startswith("on"):
                self._reject(f"คำสั่งที่ทำงานเองเมื่อเปิดเอกสาร ({name})")
                continue
            if self.word and tag == "a" and name == "href":
                # A hyperlink out of a Word document. Scheme allowlist, not
                # a denylist of the two bad ones: anything this does not
                # recognise (javascript:, data:, file:, a scheme invented
                # tomorrow) simply does not survive the rebuild.
                if not value.strip().lower().startswith(_WORD_LINK_SCHEMES):
                    self._reject("ลิงก์ที่ระบบไม่รองรับใน <a href>")
                    continue
                out.append(f' href="{escape(value.strip(), quote=True)}"')
                continue
            if self.word and tag == "img" and name == "src":
                # mammoth inlines the picture; a reference OUT of the
                # document is not a picture Word put there and is refused.
                if not _WORD_IMAGE_PREFIX.match(value.strip()):
                    self._reject("รูปภาพที่ดึงมาจากภายนอกเอกสาร (<img src>)")
                    continue
                out.append(f' src="{escape(value.strip(), quote=True)}"')
                continue
            if name in ("src", "srcset", "href", "background", "data", "action",
                        "formaction", "poster", "xlink:href"):
                self._reject(f"การดึงไฟล์จากภายนอก ({name})")
                continue
            if name.startswith("data-"):
                continue
            if name not in _ALLOWED_ATTRS and not (
                self.word and tag == "img" and name == "alt"
            ):
                continue
            if _DANGEROUS_URL.search(value):
                self._reject(f"ที่อยู่ที่รันโค้ดได้ ({name})")
                continue
            if name == "style":
                problem = _css_problem(value, where=f"<{tag} style=…>")
                if problem:
                    self._reject(problem)
                    continue
            out.append(f' {name}="{escape(value, quote=True)}"')
        return "".join(out)

    # -- text

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)
            return
        self.body.append(escape(data, quote=False))

    def handle_comment(self, data):
        # Dropped, not kept: a comment cannot help a printed document, and
        # conditional comments are a way to hide markup from this parser.
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        self._reject("คำสั่งประมวลผล (<? … ?>)")

    def unknown_decl(self, data):
        self._reject("บล็อก CDATA")

    def close_all(self) -> None:
        while self._open:
            self.body.append(f"</{self._open.pop()}>")


def sanitise(raw_html: str, *, word: bool = False) -> tuple[str, str]:
    """The draft as (body markup, CSS), or `TemplateRejected` with reasons.

    Nothing is repaired into safety. The two outputs are what survived a
    whitelist, and the caller frames them; the input string is discarded.

    Every template ingress goes through here (review v3, T02): the AI
    design, a shop's uploaded .html, and — with `word=True`, which adds
    links and inline images and nothing else — the HTML mammoth makes out
    of a shop's .docx. Before that fix the upload path stored whatever it
    was given, so a template holding `<script>`, an event handler or an
    `<iframe>` was accepted and kept unchanged.
    """
    html = (raw_html or "").strip()
    if not html:
        raise TemplateRejected(["AI ไม่ได้ส่งแบบฟอร์มกลับมา"])

    parser = _Sanitiser(word=word)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — a parser error is a rejection, not a crash
        raise TemplateRejected(["อ่านแบบฟอร์มที่ AI ส่งมาไม่ได้"]) from None
    parser.close_all()

    css = "".join(parser.styles).strip()
    problem = _css_problem(css, where="<style>")
    if problem:
        parser._reject(problem)

    if parser.problems:
        raise TemplateRejected(parser.problems)

    body = "".join(parser.body).strip()
    if not body:
        raise TemplateRejected(["แบบฟอร์มที่ AI ส่งมาว่างเปล่า"])
    return body, css



# ------------------------------------------- what is already in the store
#
# Review v3, T02 asked for the versions ALREADY stored to be checked, not
# only the new ones. Those cannot simply be run back through `sanitise`:
# a stored template is a whole framed document, and the frame this module
# writes itself carries `@import url(...)` for the Thai font (see
# `documents/html.py` for why the font is fetched rather than assumed), so
# the ingress rules would reject our own output.
#
# So the read-side guard is narrower and answers exactly the question the
# review proved mattered — is there ACTIVE CONTENT in this stored file? —
# rather than re-litigating the whole layout. What it finds, the callers
# refuse to serve or fall back away from.
#
# The limits of the claim, in the review's own terms: this makes stored
# active content unreachable through the paths below. It is not evidence
# that any of it ever executed anywhere; nothing was rendered to prove
# that, and nothing here should be described as having stopped an
# exploit.

_ACTIVE_TAGS = frozenset({
    "script", "iframe", "object", "embed", "applet", "frame", "frameset",
    "form", "input", "button", "base", "link", "svg", "math", "template",
    "meta",
})


class _ActiveContentScan(HTMLParser):
    """Reads a stored document and names what executes or loads in it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def _note(self, what: str) -> None:
        if what not in self.found:
            self.found.append(what)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "meta":
            if any((name or "").lower() == "http-equiv" for name, _ in attrs):
                self._note("<meta http-equiv>")
            return
        if tag in _ACTIVE_TAGS:
            self._note(f"<{tag}>")
        for raw_name, raw_value in attrs:
            name = (raw_name or "").lower()
            if name.startswith("on"):
                self._note(f"{name}=")
            elif _DANGEROUS_URL.search(raw_value or "") and name in (
                "href", "src", "action", "formaction", "data", "poster",
                "background", "xlink:href",
            ):
                # data: on an <img> in a Word-derived template is the
                # shop's own logo and is not active content; anywhere
                # else a code-capable URL is.
                if not (tag == "img" and name == "src"):
                    self._note(f"{name}=…")

    handle_startendtag = handle_starttag

    def handle_pi(self, data):
        self._note("<? … ?>")

    def unknown_decl(self, data):
        self._note("CDATA")


class ActiveContentInTemplate(Exception):
    """A stored template that will not be rendered or served as it is.

    Raised by the callers, not here, so each one decides what to do: the
    document paths fall back to the built-in layout, the preview refuses
    and says why.
    """


def active_content_in(html: str) -> list[str]:
    """What in this stored template executes or pulls something in.

    Empty means "nothing found by this check" — which is not the same as
    "safe"; it is the same thing the ingress rebuild guarantees by
    construction, checked after the fact on files stored before the
    rebuild existed.
    """
    scan = _ActiveContentScan()
    try:
        scan.feed(html or "")
        scan.close()
    except Exception:  # noqa: BLE001 — unparseable is not servable
        return ["อ่านไฟล์แบบฟอร์มนี้ไม่ได้"]
    return scan.found


# ------------------------------------------------------------------- frame

def frame(body: str, css: str = "") -> str:
    """The sanitised design as the complete document the engine stores.

    The frame is not the model's to choose. A renderer without a Thai font
    prints boxes and the failure is silent — `documents/html.py` explains
    why the font is fetched rather than assumed — so the @font import, the
    A4 page and the base type come from the same constants the built-in
    template uses, every time, whatever the model returned.
    """
    shop_css = f"{_SHOP_CSS_MARKER}{css.strip()}" if css.strip() else ""
    return f"{_FRAME_HEAD}{shop_css}{_FRAME_MIDDLE}{body}{_FRAME_TAIL}"


# The frame, in the three pieces `split_frame` needs to take it apart
# again. Assembled below into exactly the string `frame` used to return.
_FRAME_HEAD = f"""<!DOCTYPE html>
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
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  table td, table th {{ border: 1px solid #bbb; padding: 6px 8px;
                        vertical-align: top; }}
  table th {{ background: #f2f2f2; text-align: left; }}"""
_SHOP_CSS_MARKER = "\n  /* --- the shop's own design --- */\n  "
_FRAME_MIDDLE = "\n</style>\n</head>\n<body>\n"
_FRAME_TAIL = "\n</body>\n</html>"


def split_frame(html: str) -> tuple[str, str] | None:
    """`(body, shop CSS)` if this document is one this module framed, else None.

    Needed because a framed document comes back through the ingress: the
    chat designer sanitises and frames, then hands the result to the same
    upload route a person uses, and a shop can download a stored template
    and upload it again. Re-sanitising the whole document would reject our
    OWN frame — it fetches the Thai font with `@import url(...)`, which is
    exactly what a shop's stylesheet is not allowed to do (`documents/html.py`
    explains why the font cannot be assumed) — so the frame is recognised,
    removed, and what was inside it is checked on its own.

    Recognition is an exact match against the constant above, so "looks
    framed" cannot be spelled by hand to buy anything: matching it means
    the CSS IS ours, byte for byte, and the shop's own CSS and the body
    are both handed back to the sanitiser regardless.
    """
    text = html or ""
    if not text.startswith(_FRAME_HEAD) or not text.endswith(_FRAME_TAIL):
        return None
    rest = text[len(_FRAME_HEAD):-len(_FRAME_TAIL)]
    css, separator, body = rest.partition(_FRAME_MIDDLE)
    if not separator:
        return None
    if css.startswith(_SHOP_CSS_MARKER):
        css = css[len(_SHOP_CSS_MARKER):]
    elif css:
        return None
    return body, css


# -------------------------------------------------------------- validation

def invented_placeholders(template_html: str, document_type: str) -> list[str]:
    """The placeholders the engine has nothing to put in.

    Checked against the real sample snapshot rather than against the legend
    alone, so this answers the question that matters — "will this print a
    gap?" — with the same machinery the upload route and the preview use.
    """
    return unknown_placeholders(template_html, sample_snapshot(document_type))


def missing_essentials(template_html: str, document_type: str) -> list[str]:
    """Placeholders a document of this type is wrong without.

    A quotation with no quote number and no total is a poster, not a
    quotation. Reported to the person rather than rejected: an unusual
    layout may be deliberate, and refusing it would make the feature
    unusable for the shop that wanted exactly that.
    """
    from .fill import placeholders_in

    used = placeholders_in(template_html)
    required = {
        "quote": ("company.name", "quote.quote_id", "totals.grand_total"),
        "service_report": ("company.name", "report.report_id", "ticket.ticket_number"),
    }[document_type]
    return [name for name in required if name not in used]


# ------------------------------------------------------------------ drafting

async def draft_template(
    *,
    document_type: str,
    description: str,
    company: dict | None = None,
    previous_html: str | None = None,
    ai_client=None,
) -> tuple[str, list[str]]:
    """One draft: ask, sanitise, frame, check. Returns (html, invented).

    Raises `TemplateRejected` when what came back cannot be stored, and
    `AINotConfigured` / `AIUnavailable` straight through — the caller turns
    those into the standard unavailable reply, and neither leaves anything
    behind, because nothing is written until this function has returned.
    """
    if document_type not in DESIGNABLE_DOCUMENT_TYPES:
        raise ValueError(f"not a designable document type: {document_type!r}")

    raw = await complete(
        system_prompt=_SYSTEM_PROMPT,
        user_message=build_user_prompt(
            document_type=document_type, description=description,
            company=company, previous_html=previous_html,
        ),
        max_tokens=MAX_DRAFT_TOKENS,
        # Not 0.0: a layout is a design task, and every shop asking for
        # "ใบเสนอราคาสวย ๆ" getting byte-identical markup is a worse product
        # than a little variation. Still low — this is a business form.
        temperature=0.3,
        client=ai_client,
    )

    body, css = sanitise(extract_html(raw))
    html = frame(body, css)
    if len(html) > MAX_TEMPLATE_CHARS:
        raise TemplateRejected(["แบบฟอร์มยาวเกินกว่าที่ระบบเก็บได้"])
    return html, invented_placeholders(html, document_type)
