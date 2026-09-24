"""Round 21D — plan payloads for fakes, straight from the Data tier's own
resolve(), so a fake is never more generous than the real payload (the
17 ก.ย. 2569 lesson: a fake with extra keys hides dead code)."""
from __future__ import annotations

from chann_app.services.entitlements import PlanView
from chann_data.plans import resolve


def plan_payload(code: str, *, ai_override=None) -> dict:
    return resolve(code, ai_override=ai_override)


def plan_view(code: str) -> PlanView:
    return PlanView.from_payload(plan_payload(code))
