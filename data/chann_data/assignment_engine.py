"""Phase 11 — deciding who gets a piece of work.

Lives in the Data tier because the decision must happen INSIDE the same
transaction and lock that reads current loads and writes the assignment.
Splitting it across the tier boundary would mean reading loads, releasing
the lock to ask the Application tier who to pick, then writing — which is
exactly the race the lock exists to prevent.

Deterministic by design (Master Spec 11.5). The AI writes the rule once,
when someone types a policy; this module only reads it. Two reasons that
split is not negotiable:

* The same inputs must always produce the same assignment. A technician
  who asks "why did that job go to someone else" deserves an answer, and
  "the model chose differently that time" is not one.
* An assignment is a commitment to a person's day. Re-deriving it from
  prose on every ticket would make the whole schedule non-reproducible.

Everything here is pure except the candidate lookup and the final write:
the matching, the ordering and the capacity arithmetic can all be tested
without a database, which is what makes the race-condition test in
11.7 meaningful rather than a smoke test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

RULE_VERSION = 1

# Everything a rule may compare. Deliberately a closed list: an operator
# the engine does not implement must fail at configuration time, when a
# person is looking at the screen, rather than silently matching nothing
# months later.
OPERATORS = ("equals", "not_equals", "in", "contains", "gt", "gte", "lt", "lte")

SELECTION_STRATEGIES = ("least_load", "round_robin", "first_available")
CAPACITY_MODES = ("hard_block", "soft_warn")


class RuleInvalid(ValueError):
    """The rule JSON cannot be executed as written."""


@dataclass
class AssignmentOutcome:
    """Who got it, and why — the "why" is not decoration.

    Recorded into the audit log so an assignment can be explained later
    without re-running the engine against data that has since changed.
    """

    member_id: str | None
    reason: str
    matched_team: str | None = None
    strategy: str | None = None
    warnings: list[str] = field(default_factory=list)
    # True when nothing matched and the fallback path was used, so a
    # tenant can see that their rules are not covering real cases.
    used_fallback: bool = False


def _read_path(context: dict, path: str) -> Any:
    """Read "product.category" out of a nested context dict.

    Missing keys return None rather than raising: a ticket without a
    product is a normal thing to assign, and a rule that mentions one
    should simply not match it.
    """
    value: Any = context
    for part in str(path).split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "equals":
        return _norm(actual) == _norm(expected)
    if operator == "not_equals":
        return _norm(actual) != _norm(expected)
    if operator == "in":
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return _norm(actual) in {_norm(v) for v in values}
    if operator == "contains":
        return actual is not None and _norm(expected) in _norm(actual)
    # Numeric comparisons only apply when both sides really are numbers.
    # A rule comparing a missing field to 5 must not match by accident.
    if operator in ("gt", "gte", "lt", "lte"):
        try:
            left, right = float(actual), float(expected)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right,
        }[operator]
    raise RuleInvalid(f"unknown operator: {operator!r}")


def _norm(value: Any) -> Any:
    """Case- and whitespace-insensitive for text, unchanged otherwise.

    Categories arrive from a chat message as often as from a form, so
    "AIR_CONDITIONER" and "air_conditioner " have to match.
    """
    return value.strip().lower() if isinstance(value, str) else value


def validate_rule(rule: dict) -> list[str]:
    """Problems with a rule, as messages a person can act on.

    Returns a list rather than raising on the first one: someone fixing a
    policy wants to see everything wrong with it, not play whack-a-mole.
    """
    problems: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]

    if rule.get("version") not in (None, RULE_VERSION):
        problems.append(f"unsupported rule version: {rule.get('version')!r}")

    scope = rule.get("scope")
    if scope not in ("sales", "technician"):
        problems.append(f"scope must be 'sales' or 'technician', got {scope!r}")

    criteria = rule.get("match_criteria")
    if criteria is not None and not isinstance(criteria, list):
        problems.append("match_criteria must be a list")
    else:
        for index, item in enumerate(criteria or []):
            if not isinstance(item, dict):
                problems.append(f"match_criteria[{index}] must be an object")
                continue
            if not item.get("field"):
                problems.append(f"match_criteria[{index}] is missing 'field'")
            operator = item.get("operator")
            if operator not in OPERATORS:
                problems.append(
                    f"match_criteria[{index}] has unknown operator {operator!r} "
                    f"(expected one of: {', '.join(OPERATORS)})"
                )
            if not item.get("assign_to_team"):
                problems.append(f"match_criteria[{index}] is missing 'assign_to_team'")

    strategy = rule.get("selection_strategy", "round_robin")
    if strategy not in SELECTION_STRATEGIES:
        problems.append(
            f"selection_strategy {strategy!r} is not one of: "
            f"{', '.join(SELECTION_STRATEGIES)}"
        )

    capacity = rule.get("capacity_constraint")
    if capacity is not None:
        if not isinstance(capacity, dict):
            problems.append("capacity_constraint must be an object")
        else:
            mode = capacity.get("mode", "hard_block")
            if mode not in CAPACITY_MODES:
                problems.append(
                    f"capacity mode {mode!r} is not one of: {', '.join(CAPACITY_MODES)}"
                )
            max_per_day = capacity.get("max_per_day")
            if max_per_day is not None:
                try:
                    if int(max_per_day) < 1:
                        problems.append("capacity max_per_day must be at least 1")
                except (TypeError, ValueError):
                    problems.append(f"capacity max_per_day is not a number: {max_per_day!r}")

    return problems


def match_team(rule: dict, context: dict) -> str | None:
    """The team the first matching criterion names, or None.

    First match wins, in the order the rule lists them. Order is the
    tenant's own statement of priority — reordering the criteria is how
    someone changes which rule takes precedence, so the engine must not
    reorder or "best-match" them.
    """
    for item in rule.get("match_criteria") or []:
        try:
            if _compare(
                _read_path(context, item.get("field", "")),
                item.get("operator", "equals"),
                item.get("value"),
            ):
                return item.get("assign_to_team")
        except RuleInvalid:
            # A bad operator in one criterion should not stop the others
            # from being evaluated; validate_rule surfaces it at config
            # time, and at runtime the safest reading is "does not match".
            log.warning("skipping criterion with a bad operator: %r", item)
            continue
    return None


def order_candidates(
    candidates: list[dict], strategy: str, loads: dict[str, int],
) -> list[dict]:
    """Candidates in the order the strategy wants them tried.

    Returns an ORDER rather than a single choice so the caller can walk
    down it when capacity blocks the first pick — which is the whole
    reason hard_block does not simply fail.

    Every strategy breaks ties on member id. Without that, two equally
    loaded technicians would be ordered by however the database returned
    them, and the same inputs could assign differently between runs.
    """
    if strategy == "least_load":
        return sorted(candidates, key=lambda c: (loads.get(str(c["id"]), 0), str(c["id"])))
    if strategy == "round_robin":
        # Least-recently-assigned first, falling back to id. "Round robin"
        # with no memory of the last pick is just "first in the list"
        # forever, which is the bug this ordering avoids.
        return sorted(
            candidates,
            key=lambda c: (str(c.get("last_assigned_at") or ""), str(c["id"])),
        )
    return sorted(candidates, key=lambda c: str(c["id"]))


def choose(
    rule: dict,
    candidates: list[dict],
    loads: dict[str, int],
    *,
    matched_team: str | None,
    owner_candidates: list[dict] | None = None,
) -> AssignmentOutcome:
    """Pick one candidate, honouring capacity, or explain why none fit.

    Pure: takes the candidates and their current loads as data, so the
    race-condition test can drive it directly and the caller keeps the
    lock around the parts that touch the database.
    """
    strategy = rule.get("selection_strategy", "round_robin")
    capacity = rule.get("capacity_constraint") or {}
    max_per_day = capacity.get("max_per_day")
    mode = capacity.get("mode", "hard_block")
    warnings: list[str] = []

    if not candidates:
        # 11.1: nobody active must still produce an assignment, because an
        # unassigned job is one nobody is accountable for.
        for owner in owner_candidates or []:
            return AssignmentOutcome(
                member_id=str(owner["id"]),
                reason="no active candidate in scope; assigned to owner/admin",
                matched_team=matched_team,
                strategy=strategy,
                used_fallback=True,
            )
        return AssignmentOutcome(
            member_id=None,
            reason="no candidates and no owner to fall back to",
            matched_team=matched_team,
            strategy=strategy,
            used_fallback=True,
        )

    ordered = order_candidates(candidates, strategy, loads)

    if max_per_day is None:
        chosen = ordered[0]
        return AssignmentOutcome(
            member_id=str(chosen["id"]),
            reason=f"selected by {strategy}",
            matched_team=matched_team,
            strategy=strategy,
        )

    limit = int(max_per_day)
    for candidate in ordered:
        load = loads.get(str(candidate["id"]), 0)
        if load < limit:
            return AssignmentOutcome(
                member_id=str(candidate["id"]),
                reason=f"selected by {strategy}; load {load}/{limit}",
                matched_team=matched_team,
                strategy=strategy,
                warnings=warnings,
            )
        if mode == "soft_warn":
            # Over capacity but allowed: assign and say so, rather than
            # refusing. The tenant asked for a warning, not a wall.
            return AssignmentOutcome(
                member_id=str(candidate["id"]),
                reason=f"over capacity ({load}/{limit}) but mode is soft_warn",
                matched_team=matched_team,
                strategy=strategy,
                warnings=[f"{candidate.get('name') or candidate['id']} is over capacity"],
            )

    # Everyone in the team is full under hard_block.
    for owner in owner_candidates or []:
        return AssignmentOutcome(
            member_id=str(owner["id"]),
            reason=f"every candidate is at capacity ({limit}); assigned to owner/admin",
            matched_team=matched_team,
            strategy=strategy,
            warnings=["team is at capacity"],
            used_fallback=True,
        )
    return AssignmentOutcome(
        member_id=None,
        reason=f"every candidate is at capacity ({limit}) and no owner to fall back to",
        matched_team=matched_team,
        strategy=strategy,
        warnings=["team is at capacity"],
        used_fallback=True,
    )
