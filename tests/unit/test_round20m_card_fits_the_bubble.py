"""A customer card has to fit in a LINE bubble.

Owner's run through the real model, 18 ก.ย. 2569: "ดูข้อมูลลูกค้าสมชาย"
came back at 18 lines. The cap that was supposed to prevent that was a
PER-SECTION one — two items each — and the card has four sections plus a
header of up to six lines, so the arithmetic never added up to 15.

Worse, the comment above that cap named `simulate-phrasings` as its
evidence, and that simulator answered every model call from a local mock,
so it had never once reached this card down the model road. The cap was
measured against a road nothing drove down.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "application") not in sys.path:
    sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import chat  # noqa: E402

HEADER = [
    "C-2026-0001 · สมชาย ใจดี",
    "สถานะ: ลูกค้า",
    "โทร: 0812345678",
    "อีเมล: somchai@example.com",
    "ที่อยู่: 99/1 ถนนสุขุมวิท แขวงคลองเตย กรุงเทพฯ",
    "หมายเหตุในระเบียน: ลูกค้าเก่า ซื้อประจำทุกปี",
]


def _sections(counts=(7, 4, 3, 5)):
    names = [("บันทึก", "บันทึก"), ("นัดหมายที่จะถึง", "นัดหมาย"),
             ("ดีลที่เปิดอยู่", "ดีล"), ("ใบเสนอราคา", "ใบเสนอราคา")]
    out = []
    for (title, short), total in zip(names, counts):
        out.append((f"{title} ({total}):",
                    [f"  • รายการ {i + 1}" for i in range(min(2, total))], total, short))
    return out


class TestTheWholeCardIsBudgeted:
    def test_the_worst_case_card_fits(self):
        rows = chat._fit_card(HEADER, _sections(), "th")
        assert len(rows) <= chat.CARD_MAX_LINES, "\n".join(rows)

    def test_the_header_is_never_trimmed(self):
        rows = chat._fit_card(HEADER, _sections(), "th")
        assert rows[: len(HEADER)] == HEADER

    def test_what_did_not_fit_is_named_with_its_real_count(self):
        rows = chat._fit_card(HEADER, _sections(), "th")
        last = rows[-1]
        assert last.startswith("อื่น ๆ:")
        # The counts are the true ones, not the number of lines shown.
        assert "ดีล 3" in last and "ใบเสนอราคา 5" in last

    def test_nothing_is_dropped_in_silence(self):
        rows = chat._fit_card(HEADER, _sections(), "th")
        text = "\n".join(rows)
        for short, total in (("บันทึก", 7), ("นัดหมาย", 4), ("ดีล", 3), ("ใบเสนอราคา", 5)):
            assert short in text, f"{short} vanished from the card"
            assert str(total) in text, f"{short} lost its count"

    def test_a_small_customer_keeps_every_section_in_full(self):
        rows = chat._fit_card(HEADER[:2], _sections((1, 1, 1, 1)), "th")
        assert len(rows) <= chat.CARD_MAX_LINES
        assert not any(r.startswith("อื่น ๆ:") for r in rows)
        # One item out of one is not "…อีก 0 รายการ".
        assert not any("อีก" in r for r in rows)

    def test_one_huge_section_still_leaves_room_to_say_so(self):
        rows = chat._fit_card(HEADER, [("บันทึก (99):", [f"  • n{i}" for i in range(2)], 99, "บันทึก")], "th")
        assert len(rows) <= chat.CARD_MAX_LINES
        assert any("อีก 97 รายการ" in r for r in rows)

    def test_a_long_header_pushes_sections_into_the_tally(self):
        # Six header lines plus notes fills the bubble; the rest must fold
        # rather than run past it.
        rows = chat._fit_card(HEADER, _sections((20, 20, 20, 20)), "th")
        assert len(rows) <= chat.CARD_MAX_LINES
        assert rows[-1].startswith("อื่น ๆ:")

    def test_english_folds_too(self):
        rows = chat._fit_card(HEADER, _sections(), "en")
        assert len(rows) <= chat.CARD_MAX_LINES
        assert rows[-1].startswith("Also:")


class TestTheSimulatorNowMeasuresWhatItClaims:
    def test_edge_cases_never_reach_the_network_by_default(self):
        src = (ROOT / "scripts" / "dev" / "simulate-edge-cases.py").read_text()
        assert "_offline()" in src
        assert "if ai_client is None and not REAL:" in src

    def test_both_simulators_can_ask_the_real_model(self):
        for name in ("simulate-edge-cases", "simulate-phrasings"):
            src = (ROOT / "scripts" / "dev" / f"{name}.py").read_text()
            assert 'REAL = "--real" in sys.argv' in src, name
            assert "OR_KEY" in src, name

    def test_the_long_reply_list_no_longer_hides_a_case(self):
        src = (ROOT / "scripts" / "dev" / "simulate-phrasings.py").read_text()
        assert "same reply" in src
        assert "seen.add" not in src


class TestTheFakeIsNotMoreGenerousThanProduction:
    """`storefront_search` in the fake ignored its query and returned every
    seeded row, so no test could tell a hit from a shelf-dump — and the
    customer OA's product search rode a whole 490-case corpus without once
    being exercised (18 ก.ย. 2569). The Data tier filters
    `product_name ILIKE %q%`; the fake must do the same.
    """

    def test_it_filters_on_the_query(self):
        import asyncio
        sys.path.insert(0, str(ROOT / "tests" / "unit"))
        import test_phase6_chat as T

        client = T.FakeDataClient(permission_keys=["customer.read"], role="customer")
        client._storefront_results = [
            {"id": "p1", "product_name": "แอร์ 12000 BTU"},
            {"id": "p2", "product_name": "พัดลมตั้งพื้น 16 นิ้ว"},
        ]
        found = asyncio.run(client.storefront_search("พัดลม"))
        assert [r["product_name"] for r in found] == ["พัดลมตั้งพื้น 16 นิ้ว"]

    def test_a_miss_is_empty_not_everything(self):
        import asyncio
        import test_phase6_chat as T

        client = T.FakeDataClient(permission_keys=["customer.read"], role="customer")
        client._storefront_results = [{"id": "p1", "product_name": "แอร์ 12000 BTU"}]
        assert asyncio.run(client.storefront_search("ตู้เย็น")) == []

    def test_it_honours_the_limit(self):
        import asyncio
        import test_phase6_chat as T

        client = T.FakeDataClient(permission_keys=["customer.read"], role="customer")
        client._storefront_results = [{"id": str(i), "product_name": f"แอร์ รุ่น {i}"} for i in range(9)]
        assert len(asyncio.run(client.storefront_search("แอร์", limit=5))) == 5

    def test_the_customer_corpus_has_a_shelf_to_search(self):
        src = (ROOT / "scripts" / "dev" / "simulate-phrasings.py").read_text()
        customer = src[src.index("async def customer():"):]
        assert "_storefront_results" in customer[:1200]
