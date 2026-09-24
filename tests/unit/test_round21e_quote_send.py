"""Round 21E — "ส่งให้ลูกค้า" right after a quotation was issued.

The owner, on DEV (449819e), 24 ก.ย. 2569:

    ออกเอกสาร Q-2026-0010   -> ออกเอกสาร Q-2026-0010 เรียบร้อยแล้ว ลิงก์ดาวน์โหลด …
    ส่งให้ลูกค้า             -> ยังไม่แน่ใจว่าต้องการอะไรครับ

Measured before a line of this was written (report, item 1): the deployed
model reads "ส่งให้ลูกค้า" as action="suggest" when it is shown nothing
before it, and as {"action": "send", "entity": "quote", "fields": {"code":
"Q-2026-0010"}} when it is shown the turn before. The router's FIRST read
— the one that answers every fresh sentence on the sales OA since
model-first (11 ก.ย. 2569) — never handed the model the recent turns; only
the tail read did, and the tail is not reached once the first read shrugs.
So the context the model needed existed, and was not sent.

The rest of this file is what the send road does when the sentence names
no quotation at all: it resolves one the way "ล่าสุด" resolves for a deal
— the one in view, the newest, the named customer's, the deal's — and asks
which only when nothing points at one. It never files a form asking for a
"รหัสรายการ", and never answers "ไม่พบใบเสนอราคารหัส " with an empty code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID, _ctx  # noqa: E402

KEYS = ["quote.read", "quote.update", "deal.read", "customer.read", "invoice.read", "invoice.update"]


def _shop(*, issued=True):
    client = FakeDataClient(role="sales", permission_keys=list(KEYS))
    client._company_profile.update({
        "tax_id": "0105558123456", "company_address": "99/1", "legal_name": "บริษัท ทดสอบ จำกัด",
        "company_phone": "021234567", "company_email": "a@b.com", "vat_rate": "0.07",
    })
    client._customers = [
        {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี",
         "customer_chann_uid": "CHN-CUST-1", "stage": "customer"},
        {"id": "CUST-2", "customer_id": "C-2026-0002", "first_name": "สมหญิง", "last_name": "ร่ำรวย",
         "customer_chann_uid": "CHN-CUST-2", "stage": "customer"},
    ]
    client._line_targets = {"CHN-CUST-1": "U-somchai", "CHN-CUST-2": "U-somying"}
    client._deals = [
        {"id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1", "stage": "quoted", "products": []},
        {"id": "DEAL-2", "deal_id": "D-2026-0002", "contact_id": "CUST-2", "stage": "quoted", "products": []},
    ]
    client._quotes = [
        {"id": "QUOTE-1", "quote_id": "Q-2026-0001", "deal_id": "DEAL-1", "status": "sent",
         "generated_document_id": "GD-Q1", "total": "32100.00"},
        {"id": "QUOTE-2", "quote_id": "Q-2026-0002", "deal_id": "DEAL-2", "status": "sent",
         "generated_document_id": "GD-Q2", "total": "4280.00"},
        {"id": "QUOTE-3", "quote_id": "Q-2026-0003", "deal_id": "DEAL-1",
         "status": "sent" if issued else "draft",
         "generated_document_id": "GD-Q3" if issued else None, "total": "64200.00"},
    ]
    return client


#: What the stand-in LINE received in the current test (21E review,
#: Important 4: a notification row is not a delivery, so this counts pushes).
_LINE: list[tuple] = []


@pytest.fixture(autouse=True)
def _line_takes_pushes(line_accepts):
    global _LINE
    _LINE = line_accepts
    yield
    _LINE = []


def _pushed(client=None):
    """The pushes LINE received — not the rows the fake recorded."""
    return list(_LINE)


def _sent_to(line_user: str, code: str) -> None:
    assert _LINE, "LINE received nothing"
    assert _LINE[-1][1] == line_user and code in str(_LINE[-1][2]), _LINE[-1]


async def _send(client, fields, message="ส่งใบเสนอราคาให้ลูกค้า", ctx=None):
    return await chat._handle_quote_intent(
        client, intent={"action": "send", "entity": "quote", "fields": fields},
        ctx=ctx or _ctx(oa="sales", license_id=LICENSE_ID), license_id=LICENSE_ID, language="th",
        permission_keys=KEYS, message=message)


# ------------------------------------------------------ the owner's two turns


def _model_as_measured(seen: list[dict]):
    """The deployed model's own readings (report, item 1): with nothing
    before it the sentence is a shrug; with the issue turn before it, it
    is a send of the quotation that turn names."""
    async def fake_parse(**kw):
        seen.append(kw)
        said = " ".join(str(t.get("said") or "") + " " + str(t.get("did") or "") for t in kw.get("recent") or [])
        found = chat.QUOTE_CODE_RE.search(said)
        if kw.get("message") == "ส่งให้ลูกค้า" and found:
            return {"action": "send", "entity": "quote", "fields": {"code": found.group(1).upper()}, "missing": []}
        return {"action": "suggest", "suggestions": ["ส่งใบเสนอราคาให้ลูกค้า", "ส่งใบแจ้งหนี้ให้ลูกค้า"],
                "entity": None, "fields": {}, "missing": []}
    return fake_parse


def _issue_without_a_renderer(monkeypatch):
    from chann_app.services import quote_issue

    async def fake_issue(client, *, license_id, quote, deal, customer, company, actor_id, allow_reissue):
        await client.link_quote_document(license_id, quote["id"], "GD-NEW")
        await client.set_quote_status(license_id, quote["id"], "sent")
        return {"id": "GD-NEW", "sha256": "ab" * 32}
    monkeypatch.setattr(quote_issue, "issue_quote_document", fake_issue)


class TestTheOwnersTwoTurns:
    @pytest.mark.asyncio
    async def test_the_first_read_is_shown_what_was_said_before(self, monkeypatch):
        client = _shop(issued=False)
        _issue_without_a_renderer(monkeypatch)
        seen: list[dict] = []
        monkeypatch.setattr(chat, "parse_intent", _model_as_measured(seen))
        ctx = _ctx(oa="sales", license_id=LICENSE_ID)
        await chat.handle_chat_message(client, message="ออกเอกสาร Q-2026-0003", ctx=ctx, ai_client=None, language="th")
        await chat.handle_chat_message(client, message="ส่งให้ลูกค้า", ctx=ctx, ai_client=None, language="th")
        asked = [kw for kw in seen if kw.get("message") == "ส่งให้ลูกค้า"]
        assert asked, "the sentence never reached the model"
        said = [t.get("said") for t in asked[0].get("recent") or []]
        assert "ออกเอกสาร Q-2026-0003" in said, said

    @pytest.mark.asyncio
    async def test_issue_then_send_hands_that_quotation_over(self, monkeypatch):
        client = _shop(issued=False)
        _issue_without_a_renderer(monkeypatch)
        monkeypatch.setattr(chat, "parse_intent", _model_as_measured([]))
        ctx = _ctx(oa="sales", license_id=LICENSE_ID)
        issued = await chat.handle_chat_message(client, message="ออกเอกสาร Q-2026-0003", ctx=ctx, ai_client=None, language="th")
        assert "Q-2026-0003" in issued.text
        reply = await chat.handle_chat_message(client, message="ส่งให้ลูกค้า", ctx=ctx, ai_client=None, language="th")
        assert "ยังไม่แน่ใจ" not in reply.text
        assert "Q-2026-0003" in reply.text and "สมชาย" in reply.text and "ไลน์" in reply.text, reply.text
        assert len(_pushed(client)) == 1
        _sent_to("U-somchai", "Q-2026-0003")


class TestIssuingAQuotationLeadsToSendingIt:
    @pytest.mark.asyncio
    async def test_the_issued_quotation_is_the_one_in_view_and_a_tap_sends_it(self, monkeypatch):
        client = _shop(issued=False)
        _issue_without_a_renderer(monkeypatch)
        ctx = _ctx(oa="sales", license_id=LICENSE_ID)
        reply = await chat._handle_quote_issue(
            client, license_id=LICENSE_ID, code="Q-2026-0003", permission_keys=KEYS,
            language="th", actor_id=ctx.chann_uid, allow_reissue=False, ctx=ctx)
        assert client._last_entity_ref["entity_type"] == "quote"
        assert client._last_entity_ref["code"] == "Q-2026-0003"
        assert ("ส่งให้ลูกค้า", "ส่งใบเสนอราคา Q-2026-0003 ให้ลูกค้า") in reply.quick_replies


# -------------------------------------------- a send that names no quotation


class TestASendThatNamesNoQuotation:
    def test_the_generic_form_does_not_ask_for_a_code(self):
        # "ส่งใบเสนอราคาให้ลูกค้าทางแชท" is read send/quote missing=["code"];
        # the gate filed a form ("กรุณาระบุรหัสรายการ") and the code typed
        # into it opened the quotation instead of sending it (converse D).
        for entity in ("quote", "invoice"):
            assert chat._prune_missing(["code"], {"action": "send", "entity": entity}, "ส่งให้ลูกค้าทางแชท") == []

    @pytest.mark.asyncio
    async def test_the_quotation_in_view_is_the_one_sent(self):
        client = _shop()
        client._last_entity_ref = {"entity_type": "quote", "entity_id": "QUOTE-2", "code": "Q-2026-0002"}
        reply = await _send(client, {})
        assert "Q-2026-0002" in reply.text and "สมหญิง" in reply.text
        assert len(_pushed(client)) == 1
        _sent_to("U-somying", "Q-2026-0002")

    @pytest.mark.asyncio
    async def test_the_deal_in_view_sends_its_newest_quotation(self):
        client = _shop()
        client._last_entity_ref = {"entity_type": "deal", "entity_id": "DEAL-1", "code": "D-2026-0001"}
        reply = await _send(client, {})
        assert "Q-2026-0003" in reply.text, reply.text
        _sent_to("U-somchai", "Q-2026-0003")

    @pytest.mark.asyncio
    async def test_latest_is_the_newest_quotation(self):
        # Measured: "ส่งใบเสนอราคาล่าสุดให้ลูกค้า" -> fields {"code": "latest"};
        # it answered "ไม่พบใบเสนอราคารหัส LATEST".
        client = _shop()
        reply = await _send(client, {"code": "latest"}, "ส่งใบเสนอราคาล่าสุดให้ลูกค้า")
        assert "LATEST" not in reply.text
        assert "Q-2026-0003" in reply.text, reply.text
        _sent_to("U-somchai", "Q-2026-0003")

    @pytest.mark.asyncio
    async def test_a_named_customer_is_sent_their_newest_quotation(self):
        # Measured: "ส่งใบเสนอราคาให้คุณสมหญิง" -> {"target_name": "คุณสมหญิง"};
        # it answered "ไม่พบใบเสนอราคารหัส " with nothing after it.
        client = _shop()
        reply = await _send(client, {"target_name": "คุณสมหญิง"}, "ส่งใบเสนอราคาให้คุณสมหญิง")
        assert "Q-2026-0002" in reply.text and "สมหญิง" in reply.text, reply.text
        assert len(_pushed(client)) == 1
        _sent_to("U-somying", "Q-2026-0002")

    @pytest.mark.asyncio
    async def test_a_deal_code_sends_that_deals_newest_quotation(self):
        # Measured: "ส่งใบเสนอราคาของดีล D-2026-0001 ให้ลูกค้า" -> {"code": "D-2026-0001"}.
        client = _shop()
        reply = await _send(client, {"code": "D-2026-0001"}, "ส่งใบเสนอราคาของดีล D-2026-0001 ให้ลูกค้า")
        assert "Q-2026-0003" in reply.text, reply.text
        _sent_to("U-somchai", "Q-2026-0003")

    @pytest.mark.asyncio
    async def test_the_newest_one_still_a_draft_says_issue_it_first(self):
        client = _shop(issued=False)
        reply = await _send(client, {"code": "latest"}, "ส่งใบเสนอราคาล่าสุดให้ลูกค้า")
        assert "Q-2026-0003" in reply.text and "ออกเอกสาร" in reply.text
        assert ("ออกเอกสาร", "ออกเอกสาร Q-2026-0003") in reply.quick_replies
        assert not _pushed(client)

    @pytest.mark.asyncio
    async def test_with_nothing_to_go_on_it_asks_which_and_sends_nothing(self):
        client = _shop()
        reply = await _send(client, {})
        assert "รหัส \n" not in reply.text + "\n" and not reply.text.rstrip().endswith("รหัส")
        assert "ใบไหน" in reply.text
        buttons = [b[1] for b in reply.quick_replies]
        assert "ส่งใบเสนอราคา Q-2026-0003 ให้ลูกค้า" in buttons
        assert not _pushed(client)

    @pytest.mark.asyncio
    async def test_an_invoice_send_with_nothing_to_go_on_asks_which_too(self):
        client = _shop()
        client._invoices = [{"id": "INV-1", "invoice_id": "INV-2026-0001", "deal_id": "DEAL-1", "contact_id": "CUST-1",
                             "status": "issued", "generated_document_id": "GD-1", "total": "32100.00"}]
        reply = await chat._handle_document_send(
            client, ctx=_ctx(oa="sales", license_id=LICENSE_ID), license_id=LICENSE_ID, entity="invoice",
            fields={}, permission_keys=KEYS, language="th", message="ส่งใบแจ้งหนี้ให้ลูกค้า")
        assert "ใบไหน" in reply.text and not reply.text.rstrip().endswith("รหัส")
        assert ("ส่ง INV-2026-0001", "ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า") in reply.quick_replies
        assert not _pushed(client)

    @pytest.mark.asyncio
    async def test_with_nothing_issued_it_says_so_instead_of_offering_nothing(self):
        client = _shop(issued=False)
        for q in client._quotes:
            q["status"], q["generated_document_id"] = "draft", None
        reply = await _send(client, {})
        assert "ยังไม่มีใบเสนอราคาที่ออกเอกสารแล้ว" in reply.text
        assert not _pushed(client)
