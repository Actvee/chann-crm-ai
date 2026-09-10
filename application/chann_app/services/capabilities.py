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

REGISTRY: dict[tuple[str, str], Capability] = {
    (c.entity, c.action): c for c in (CUSTOMER_CREATE,)
}


def capability(entity: str, action: str) -> Capability | None:
    return REGISTRY.get((str(entity or ""), str(action or "")))


for _c in REGISTRY.values():  # a required field can never be "never needed"
    _overlap = set(_c.required) & set(_c.never_needed)
    if _overlap:
        raise AssertionError(f"{_c.entity}.{_c.action}: {sorted(_overlap)} both required and never needed")
del _c
