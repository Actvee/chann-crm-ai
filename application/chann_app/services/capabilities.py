"""The one place a capability is described.

Requirement 4 of the owner's directive: prompt and code must read the same
definition, so a field cannot be "required" in one file and "never needed"
in another. `last_name` was exactly that — the model was told it was
required, `_prune_missing` deleted it from the model's own report, and the
create handler asked for it again a turn later.

This module holds no behaviour. It states what a capability needs; the
handlers keep doing the work and the guard keeps deciding whether to act.
Rows are added as flows are converted; nothing here is wired into a flow
that has not been checked against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    """One thing a person can ask for."""

    action: str
    entity: str
    #: Fields a write cannot go ahead without.
    required: tuple[str, ...] = ()
    #: Fields the person may give, in any order, and may leave out.
    optional: tuple[str, ...] = ()
    #: Fields a model sometimes reports as missing that this flow never
    #: needs. Anything here is dropped from the model's `missing` list; a
    #: field in `required` must never appear here.
    never_needed: tuple[str, ...] = ()
    #: Fields this codebase reads out of the sentence itself. The model may
    #: report one as missing while the parser can see it; the parser wins.
    parser_supplies: tuple[str, ...] = ()
    #: A named, deliberate departure from `required`, with the reason.
    exceptions: dict[str, str] = field(default_factory=dict)

    def prune_missing(self, missing: list[str]) -> list[str]:
        """The model's report, minus fields this flow genuinely never needs."""
        return [m for m in missing if m not in self.never_needed]


CUSTOMER_CREATE = Capability(
    action="create",
    entity="customer",
    # Owner, 11 ก.ย. 2569: "ถ้าจะเพิ่มลูกค้าต้องมีชื่อจริงด้วย" — a first
    # name joins the last name and the phone as required.
    required=("first_name", "last_name", "phone"),
    optional=("email", "address", "notes"),
    # Reported live (9 ก.ย. 2569): a perfectly good paste was blocked on
    # "กรุณาระบุอีเมล" because the model listed email as missing.
    never_needed=("email", "address", "notes"),
    exceptions={
        "customer_bulk": (
            "A pasted list is 'name … phone' per line, so the phone is always "
            "there but a one-word name is common. Those rows are created with "
            "no surname rather than rejecting the whole paste."
        ),
    },
)

FOLLOWUP_CREATE = Capability(
    action="create",
    entity="followup",
    # A reminder that cannot ring is not a reminder. The date is required,
    # but the parser reads it out of the sentence — see parser_supplies.
    required=("due_date",),
    optional=("target_name", "code", "due_time", "notes"),
    # The 12:03 loop (2 ก.ย. 2569): the model reported missing=["due_time"]
    # over a complete request, the assistant demanded a raw key, and
    # answering "9.00" produced the same demand again. A reminder's time
    # defaults to 09:00 and is echoed in the confirmation.
    never_needed=("due_time",),
    parser_supplies=("due_date",),
    exceptions={
        "target": (
            "Which record the reminder is about is resolved from a code in "
            "the sentence, a customer name, or the record already in "
            "context — not from one field, so it is not listed as required."
        ),
    },
)

QUOTE_CREATE = Capability(
    action="create",
    entity="quote",
    # A quote is always made FROM an existing deal, never invented, and its
    # own code is generated afterwards — never asked for and never accepted
    # from the model (_handle_quote_intent). The deal itself is found by
    # the handler: from the code, from the customer NAMED ("ทำใบเสนอราคาให้
    # สมชาย" — his open deal), or from the deal the conversation is on —
    # so the generic slot-fill must not ask for a code first (14 ก.ย. 2569).
    required=(),
    optional=("deal_code", "target_name"),
    never_needed=("deal_code", "target_name"),
)
#: Dispatch finds the job (code, running number, the job in context, or the
#: one open job) and asks for the person with buttons of its own.
TICKET_ASSIGN = Capability(action="assign", entity="ticket", never_needed=("code", "target_name"))
#: A line on a deal: quantity unsaid is one, price unsaid comes from the
#: catalogue, and the handler asks for the price BY NAME when it must —
#: never the generic "กรุณาระบุรายละเอียดที่เหลือ" (test team, 10 ก.ย. 2569).
LINE_ITEM_CREATE = Capability(
    action="create", entity="line_item",
    optional=("target_name", "code", "qty", "quoted_unit_price"),
    never_needed=("target_name", "code", "qty", "quoted_unit_price", "qty_change"),
)
LINE_ITEM_UPDATE = Capability(
    action="update", entity="line_item",
    optional=("target_name", "code", "qty", "quoted_unit_price", "qty_change"),
    never_needed=("target_name", "code", "qty", "quoted_unit_price", "qty_change"),
)

TEAM_CREATE = Capability(
    action="create",
    entity="team",
    # A team is a name. Who is in it comes next, in its own sentence —
    # "เพิ่ม <ชื่อ> เข้าทีม <ทีม>" — and the create trigger deliberately
    # takes everything after it as the NAME, so members cannot be part of
    # the same call anyway.
    required=("team_name",),
    optional=("scope", "members"),
    # Measured against the deployed model, 11 ก.ย. 2569: asked
    # "สร้างกลุ่มขาย เหนือ" it answered
    # {"action":"create","entity":"team","fields":{"team_name":"เหนือ",
    #  "scope":"sales"},"missing":["members"]} — a complete request with a
    # field listed as missing that this flow never needs. The reply was
    # "กรุณาระบุรายละเอียดที่เหลือ" and no group was made.
    never_needed=("members", "scope"),
)

#: Sales groups take the same shape; the entity name is the only difference.
SALES_GROUP_CREATE = Capability(
    action="create", entity="sales_group",
    required=TEAM_CREATE.required, optional=TEAM_CREATE.optional,
    never_needed=TEAM_CREATE.never_needed,
)

APPROVAL_APPROVE = Capability(
    action="approve",
    entity="approval",
    # The report is found by its code, by the one just looked at, or — with
    # one waiting — by itself; several waiting get buttons, never a
    # question about a code the person has not seen (_handle_approval_act).
    never_needed=("code",),
)
APPROVAL_REJECT = Capability(
    action="reject",
    entity="approval",
    optional=("reason",),
    # The handler asks for the reason itself, naming the report.
    never_needed=("code", "reason"),
)

#: The job at hand needs no code: the handlers take the one job assigned,
#: the one in progress, or the one just looked at, and ask with buttons
#: when several qualify. Measured 11 ก.ย. 2569: "ผมรับเองครับ" came back
#: claim/ticket with missing ["code"] and was answered "กรุณาระบุ
#: รายละเอียดที่เหลือ" at a technician with exactly one job to take.
TICKET_CLAIM = Capability(action="claim", entity="ticket", never_needed=("code", "target_name"))
TICKET_READ = Capability(action="read", entity="ticket", never_needed=("code", "target_name", "scope"))
SERVICE_REPORT_CHECK_IN = Capability(action="check_in", entity="service_report", never_needed=("code",))
SERVICE_REPORT_CHECK_OUT = Capability(
    action="check_out", entity="service_report",
    optional=("found_issue", "work_done", "parts_changed", "notes"),
    # The close-out asks for what it found and did in its own words.
    never_needed=("code", "found_issue", "work_done", "parts_changed", "notes"),
)

#: A situation on a job is filed from the sentence; nothing about it is
#: asked for. "ลูกค้าไม่อยู่บ้านครับ" came back with missing ["scheduled_date"]
#: and was answered "กรุณาระบุรายละเอียดที่เหลือ" (11 ก.ย. 2569).
TICKET_UPDATE = Capability(
    action="update", entity="ticket",
    optional=("scheduled_date", "scheduled_time", "service_address", "reason", "notes", "status"),
    never_needed=("code", "scheduled_date", "scheduled_time", "service_address", "reason", "notes", "status", "situation"),
)
SERVICE_REPORT_CREATE = Capability(
    action="create", entity="service_report",
    optional=("found_issue", "work_done", "parts_changed", "notes"),
    never_needed=("code", "found_issue", "work_done", "parts_changed", "notes"),
)

# Staff recording a sold unit (round 18b): the serial is the record; the
# customer is optional by design — a unit sold over the counter has none
# yet — so the model's "missing: target_name" must not open a form. When a
# name IS given and nobody has it, the handler refuses rather than
# registering nobody (tester, 14 ก.ย. 2569).
WARRANTY_CREATE = Capability(
    action="create", entity="warranty", required=("serial_number",),
    never_needed=("target_name", "product_name", "warranty_end", "warranty_start"),
)

#: The pictures on a job (round 19l). The job is never asked for: the
#: technician saying "เอารูปแรกออก" is standing on it, and the handler finds
#: it the same way check-in and check-out do. Which picture IS asked for
#: when no number was said — removing the wrong one is not undoable.
PHOTO_READ = Capability(action="read", entity="photo", never_needed=("code", "index"))
PHOTO_DELETE = Capability(
    action="delete", entity="photo", required=("index",), optional=("code",),
    never_needed=("code",),
)
#: Round 20V — the bill is made FROM a quote or a deal the handler finds
#: (the code said, or the record in context), so nothing is asked for first.
INVOICE_CREATE = Capability(
    action="create", entity="invoice",
    required=(), optional=("quote_code", "deal_code", "target_name", "note"),
    never_needed=("quote_code", "deal_code", "target_name", "note"),
)
#: A payment's amount is asked for by the handler itself, as a closed
#: follow-up read by hand ("รับชำระ INV-…" → "เท่าไหร่ครับ"), never by the
#: generic slot-fill — which would send "5000" back to the model.
INVOICE_PAY = Capability(
    action="pay", entity="invoice",
    required=(), optional=("code", "amount", "method", "reference", "full"),
    never_needed=("code", "amount", "payment_amount", "method", "reference", "full"),
)
INVOICE_UPDATE = Capability(
    action="update", entity="invoice",
    required=(), optional=("code", "amount", "method", "status"),
    never_needed=("code", "amount", "payment_amount", "method", "status"),
)
#: Handing an issued document over (round 21C). The handler finds WHICH
#: document itself — the code said, the one in view, "ล่าสุด", the named
#: customer's, the deal's — and asks with buttons when nothing points at
#: one. The generic slot-fill asked "กรุณาระบุรหัสรายการ" instead, and the
#: code typed into it opened the quotation rather than sending it (owner,
#: DEV, 24 ก.ย. 2569; converse, round 21E).
QUOTE_SEND = Capability(
    action="send", entity="quote",
    required=(), optional=("code", "quote_code", "deal_code", "target_name"),
    never_needed=("code", "quote_code", "deal_code", "target_name", "customer"),
)
INVOICE_SEND = Capability(
    action="send", entity="invoice",
    required=(), optional=("code", "kind", "target_name"),
    never_needed=("code", "kind", "document", "target_name", "customer"),
)
PHOTO_UPDATE = Capability(
    action="update", entity="photo", required=("index", "caption"), optional=("code",),
    never_needed=("code",),
)

REGISTRY: dict[tuple[str, str], Capability] = {
    (c.entity, c.action): c for c in (
        WARRANTY_CREATE,
        CUSTOMER_CREATE, FOLLOWUP_CREATE, QUOTE_CREATE, TEAM_CREATE, SALES_GROUP_CREATE,
        APPROVAL_APPROVE, APPROVAL_REJECT,
        TICKET_CLAIM, TICKET_READ, TICKET_UPDATE, TICKET_ASSIGN, LINE_ITEM_CREATE, LINE_ITEM_UPDATE, SERVICE_REPORT_CHECK_IN, SERVICE_REPORT_CHECK_OUT,
        SERVICE_REPORT_CREATE,
        PHOTO_READ, PHOTO_DELETE, PHOTO_UPDATE,
        INVOICE_CREATE, INVOICE_PAY, INVOICE_UPDATE,
        QUOTE_SEND, INVOICE_SEND,
    )
}


def capability(entity: str, action: str) -> Capability | None:
    """The row for this request, or the entity's create row.

    A model that reports missing fields without naming an action is
    describing a record being made, and the field rules ("a reminder's
    time defaults to 09:00", "nothing here needs an email") hold for an
    edit of that record too. Falling back keeps the pruning as wide as it
    was before the registry existed.
    """
    exact = REGISTRY.get((str(entity or ""), str(action or "")))
    if exact is not None:
        return exact
    return REGISTRY.get((str(entity or ""), "create"))


for _c in REGISTRY.values():  # a required field can never be "never needed"
    _overlap = set(_c.required) & set(_c.never_needed)
    if _overlap:
        raise AssertionError(f"{_c.entity}.{_c.action}: {sorted(_overlap)} both required and never needed")
del _c
