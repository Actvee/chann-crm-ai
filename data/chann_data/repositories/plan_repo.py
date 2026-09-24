"""Round 21D — a licence's plan, read from the database.

The matrix is chann_data/plans.py. This reads which plan a licence is on,
the admin's AI-credit override and how many people hold a seat, and makes
the checks that must share a transaction with the change they guard: a
seat (a join, a reactivation, a move), a plan change (owner, 24 ก.ย. 2569:
a downgrade is refused while the shop is over the target's limit) and a
feature the Data tier itself acts on.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ApiKey, ApprovalWorkflow, ChatSession, CustomerLicenseLink, CustomRole, DocumentTemplate,
    License, LicenseMember, LicenseSetting, ServiceTicket, Warranty,
)
from ..permissions import DEFAULT_ROLE_TEMPLATES
from ..plans import (
    FEATURE_KEYS, PLANS, MemberLimitReached, PlanDowngradeRefused, PlanFeatureLocked, UnknownPlan,
    over_limit, plan, resolve,
)

#: The same two settings rows LicenseSettingRepository has always used.
QUOTA_KEY = "ai_chart_quota"
USAGE_KEY = "ai_chart_usage"
OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")


class PlanRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ reads

    def plan_code(self, license_id) -> str:
        code = self._s.execute(
            select(License.plan_code).where(License.id == license_id)
        ).scalar_one_or_none()
        return plan(code).code

    def ai_override(self, license_id):
        return self._s.execute(
            select(LicenseSetting.setting_value).where(
                LicenseSetting.license_id == license_id, LicenseSetting.setting_key == QUOTA_KEY,
            )
        ).scalar_one_or_none()

    def payload(self, license_id) -> dict:
        return resolve(self.plan_code(license_id), ai_override=self.ai_override(license_id))

    def active_people(self, license_id) -> int:
        """Distinct PEOPLE with an active row on any channel — a person who
        is both sales and technician is one user; customers have no row."""
        return int(self._s.execute(
            select(func.count(func.distinct(LicenseMember.chann_uid))).where(
                LicenseMember.license_id == license_id, LicenseMember.status == "active",
            )
        ).scalar_one() or 0)

    def is_active_person(self, license_id, chann_uid: str) -> bool:
        return self._s.execute(
            select(LicenseMember.id).where(
                LicenseMember.license_id == license_id,
                LicenseMember.chann_uid == chann_uid,
                LicenseMember.status == "active",
            ).limit(1)
        ).first() is not None

    def owner_uid(self, license_id) -> str | None:
        return self._s.execute(
            select(LicenseMember.chann_uid)
            .join(CustomRole, (CustomRole.license_id == LicenseMember.license_id)
                  & (CustomRole.role_name == LicenseMember.role))
            .where(LicenseMember.license_id == license_id, LicenseMember.status == "active",
                   CustomRole.is_owner.is_(True))
            .order_by(LicenseMember.created_at).limit(1)
        ).scalar_one_or_none()

    def ai_used(self, license_id, *, month: str) -> int:
        value = self._s.execute(
            select(LicenseSetting.setting_value).where(
                LicenseSetting.license_id == license_id, LicenseSetting.setting_key == USAGE_KEY,
            )
        ).scalar_one_or_none()
        if not isinstance(value, dict) or str(value.get("month") or "") != month:
            return 0
        try:
            return int(value.get("used") or 0)
        except (TypeError, ValueError):
            return 0

    def usage(self, license_id, *, month: str) -> dict:
        limits = self.payload(license_id)["limits"]
        return {
            "members": self.active_people(license_id),
            "members_limit": limits["members"],
            "ai_reports_used": self.ai_used(license_id, month=month),
            "ai_reports_allowance": limits["ai_reports_per_month"],
            "ai_reports_month": month,
        }

    # ------------------------------------------------------------ checks

    def require_feature(self, license_id, feature: str, **extra) -> None:
        self.require_features(license_id, feature, **extra)

    def require_features(self, license_id, *features: str, **extra) -> None:
        """Every feature in order, the plan read ONCE; the first one the
        plan lacks is refused. No features: no read."""
        if not features:
            return
        code = self.plan_code(license_id)
        for feature in features:
            if not PLANS[code].has(feature):
                raise PlanFeatureLocked(feature, code, **extra)

    def require_seat(self, license_id, chann_uid: str) -> None:
        """One more PERSON would join. Locks the licence row first, so two
        redemptions in the same second queue here and the second one counts
        the first (spec §5.7). A person who already holds an active row on
        any channel takes no new seat."""
        row = self._s.execute(
            select(License).where(License.id == license_id).with_for_update()
        ).scalar_one_or_none()
        if row is None or self.is_active_person(license_id, chann_uid):
            return
        current = plan(row.plan_code)
        if current.members is None:
            return
        if self.active_people(license_id) >= current.members:
            raise MemberLimitReached(
                current.members, current.code, license_id=str(license_id),
                company_name=row.company_name, owner_chann_uid=self.owner_uid(license_id),
            )

    def licences_without(self, feature: str) -> list:
        """Every licence whose plan lacks `feature` — for the platform's own
        sweeps, which have no principal (spec §5.9)."""
        codes = [code for code, p in PLANS.items() if not p.has(feature)]
        if not codes:
            return []
        return list(self._s.execute(select(License.id).where(License.plan_code.in_(codes))).scalars())

    def check_change(self, license_id, to_code: str) -> None:
        """Owner, 24 ก.ย. 2569: refuse a downgrade while active members
        exceed the target's limit — nobody is left over a limit."""
        if to_code not in PLANS:
            raise UnknownPlan(f"unknown plan '{to_code}'")
        if to_code == self.plan_code(license_id):
            return  # the same plan: nothing changes, nothing to refuse
        self._s.execute(select(License.id).where(License.id == license_id).with_for_update())
        people = self.active_people(license_id)
        if over_limit(people, PLANS[to_code]):
            raise PlanDowngradeRefused(people, PLANS[to_code].members, to_code)

    # ------------------------------------------------------------ the admin's preview

    def preview(self, license_id) -> dict:
        current = PLANS[self.plan_code(license_id)]
        people = self.active_people(license_id)
        counts = self._feature_counts(license_id)
        previews = {}
        for code, target in PLANS.items():
            if code == current.code:
                continue
            excess = over_limit(people, target)
            previews[code] = {
                "plan": code, "label": target.label,
                "locks": [{"feature": key, "counts": counts[key]}
                          for key in FEATURE_KEYS if current.has(key) and not target.has(key)],
                "members": people, "limit": target.members,
                "refused": excess > 0, "inactivate": excess,
            }
        return {"current": current.code, "previews": previews}

    def _count(self, model, *where) -> int:
        return int(self._s.execute(select(func.count()).select_from(model).where(*where)).scalar_one() or 0)

    def _feature_counts(self, license_id) -> dict[str, dict[str, int]]:
        custom_roles = [
            name for name in self._s.execute(
                select(CustomRole.role_name).where(CustomRole.license_id == license_id)
            ).scalars()
            if name not in DEFAULT_ROLE_TEMPLATES
        ]
        # The deepest active chain, not whichever workflow the query
        # happened to return first: that depth is what a downgrade locks.
        depth = max((
            len(list((rules or {}).get("steps") or []))
            for rules in self._s.execute(
                select(ApprovalWorkflow.rules_json).where(
                    ApprovalWorkflow.license_id == license_id, ApprovalWorkflow.is_active.is_(True),
                )
            ).scalars()
            if isinstance(rules, dict)
        ), default=0)
        technicians = int(self._s.execute(
            select(func.count(func.distinct(LicenseMember.chann_uid))).where(
                LicenseMember.license_id == license_id, LicenseMember.status == "active",
                LicenseMember.channel == "technician",
            )
        ).scalar_one() or 0)
        return {
            "feature.service": {
                "open_tickets": self._count(ServiceTicket, ServiceTicket.license_id == license_id,
                                            ServiceTicket.status.in_(OPEN_TICKET_STATUSES)),
                "technicians": technicians,
            },
            "feature.warranty": {"warranties": self._count(Warranty, Warranty.license_id == license_id)},
            "feature.live_chat": {"open_chats": self._count(
                ChatSession, ChatSession.license_id == license_id, ChatSession.status == "open")},
            "feature.customer_line_link": {"linked_customers": self._count(
                CustomerLicenseLink, CustomerLicenseLink.license_id == license_id)},
            "feature.custom_documents": {"templates": self._count(
                DocumentTemplate, DocumentTemplate.license_id == license_id, DocumentTemplate.is_active.is_(True))},
            "feature.custom_roles": {"custom_roles": len(custom_roles)},
            "feature.multi_level_approval": {"steps": depth},
            "feature.external_api": {"live_keys": self._count(
                ApiKey, ApiKey.license_id == license_id, ApiKey.revoked_at.is_(None))},
        }
