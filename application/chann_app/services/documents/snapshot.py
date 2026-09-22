"""Phase 10 — the quote data snapshot (Master Spec 10.3/10.4).

`docs/SMARTBROWZ_DOCUMENT_ENGINE.md` states the rule this module exists to
enforce: "All money, tax, status, identity, permission and other business
values come from deterministic Application/Domain logic." Nothing here is
ever produced by, corrected by, or passed through the AI layer.

Why a snapshot at all, rather than the template reading live rows: a
`generated_documents` row has to be reproducible byte-for-byte later
(10.3's own stated reason for `data_snapshot` + `sha256`). A customer's
address, a product's price, or the tenant's VAT rate can all change the
day after a quote is sent. Freezing every value that affects the rendered
output — including the VAT rate itself — is what makes "reproduce the PDF
we actually sent" answerable rather than approximate.

Money is Decimal throughout, never float. Quantised to 2 decimal places at
each step and rounded half-up, which is what Thai invoicing conventions
expect and what a person checking the arithmetic by hand will get.
Half-even ("banker's rounding", Python's default) would disagree with the
customer's own calculator on exactly the .005 cases, which is the worst
possible place to be subtly different.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

SNAPSHOT_VERSION = 1

_CENTS = Decimal("0.01")


class QuoteNotRenderable(RuntimeError):
    """The quote cannot be turned into a document yet, and the reason is a
    missing prerequisite the tenant can fix — not a provider failure.

    Deliberately distinct from a render error: this must never produce a
    document with a blank tax ID or a placeholder company name. 10.6's rule
    that a provider outage must never cause a fabricated document applies
    just as much to incomplete tenant data.
    """


def _money(value) -> Decimal:
    """Everything monetary passes through here, so a str from JSON, an int,
    or a Decimal all become the same 2-dp Decimal. float is accepted but
    converted via str, since Decimal(0.1) is not 0.1."""
    if isinstance(value, Decimal):
        raw = value
    elif isinstance(value, float):
        raw = Decimal(str(value))
    else:
        raw = Decimal(str(value or "0"))
    return raw.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _percent_str(rate: Decimal) -> str:
    """A rate as a human percent string: 0.07 -> "7", 0.075 -> "7.5".

    Not `Decimal.normalize()`, which turns 100.00 into "1E+2" — a VAT rate
    printed in scientific notation on a customer document. Not `:g` either,
    which leaves Decimal("7.00") as "7.00". Trailing zeros are stripped
    explicitly instead.
    """
    text = f"{rate * 100:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _customer_display_name(customer: dict) -> str:
    parts = [customer.get("first_name") or "", customer.get("last_name") or ""]
    return " ".join(p for p in parts if p).strip()


def build_line_items(products: list[dict]) -> list[dict]:
    """One entry per deal product, with its own computed line total.

    `product_name` and `quoted_unit_price` are copied from the deal row
    rather than resolved through the product catalogue: Phase 9 allows
    off-catalogue items (`product_id` is nullable), and even for catalogue
    items the price quoted on the deal is the price that was agreed, not
    whatever the catalogue says today.
    """
    items = []
    for index, product in enumerate(products or [], start=1):
        qty = int(product.get("qty") or 0)
        unit_price = _money(product.get("quoted_unit_price"))
        items.append({
            "line_no": index,
            "product_name": product.get("product_name") or "",
            "qty": qty,
            "unit_price": str(unit_price),
            "line_total": str(_money(unit_price * qty)),
            "notes": product.get("notes") or None,
        })
    return items


def compute_totals(line_items: list[dict], vat_rate, discount=None) -> dict:
    """Subtotal, VAT and grand total.

    `vat_rate` is a fraction (0.07), or None for a tenant that is not
    VAT-registered. None is NOT the same as 0: a non-registered company's
    document carries no VAT line at all, so `vat_applicable` is returned
    alongside the numbers and the template uses it to decide whether the
    row exists. Rendering "VAT 0.00" for a company with no VAT
    registration would misstate its tax status on a document.
    """
    subtotal = _money(sum((Decimal(item["line_total"]) for item in line_items), Decimal("0")))

    # A discount comes off BEFORE VAT. Thai VAT is charged on what the
    # customer actually pays, so discounting after tax would overstate the
    # tax due and put a number on the document that does not reconcile.
    #
    # Clamped at the subtotal: a larger discount would make the shop owe
    # the customer money.
    discount_amount = _money(min(Decimal(str(discount or 0)), subtotal))
    net = _money(subtotal - discount_amount)
    has_discount = discount_amount > 0

    if vat_rate is None or str(vat_rate).strip() == "":
        return {
            "subtotal": str(subtotal),
            "discount_applicable": has_discount,
            "discount_amount": str(discount_amount) if has_discount else None,
            "net_total": str(net),
            "vat_applicable": False,
            "vat_rate": None,
            "vat_rate_percent": None,
            "vat_amount": None,
            "grand_total": str(net),
        }

    rate = Decimal(str(vat_rate))
    vat_amount = _money(net * rate)
    return {
        "subtotal": str(subtotal),
        "discount_applicable": has_discount,
        "discount_amount": str(discount_amount) if has_discount else None,
        "net_total": str(net),
        "vat_applicable": True,
        # Both forms are frozen: the fraction is what the arithmetic used,
        # the percent is what the document prints. Deriving one from the
        # other at render time would let a formatting change alter how an
        # already-issued document reads.
        "vat_rate": str(rate),
        "vat_rate_percent": _percent_str(rate),
        "vat_amount": str(vat_amount),
        "grand_total": str(_money(net + vat_amount)),
    }


def build_quote_snapshot(
    *,
    quote: dict,
    deal: dict,
    customer: dict,
    company: dict,
    issued_at: datetime | None = None,
) -> dict:
    """The complete, frozen input to a quote render.

    Refuses rather than degrades when the tenant is not document-ready:
    `missing_for_documents` is computed by the Data tier and is the single
    source of truth for that question, so this does not re-implement the
    rule, it just enforces it.
    """
    missing = company.get("missing_for_documents") or []
    if missing:
        raise QuoteNotRenderable(
            "company profile is incomplete: " + ", ".join(missing)
        )

    # The QUOTE's lines, not the deal's. A quote owns its line items from
    # the moment it is created, so that a discount agreed on one offer
    # does not silently apply to the deal and every other quote made from
    # it. Falls back to the deal only for quotes created before that was
    # true and never backfilled.
    line_items = build_line_items(
        quote.get("products") or deal.get("products") or []
    )
    # The discount as an ABSOLUTE amount, resolved here rather than
    # stored: a percentage is meaningless once the lines change, and the
    # snapshot must freeze what the customer was actually charged.
    discount = Decimal("0")
    subtotal_for_discount = sum(
        (Decimal(item["line_total"]) for item in line_items), Decimal("0")
    )
    if quote.get("discount_percent") is not None:
        discount = _money(
            subtotal_for_discount * Decimal(str(quote["discount_percent"])) / 100
        )
    elif quote.get("discount_amount") is not None:
        discount = Decimal(str(quote["discount_amount"]))

    totals = compute_totals(line_items, company.get("vat_rate"), discount)
    stamped_at = issued_at or datetime.now(timezone.utc)

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "issued_at": stamped_at.isoformat(),
        "issued_on": stamped_at.date().isoformat(),
        "company": {
            # legal_name falls back to company_name: a sole trader may have
            # registered under the same name they trade as, and an empty
            # heading on a tax document is worse than a duplicated one.
            "name": company.get("legal_name") or company.get("company_name") or "",
            "trading_name": company.get("company_name") or "",
            "tax_id": company.get("tax_id") or "",
            "address": company.get("company_address") or "",
            "phone": company.get("company_phone") or "",
            "email": company.get("company_email") or "",
        },
        "customer": {
            "name": _customer_display_name(customer),
            "phone": customer.get("phone") or "",
            "email": customer.get("email") or "",
            "address": customer.get("address") or "",
        },
        "quote": {
            "quote_id": quote.get("quote_id") or "",
            "status": quote.get("status") or "",
            # Frozen with the rest: the document says the offer stands
            # until a date, and that sentence must not change afterwards
            # because someone edited the quote's validity later.
            "valid_until": quote.get("valid_until") or "",
        },
        "deal": {"deal_id": deal.get("deal_id") or ""},
        "line_items": line_items,
        "totals": totals,
    }


def snapshot_issued_date(snapshot: dict) -> date:
    return date.fromisoformat(snapshot["issued_on"])


# ------------------------------------------------------------ Round 20V
# The invoice and the receipt, frozen the same way. Owner, 21 ก.ย. 2569:
# "ทำข้อ 2 … รวมเอาเรื่อง invoice". The lines and the arithmetic are the
# quotation's — `build_line_items` and `compute_totals` — so an invoice
# made from a quote prints the same numbers the quote did, to the satang,
# and a VAT rule fixed in one place stays fixed in both documents.

PAYMENT_METHOD_LABELS = {
    "cash": {"th": "เงินสด", "en": "cash"},
    "transfer": {"th": "โอนเงิน", "en": "bank transfer"},
    "promptpay": {"th": "พร้อมเพย์", "en": "PromptPay"},
    "card": {"th": "บัตร", "en": "card"},
    "other": {"th": "อื่น ๆ", "en": "other"},
}


def build_invoice_snapshot(
    *,
    lines: list[dict],
    customer: dict,
    company: dict,
    invoice: dict | None = None,
    quote: dict | None = None,
    deal: dict | None = None,
    discount=None,
    issued_at: datetime | None = None,
    due_date: date | None = None,
) -> dict:
    """The complete, frozen input to an invoice render.

    `lines` are deal/quote product rows (product_name, qty,
    quoted_unit_price, notes) — whichever record the invoice is made from;
    the caller decides, this freezes. `invoice` may be empty at creation:
    the number and the dates are stamped by `restamp_invoice_snapshot`
    when the Data tier has allocated them, so the snapshot stored on the
    row and the one printed on the PDF are the same object.

    Refuses, like the quote, when the company profile is not
    document-ready: an invoice with a blank tax id is not a tax document.
    """
    missing = company.get("missing_for_documents") or []
    if missing:
        raise QuoteNotRenderable(
            "company profile is incomplete: " + ", ".join(missing)
        )
    line_items = build_line_items(lines or [])
    totals = compute_totals(line_items, company.get("vat_rate"), discount)
    stamped_at = issued_at or datetime.now(timezone.utc)
    invoice = invoice or {}
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "issued_at": stamped_at.isoformat(),
        "issued_on": str(invoice.get("issue_date") or stamped_at.date().isoformat()),
        "company": {
            "name": company.get("legal_name") or company.get("company_name") or "",
            "trading_name": company.get("company_name") or "",
            "tax_id": company.get("tax_id") or "",
            "address": company.get("company_address") or "",
            "phone": company.get("company_phone") or "",
            "email": company.get("company_email") or "",
        },
        "customer": {
            "name": _customer_display_name(customer),
            "phone": customer.get("phone") or "",
            "email": customer.get("email") or "",
            "address": customer.get("address") or "",
        },
        "invoice": {
            "invoice_id": invoice.get("invoice_id") or "",
            "status": invoice.get("status") or "draft",
            "issue_date": str(invoice.get("issue_date") or ""),
            "due_date": str(due_date or invoice.get("due_date") or ""),
            "note": invoice.get("note") or "",
        },
        "quote": {"quote_id": (quote or {}).get("quote_id") or ""},
        "deal": {"deal_id": (deal or {}).get("deal_id") or ""},
        "line_items": line_items,
        "totals": totals,
    }


def restamp_invoice_snapshot(
    snapshot: dict, *, invoice: dict, issued_at: datetime | None = None,
) -> dict:
    """The stored snapshot with the invoice's allocated number and dates.

    Numbering and the due date come from the Data tier after the snapshot
    was first built, so the copy the PDF prints is refreshed from the row
    — and only those fields: the lines, the customer and the totals stay
    exactly as frozen at creation.
    """
    stamped_at = issued_at or datetime.now(timezone.utc)
    out = dict(snapshot or {})
    out["issued_at"] = stamped_at.isoformat()
    out["issued_on"] = str(invoice.get("issue_date") or stamped_at.date().isoformat())
    out["invoice"] = {
        **(out.get("invoice") or {}),
        "invoice_id": invoice.get("invoice_id") or "",
        "status": invoice.get("status") or "",
        "issue_date": str(invoice.get("issue_date") or out["issued_on"]),
        "due_date": str(invoice.get("due_date") or ""),
        "note": invoice.get("note") or (out.get("invoice") or {}).get("note") or "",
    }
    return out


def build_receipt_snapshot(
    *, invoice: dict, payments: list[dict], company: dict, language: str = "th",
    issued_at: datetime | None = None,
) -> dict:
    """The receipt: the invoice's frozen snapshot plus the ledger.

    Issued only for an invoice paid in full (repositories/invoices.py
    refuses the link otherwise), so `receipt.paid_total` equals the grand
    total and the document can say "received in full". A deposit is not
    a receipt — it is a line on this table, printed when the balance
    clears. Money stays Decimal→str; the method is printed in words, in
    the same vocabulary the chat and the dashboard use.
    """
    missing = company.get("missing_for_documents") or []
    if missing:
        raise QuoteNotRenderable(
            "company profile is incomplete: " + ", ".join(missing)
        )
    base = restamp_invoice_snapshot(invoice.get("data_snapshot") or {}, invoice=invoice)
    stamped_at = issued_at or datetime.now(timezone.utc)
    rows = []
    paid_total = Decimal("0")
    for index, payment in enumerate(payments or [], start=1):
        amount = _money(payment.get("amount"))
        paid_total += amount
        method = str(payment.get("method") or "other")
        rows.append({
            "line_no": index,
            "paid_on": str(payment.get("paid_at") or "")[:10],
            "method": method,
            "method_label": (PAYMENT_METHOD_LABELS.get(method) or PAYMENT_METHOD_LABELS["other"])[
                "en" if language == "en" else "th"
            ],
            "reference": payment.get("reference") or "",
            "amount": str(amount),
        })
    return {
        **base,
        "issued_at": stamped_at.isoformat(),
        "issued_on": stamped_at.date().isoformat(),
        "receipt": {
            "receipt_id": f"RCPT-{invoice.get('invoice_id') or ''}",
            "invoice_id": invoice.get("invoice_id") or "",
            "paid_total": str(_money(paid_total)),
            "payment_count": len(rows),
            "last_paid_on": rows[-1]["paid_on"] if rows else "",
            "methods": ", ".join(sorted({r["method_label"] for r in rows})),
        },
        "payments": rows,
    }
