"""Every list opens the same way, and a notification leads somewhere.

Owner, 18 ก.ย. 2569, on the appointments page: "ทำไมไม่ทำเหมือนหน้าอื่นที่
ให้กดเข้าไปดูผ่านตัว record เลย ไม่ต้องใช้ปุ่ม" — and on the notification
control: "ออกแบบไม่ดีวางในตำแหน่งที่แย่".

These read the source, because the behaviour they pin is structural and
there is no browser in this suite. A component test that renders nothing
would pass while the bell sits on four pages out of nineteen, which is
exactly the state this round found.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIFF = ROOT / "presentation" / "app" / "liff"
BELL = ROOT / "presentation" / "lib" / "NotificationBell.tsx"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheBellIsOnEveryPage:
    def test_the_shell_carries_it_so_no_page_has_to_remember(self):
        shell = read(LIFF / "sales" / "_shell.tsx")
        assert "NotificationBell" in shell
        assert "tools=" in shell

    def test_the_top_bar_has_a_slot_for_it(self):
        components = read(LIFF / "sales" / "_components.tsx")
        assert "tools?: ReactNode" in components
        assert "{tools}" in components

    def test_no_page_mounts_its_own_copy_any_more(self):
        # Four pages used to import it by hand; the other fifteen had none,
        # so whether you could see your notifications depended on which
        # screen you were standing on.
        stragglers = [
            p.relative_to(ROOT).as_posix()
            for p in LIFF.rglob("*.tsx")
            if p.name not in ("_shell.tsx",) and "NotificationBell" in read(p)
        ]
        assert stragglers == [], stragglers


class TestATapIsTheWholeGesture:
    def test_a_notification_row_is_a_link_not_a_row_with_a_button(self):
        bell = read(BELL)
        assert "row-link notif-open" in bell
        # The per-row "ทำเครื่องหมายว่าอ่านแล้ว" button is gone.
        assert 'className="notif-read"' not in bell

    def test_tapping_marks_it_read_and_closes_the_panel(self):
        bell = read(BELL)
        assert "onRead(item.id)" in bell
        assert "setOpen(false)" in bell

    def test_it_uses_the_entity_the_row_already_carries(self):
        bell = read(BELL)
        assert "RECORD_PAGES" in bell and "LIST_PAGES" in bell
        assert "item.entity_type" in bell and "item.entity_id" in bell

    def test_only_types_with_a_page_get_a_link(self):
        # A link that 404s is worse than no link. Every destination named in
        # the component must exist as a route in the app directory.
        bell = read(BELL)
        import re

        for path in set(re.findall(r'"(/liff/sales/[a-z-]+)"', bell)):
            folder = ROOT / "presentation" / "app" / path.lstrip("/")
            assert folder.is_dir(), f"{path} is not a page"

    def test_a_row_with_nowhere_to_go_can_still_be_dismissed(self):
        assert 'type="button" className="notif-open"' in read(BELL)


class TestOneWayToOpenARecord:
    LISTS = {
        "sales/customers/CustomerList.tsx": "/liff/sales/customers/",
        "sales/deals/DealList.tsx": "/liff/sales/deals/",
        "sales/quotes/QuoteList.tsx": "/liff/sales/quotes/",
        "sales/appointments/Appointments.tsx": None,
    }

    def test_every_list_row_uses_the_shared_class(self):
        for name in self.LISTS:
            text = read(LIFF / name)
            assert 'className="row-link"' in text, name

    def test_none_of_them_hand_rolls_the_link_reset_any_more(self):
        # Three copies of `textDecoration: "none", color: "inherit"` — and
        # none of them drew the chevron that says the row is a door.
        for name in self.LISTS:
            text = read(LIFF / name)
            assert 'textDecoration: "none"' not in text, name

    def test_every_row_link_wraps_a_row_body(self):
        # `.row-link` is a flex row: without `.row-body` the title and the
        # meta line are laid out side by side instead of stacked.
        for name in self.LISTS:
            text = read(LIFF / name)
            assert 'className="row-body"' in text, name

    def test_the_appointments_page_lost_its_open_record_button(self):
        text = read(LIFF / "sales/appointments/Appointments.tsx")
        assert "copy.openRecord" not in text

    def test_its_action_buttons_stay_outside_the_link(self):
        # A button inside a link is two stops for a keyboard and one
        # confused control for a screen reader.
        text = read(LIFF / "sales/appointments/Appointments.tsx")
        link_at = text.index('className="row-link"')
        close_at = text.index("</Link>", link_at)
        assert "<button" not in text[link_at:close_at]
