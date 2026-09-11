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
    required=("last_name", "phone"),
    optional=("first_name", "email", "address", "notes"),
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
    # from the model (_handle_quote_intent).
    required=("deal_code",),
    optional=(),
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

REGISTRY: dict[tuple[str, str], Capability] = {
    (c.entity, c.action): c for c in (
        CUSTOMER_CREATE, FOLLOWUP_CREATE, QUOTE_CREATE, TEAM_CREATE, SALES_GROUP_CREATE,
        APPROVAL_APPROVE, APPROVAL_REJECT,
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
