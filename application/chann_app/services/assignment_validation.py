"""The assignment rule vocabulary, as the Application tier sees it.

A deliberate second copy of what `chann_data/assignment_engine.py` also
knows. The two tiers do not import from each other — that boundary is
enforced by tests — and the alternative to duplicating a closed list of
eight operators is a shared package for the sake of forty lines.

What matters is that they cannot drift silently: a test asserts the two
vocabularies are identical, so adding an operator to one and not the
other fails the build rather than producing a rule the engine will not
execute.
"""
from __future__ import annotations


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


