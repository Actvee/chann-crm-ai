"""Filling a shop's own template.

The security shape matters more than the feature here: this HTML comes
from a tenant and is rendered on our infrastructure with a snapshot in
scope. Placeholder substitution is the whole design — no expressions, no
loops beyond one purpose-built row block, no way to reach outside the
data handed in.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.documents.fill import (  # noqa: E402
    fill_template,
    placeholders_in,
    unknown_placeholders,
)

SNAPSHOT = {
    "company": {"legal_name": "ร้านแอร์ดี", "phone": "021234567"},
    "customer": {"name": "จุใจ มาติกา", "address": "99/1 สุขุมวิท"},
    "quote": {"quote_id": "Q-2026-0001", "valid_until": "2026-09-15"},
    "line_items": [
        {"name": "พัดลม", "qty": "2", "unit_price": "1500.00", "line_total": "3000.00"},
        {"name": "ค่าติดตั้ง", "qty": "1", "unit_price": "500.00", "line_total": "500.00"},
    ],
    "totals": {"grand_total": "3745.00", "discount_applicable": True},
}


class TestSubstitution:
    def test_a_dotted_path_is_replaced(self):
        assert fill_template("{{quote.quote_id}}", SNAPSHOT) == "Q-2026-0001"

    def test_whitespace_inside_the_braces_is_tolerated(self):
        """People type it both ways and neither is wrong."""
        assert fill_template("{{ quote.quote_id }}", SNAPSHOT) == "Q-2026-0001"

    def test_an_unknown_path_becomes_empty_rather_than_failing(self):
        """A template written against an older snapshot should print a
        blank where a field used to be, not stop the shop issuing
        documents."""
        assert fill_template("[{{no.such.thing}}]", SNAPSHOT) == "[]"

    def test_a_boolean_does_not_print(self):
        """A bare "True" on a printed document means nothing to a reader."""
        assert fill_template("[{{totals.discount_applicable}}]", SNAPSHOT) == "[]"


class TestRowBlock:
    def test_the_block_repeats_once_per_line(self):
        out = fill_template(
            "{{#line_items}}<li>{{item.name}}</li>{{/line_items}}", SNAPSHOT,
        )
        assert out == "<li>พัดลม</li><li>ค่าติดตั้ง</li>"

    def test_rows_are_numbered(self):
        out = fill_template(
            "{{#line_items}}{{item.index}}.{{/line_items}}", SNAPSHOT,
        )
        assert out == "1.2."

    def test_document_placeholders_still_work_around_the_block(self):
        out = fill_template(
            "{{quote.quote_id}}|{{#line_items}}x{{/line_items}}|{{totals.grand_total}}",
            SNAPSHOT,
        )
        assert out == "Q-2026-0001|xx|3745.00"

    def test_a_block_with_no_lines_produces_nothing(self):
        out = fill_template(
            "[{{#line_items}}{{item.name}}{{/line_items}}]",
            {**SNAPSHOT, "line_items": []},
        )
        assert out == "[]"


class TestItIsNotATemplateLanguage:
    """The point of the design. Anything that looks like an expression is
    left alone, because evaluating tenant-authored expressions on our
    server against another document's data is the thing being avoided."""

    def test_an_expression_is_not_evaluated(self):
        out = fill_template("{{ 1 + 1 }}", SNAPSHOT)
        assert "2" not in out

    def test_a_python_attribute_walk_finds_nothing(self):
        out = fill_template("{{quote.__class__}}", SNAPSHOT)
        assert out == ""

    def test_a_dunder_path_cannot_reach_anything(self):
        assert fill_template("{{__builtins__.eval}}", SNAPSHOT) == ""


class TestEscaping:
    def test_values_are_escaped(self):
        """A customer's name containing "<" is a name, not an attack — but
        a tenant's template is not the place to decide which values are
        trusted, and unescaped output on a document that gets emailed is
        how stored XSS becomes someone else's problem."""
        out = fill_template(
            "{{customer.name}}",
            {**SNAPSHOT, "customer": {"name": '<script>alert(1)</script>'}},
        )
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_escaping_applies_inside_row_blocks_too(self):
        out = fill_template(
            "{{#line_items}}{{item.name}}{{/line_items}}",
            {**SNAPSHOT, "line_items": [{"name": "<b>x</b>"}]},
        )
        assert "<b>" not in out


class TestPlaceholderReporting:
    """So a shop finds out a field will be blank before publishing, not
    after a customer receives a document with a gap in it."""

    def test_it_lists_what_a_template_asks_for(self):
        found = placeholders_in(
            "{{company.legal_name}} {{#line_items}}{{item.name}}{{/line_items}}"
        )
        assert found == {"company.legal_name", "item.name"}

    def test_unknown_paths_are_reported(self):
        unknown = unknown_placeholders(
            "{{company.legal_name}} {{company.motto}}", SNAPSHOT,
        )
        assert unknown == ["company.motto"]

    def test_the_row_index_is_not_reported_as_unknown(self):
        """It is supplied by the filler rather than coming from the
        snapshot, so it always resolves."""
        assert unknown_placeholders("{{#line_items}}{{item.index}}{{/line_items}}", SNAPSHOT) == []

    def test_a_valid_template_reports_nothing(self):
        template = """
        <h1>{{company.legal_name}}</h1>
        <p>{{customer.name}} — {{quote.quote_id}}</p>
        {{#line_items}}<tr><td>{{item.name}}</td><td>{{item.line_total}}</td></tr>{{/line_items}}
        <p>{{totals.grand_total}}</p>
        """
        assert unknown_placeholders(template, SNAPSHOT) == []
