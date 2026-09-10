"""The machine a fault report is about.

Owner, 10 ก.ย. 2569: "เวลาลูกค้าแจ้งเสียควรจะมีผูกกับสินค้าที่ลงทะเบียนเอาไว้"
— a fault report should be tied to the product the customer registered.

`service_tickets` has carried `product_id` and `serial_number` since Phase
12 and `TicketIn` has always accepted both, but nothing ever filled
`product_id` in: the customer flow asked which registered unit the fault
was about and then sent only the serial. So the link existed as a column
and never as a fact, and a technician arriving at a house still had to ask
which machine and whether it was still covered.

This module is the one place that answers "which machine, and is it still
in warranty" so the four surfaces that show it — the ticket detail in
chat, the new-fault card the dispatchers get, the dispatch card the
technician gets, and the ticket lists on the dashboard — cannot disagree.

Two deliberate choices:

* **The warranty state is not recomputed here.** `WarrantyRepository.
  effective_status` (Data tier) already derives in-warranty vs expired
  from the real end date on the Bangkok calendar, and every warranty
  leaving the Data tier goes through it. This tier reads that `status`
  and translates it; a second date comparison in the Application tier is
  a second thing that can be wrong about the same day.
* **The link is looked up by serial, not stored twice.** A ticket keeps
  `product_id` and `serial_number` because the dispatch gate must show
  what was true for THIS visit, but the product NAME and the cover state
  are read live from the warranty, so a certificate renewed next month
  shows as renewed.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Enough for a shop's whole book in one call; the alternative is one
# lookup per ticket, which is the N+1 that made the ticket list slow
# enough to notice.
_WARRANTY_PAGE = 500

MACHINE_LABEL = {"th": "เครื่อง", "en": "Machine"}
IN_WARRANTY = {"th": "อยู่ในประกันถึง {end}", "en": "under warranty until {end}"}
WARRANTY_OVER = {"th": "หมดประกันแล้ว", "en": "out of warranty"}
NO_MACHINE = {
    "th": "ยังไม่ได้ผูกกับเครื่องที่ลงทะเบียน",
    "en": "no registered machine attached",
}


def _t(table: dict[str, str], language: str) -> str:
    """Thai-first fallback, matching Phase 5 (chat._t)."""
    return table.get(language) or table["th"]


def _thai_date(value) -> str:
    from .thai_datetime import format_thai_date
    from datetime import date

    if not value:
        return ""
    try:
        return format_thai_date(date.fromisoformat(str(value)[:10]))
    except ValueError:
        return str(value)


def in_warranty(warranty: dict | None) -> bool:
    """Is this cover live TODAY?

    Reads the status the Data tier already derived from the real dates
    (`WarrantyRepository.effective_status`), rather than comparing
    `warranty_end` again here. "active" is the only value that means
    covered; "expired" and "void" are both final.
    """
    return str((warranty or {}).get("status") or "") == "active"


async def warranties_by_serial(client, license_id: str) -> dict[str, dict]:
    """The shop's registered units, keyed by upper-cased serial.

    Best-effort: a ticket list that cannot reach the warranty book is
    still a ticket list, and refusing to show the queue because the
    machine names are unavailable would be the worse failure.
    """
    try:
        rows = await client.list_warranties(str(license_id), limit=_WARRANTY_PAGE)
    except Exception:
        log.exception("could not read the warranty book to name ticket machines")
        return {}
    index: dict[str, dict] = {}
    for row in rows or []:
        serial = str(row.get("serial_number") or "").strip().upper()
        if serial and serial not in index:
            index[serial] = row
    return index


async def warranty_for_serial(client, license_id: str, serial: str) -> dict | None:
    """The registered unit with this serial, or None.

    Used on the write paths, where one serial is being linked and pulling
    the whole book would be wasteful.
    """
    serial = (serial or "").strip()
    if not serial:
        return None
    try:
        rows = await client.list_warranties(str(license_id), serial_number=serial)
    except Exception:
        log.exception("could not look up the registered unit for %s", serial)
        return None
    for row in rows or []:
        if str(row.get("serial_number") or "").upper() == serial.upper():
            return row
    return None


def link_fields(warranty: dict | None, serial: str = "") -> dict:
    """What a ticket payload should carry about the machine.

    `product_id` only when the warranty actually names one: a unit
    registered by serial alone (the CSV import allows it) links the serial
    and leaves the product NULL rather than inventing a catalogue row.
    """
    fields: dict[str, Any] = {}
    serial = (serial or str((warranty or {}).get("serial_number") or "")).strip()
    if serial:
        fields["serial_number"] = serial
    product_id = str((warranty or {}).get("product_id") or "")
    if product_id:
        fields["product_id"] = product_id
    return fields


def attach(tickets: Iterable[dict], index: dict[str, dict]) -> list[dict]:
    """Add the machine's name and cover state to each ticket.

    Composed here rather than in the Data tier: `TicketOut` is the ticket's
    own row, and a ticket that quoted a product name would be a second
    copy of it to keep in step. Listed in check-fields' ACCEPTED_TS with
    this route as the composer.

    A ticket with no serial, or one whose serial is not in the book, comes
    back untouched — every display has to render without a link (existing
    tickets all have NULL product_id and are not backfilled).
    """
    out: list[dict] = []
    for ticket in tickets or []:
        row = dict(ticket)
        warranty = index.get(str(row.get("serial_number") or "").strip().upper())
        if warranty:
            row["product_name"] = warranty.get("product_name")
            row["warranty_number"] = warranty.get("warranty_number")
            row["warranty_end"] = warranty.get("warranty_end")
            row["warranty_status"] = warranty.get("status")
        out.append(row)
    return out


async def attach_to(client, license_id: str, tickets: Iterable[dict]) -> list[dict]:
    """`attach`, fetching the book itself. One call for the whole list."""
    rows = list(tickets or [])
    if not any(str(t.get("serial_number") or "").strip() for t in rows):
        # Nothing to look up: a queue of tickets nobody linked must not
        # cost a round trip on every refresh.
        return rows
    return attach(rows, await warranties_by_serial(client, license_id))


def describe(ticket: dict, language: str = "th") -> str:
    """"แอร์ 12000 BTU (S/N SN12345678) · อยู่ในประกันถึง 1 ม.ค. 2570"

    Empty when the ticket names no machine at all, so a caller can drop
    the line rather than print a label with nothing after it.
    """
    ticket = ticket or {}
    serial = str(ticket.get("serial_number") or "").strip()
    name = str(ticket.get("product_name") or "").strip()
    if not serial and not name:
        return ""

    head = name or ("เครื่องที่ลงทะเบียน" if language != "en" else "registered unit")
    if serial:
        head = f"{head} (S/N {serial})"

    status = str(ticket.get("warranty_status") or "")
    if status == "active":
        end = _thai_date(ticket.get("warranty_end")) if language != "en" else str(
            ticket.get("warranty_end") or ""
        )[:10]
        if end:
            return f"{head} · " + _t(IN_WARRANTY, language).format(end=end)
        return head
    if status in ("expired", "void"):
        return f"{head} · " + _t(WARRANTY_OVER, language)
    return head


def machine_line(ticket: dict, language: str = "th") -> str:
    """The described machine as a labelled line, or "" when unlinked."""
    described = describe(ticket, language)
    if not described:
        return ""
    return f"{_t(MACHINE_LABEL, language)}: {described}"


def machine_suffix(ticket: dict, language: str = "th") -> str:
    """The machine line ready to append to a confirmation, always saying
    something — a customer who has just been asked which machine, or told
    the shop needs to know, is owed the answer either way."""
    line = machine_line(ticket, language)
    return "\n" + (line or _t(NO_MACHINE, language))
