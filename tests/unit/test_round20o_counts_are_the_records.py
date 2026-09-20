"""A count has to be the record's, not the page's.

Round 20N made every list search and page in the database. Round 20O
finishes the job on the three lists that load differently — the audit
trail, a record's notes, the chat inbox — and on the one number that was
provably wrong: the customer card said "บันทึก (7)" by measuring the
length of a page capped at twenty, so a customer with thirty notes was
told they had twenty. The cap's number, printed as the record's
(20 ก.ย. 2569).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for tier in ("application", "data"):
    if str(ROOT / tier) not in sys.path:
        sys.path.insert(0, str(ROOT / tier))


def src_of(module: str) -> str:
    return Path(importlib.import_module(module).__file__).read_text(encoding="utf-8")


class TestTheLastThreeListsArePagedToo:
    CASES = (
        ("chann_data.repositories.audit", "AuditRepository", "count_for_license"),
        ("chann_data.repositories.phase6", "NoteRepository", "count_for_entity"),
        ("chann_data.repositories.phase15", "ChatSessionRepository", "count_for_license"),
    )

    def test_each_one_can_count(self):
        for module, name, counter in self.CASES:
            cls = getattr(importlib.import_module(module), name)
            assert hasattr(cls, counter), f"{name}.{counter}"

    def test_the_page_and_the_count_share_one_narrowing(self):
        for module, name, _ in self.CASES:
            cls = getattr(importlib.import_module(module), name)
            assert hasattr(cls, "_narrow"), name
            body = src_of(module)
            body = body[body.index(f"class {name}"):]
            nxt = body.find("\nclass ", 1)
            body = body[:nxt] if nxt > 0 else body
            assert body.count("self._narrow(") >= 2, name

    def test_each_one_takes_an_offset(self):
        import inspect

        for module, name, _ in self.CASES:
            cls = getattr(importlib.import_module(module), name)
            lister = next(
                m for m in ("list_for_license", "list_for_entity") if hasattr(cls, m)
            )
            assert "offset" in inspect.signature(getattr(cls, lister)).parameters, name

    def test_each_one_breaks_the_timestamp_tie_with_an_id(self):
        """Rows written inside one request share a timestamp; without the
        id a page boundary repeats a row or drops one."""
        for module, name, _ in self.CASES:
            body = src_of(module)
            body = body[body.index(f"class {name}"):]
            nxt = body.find("\nclass ", 1)
            body = body[:nxt] if nxt > 0 else body
            assert ".id.desc()" in body, name


class TestTheCustomerCardCountsTheRecord:
    def test_it_asks_for_the_true_total(self):
        chat = src_of("chann_app.services.chat")
        card = chat[chat.index("async def _customer_activity"):]
        card = card[: card.index("\ndef ", 10)]
        assert "list_notes_with_total(" in card
        # The give-away: counting the rows that came back.
        assert "format(n=len(notes))" not in card
        assert "format(n=note_total)" in card

    def test_the_client_reads_the_header(self):
        client = src_of("chann_app.data_client")
        body = client[client.index("async def list_notes_with_total"):]
        body = body[: body.index("\n    async def ", 10)]
        assert "_total_of(resp, rows)" in body


class TestTheCategoriesComeFromTheDatabase:
    """Round 20N built the filter's options from the rows the page held and
    wrote the limitation down rather than leaving it to be found: a
    category used only by a product on page three was missing from the
    filter that would have found it."""

    def test_the_repository_asks_for_distinct(self):
        body = src_of("chann_data.repositories.phase7")
        body = body[body.index("def categories("):]
        body = body[: body.index("\n    def ", 10)]
        assert ".distinct()" in body
        assert "Product.category" in body

    def test_archived_products_do_not_haunt_the_filter(self):
        body = src_of("chann_data.repositories.phase7")
        body = body[body.index("def categories("):]
        body = body[: body.index("\n    def ", 10)]
        assert "archived_at.is_(None)" in body

    def test_the_route_is_reachable_and_guarded(self):
        app = src_of("chann_app.routers_phase2")
        route = app[app.index('@router.get("/licenses/{license_id}/product-categories")'):]
        route = route[: route.index("@router.", 10)]
        assert 'require_any("product.read", "product.manage")' in route
        assert "_require_same_tenant(" in route

    def test_the_screen_asks_for_them_rather_than_deriving_them(self):
        page = (ROOT / "presentation/app/liff/sales/products/ProductList.tsx").read_text(
            encoding="utf-8",
        )
        assert "product-categories" in page
        # The accumulating set that 20N used is gone.
        assert "seenCategories" not in page


class TestTheSimulatorsNumberMeansSomething:
    """"9 not as expected" was six cases answered correctly on the other
    road plus three pieces of gibberish answered correctly as gibberish,
    and I read the sum to the owner twice as if it were a defect list."""

    def test_the_report_splits_the_two(self):
        sim = (ROOT / "scripts/dev/simulate-phrasings.py").read_text(encoding="utf-8")
        assert "answered badly" in sim
        assert "answered fine on the other road" in sim

    def test_the_headline_line_still_matches_the_deploy_gate(self):
        # The gate greps "cases · N not as expected · 0 long replies".
        sim = (ROOT / "scripts/dev/simulate-phrasings.py").read_text(encoding="utf-8")
        assert "not as expected" in sim and "long replies ===" in sim

    def test_gibberish_has_an_expectation_of_its_own(self):
        sim = (ROOT / "scripts/dev/simulate-phrasings.py").read_text(encoding="utf-8")
        assert '("asdfgh", "unsure")' in sim
        assert 'expect == "unsure"' in sim
