"""Hand a document to the customer on LINE (round 21C).

Owner, 23 ก.ย. 2569: "ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้วสำหรับใบเสนอราคา
หรือ invoice ต่างๆสามารถออกคำสั่งหรือกดปุ่มส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มี
ผูกก็แจ้งว่าไม่ได้หรือทำปุ่มเป็นไม่พร้อมใช้งาน".

A receipt has been pushed this way since round 20V
(`invoices.notify_customer_receipt`). This is that one function, widened
to the three documents a shop hands over, so there is one road and not
three: the same signed link, the same notification row (which is what the
platform counts), the same refusal when there is nobody to push to.

The receipt keeps the sentence and the notification type it has had since
round 20V (`receipt_issued`): widening the road must not silently reword
a message customers already receive, nor retype a row the OA audit counts
by type.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient

log = logging.getLogger(__name__)

KIND_WORDS = {
    "quote": {"th": "ใบเสนอราคา", "en": "quotation"},
    "invoice": {"th": "ใบแจ้งหนี้", "en": "invoice"},
    "receipt": {"th": "ใบเสร็จรับเงิน", "en": "receipt"},
}
SEND_MESSAGE = {
    "th": "{what} {code} จาก {company}\nเปิดเอกสาร (ลิงก์ใช้ได้ 7 วัน):\n{url}",
    "en": "{what} {code} from {company}\nOpen it (link valid 7 days):\n{url}",
}
SEND_MESSAGE_NO_LINK = {
    "th": "{what} {code} จาก {company} — เปิดดูได้จากหน้าลูกค้า",
    "en": "{what} {code} from {company} — it is on your customer page.",
}
# Round 20V's words, unchanged: the person being told is the one who just
# paid, and "ชำระครบแล้ว ขอบคุณครับ" with the amount is the whole point of
# that push.
RECEIPT_MESSAGE = {
    "th": "ใบเสร็จรับเงินของใบแจ้งหนี้ {code} ({company}) ยอด {total} บาท ชำระครบแล้ว ขอบคุณครับ\nเปิดใบเสร็จ (ลิงก์ใช้ได้ 7 วัน):\n{url}",
    "en": "Your receipt for invoice {code} ({company}), {total} baht, paid in full — thank you.\nOpen the receipt (link valid 7 days):\n{url}",
}
RECEIPT_MESSAGE_NO_LINK = {
    "th": "ใบเสร็จรับเงินของใบแจ้งหนี้ {code} ({company}) ยอด {total} บาท ชำระครบแล้ว ขอบคุณครับ — เปิดดูได้จากหน้าลูกค้า",
    "en": "Your receipt for invoice {code} ({company}), {total} baht, paid in full — thank you. It is on your customer page.",
}
# The notification type each kind is recorded under. The receipt keeps its
# own, which round 20V's tests and `notify.TYPE_TO_OA` already name.
NOTIFICATION_TYPE = {"quote": "document_sent", "invoice": "document_sent", "receipt": "receipt_issued"}


class CustomerNotLinked(RuntimeError):
    """The shop's record for this person has no LINE identity, so there is
    nowhere to push to. Carries the name, because "ส่งไม่ได้" without one
    tells the salesperson nothing about who to go and add."""

    def __init__(self, customer_name: str):
        super().__init__(f"{customer_name} is not linked on LINE")
        self.customer_name = customer_name


class DocumentNotIssued(RuntimeError):
    """There is no PDF yet. Issue it first; sending a link to nothing is
    worse than refusing."""


class DocumentNotSendable(DocumentNotIssued):
    """There IS a PDF, but it must not reach the customer (final review
    C1, 23 ก.ย. 2569). `reason` is one of:

    * ``"void"`` — the bill was cancelled; a cancelled bill sent as a bill
      to be paid is the worst thing this road could do.
    * ``"needs_reissue"`` — the bill was corrected after its PDF was made,
      so the PDF carries the old total. Re-issue first.
    * ``"quote_closed"`` — a rejected or expired quotation is an offer
      that no longer stands.

    A subclass of `DocumentNotIssued` so every caller that already refuses
    "no PDF yet" refuses this too, and the cure is the same shape: fix the
    document, then send.
    """

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


#: Quote statuses whose offer no longer stands.
CLOSED_QUOTE_STATUSES = ("rejected", "expired")


def why_not_sendable(kind: str, record: dict) -> str | None:
    """The one answer to "may this document go to the customer as it is?",
    shared by the push and by the dashboard (which disables its button on
    the same reasons). None means yes."""
    record = record or {}
    status = str(record.get("status") or "")
    if kind in ("invoice", "receipt") and status == "void":
        return "void"
    if kind == "invoice" and (record.get("needs_reissue")
                              or (record.get("data_snapshot") or {}).get("needs_reissue_at")):
        return "needs_reissue"
    if kind == "quote" and status in CLOSED_QUOTE_STATUSES:
        return "quote_closed"
    return None


def customer_is_linked(customer: dict) -> bool:
    """Is there a LINE identity to push to? One predicate, because the
    dashboard's send button and the push itself must answer the same
    question: a button that is enabled and a send that refuses is worse
    than either failure on its own.
    """
    return bool((customer or {}).get("customer_chann_uid"))


def customer_name(customer: dict) -> str:
    parts = [str(customer.get("first_name") or "").strip(), str(customer.get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or str(customer.get("customer_id") or "ลูกค้า")


def record_code(kind: str, record: dict) -> str:
    return str(record.get("invoice_id") or record.get("quote_id") or record.get("id") or "")


def entity_of(kind: str) -> str:
    """A receipt is not a record of its own — it is the invoice's receipt,
    so it is filed against the invoice."""
    return "invoice" if kind in ("invoice", "receipt") else "quote"


async def already_sent(client: DataClient, *, license_id: str, kind: str,
                       record: dict, document_id: str, chann_uid: str) -> bool:
    """Has THIS version of the document already gone out? Asked of the
    notification rows rather than kept as a flag, so re-issuing resets it
    for free: a new document id is a new question.

    `list_notifications` is the customer's own list and takes no entity
    filter, so the narrowing is done here. It also returns only rows with
    `delivery_dashboard` set, which these are not — so in the running
    system this answers False until the Data tier can be asked for the
    suppressed rows too (round 21C left the Data tier untouched). The
    caller therefore treats `resent` as "known to be a repeat", never as
    "not a repeat".
    """
    entity, wanted = entity_of(kind), NOTIFICATION_TYPE[kind]
    try:
        rows = await client.list_notifications(str(license_id), chann_uid=chann_uid, limit=20)
    except Exception:  # noqa: BLE001
        return False
    return any(
        str(row.get("type") or "") == wanted
        and str(row.get("entity_type") or "") == entity
        and str(row.get("entity_id") or "") == str(record.get("id") or "")
        and str(document_id) in str(row.get("message") or "")
        for row in rows or []
    )


async def send_document_to_customer(
    client: DataClient, *, license_id: str, kind: str, record: dict,
    document_id: str | None, customer: dict, company: dict,
    language: str = "th", actor_id: str | None = None,
) -> dict:
    """Push the document to the customer's LINE. Raises `DocumentNotSendable`
    (void, edited-but-not-reissued, closed quote), `DocumentNotIssued` or
    `CustomerNotLinked`; otherwise returns what happened."""
    from .chat import document_download_url
    from .invoices import baht
    from .notify import send_notification

    if kind not in KIND_WORDS:
        raise ValueError(f"unknown document kind: {kind!r}")
    # The document's state first: a void bill that also has no PDF is
    # "cancelled", not "issue it first" — issuing it would be wrong too.
    reason = why_not_sendable(kind, record)
    if reason:
        raise DocumentNotSendable(reason, f"{kind} {record_code(kind, record)}: {reason}")
    if not document_id:
        raise DocumentNotIssued(f"{kind} {record_code(kind, record)} has no document yet")
    name = customer_name(customer)
    if not customer_is_linked(customer):
        raise CustomerNotLinked(name)
    uid = str(customer.get("customer_chann_uid") or "")
    resent = await already_sent(client, license_id=license_id, kind=kind,
                                record=record, document_id=document_id, chann_uid=uid)
    url = document_download_url(str(license_id), str(document_id))
    if kind == "receipt":
        table = RECEIPT_MESSAGE if url else RECEIPT_MESSAGE_NO_LINK
    else:
        table = SEND_MESSAGE if url else SEND_MESSAGE_NO_LINK
    values = {
        "what": KIND_WORDS[kind]["th"], "code": record_code(kind, record),
        "company": company.get("company_name") or company.get("legal_name") or "",
        "url": url or "",
    }
    if kind == "receipt":
        # Only the receipt says the amount out loud ("ชำระครบแล้ว ยอด …");
        # the quote and the invoice tables have no {total} to fill, and a
        # number formatted for nobody to read is a number that can only be
        # wrong.
        values["total"] = baht(record.get("total"))
    values_en = {**values, "what": KIND_WORDS[kind]["en"]}
    line_uid = await client.line_target_of(uid)
    await send_notification(
        client, license_id=str(license_id), target_chann_uid=uid, target_line_user_id=line_uid,
        type=NOTIFICATION_TYPE[kind], message=table["th"].format(**values),
        message_en=table["en"].format(**values_en),
        entity_type=entity_of(kind), entity_id=str(record.get("id") or ""),
        # The customer reads it in LINE and on their own page; there is no
        # dashboard bell for them (the same choice notify_customer_receipt
        # made).
        delivery_dashboard=False, oa="customer", language=language,
    )
    # Who handed it over: the notification row is written by the system, so
    # this line is the only trace of the person who pressed send.
    log.info("%s %s sent to %s by %s%s", kind, values["code"], uid, actor_id or "system",
             " (again)" if resent else "")
    return {"sent": True, "resent": resent, "url": url, "customer_name": name}
