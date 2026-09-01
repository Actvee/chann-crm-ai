"""Filling a tenant's own template with their document's values.

Placeholder substitution, deliberately, and nothing more. A template
language — Jinja, Handlebars, anything with expressions or loops — would
be a code path a tenant controls, executing on our server, with a
snapshot in scope. The safe version of "upload your own design" is one
that can only put values into holes.

So: `{{quote.quote_id}}` is replaced by a value. There is no way to write
a condition, call a function, or reach outside the snapshot handed in.
Line items are the one repeating thing a quote needs, and they get a
single purpose-built block rather than a general loop.
"""
from __future__ import annotations

import re
from html import escape

# {{ a.b.c }} — dotted paths into the snapshot, whitespace tolerated
# because people type it both ways.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+(?:\.[a-z_]+)*)\s*\}\}", re.IGNORECASE)

# The one repeating construct: everything between the markers is emitted
# once per line item, with {{item.field}} resolved against that item.
_ROW_BLOCK = re.compile(
    r"\{\{#line_items\}\}(.*?)\{\{/line_items\}\}", re.DOTALL | re.IGNORECASE,
)


def _read_path(data: dict, path: str):
    value = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _render_value(value) -> str:
    """A value as safe HTML.

    Escaped without exception. A customer's name containing "<" is not an
    attack, it is a name — but a shop's own template is not a place to
    decide which values are trusted, and unescaped output on a document
    that gets emailed is how a stored-XSS becomes someone else's problem.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        # A bare "True" on a printed document means nothing to a reader.
        return ""
    return escape(str(value))


def fill_template(template: str, snapshot: dict) -> str:
    """The tenant's template with this document's values in it.

    Unknown placeholders resolve to empty rather than raising: a template
    written against an older snapshot shape should print a blank where a
    field used to be, not fail to produce a document at all.
    """
    line_items = snapshot.get("line_items") or []

    def _fill_rows(match: re.Match) -> str:
        body = match.group(1)
        out = []
        for index, item in enumerate(line_items, start=1):
            scoped = {**snapshot, "item": {**item, "index": index}}
            out.append(
                _PLACEHOLDER.sub(
                    lambda m: _render_value(_read_path(scoped, m.group(1))), body,
                )
            )
        return "".join(out)

    # Rows first: the row block's own placeholders must be resolved
    # against each item, not against the document once.
    filled = _ROW_BLOCK.sub(_fill_rows, template)
    return _PLACEHOLDER.sub(
        lambda m: _render_value(_read_path(snapshot, m.group(1))), filled,
    )


def placeholders_in(template: str) -> set[str]:
    """Every path a template asks for.

    Used to tell someone which of their placeholders will come out blank
    BEFORE they publish, rather than after a customer receives a document
    with a gap in it.
    """
    body = _ROW_BLOCK.sub(lambda m: m.group(1), template)
    return {m.group(1).lower() for m in _PLACEHOLDER.finditer(body)}


def unknown_placeholders(template: str, sample: dict) -> list[str]:
    """The paths that resolve to nothing against a real snapshot."""
    unknown = []
    for path in sorted(placeholders_in(template)):
        if path.startswith("item."):
            # Resolved per row, so checked against the first item. `index`
            # is supplied by the filler rather than coming from the
            # snapshot, so it always resolves and must not be reported.
            if path == "item.index":
                continue
            items = sample.get("line_items") or []
            if items and _read_path({"item": items[0]}, path) is None:
                unknown.append(path)
            continue
        if _read_path(sample, path) is None:
            unknown.append(path)
    return unknown
