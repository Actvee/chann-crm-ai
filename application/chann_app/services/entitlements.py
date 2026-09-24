"""What the shop's plan lets it do (round 21D) — the Application's half.

The Data tier owns the matrix (chann_data/plans.py) and sends the resolved
plan with every membership row, API-key resolution and tenant row; this
module READS that payload and never decides a plan. Entitlement sits beside
permission, never instead of it (spec §1): a role grants only what the plan
includes, and a plan grants nothing a role does not.

The copies here (labels, the Pro fallback) exist because the Application
cannot import chann_data (tier boundary); tests/unit/test_round21d_
entitlements.py keeps them equal to the Data tier's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status

from ..config import settings

log = logging.getLogger(__name__)

AI_REPORTS = "quota.ai_reports_per_month"
MEMBERS = "limit.members"
PLAN_ORDER = ("starter", "pro", "enterprise", "enterprise_plus")
PLAN_LABELS = {"starter": "Starter", "pro": "Pro", "enterprise": "Enterprise", "enterprise_plus": "Enterprise Plus"}

#: Spec §4 — the refusal and admin wording, both languages.
FEATURE_LABELS: dict[str, dict[str, str]] = {
    "feature.customer_line_link": {"th": "ผูก LINE ลูกค้ากับร้าน", "en": "Customer LINE link"},
    "feature.live_chat": {"th": "แชทกับลูกค้า", "en": "Customer chat"},
    "feature.service": {"th": "งานบริการ / งานซ่อม และทีมช่าง", "en": "Service jobs and technicians"},
    "feature.warranty": {"th": "ทะเบียนเครื่อง / ประกัน", "en": "Warranty register"},
    "feature.custom_documents": {"th": "แบบฟอร์มเอกสารของร้านเอง", "en": "Custom document templates"},
    "feature.custom_roles": {"th": "สร้างและแก้บทบาทเอง", "en": "Custom roles"},
    "feature.multi_level_approval": {"th": "ขั้นตอนอนุมัติหลายระดับ", "en": "Multi-level approval"},
    "feature.external_api": {"th": "เชื่อมต่อ API ภายนอก", "en": "External API"},
    AI_REPORTS: {"th": "ถามรายงานด้วย AI", "en": "AI reports"},
    MEMBERS: {"th": "จำนวนผู้ใช้งาน", "en": "Users"},
}

#: The payload of Pro — what a principal with no plan behaves as (spec §5.1).
UNKNOWN_PLAN: dict = {
    "code": "pro", "label": "Pro", "rank": 1,
    "features": ["feature.custom_documents", "feature.custom_roles", "feature.customer_line_link",
                 "feature.live_chat", "feature.service", "feature.warranty"],
    "locked": {"feature.multi_level_approval": "enterprise", "feature.external_api": "enterprise"},
    "limits": {"members": 15, "ai_reports_per_month": 30},
    "quota_top_up": True,
}

#: A permission key in one of these families is usable only when the plan
#: has the feature (spec §4.1).
PERMISSION_FEATURE: dict[str, str] = {
    "ticket.": "feature.service",
    "service_report.": "feature.service",
    "approval.": "feature.service",      # approvals are of service reports only
    "warranty.": "feature.warranty",
    "chat_session.": "feature.live_chat",
}
#: Every other permission key, named — so a NEW key has to be classified,
#: not forgotten (check-perms.py and the unit test both enforce it).
ALWAYS_ON_PERMISSIONS: frozenset[str] = frozenset({
    "customer.read", "customer.create", "customer.update", "customer.archive",
    "deal.read", "deal.create", "deal.update", "deal.archive", "deal.reopen",
    "note.read", "note.create", "note.update",
    "followup.read", "followup.create", "followup.update",
    "product.read", "product.manage", "team.manage",
    "quote.read", "quote.create", "quote.update",
    "invoice.read", "invoice.create", "invoice.update", "invoice.void",
    "reassign_records", "view_reports", "role.manage", "member.manage", "setting.manage",
    "audit_log.view", "platform.admin.access", "platform.admin.break_glass",
    "pdpa.request.view", "pdpa.request.process",
})
#: To the person minting an invite at the member limit (spec §6.2). Here,
#: not in chat: the Registration service and chat both say it, and chat
#: imports registration.
MEMBER_LIMIT_REACHED = {
    "th": "ร้านมีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว — เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม",
    "en": "The shop has all {limit} users its {plan} plan allows — remove someone who no longer uses it, or upgrade to invite more.",
}
#: Spec §7.2 — a technician invite code typed on a shop without service.
TECH_INVITE_PLAN_LOCKED = {
    "th": "รหัสนี้ใช้ไม่ได้ตอนนี้ — ร้าน {company} ใช้แพ็กเกจ {plan} ซึ่งยังไม่มีงานบริการและทีมช่าง แจ้งเจ้าของร้านได้เลย",
    "en": "This code can't be used right now — {company} is on the {plan} plan, which has no service jobs or technicians. Let the shop owner know.",
}
#: Spec §7.2 — an existing technician of a shop that downgraded.
TECH_OA_PLAN_LOCKED = {
    "th": "ร้าน {company} ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ {plan}) — งานและรายงานเดิมยังอยู่ครบ แจ้งเจ้าของร้านถ้าต้องการเปิดใช้",
    "en": "{company} has service jobs switched off for now ({plan} plan) — earlier jobs and reports are all kept. Tell the shop owner if you need them.",
}
#: Spec §7.3 — the Customer OA of a shop without the Customer LINE link.
CUSTOMER_OA_PLAN_LOCKED = {
    "th": "ร้าน {company} ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ {phone}",
    "en": "{company} does not serve customers on LINE yet — reach the shop on {phone}",
}
CUSTOMER_OA_PLAN_LOCKED_NO_PHONE = {
    "th": "ร้าน {company} ยังไม่เปิดให้บริการลูกค้าทาง LINE",
    "en": "{company} does not serve customers on LINE yet",
}
#: Spec §5.7 — the person whose join was turned away, and the owner, who
#: must hear it (a failed join is never silent).
MEMBER_LIMIT_JOIN_REFUSED = {
    "th": "ร้าน {company} มีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว ยังเข้าร่วมไม่ได้ — แจ้งเจ้าของร้านได้เลย",
    "en": "{company} already has all {limit} users its {plan} plan allows, so you can't join yet — let the shop owner know.",
}
MEMBER_LIMIT_OWNER_NOTICE = {
    "th": "มีคนใช้รหัสเชิญเข้าร้าน {company} ไม่สำเร็จ เพราะผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว — เอาสมาชิกที่ไม่ได้ใช้ออก หรือติดต่อทีม Chann1 เพื่ออัปเกรด",
    "en": "Someone could not join {company} with an invite code: all {limit} users of the {plan} plan are taken. Remove someone who no longer uses it, or contact Chann1 to upgrade.",
}


def customer_oa_plan_locked_reply(company: str, phone: str, language: str) -> str:
    """Spec §7.3, one phone-or-no-phone choice (R6 — chat.py's Customer OA
    gate and registration.py's refused link both format the same sentence
    from the shop's name and phone; a caller with no phone gets the shorter
    table, not an empty `{phone}`)."""
    table = CUSTOMER_OA_PLAN_LOCKED if phone else CUSTOMER_OA_PLAN_LOCKED_NO_PHONE
    text = table["en" if language == "en" else "th"]
    return text.format(company=company or "", phone=phone or "")


async def owner_notice(
    client, *, license_id: str, owner_chann_uid: str, kind: str,
    message: str, message_en: str,
) -> None:
    """Spec §5.7 (a turned-away join) and Task 13's plan-changed notice
    (R6): one best-effort "tell the owner" send, so whoever triggered the
    event never waits on it. `kind` is the notification `type`
    (`notify.TYPE_TO_OA` routes both to the Sales OA); the message is
    already formatted by the caller, since the two callers fill different
    words into their own tables."""
    from .notify import send_notification

    if not owner_chann_uid or not license_id:
        return
    try:
        await send_notification(
            client, license_id=license_id, target_chann_uid=owner_chann_uid,
            target_line_user_id=await client.line_target_of(owner_chann_uid),
            type=kind, message=message, message_en=message_en,
            entity_type="license", entity_id=license_id, oa="sales",
        )
    except Exception:  # noqa: BLE001
        log.exception("could not tell the owner of %s about %s", license_id, kind)


#: Owner decision Q3 / Ruling 28 — the plans whose admin AI top-up applies
#: (chann_data.plans `quota_top_up`; test_round21d_admin keeps them equal).
QUOTA_TOP_UP_PLANS: frozenset[str] = frozenset({"pro", "enterprise", "enterprise_plus"})
#: The admin API's refusal of a top-up on a plan without one. `whole`: the
#: same request also changed the plan, and none of it was written.
AI_QUOTA_NOT_ON_PLAN = {
    False: "แพ็กเกจ {plan} ไม่มีการเพิ่มโควต้ารายงาน AI — ไม่ได้บันทึก",
    True: "แพ็กเกจ {plan} ไม่มีการเพิ่มโควต้ารายงาน AI — ไม่ได้บันทึกอะไร แพ็กเกจยังเป็นแบบเดิม",
}


def ai_quota_refusal(plan_code: str, *, whole: bool) -> dict:
    label = PLAN_LABELS.get(plan_code, plan_code)
    return {"error": "ai_quota_not_on_plan", "plan": plan_code, "plan_label": label,
            "message": AI_QUOTA_NOT_ON_PLAN[whole].format(plan=label)}


#: Spec §3.4 — what the owner hears when the admin changes the plan.
PLAN_CHANGED = {"th": "แพ็กเกจของ {company} เปลี่ยนเป็น {plan} แล้ว", "en": "{company} is now on the {plan} plan"}
PLAN_CHANGED_GAINED = {"th": "เปิดใช้: {features}", "en": "Now included: {features}"}
PLAN_CHANGED_LOCKED = {"th": "ล็อกไว้ (ข้อมูลเดิมยังอยู่ครบ): {features}",
                       "en": "Locked (everything already recorded is kept): {features}"}


def plan_change_text(old: dict | None, new: dict | None, company: str, language: str = "th") -> str:
    """Spec §3.4 — what the owner is told when the admin changes the plan:
    the new plan, then one line for what it gained and one for what it
    locked. Three lines at most. Sent through `owner_notice` (R6)."""
    lang = "en" if language == "en" else "th"
    before, after = PlanView.from_payload(old), PlanView.from_payload(new)
    keys = [k for k in FEATURE_LABELS if k != MEMBERS]
    gained = [k for k in keys if after.has(k) and not before.has(k)]
    locked = [k for k in keys if before.has(k) and not after.has(k)]
    lines = [PLAN_CHANGED[lang].format(company=company, plan=after.label)]
    if gained:
        lines.append(PLAN_CHANGED_GAINED[lang].format(features=" · ".join(feature_label(k, lang) for k in gained)))
    if locked:
        lines.append(PLAN_CHANGED_LOCKED[lang].format(features=" · ".join(feature_label(k, lang) for k in locked)))
    return "\n".join(lines)


#: Features that share a key with always-on work, checked by NAME at the
#: route / handler (spec §4.1). Keyed (action, entity) exactly like
#: chat.ACTION_PERMISSIONS, which check-parity.py reads beside it.
#: Reading and revoking API keys stay open on every plan (a downgraded
#: owner must be able to see and revoke what an outside system holds);
#: only MAKING one is the feature — the same rule on both surfaces.
NAMED_FEATURE_CHECKS: dict[tuple[str, str], str] = {
    ("create", "api_key"): "feature.external_api",
    ("create", "role"): "feature.custom_roles",
    ("update", "role"): "feature.custom_roles",
    ("read", "team"): "feature.service",
    ("create", "team"): "feature.service",
    ("update", "team"): "feature.service",
    ("delete", "team"): "feature.service",
    ("archive", "team"): "feature.service",
    ("read", "survey"): "feature.service",
    ("send", "invoice"): "feature.customer_line_link",
    ("send", "quote"): "feature.customer_line_link",
}
#: Owner decision Q4: on a plan without service jobs the basic-report card
#: shows three reports, not five.
SERVICE_BASIC_REPORTS = ("open_jobs_by_tech", "satisfaction_avg")


def feature_of(permission_key: str | None) -> str | None:
    key = str(permission_key or "")
    for prefix, feature in PERMISSION_FEATURE.items():
        if key.startswith(prefix):
            return feature
    return None


def feature_for_intent(action: str | None, entity: str | None, permission_key: str | None) -> str | None:
    return NAMED_FEATURE_CHECKS.get((str(action or ""), str(entity or ""))) or feature_of(permission_key)


def feature_label(feature: str, language: str = "th") -> str:
    entry = FEATURE_LABELS.get(feature) or {}
    return entry.get("en" if language == "en" else "th") or feature


@dataclass(frozen=True)
class PlanView:
    code: str
    label: str
    features: frozenset[str]
    locked: tuple[tuple[str, str], ...]     # (key, min plan code); a tuple so the view stays hashable
    members_limit: int | None
    ai_allowance: int
    known: bool = True

    @classmethod
    def from_payload(cls, payload, *, known: bool = True) -> "PlanView":
        if not isinstance(payload, dict) or payload.get("code") not in PLAN_LABELS:
            return cls.unknown()
        if payload.get("features") is None:
            # A code with no feature list is a payload we cannot read, not
            # a plan with no features: behave as Pro like any other
            # unreadable plan (spec §5.1), never lock everything.
            return cls.unknown()
        limits = payload.get("limits") or {}
        return cls(
            code=str(payload["code"]),
            label=str(payload.get("label") or PLAN_LABELS[payload["code"]]),
            features=frozenset(str(f) for f in payload.get("features") or ()),
            locked=tuple(sorted((str(k), str(v)) for k, v in (payload.get("locked") or {}).items())),
            members_limit=limits.get("members"),
            ai_allowance=int(limits.get("ai_reports_per_month") or 0),
            known=known,
        )

    @classmethod
    def unknown(cls) -> "PlanView":
        """No plan from the Data tier (an older image mid-deploy, a read
        that failed): behave as Pro, the backfill value, so a transient
        failure never locks a paying shop out — the quota already fails open
        for the same reason (spec §5.1)."""
        return cls.from_payload(UNKNOWN_PLAN, known=False)

    def has(self, key: str) -> bool:
        if key == MEMBERS:
            return True
        if key == AI_REPORTS:
            return key not in dict(self.locked)
        return key in self.features

    def min_plan(self, key: str) -> str:
        return dict(self.locked).get(key) or "pro"

    def min_label(self, key: str) -> str:
        return PLAN_LABELS.get(self.min_plan(key), "Pro")

    def as_payload(self) -> dict:
        return {
            "code": self.code, "label": self.label, "features": sorted(self.features),
            "locked": dict(self.locked),
            "limits": {"members": self.members_limit, "ai_reports_per_month": self.ai_allowance},
            "known": self.known,
        }


def effective_keys(role_keys, plan: PlanView, *, whole_road: str | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """(keys the person may use, keys their role holds that the plan locks).
    Subtracting here is what makes every existing `principal.require(...)`
    and every chat `"x" in set(permission_keys)` plan-aware without editing
    them (spec §5.1). `whole_road`: a feature without which NONE of the keys
    mean anything (a customer of a shop without the Customer LINE link)."""
    keys = frozenset(role_keys or ())
    if whole_road is not None and not plan.has(whole_road):
        return frozenset(), keys
    locked = frozenset(k for k in keys if (feature := feature_of(k)) is not None and not plan.has(feature))
    return keys - locked, locked


def entitled(payload, key: str) -> bool:
    """For code with no principal (sweeps): the plan payload a tenant row carries."""
    return PlanView.from_payload(payload).has(key)


def refusal_detail(feature: str, plan: PlanView) -> dict:
    need = plan.min_plan(feature)
    return {
        "error": "plan_required", "feature": feature, "plan": plan.code, "min_plan": need,
        "message": (f"{feature_label(feature, 'en')}: included from the {PLAN_LABELS.get(need, 'Pro')} "
                    f"plan. This shop is on {plan.label}."),
    }


def plan_required(feature: str, plan: PlanView) -> HTTPException:
    """The standard refusal (spec §5.2) — 403, `error: plan_required`."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal_detail(feature, plan))


PLAN_REFUSAL_CODES = ("plan_required", "member_limit_reached", "plan_member_limit")


def is_plan_refusal(exc) -> bool:
    body = getattr(exc, "structured", None) or getattr(exc, "detail", None)
    return isinstance(body, dict) and body.get("error") in PLAN_REFUSAL_CODES


async def plan_for(client, license_id: str) -> tuple[PlanView, dict]:
    """(plan, usage) for code that has a licence but no principal. Fails
    open as Pro, like the principal does."""
    try:
        out = await client.license_plan(str(license_id)) or {}
    except Exception:  # noqa: BLE001 — a read that failed must not lock a shop out
        log.exception("could not read the plan of %s; acting as Pro", license_id)
        return PlanView.unknown(), {}
    return PlanView.from_payload(out.get("plan")), dict(out.get("usage") or {})


async def feature_allowed(client, license_id: str, feature: str) -> bool:
    """Round 21D (spec §5.9, controller ruling R5): the ONE gate every
    background sweep and customer push shares — `notify.send_notification`,
    `approval.send_survey`, `chat._notify_customer` and
    `selection.resolve_tenant_template` all call this instead of each
    repeating its own `plan_for` -> `has` -> skip block.

    Logged once at INFO, never raised: a shop without the feature skipping
    a push is the plan working as designed, not a failure to report loud."""
    plan, _usage = await plan_for(client, str(license_id))
    if plan.has(feature):
        return True
    log.info("licence %s: plan %s lacks %s, skipped", license_id, plan.code, feature)
    return False


#: Ruling 26 (round 21D task 11): an assignment rule's SCOPE decides the
#: feature it needs, not its entity name — "technician" belongs to
#: feature.service (the technician roster and jobs it dispatches), "sales"
#: is always on (sales groups stay open on every plan). NAMED_FEATURE_CHECKS
#: cannot see this: it is keyed by (action, entity), and both scopes share
#: the one entity "assignment_rule".
ASSIGNMENT_RULE_SCOPE_FEATURE: dict[str, str] = {"technician": "feature.service"}

#: Ruling 29 (21D final fix round 1): a shop setting that belongs to a
#: locked feature. `setting.manage` is always on, so the plan — not the
#: permission key — is the gate for these keys, on the dashboard's
#: PUT settings/{key} and on chat's "ตั้งเวลาตอบแชท" alike. The chat
#: answer-time and idle-close minutes are live chat's (plan matrix).
SETTING_KEY_FEATURE: dict[str, str] = {
    "chat_sla_minutes": "feature.live_chat",
    "chat_timeout_minutes": "feature.live_chat",
}


def sales_contact() -> dict | None:
    """Owner decision Q5: `CHANN_SALES_CONTACT` = "label|url". None when
    unset (the upgrade button is then not drawn). A URL that is not https
    is dropped — it would be put behind a button — and the label kept."""
    raw = str(settings.chann_sales_contact or "").strip()
    if not raw:
        return None
    label, _sep, url = raw.partition("|")
    label, url = label.strip(), url.strip()
    if not url.startswith("https://"):
        url = ""
    if not label and not url:
        return None
    return {"label": label or url, "url": url}
