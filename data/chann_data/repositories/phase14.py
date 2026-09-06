"""Phase 14 — approval workflows, steps, and satisfaction surveys.

Owner decisions (3 Sep 2569) that this file encodes rather than
configures:

* **Default flow is one step**: the CS who owns the ticket. Created
  lazily per license the first time a report needs approving. If the
  ticket has no owner, the step falls back to the `admin` role so a
  report is never stuck with nobody able to pass it.
* **"ปิดงาน" is the last approver passing.** The moment no pending step
  remains, the service report becomes `approved` and a survey row is
  created — in the SAME transaction as that final step. Two facts that
  must agree get written together; the check-out route learned this the
  hard way in Phase 13.
* **Reject stops the flow.** One rejection marks the report `rejected`
  and leaves later steps untouched (they never become actionable); a
  resubmit after the technician fixes it starts a fresh set of steps.

Every read and write takes a TenantScope: an approver in tenant A cannot
see, let alone pass, a step in tenant B, and the multi-tenant test in
§14.6 pins that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CustomRole, LicenseMember

from ..models import (
    ApprovalStep, ApprovalWorkflow, SatisfactionSurvey, ServiceReport, ServiceTicket,
)
from .tenant_scope import TenantScope

DEFAULT_SCALE = {"1": "ไม่ดี", "2": "พอใช้", "3": "ดีเยี่ยม"}

DEFAULT_WORKFLOW = {
    "version": 1,
    "entity_type": "service_report",
    "steps": [
        # The owner's default: exactly this, nothing after it.
        {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
    ],
    "on_reject": "notify_submitter",
    "on_all_approved": "send_survey",
}


class ApprovalNotFound(Exception):
    pass


class ApprovalConflict(Exception):
    """Acting on a step that is not this actor's, or not pending."""


class ApprovalRepository:
    def __init__(self, session: Session):
        self._s = session

    # ----------------------------------------------------------- workflows

    def active_workflow(self, scope: TenantScope, entity_type: str) -> ApprovalWorkflow:
        """The tenant's active workflow for an entity type, defaulting lazily.

        Lazy, not seeded: a migration that inserts business rules for
        every tenant is a rule nobody can later find the origin of. A
        default created here has `updated_by=None`, which IS the record
        that nobody chose it.
        """
        row = self._s.execute(
            select(ApprovalWorkflow).where(
                ApprovalWorkflow.license_id == scope.license_id,
                ApprovalWorkflow.entity_type == entity_type,
                ApprovalWorkflow.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
        row = ApprovalWorkflow(
            license_id=scope.license_id, entity_type=entity_type,
            rules_json={**DEFAULT_WORKFLOW, "entity_type": entity_type},
            is_active=True,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def replace_workflow(
        self, scope: TenantScope, entity_type: str, rules_json: dict,
        updated_by: uuid.UUID | None,
    ) -> ApprovalWorkflow:
        """Retire the active workflow and install a new one.

        The old row stays with is_active=false: "who changed the approval
        flow and to what" is an audit question the rows should answer.
        Validation is structural only — order/approver shape — because
        whether a role exists is the caller's (Application tier's)
        question, where the role catalogue lives.
        """
        steps = rules_json.get("steps") or []
        if not steps:
            raise ApprovalConflict("a workflow needs at least one step")
        seen = set()
        for step in steps:
            order = step.get("order")
            if not isinstance(order, int) or order < 1 or order in seen:
                raise ApprovalConflict(f"bad step order: {order!r}")
            seen.add(order)
            if step.get("approver_type") not in ("role", "user"):
                raise ApprovalConflict(f"bad approver_type: {step.get('approver_type')!r}")
            if not str(step.get("approver_ref") or "").strip():
                raise ApprovalConflict("approver_ref is required")

        for old in self._s.execute(
            select(ApprovalWorkflow).where(
                ApprovalWorkflow.license_id == scope.license_id,
                ApprovalWorkflow.entity_type == entity_type,
                ApprovalWorkflow.is_active.is_(True),
            )
        ).scalars():
            old.is_active = False
        row = ApprovalWorkflow(
            license_id=scope.license_id, entity_type=entity_type,
            rules_json={**rules_json, "entity_type": entity_type, "version": 1},
            is_active=True, updated_by=updated_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    # --------------------------------------------------------------- steps

    def open_steps_for_report(
        self, scope: TenantScope, report: ServiceReport,
    ) -> list[ApprovalStep]:
        """Create the pending steps for a freshly submitted report.

        Resolves `ticket_owner` to the ticket's owner_member_id at this
        moment — the person responsible when the report was filed — and
        falls back to the `admin` role when the ticket has no owner, so a
        report never waits on nobody. A second submit after a reject
        clears the old steps first: the UNIQUE(entity, step_order) would
        otherwise refuse, correctly, to file the same step twice.
        """
        scope.assert_owns(report.license_id)
        ticket = self._s.get(ServiceTicket, report.ticket_id)
        if ticket is None:
            raise ApprovalNotFound("ticket for report not found")
        scope.assert_owns(ticket.license_id)

        for old in self._s.execute(
            select(ApprovalStep).where(
                ApprovalStep.license_id == scope.license_id,
                ApprovalStep.entity_type == "service_report",
                ApprovalStep.entity_id == report.id,
            )
        ).scalars():
            self._s.delete(old)
        self._s.flush()

        workflow = self.active_workflow(scope, "service_report")
        created: list[ApprovalStep] = []
        for spec in sorted(workflow.rules_json.get("steps") or [], key=lambda s: s["order"]):
            approver_type = spec["approver_type"]
            approver_ref = str(spec["approver_ref"])
            if approver_type == "user" and approver_ref == "ticket_owner":
                if ticket.owner_member_id is not None:
                    approver_ref = str(ticket.owner_member_id)
                else:
                    approver_type, approver_ref = "role", self._fallback_role(scope)
            step = ApprovalStep(
                license_id=scope.license_id, entity_type="service_report",
                entity_id=report.id, workflow_id=workflow.id,
                step_order=int(spec["order"]), approver_type=approver_type,
                approver_ref=approver_ref, status="pending",
            )
            self._s.add(step)
            created.append(step)
        self._s.flush()
        return created

    def _fallback_role(self, scope: TenantScope) -> str:
        """The role that approves when a ticket has no owner: "admin" when
        somebody holds it, otherwise the tenant's owner role — an owner-
        only shop used to get a step nobody could act on (review, 6 Sep)."""
        has_admin = self._s.execute(
            select(LicenseMember.id).where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.role == "admin",
                LicenseMember.status == "active",
            )
        ).first()
        if has_admin is not None:
            return "admin"
        owner_role = self._s.execute(
            select(CustomRole.role_name).where(
                CustomRole.license_id == scope.license_id, CustomRole.is_owner.is_(True),
            )
        ).scalar_one_or_none()
        return owner_role or "admin"

    def pending_for(
        self, scope: TenantScope, *, member_id: uuid.UUID | None, role_names: list[str],
    ) -> list[ApprovalStep]:
        """Steps this member may act on NOW — their own, or their roles' —
        and only the lowest pending order per entity, so step 2 is not
        offered while step 1 is still open."""
        rows = self._s.execute(
            select(ApprovalStep).where(
                ApprovalStep.license_id == scope.license_id,
                ApprovalStep.status == "pending",
            ).order_by(ApprovalStep.entity_id, ApprovalStep.step_order)
        ).scalars().all()
        first_pending: dict[uuid.UUID, ApprovalStep] = {}
        for row in rows:
            first_pending.setdefault(row.entity_id, row)
        mine = []
        for row in first_pending.values():
            if row.approver_type == "user" and member_id is not None \
                    and row.approver_ref == str(member_id):
                mine.append(row)
            elif row.approver_type == "role" and row.approver_ref in role_names:
                mine.append(row)
        return mine

    def steps_for_entity(
        self, scope: TenantScope, entity_type: str, entity_id: uuid.UUID,
    ) -> list[ApprovalStep]:
        return list(self._s.execute(
            select(ApprovalStep).where(
                ApprovalStep.license_id == scope.license_id,
                ApprovalStep.entity_type == entity_type,
                ApprovalStep.entity_id == entity_id,
            ).order_by(ApprovalStep.step_order)
        ).scalars())

    def act(
        self, scope: TenantScope, step_id: uuid.UUID, *, approve: bool,
        member_id: uuid.UUID | None, role_names: list[str], reason: str | None = None,
    ) -> tuple[ApprovalStep, str, SatisfactionSurvey | None]:
        """Approve or reject one step. Returns (step, report_status, survey).

        `report_status` is what the service report is AFTER this call —
        'submitted' while steps remain, 'approved' when this was the last,
        'rejected' on a reject. The survey is returned only when it was
        created here, so the caller can send it in the same breath.
        """
        # Locked: two approvers acting on the same last step at once both
        # passed the "pending" check and the second died on the survey's
        # unique constraint after the first had committed (review, 6 Sep).
        step = self._s.execute(
            select(ApprovalStep).where(ApprovalStep.id == step_id).with_for_update()
        ).scalars().first()
        if step is None:
            raise ApprovalNotFound("approval step not found")
        scope.assert_owns(step.license_id)
        if step.status != "pending":
            raise ApprovalConflict("step already acted on")

        # An earlier step that is still open OR was rejected blocks this
        # one: a report CS sent back must not be approved by the admin on
        # the next step (review, 6 Sep 2026).
        earlier_blocking = self._s.execute(
            select(ApprovalStep).where(
                ApprovalStep.license_id == scope.license_id,
                ApprovalStep.entity_type == step.entity_type,
                ApprovalStep.entity_id == step.entity_id,
                ApprovalStep.step_order < step.step_order,
                ApprovalStep.status.in_(("pending", "rejected")),
            )
        ).first()
        if earlier_blocking is not None:
            raise ApprovalConflict("an earlier step is still pending or was rejected")

        allowed = (
            (step.approver_type == "user" and member_id is not None
             and step.approver_ref == str(member_id))
            or (step.approver_type == "role" and step.approver_ref in role_names)
        )
        if not allowed:
            raise ApprovalConflict("not this member's step to act on")

        step.status = "approved" if approve else "rejected"
        step.acted_by = member_id
        step.acted_at = datetime.now(timezone.utc)
        step.reason = reason

        report = self._s.get(ServiceReport, step.entity_id)
        if report is None:
            raise ApprovalNotFound("service report for step not found")
        scope.assert_owns(report.license_id)

        if report.status == "rejected":
            raise ApprovalConflict("this report was sent back; wait for the technician to resubmit")

        survey: SatisfactionSurvey | None = None
        if not approve:
            report.status = "rejected"
            report_status = "rejected"
            # The technician can fix it and check out again only if the
            # ticket is back in progress: check-out refuses a completed
            # ticket (13.4), and until 6 Sep 2026 a rejected report was a
            # dead end the reply text promised a way out of.
            ticket = self._s.get(ServiceTicket, report.ticket_id)
            if ticket is not None and ticket.status == "completed":
                ticket.status = "in_progress"
        else:
            remaining = self._s.execute(
                select(ApprovalStep).where(
                    ApprovalStep.license_id == scope.license_id,
                    ApprovalStep.entity_type == step.entity_type,
                    ApprovalStep.entity_id == step.entity_id,
                    ApprovalStep.status == "pending",
                )
            ).first()
            if remaining is None:
                # The owner's "ปิดงาน": the last approver passing. Report
                # approved and survey created in this same flush.
                report.status = "approved"
                report_status = "approved"
                survey = self._ensure_survey(scope, report.ticket_id)
            else:
                report_status = "submitted"
        self._s.flush()
        return step, report_status, survey

    # ------------------------------------------------------------- surveys

    def _ensure_survey(self, scope: TenantScope, ticket_id: uuid.UUID) -> SatisfactionSurvey:
        row = self._s.execute(
            select(SatisfactionSurvey).where(
                SatisfactionSurvey.license_id == scope.license_id,
                SatisfactionSurvey.ticket_id == ticket_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = SatisfactionSurvey(
                license_id=scope.license_id, ticket_id=ticket_id,
                scale_config_json=dict(DEFAULT_SCALE),
            )
            self._s.add(row)
            self._s.flush()
        return row

    def mark_survey_sent(self, scope: TenantScope, survey_id: uuid.UUID) -> SatisfactionSurvey:
        row = self._s.get(SatisfactionSurvey, survey_id)
        if row is None:
            raise ApprovalNotFound("survey not found")
        scope.assert_owns(row.license_id)
        row.sent_at = datetime.now(timezone.utc)
        self._s.flush()
        return row

    def pending_survey_for_ticket(
        self, scope: TenantScope, ticket_id: uuid.UUID,
    ) -> SatisfactionSurvey | None:
        return self._s.execute(
            select(SatisfactionSurvey).where(
                SatisfactionSurvey.license_id == scope.license_id,
                SatisfactionSurvey.ticket_id == ticket_id,
                SatisfactionSurvey.submitted_at.is_(None),
            )
        ).scalar_one_or_none()

    def submit_survey(
        self, scope: TenantScope, survey_id: uuid.UUID, *, score: int, comment: str | None,
        actor_chann_uid: str | None = None,
    ) -> SatisfactionSurvey:
        row = self._s.get(SatisfactionSurvey, survey_id)
        if row is None:
            raise ApprovalNotFound("survey not found")
        scope.assert_owns(row.license_id)
        if actor_chann_uid:
            # The customer the ticket belongs to — not the CS who approved
            # it, not another customer of the shop (review, 6 Sep 2026).
            ticket = self._s.get(ServiceTicket, row.ticket_id)
            owner = str((ticket.customer_chann_uid if ticket is not None else "") or "")
            if owner and owner != actor_chann_uid:
                raise ApprovalNotFound("survey not found")
        if row.submitted_at is not None:
            raise ApprovalConflict("survey already answered")
        if str(score) not in row.scale_config_json:
            raise ApprovalConflict(f"score {score} is not on this survey's scale")
        row.score = score
        row.comment = comment
        row.submitted_at = datetime.now(timezone.utc)
        self._s.flush()
        return row
