"""Phase 11.6 — turning a typed policy into a rule the engine can execute.

The ONLY place a model touches assignment. It runs once, when an owner
types a policy, and its output is shown back for confirmation before
anything is saved. The runtime engine never calls this — see
`chann_data/assignment_engine.py` for why that separation is the point of
the phase.

Everything the model produces is validated against the same closed
vocabulary the engine implements, and a rule that fails validation is
never offered for confirmation. A model that invents an operator would
otherwise produce a rule that silently matches nothing, months later,
with no error anywhere.
"""
from __future__ import annotations

import json
import logging

from ..assignment_validation import validate_rule
from .client import complete

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert a business policy written in Thai or English
into assignment rule JSON. Reply with JSON only — no prose, no markdown
fences.

Shape:
{{"version": 1, "scope": "...", "match_criteria": [...],
  "selection_strategy": "...", "capacity_constraint": {{...}},
  "fallback": "round_robin_in_team",
  "no_active_fallback": "assign_to_owner_or_admin"}}

scope is "technician" or "sales" — technician for repair/installation/
service work (jobs, tickets), sales for new customers and leads. The
scope follows the team the person names: a technician team means
"technician", a sales group means "sales".

Each match_criteria entry is:
  {{"field": "...", "operator": "...", "value": ..., "assign_to_team": "..."}}

field is a dotted path into the record being assigned. Use only:
  for technician rules: product.category, product.name
  for sales rules: customer.stage, customer.source
customer.stage is "lead" for a new lead; customer.source is one of
  line (the customer linked the shop on LINE themselves), staff (a
  staff member typed them in), csv (imported), dashboard.
A policy that names no condition ("แจกงานให้ทีม X สลับกัน") has an
empty match_criteria and assign_to_team is carried on a single entry
with field "customer.stage", operator "not_equals", value "".

operator MUST be one of: equals, not_equals, in, contains, gt, gte, lt, lte
Any other operator is invalid and will be rejected.

selection_strategy MUST be one of: least_load, round_robin, first_available
  least_load  — give it to whoever has the fewest jobs today
  round_robin — take turns
  first_available — the first one on the list

capacity_constraint is optional:
  {{"max_per_day": <number 1 or more>, "mode": "hard_block" or "soft_warn"}}
  hard_block — never exceed the limit, pick someone else instead
  soft_warn  — allow it but flag it

assign_to_team must be the team name the person actually wrote. Do not
translate it, do not tidy it, do not invent one — it has to match a team
that exists in their system, and a renamed team matches nothing.

If the policy does not state something, leave the key out rather than
guessing a value. A missing capacity_constraint means no limit, which is
a real and common answer.

Technician teams in this company: {teams}
Sales groups in this company: {sales_groups}
"""


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        # Models fence JSON despite being told not to; stripping is cheaper
        # than a retry and does not change what was produced.
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


TEAM_UNKNOWN = {
    "th": "ไม่มี{kind}ชื่อ \"{name}\" ในร้าน (ที่มี: {known})",
    "en": "There is no {kind} named \"{name}\" (existing: {known})",
}
KIND_LABEL = {
    "technician": {"th": "ทีมช่าง", "en": "technician team"},
    "sales": {"th": "กลุ่มขาย", "en": "sales group"},
}


def team_problems(rule: dict, *, teams: list[str], sales_groups: list[str], language: str = "th") -> list[str]:
    """Every assign_to_team must be a team that exists for the rule's
    scope. The model is told the lists, but a policy can still name a
    team that was never created ("ให้ทีมขายองค์กร") — and a saved rule
    pointing at nothing would confirm "บันทึกแล้ว" and then assign nobody,
    forever (round 18, 14 Sep 2026)."""
    scope = str(rule.get("scope") or "technician")
    known = sales_groups if scope == "sales" else teams
    lowered = {k.strip().lower(): k for k in known}
    problems: list[str] = []
    for item in rule.get("match_criteria") or []:
        name = str(item.get("assign_to_team") or "").strip()
        if name and name.lower() not in lowered:
            problems.append(
                TEAM_UNKNOWN[language if language in TEAM_UNKNOWN else "th"].format(
                    kind=KIND_LABEL[scope][language if language in ("th", "en") else "th"],
                    name=name, known=", ".join(known) if known else "-",
                )
            )
        elif name:
            item["assign_to_team"] = lowered[name.lower()]
    return problems


async def policy_to_rule(
    policy: str, *, teams: list[str], sales_groups: list[str] | None = None,
    scope_hint: str | None = None, client=None, language: str = "th",
) -> tuple[dict | None, list[str]]:
    """(rule, problems). A rule is only returned when it validates.

    Returning problems rather than raising lets the caller show the person
    what was wrong with their policy — "I did not understand 'sounds like'"
    is actionable, an exception is not.
    """
    sales_groups = list(sales_groups or [])
    prompt = SYSTEM_PROMPT.format(
        teams=", ".join(teams) if teams else "(none yet)",
        sales_groups=", ".join(sales_groups) if sales_groups else "(none yet)",
    )
    if scope_hint:
        prompt += f"\nThe person is configuring the '{scope_hint}' scope.\n"

    try:
        raw = await complete(
            system_prompt=prompt, user_message=policy, max_tokens=800, client=client,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("policy translation failed")
        return None, [f"AI unavailable: {exc}"]

    try:
        rule = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        log.warning("policy translation returned non-JSON: %r", raw[:200])
        return None, ["the model did not return valid JSON"]

    if not isinstance(rule, dict):
        return None, ["the model did not return a rule object"]

    rule.setdefault("version", 1)
    if scope_hint:
        # The caller knows which scope is being configured; the model's
        # guess must not silently reassign a technician policy to sales.
        rule["scope"] = scope_hint

    problems = validate_rule(rule)
    if not problems:
        problems = team_problems(rule, teams=teams, sales_groups=sales_groups, language=language)
    return (None, problems) if problems else (rule, [])


def describe_rule(rule: dict, language: str = "th") -> str:
    """The rule in words, for the confirmation step (11.6).

    Generated from the STRUCTURE, not from the model's own summary: the
    person is confirming what will actually execute, and a summary written
    by the same model that produced the rule could describe something it
    did not emit.
    """
    lines: list[str] = []
    scope_label = {"technician": "ช่าง", "sales": "ฝ่ายขาย"}.get(
        rule.get("scope", ""), rule.get("scope", "")
    )
    lines.append(f"ขอบเขต: {scope_label}")

    criteria = rule.get("match_criteria") or []
    if criteria:
        lines.append("เงื่อนไข:")
        for item in criteria:
            lines.append(
                f"  · ถ้า {item.get('field')} {item.get('operator')} "
                f"{item.get('value')} → ทีม {item.get('assign_to_team')}"
            )
    else:
        lines.append("เงื่อนไข: ไม่มี (ใช้กับทุกงาน)")

    strategy = {
        "least_load": "เลือกคนที่งานน้อยที่สุด",
        "round_robin": "สลับกันไปตามลำดับ",
        "first_available": "คนแรกที่ว่าง",
    }.get(rule.get("selection_strategy", "round_robin"), rule.get("selection_strategy"))
    lines.append(f"วิธีเลือก: {strategy}")

    capacity = rule.get("capacity_constraint")
    if capacity:
        mode = (
            "ห้ามเกิน" if capacity.get("mode", "hard_block") == "hard_block"
            else "เกินได้แต่จะเตือน"
        )
        lines.append(f"จำกัดงาน: วันละ {capacity.get('max_per_day')} งาน ({mode})")
    else:
        lines.append("จำกัดงาน: ไม่จำกัด")

    return "\n".join(lines)
