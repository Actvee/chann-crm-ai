"""Phase 14-B — turning a typed approval policy into workflow JSON.

The same shape as assignment_policy.py, for the same reasons: the model
runs once, when an owner types "ตั้งการอนุมัติ ...", its output is
validated against the closed vocabulary the Data Tier's replace_workflow
accepts, and the flow is shown back in words before anything is saved.
The runtime executor (services/approval.py) never calls a model.
"""
from __future__ import annotations

import json
import logging

from .assignment_policy import _strip_fences
from .client import complete

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You convert an approval policy written in Thai or English
into approval workflow JSON. Reply with JSON only — no prose, no markdown
fences.

Shape:
{{"version": 1, "entity_type": "service_report",
  "steps": [{{"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"}},
            {{"order": 2, "approver_type": "role", "approver_ref": "admin"}}],
  "on_reject": "notify_submitter", "on_all_approved": "send_survey"}}

steps are in the order they must pass; order starts at 1 and counts up.

approver_type MUST be "user" or "role".
  "user" with approver_ref "ticket_owner" means the CS person who owns the
  ticket (the person the customer is assigned to). This is the only
  allowed value for a "user" step.
  "role" with approver_ref set to one of the role names that exist in this
  company (listed below). Write the role name exactly as listed.

"CS", "ซีเอส", "เจ้าของงาน", "คนดูแลลูกค้า" all mean the ticket_owner user step.
"admin", "แอดมิน", "ผู้ดูแล" mean the role "admin"; "owner", "เจ้าของร้าน" mean
the role "owner".

Keep on_reject as "notify_submitter" and on_all_approved as "send_survey"
unless the policy clearly says otherwise. If the policy names nothing at
all, return the one-step default (ticket_owner only).

Roles that exist in this company: {roles}
"""


def validate_workflow(rules: dict, *, roles: list[str]) -> list[str]:
    """The Data Tier's structural rules plus the one it cannot check: that
    a role approver names a role this company actually has."""
    problems: list[str] = []
    steps = rules.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["ต้องมีอย่างน้อยหนึ่งขั้น (steps)"]
    seen: set[int] = set()
    known = {r.lower() for r in roles}
    for step in steps:
        if not isinstance(step, dict):
            problems.append("แต่ละขั้นต้องเป็น object")
            continue
        order = step.get("order")
        if not isinstance(order, int) or order < 1:
            problems.append(f"ลำดับขั้นไม่ถูกต้อง: {order!r}")
        elif order in seen:
            problems.append(f"ลำดับขั้น {order} ซ้ำ")
        else:
            seen.add(order)
        kind = step.get("approver_type")
        ref = str(step.get("approver_ref") or "")
        if kind not in ("user", "role"):
            problems.append(f"approver_type ต้องเป็น user หรือ role ไม่ใช่ {kind!r}")
        elif kind == "user" and ref != "ticket_owner":
            problems.append(f"ขั้นแบบ user รองรับเฉพาะ ticket_owner ไม่ใช่ {ref!r}")
        elif kind == "role" and ref.lower() not in known:
            problems.append(f"ไม่มีบทบาทชื่อ '{ref}' ในบริษัทนี้")
    return problems


async def policy_to_workflow(
    policy: str, *, roles: list[str], client=None,
) -> tuple[dict | None, list[str]]:
    """(workflow, problems). A workflow is only returned when it validates."""
    prompt = SYSTEM_PROMPT.format(roles=", ".join(roles) if roles else "(none)")
    try:
        raw = await complete(
            system_prompt=prompt, user_message=policy, max_tokens=600, client=client,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("approval policy translation failed")
        return None, [f"AI unavailable: {exc}"]

    try:
        rules = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        log.warning("approval policy translation returned non-JSON: %r", raw[:200])
        return None, ["the model did not return valid JSON"]
    if not isinstance(rules, dict):
        return None, ["the model did not return a workflow object"]

    rules.setdefault("version", 1)
    rules["entity_type"] = "service_report"
    rules.setdefault("on_reject", "notify_submitter")
    rules.setdefault("on_all_approved", "send_survey")
    # Role names as the company spells them, whatever case the model used.
    canonical = {r.lower(): r for r in roles}
    for step in rules.get("steps") or []:
        if isinstance(step, dict) and step.get("approver_type") == "role":
            step["approver_ref"] = canonical.get(str(step.get("approver_ref") or "").lower(), step.get("approver_ref"))

    problems = validate_workflow(rules, roles=roles)
    return (None, problems) if problems else (rules, [])
