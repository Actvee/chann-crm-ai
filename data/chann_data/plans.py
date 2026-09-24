"""The four sales plans (round 21D) — the owner's comparison table as one
literal.

Owner, 24 ก.ย. 2569: "ตัวควบคุมฟีเจอร์ตาม plan". The table is
docs/superpowers/specs/2026-09-24-sales-plans-source.html, and
tests/unit/test_round21d_plan_matrix.py parses it and fails the moment this
file and the table disagree.

A plan is code, not a row: a feature key means nothing until code enforces
it (review C9, 6 Sep 2026 — "catalogue entries nothing enforced"), so a
plan change ships with code and a gate run. Per-licence exceptions (more AI
credits for one shop) are `license_settings` rows, as they always were.

This module holds the table, `resolve()` and the three refusals every tier
speaks. Reading a licence's plan from the database is
`repositories/plan_repo.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

FEATURE_KEYS: tuple[str, ...] = (
    "feature.customer_line_link",
    "feature.live_chat",
    "feature.service",
    "feature.warranty",
    "feature.custom_documents",
    "feature.custom_roles",
    "feature.multi_level_approval",
    "feature.external_api",
)
AI_REPORTS = "quota.ai_reports_per_month"
MEMBERS = "limit.members"
QUOTA_KEYS: tuple[str, ...] = (AI_REPORTS,)
LIMIT_KEYS: tuple[str, ...] = (MEMBERS,)
ENTITLEMENT_KEYS: tuple[str, ...] = FEATURE_KEYS + QUOTA_KEYS + LIMIT_KEYS

#: The §4 English labels — the `message` of a refusal. The Thai labels
#: (chat, dashboard) live in the Application's entitlements.py; a unit test
#: keeps the two English copies equal.
FEATURE_LABELS_EN: dict[str, str] = {
    "feature.customer_line_link": "Customer LINE link",
    "feature.live_chat": "Customer chat",
    "feature.service": "Service jobs and technicians",
    "feature.warranty": "Warranty register",
    "feature.custom_documents": "Custom document templates",
    "feature.custom_roles": "Custom roles",
    "feature.multi_level_approval": "Multi-level approval",
    "feature.external_api": "External API",
    AI_REPORTS: "AI reports",
    MEMBERS: "Users",
}

#: Owner, 24 ก.ย. 2569 (spec §13 Q2): a downgrade is refused, in these words,
#: while the shop has more active members than the target plan allows.
DOWNGRADE_REFUSED_TH = "ต้องปิดใช้งาน (inactivate) สมาชิก {n} คนก่อนลดแพ็กเกจเป็น {plan}"


@dataclass(frozen=True)
class Plan:
    code: str
    label: str
    rank: int
    features: frozenset[str]
    ai_reports_per_month: int
    members: int | None          # None = no cap
    quota_top_up: bool           # the admin's ai_chart_quota override applies (§13 Q3)

    def has(self, key: str) -> bool:
        """The PLAN's answer. On Starter the AI-report road is locked, not
        used up — the person hears the plan, not a counter (spec §4)."""
        if key == AI_REPORTS:
            return self.ai_reports_per_month > 0
        if key in LIMIT_KEYS:
            return True
        return key in self.features


_PRO = frozenset({
    "feature.customer_line_link", "feature.live_chat", "feature.service",
    "feature.warranty", "feature.custom_documents", "feature.custom_roles",
})
_ENTERPRISE = _PRO | frozenset({"feature.multi_level_approval", "feature.external_api"})

PLANS: dict[str, Plan] = {
    "starter": Plan("starter", "Starter", 0, frozenset(), 0, 5, False),
    "pro": Plan("pro", "Pro", 1, _PRO, 30, 15, True),
    "enterprise": Plan("enterprise", "Enterprise", 2, _ENTERPRISE, 100, 50, True),
    "enterprise_plus": Plan("enterprise_plus", "Enterprise Plus", 3, _ENTERPRISE, 100, None, True),
}
PLAN_CODES: tuple[str, ...] = tuple(PLANS)
DEFAULT_PLAN = "pro"

ALWAYS_ON = "always_on"

#: Ruling 26: an assignment rule's SCOPE decides the feature it needs —
#: technician rules dispatch service jobs; sales rules are always on. The
#: Application's copy is entitlements.ASSIGNMENT_RULE_SCOPE_FEATURE
#: (test_round21d_entitlements keeps the two equal).
ASSIGNMENT_RULE_SCOPE_FEATURE: dict[str, str] = {"technician": "feature.service"}

#: The table's 41 rows in order: (row label as the HTML says it, key).
#: Labels repeat ("Import CSV", "บันทึกเอกสารทุกครั้งที่ออก"), so this is an
#: ordered tuple, not a dict; the test compares the two sequences.
ROW_MAP: tuple[tuple[str, str], ...] = (
    ("เพิ่มลูกค้า (ทีละคน / หลายคน / Import CSV)", ALWAYS_ON),
    ("ตรวจข้อมูลซ้ำจากเบอร์/อีเมล", ALWAYS_ON),
    ("เปลี่ยน Lead เป็นลูกค้ายืนยัน", ALWAYS_ON),
    ("จัดเก็บข้อมูลลูกค้า", ALWAYS_ON),
    ("ประวัติการทำรายการของลูกค้า", ALWAYS_ON),
    ("ผูก LINE ลูกค้ากับร้าน", "feature.customer_line_link"),
    ("สร้างดีล กำหนดมูลค่า วันปิดดีล", ALWAYS_ON),
    ("อัปเดตและติดตามสถานะของดีล", ALWAYS_ON),
    ("จัดการหลายดีลพร้อมกัน", ALWAYS_ON),
    ("สร้างและจัดการรายการสินค้า", ALWAYS_ON),
    ("รหัสสินค้า ราคา และรายละเอียด", ALWAYS_ON),
    ("Import CSV", ALWAYS_ON),
    ("ค้นหาสินค้าผ่าน LINE / Dashboard", ALWAYS_ON),
    ("สร้างใบเสนอราคา", ALWAYS_ON),
    ("อัปเดตและติดตามสถานะใบเสนอราคา", ALWAYS_ON),
    ("บันทึกเอกสารทุกครั้งที่ออก", ALWAYS_ON),
    ("สร้างใบแจ้งหนี้", ALWAYS_ON),
    ("ติดตามสถานะใบแจ้งหนี้และการชำระเงิน", ALWAYS_ON),
    ("บันทึกเอกสารทุกครั้งที่ออก", ALWAYS_ON),
    ("นัดหมายลูกค้า / ติดตามงาน", ALWAYS_ON),
    ("แจ้งเตือนอัตโนมัติ", ALWAYS_ON),
    ("Daily Summary ทุกเช้า 08:00 น.", ALWAYS_ON),
    ("แชทกับลูกค้า (Live Chat + รูป)", "feature.live_chat"),
    ("LINE บริการลูกค้า", "feature.customer_line_link"),
    ("ตั้งค่า SLA การตอบแชท", "feature.live_chat"),
    ("รายงานพื้นฐาน", ALWAYS_ON),
    ("ถามรายงานด้วย AI (ตาราง/กราฟ)", AI_REPORTS),
    ("LINE ช่าง / ทีมช่าง", "feature.service"),
    ("รับงาน เช็คอิน + พิกัด ส่งรูปหน้างาน", "feature.service"),
    ("รายงานการซ่อม PDF + ลายเซ็น", "feature.service"),
    ("แบบประเมินหลังงานเสร็จ", "feature.service"),
    ("บันทึก Serial Number รุ่นที่ซื้อ-ระยะประกัน", "feature.warranty"),
    ("Import CSV", "feature.warranty"),
    ("ตรวจสถานะประกันจากงานซ่อม", "feature.warranty"),
    ("AI ร่างแบบฟอร์ม / อัปโหลด Word Template", "feature.custom_documents"),
    ("บทบาทมาตรฐาน", "feature.custom_roles"),
    ("ขั้นตอนอนุมัติหลายระดับ", "feature.multi_level_approval"),
    ("ประวัติการใช้งาน (Audit Log)", ALWAYS_ON),
    ("โอนความเป็นเจ้าของร้าน", ALWAYS_ON),
    ("เชื่อมต่อ API ภายนอก", "feature.external_api"),
    ("จำนวนผู้ใช้งาน", MEMBERS),
)


def plan(code: str | None) -> Plan:
    """The plan for a code; anything unknown is the default (Pro) — the
    backfill value, so a bad row never locks a paying shop out."""
    return PLANS.get(str(code or ""), PLANS[DEFAULT_PLAN])


def _by_rank() -> list[Plan]:
    return sorted(PLANS.values(), key=lambda p: p.rank)


def min_plan(key: str) -> str | None:
    """The lowest-ranked plan that has the key ("Pro ขึ้นไป"), computed —
    never typed by hand. None for the limit, which every plan has."""
    if key in LIMIT_KEYS:
        return None
    for candidate in _by_rank():
        if candidate.has(key):
            return candidate.code
    return None


def over_limit(people: int, p: Plan) -> int:
    """How many active members must be inactivated before `people` fit
    plan `p` — 0 when they fit (or the plan has no cap). The one place the
    downgrade refusal, the admin's preview and the refusal body do the
    arithmetic."""
    if p.members is None:
        return 0
    return max(0, int(people) - p.members)


def smallest_plan_for(people: int, *, at_least: str = "starter") -> str:
    """The lowest plan at or above `at_least` whose member limit holds
    `people` — what the migration's assertion moves an over-limit shop to."""
    floor = PLANS[at_least].rank
    for candidate in _by_rank():
        if candidate.rank >= floor and (candidate.members is None or people <= candidate.members):
            return candidate.code
    return "enterprise_plus"


def ai_allowance(p: Plan, override) -> int:
    """Plan default, unless the admin set an override and the plan honours
    one (owner, 24 ก.ย. 2569: Pro and up). `0` is a value — the admin
    switching the shop's AI reports off — and not "unset"; "" and None are
    unset."""
    if override is None or override == "" or not p.quota_top_up:
        return p.ai_reports_per_month
    try:
        return max(0, int(str(override).strip()))
    except (TypeError, ValueError):
        return p.ai_reports_per_month


def resolve(code: str | None, *, ai_override=None) -> dict:
    """The payload every principal carries (spec §3.2)."""
    p = plan(code)
    locked = {key: min_plan(key) for key in FEATURE_KEYS if not p.has(key)}
    if not p.has(AI_REPORTS):
        locked[AI_REPORTS] = min_plan(AI_REPORTS)
    return {
        "code": p.code,
        "label": p.label,
        "rank": p.rank,
        "features": sorted(key for key in FEATURE_KEYS if p.has(key)),
        "locked": locked,
        "limits": {"members": p.members, "ai_reports_per_month": ai_allowance(p, ai_override)},
        "quota_top_up": p.quota_top_up,
    }


class UnknownPlan(ValueError):
    """A plan code that is not one of the four."""


class PlanFeatureLocked(Exception):
    """The shop's plan does not have this feature. `extra` rides along in
    the body (company name/phone for the OA sentences)."""

    def __init__(self, feature: str, plan_code: str, **extra):
        super().__init__(f"{feature} is not in the {plan_code} plan")
        self.feature, self.plan_code, self.extra = feature, plan(plan_code).code, extra

    def detail(self) -> dict:
        need = min_plan(self.feature) or DEFAULT_PLAN
        return {
            "error": "plan_required", "feature": self.feature, "plan": self.plan_code,
            "min_plan": need,
            # "<label>: included from …" — the labels are singular and
            # plural nouns alike, so the sentence carries no verb to agree.
            "message": (
                f"{FEATURE_LABELS_EN.get(self.feature, self.feature)}: included from the "
                f"{PLANS[need].label} plan. This shop is on {PLANS[self.plan_code].label}."
            ),
            **self.extra,
        }


class MemberLimitReached(Exception):
    """One more person would take the shop past its plan's user limit."""

    def __init__(self, limit: int, plan_code: str, **extra):
        super().__init__(f"member limit {limit} reached on {plan_code}")
        self.limit, self.plan_code, self.extra = limit, plan(plan_code).code, extra

    def detail(self) -> dict:
        return {
            "error": "member_limit_reached", "limit": self.limit, "plan": self.plan_code,
            "message": f"The shop has all {self.limit} users its {PLANS[self.plan_code].label} plan allows.",
            **self.extra,
        }


class PlanDowngradeRefused(Exception):
    """Owner, 24 ก.ย. 2569: never leave a shop over its limit — inactivate first."""

    def __init__(self, members: int, limit: int, plan_code: str):
        super().__init__(f"{members} active members, {plan_code} allows {limit}")
        self.members, self.limit, self.plan_code = members, limit, plan(plan_code).code

    def detail(self) -> dict:
        label = PLANS[self.plan_code].label
        n = over_limit(self.members, PLANS[self.plan_code])
        return {
            "error": "plan_member_limit", "members": self.members, "limit": self.limit,
            "inactivate": n, "plan": self.plan_code, "plan_label": label,
            "message": DOWNGRADE_REFUSED_TH.format(n=n, plan=label),
        }
