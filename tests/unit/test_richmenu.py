"""Phase 19 (PLAN_3OA B7) + v3 design — two rich-menu pages per OA in two
languages. The layout is pure JSON; every message tile must be a phrase the
chat engine answers literally (a tile that reaches the AI is a dead button),
the six cards must not overlap or touch, and the tabs must point at the
aliases the apply script creates for that language.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

spec = importlib.util.spec_from_file_location("richmenu_generate", ROOT / "scripts" / "richmenu" / "generate.py")
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)  # type: ignore[union-attr]

from chann_app.services import chat as chat_module  # noqa: E402


def _handled_literally(text: str) -> bool:
    groups = [
        chat_module.HELP_TRIGGERS, chat_module.TICKET_MINE_PHRASES, chat_module.TICKET_OPEN_PHRASES,
        chat_module.CUSTOMER_STATUS_PHRASES, chat_module.CUSTOMER_CONTACT_PHRASES,
        chat_module.CUSTOMER_WARRANTY_MINE_PHRASES, chat_module.CUSTOMER_ORDERS_PHRASES,
        chat_module.CUSTOMER_CHAT_PHRASES, chat_module.CUSTOMER_REPORT_BARE,
        chat_module.PRODUCT_LIST_PHRASES, chat_module.DEAL_LIST_PHRASES, chat_module.TEAM_LIST_PHRASES,
        chat_module.TICKET_TEAM_PHRASES, chat_module.TICKET_REJECT_TRIGGERS, chat_module.CAPABILITY_PHRASES,
        chat_module.LANGUAGE_TOGGLE_PHRASES, chat_module.COMPANY_VIEW_PHRASES,
        chat_module.TODAY_WORK_PHRASES, chat_module.APPROVAL_LIST_PHRASES,
        chat_module.SALES_SUMMARY_PHRASES, chat_module.REPORT_LIST_PHRASES,
    ]
    if any(chat_module._matches_phrase(text, tuple(g)) for g in groups):
        return True
    lowered = text.lower()
    for trigger_group in (
        chat_module.SERIAL_REGISTER_TRIGGERS, chat_module.CHECKIN_TRIGGERS,
        chat_module.CHECKOUT_TRIGGERS,
    ):
        if any(t in lowered for t in trigger_group):
            return True
    # Handled by literal comparison in the dispatcher (see test_phase6_chat).
    return text in {"นัดหมายทั้งหมด", "ข้อมูลของฉัน", "รายชื่อลูกค้า"}


class TestLayout:
    @pytest.mark.parametrize("oa", ["sales", "technician", "customer"])
    @pytest.mark.parametrize("lang", ["th", "en"])
    def test_each_page_has_six_tiles_and_two_tabs(self, oa, lang):
        tail = "" if lang == "th" else "-en"
        for page in generate.PAGES:
            doc = generate.layout(oa, page, lang)
            tabs = [a for a in doc["areas"] if a["action"]["type"] == "richmenuswitch"]
            tiles = [a for a in doc["areas"] if a["action"]["type"] != "richmenuswitch"]
            assert len(tabs) == 2 and len(tiles) == 6
            assert {t["action"]["richMenuAliasId"] for t in tabs} == {
                f"chann-{oa}-main{tail}", f"chann-{oa}-more{tail}",
            }
            assert doc["_alias"] == f"chann-{oa}-{page}{tail}"
            assert doc["name"] == f"chann-{oa}-v3-{page}{tail}"
            assert doc["chatBarText"] == ("เมนู" if lang == "th" else "Menu")
            for area in doc["areas"]:
                b = area["bounds"]
                assert 0 <= b["x"] and b["x"] + b["width"] <= generate.W
                assert 0 <= b["y"] and b["y"] + b["height"] <= generate.H

    def test_the_language_variants_share_actions_and_tap_areas(self):
        # Same tile, same action, same place — only the picture reads the
        # other way, so a person who switches language never relearns the menu.
        for oa in generate.TILES:
            for page in generate.PAGES:
                th = generate.layout(oa, page, "th")["areas"]
                en = generate.layout(oa, page, "en")["areas"]
                assert [a["bounds"] for a in th] == [a["bounds"] for a in en]
                assert [a["action"] for a in th if a["action"]["type"] != "richmenuswitch"] == \
                    [a["action"] for a in en if a["action"]["type"] != "richmenuswitch"]

    def test_cards_do_not_overlap_and_keep_a_gutter(self):
        boxes = generate.card_bounds()
        assert len(boxes) == 6
        for i, a in enumerate(boxes):
            assert a[2] - a[0] >= 400 and a[3] - a[1] >= 600, a
            for b in boxes[i + 1:]:
                separated = a[2] + generate.GAP <= b[0] or b[2] + generate.GAP <= a[0] \
                    or a[3] + generate.GAP <= b[1] or b[3] + generate.GAP <= a[1]
                assert separated, (a, b)
        # The primary action is the biggest card and the first tap area.
        primary = boxes[0]
        assert all((primary[2] - primary[0]) * (primary[3] - primary[1])
                   > (b[2] - b[0]) * (b[3] - b[1]) for b in boxes[1:])

    @pytest.mark.parametrize("oa", ["sales", "technician", "customer"])
    def test_every_message_tile_is_answered_without_the_ai(self, oa):
        dead = []
        for page in generate.PAGES:
            for thai, _en, _icon, action in generate.TILES[oa][page]:
                if action["type"] == "message" and not _handled_literally(action["text"]):
                    dead.append((page, thai, action["text"]))
        assert not dead, dead

    def test_page_one_keeps_the_dashboard_and_help_tiles(self):
        for oa in generate.TILES:
            labels = [t[0] for t in generate.TILES[oa]["main"]]
            assert "วิธีใช้" in labels
            assert any(t[3]["type"] == "uri" for t in generate.TILES[oa]["main"])
            assert generate.TILES[oa]["more"][-1][3]["text"] == "สลับภาษา"

    def test_the_apply_script_strips_the_alias_marker_and_keeps_paths(self):
        script = (ROOT / "scripts" / "richmenu" / "richmenu-apply.sh").read_text(encoding="utf-8")
        assert "del(._alias)" in script
        assert "richmenu/alias" in script
        assert 'sub("\\\\{" + $var + "\\\\}"; $val)' in script
        # Four menus per OA: both pages in both languages, Thai main as default.
        assert "for page in main more main-en more-en; do" in script
        assert '"chann-${oa}-main-en" "chann-${oa}-more-en"' in script
