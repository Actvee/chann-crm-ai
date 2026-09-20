"""The search term has to leave the browser.

Round 20N's whole claim is that a list is searched by the DATABASE. The
integration suite proves the database end against Postgres; these pin the
road there, because the failure mode is silent: a screen that filters the
rows it already holds looks identical to one that asked the server, right
up until the list is capped and the answer is "ไม่พบ" for a record that
exists (20 ก.ย. 2569).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for tier in ("application", "data"):
    if str(ROOT / tier) not in sys.path:
        sys.path.insert(0, str(ROOT / tier))

LISTS = {
    "customers/CustomerList.tsx": "licenses/${licenseId}/customers",
    "deals/DealList.tsx": "licenses/${licenseId}/deals",
    "quotes/QuoteList.tsx": "licenses/${licenseId}/quotes",
    "products/ProductList.tsx": "licenses/${licenseId}/products",
    "tickets/SalesTickets.tsx": "licenses/${licenseId}/tickets",
    "warranties/SalesWarranties.tsx": "licenses/${licenseId}/warranties",
}
PAGES = ROOT / "presentation/app/liff/sales"
HOOK = ROOT / "presentation/app/liff/_paged-list.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestEveryListAsksTheServer:
    def test_all_six_use_the_shared_hook(self):
        for name, path in LISTS.items():
            page = read(PAGES / name)
            assert "usePagedList" in page, name
            assert path in page, name

    def test_none_of_them_filters_in_the_browser_any_more(self):
        # `matchesQuery` over the loaded rows is exactly the bug.
        for name in LISTS:
            assert "matchesQuery(" not in read(PAGES / name), name

    def test_the_hook_sends_the_search_term(self):
        hook = read(HOOK)
        assert 'search.set("q"' in hook
        assert 'search.set("limit"' in hook
        assert 'search.set("offset"' in hook


class TestTheHookCannotShowAStaleAnswer:
    """Two requests in flight land out of order and the slower one wins —
    the chat inbox shipped that twice (round 20i). Both suspension points
    have to be guarded, not just the last one."""

    def test_it_stamps_every_request(self):
        assert "++seq.current" in read(HOOK)

    def test_it_checks_after_the_fetch_and_after_the_json(self):
        hook = read(HOOK)
        assert hook.count("mine !== seq.current") >= 2

    def test_it_debounces_rather_than_asking_per_keystroke(self):
        assert "DEBOUNCE_MS" in read(HOOK)


class TestTheCountIsTakenThroughTheSameFilter:
    """A total counted over a wider set than the page it describes is a
    number the screen prints as the truth."""

    def test_every_repository_narrows_in_one_place(self):
        import importlib

        for module, repo in (
            ("chann_data.repositories.phase9", "CustomerRepository"),
            ("chann_data.repositories.phase9", "DealRepository"),
            ("chann_data.repositories.phase10", "QuoteRepository"),
            ("chann_data.repositories.phase7", "ProductRepository"),
            ("chann_data.repositories.phase12", "ServiceTicketRepository"),
            ("chann_data.repositories.phase16", "WarrantyRepository"),
        ):
            cls = getattr(importlib.import_module(module), repo)
            assert hasattr(cls, "_narrow"), repo
            src = Path(importlib.import_module(module).__file__).read_text(encoding="utf-8")
            body = src[src.index(f"class {repo}"):]
            nxt = body.find("\nclass ", 1)
            body = body[:nxt] if nxt > 0 else body
            # The list and the count both go through it.
            assert body.count("self._narrow(") >= 2, repo

    def test_a_wildcard_the_person_typed_is_escaped(self):
        from chann_data.repositories.search import like_any
        from chann_data.models import Customer

        clause = like_any("50%", Customer.first_name)
        rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
        # The % they typed is escaped; the ones wrapping it are ours.
        assert "50\\%" in rendered

    def test_an_empty_search_is_no_clause_at_all(self):
        from chann_data.repositories.search import like_any
        from chann_data.models import Customer

        assert like_any("", Customer.first_name) is None
        assert like_any("   ", Customer.first_name) is None
        assert like_any(None, Customer.first_name) is None


class TestTheServerAnswersTheScreensOwnWords:
    def test_open_is_a_stage_the_database_understands(self):
        """The deals queue computed "not won and not lost" in JavaScript,
        which stops being true the moment the list is paged."""
        from chann_data.repositories.phase9 import DealRepository

        src = Path(
            sys.modules["chann_data.repositories.phase9"].__file__
        ).read_text(encoding="utf-8")
        assert 'stage == "open"' in src
        assert DealRepository.CLOSED_STAGES == ("won", "lost")

    def test_the_queue_asks_for_its_statuses_in_one_request(self):
        """SalesTickets fired one request per open status and merged them
        in the browser — unpageable and uncountable."""
        page = read(PAGES / "tickets/SalesTickets.tsx")
        assert "OPEN_STATUSES.join(\",\")" in page
        assert "Promise.all(urls" not in page

    def test_the_ticket_repository_takes_a_list_of_statuses(self):
        src = Path(
            sys.modules["chann_data.repositories.phase12"].__file__
        ).read_text(encoding="utf-8")
        assert 'str(status).split(",")' in src


class TestTheNotificationPanelStaysOnScreen:
    """Owner, 20 ก.ย. 2569: the popover ran off the left of a phone and the
    notifications could not be read. It was anchored to the bell with
    `right: 0`, and the bell is not at the right edge of the screen."""

    def test_it_is_fixed_to_the_viewport(self):
        css = read(ROOT / "presentation/app/globals.css")
        panel = css[css.index(".notif-panel {"):]
        panel = panel[: panel.index("}")]
        assert "position: fixed" in panel
        assert "right: max(16px" in panel

    def test_the_top_is_measured_from_the_bell(self):
        # The top bar wraps to two rows on a narrow phone, so its height is
        # not a constant a stylesheet could hold.
        bell = read(ROOT / "presentation/lib/NotificationBell.tsx")
        assert "getBoundingClientRect()" in bell
        assert "style={{ top: panelTop }}" in bell
