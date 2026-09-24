"""The five questions a shop asks every day (round 21C).

Owner, 23 ก.ย. 2569: these five must be right, always. So there is no
spec, no whitelist walk and no model anywhere in the number path — the
model only reads a sentence and says WHICH of the five it is, which is
model-first without handing arithmetic to anyone (docs/MODEL_FIRST.md).

Every one returns the same envelope, so the chat reply and the dashboard
card render from one payload and cannot disagree about a total. Nothing
downstream recomputes: the differences and the percentages are worked out
here, in Decimal, not in TypeScript and not in a reply builder (the round
20K lesson).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from ..models import (
    ChannIdentity, Deal, Invoice, LicenseMember, SatisfactionSurvey, ServiceTicket,
    TechnicianTeam,
)
from .deal_value import deal_value_subquery
from .phase17 import BANGKOK, date_window
from .phase9 import DEAL_STAGES, DealRepository
from .tenant_scope import TenantScope

REPORT_KEYS = (
    "pipeline_value", "won_this_month", "open_jobs_by_tech",
    "outstanding_invoices", "satisfaction_avg",
)
OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")
OPEN_INVOICE_STATUSES = ("issued", "partially_paid")
STAGE_WORDS = {
    "new": ("ใหม่", "New"), "proposed": ("เสนอราคาแล้ว", "Proposed"),
    "won": ("ปิดสำเร็จ", "Won"), "lost": ("ไม่สำเร็จ", "Lost"),
}
UNASSIGNED = ("ยังไม่มอบหมาย", "Unassigned")


def _row(key: str, label_th: str, label_en: str, value, count: int | None = None) -> dict:
    row = {"key": key, "label_th": label_th, "label_en": label_en, "value": float(value or 0)}
    if count is not None:
        row["count"] = int(count)
    return row


def _envelope(key: str, title_th: str, title_en: str, unit: str, headline: dict,
              rows: list[dict], notes_th: list[str] | None = None,
              notes_en: list[str] | None = None) -> dict:
    return {
        "key": key, "title_th": title_th, "title_en": title_en, "unit": unit,
        "headline": headline, "rows": rows,
        "notes_th": notes_th or [], "notes_en": notes_en or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _headline(label_th: str, label_en: str, value) -> dict:
    return {"label_th": label_th, "label_en": label_en, "value": float(value or 0)}


class BasicReportRepository:
    def __init__(self, session: Session):
        self._s = session

    def run(self, scope: TenantScope, key: str, *, today: date | None = None) -> dict:
        if key not in REPORT_KEYS:
            raise ValueError(f"unknown report: {key!r}")
        return getattr(self, f"_{key}")(scope, today=today)

    # ---------------------------------------------------------------- 1
    def _pipeline_value(self, scope: TenantScope, *, today: date | None = None) -> dict:
        """Straight from the pipeline card, reshaped — not a second query.
        Two queries that agree today are two queries that will disagree
        one day (diagnosis §1d)."""
        card = DealRepository(self._s).pipeline_summary(scope)
        rows = [
            _row(stage, *STAGE_WORDS[stage],
                 value=Decimal(card["by_stage"].get(stage, {}).get("value") or 0),
                 count=int(card["by_stage"].get(stage, {}).get("count") or 0))
            for stage in ("new", "proposed", "won", "lost") if stage in DEAL_STAGES
        ]
        total = sum((Decimal(b["value"]) for b in card["by_stage"].values()), Decimal("0"))
        return _envelope(
            "pipeline_value", "มูลค่าดีลทั้งหมด", "Pipeline value", "money",
            _headline("มูลค่ารวมทุกดีล", "All deals", total), rows,
            notes_th=[f"ยังเปิดอยู่ {card['open_value']} บาท · คาดว่าจะปิดเดือนนี้ {card['closing_this_month']} บาท"],
            notes_en=[f"Open {card['open_value']} · forecast to close this month {card['closing_this_month']}"],
        )

    # ---------------------------------------------------------------- 2
    def _won_this_month(self, scope: TenantScope, *, today: date | None = None) -> dict:
        rows = []
        for range_key, th, en in (("this_month", "เดือนนี้", "This month"),
                                  ("last_month", "เดือนที่แล้ว", "Last month")):
            start, end = date_window(range_key, today=today)
            inner = (
                deal_value_subquery()
                .where(
                    Deal.license_id == scope.license_id,
                    Deal.archived_at.is_(None),
                    Deal.stage == "won",
                    Deal.closed_at >= start,
                    Deal.closed_at < end,
                )
                .subquery()
            )
            # One round trip for the sum and the count, the same shape
            # `_outstanding_invoices` uses below: two queries over the same
            # window are two queries that can disagree about it.
            value, count = self._s.execute(
                select(func.coalesce(func.sum(inner.c.value), 0), func.count())
                .select_from(inner)
            ).one()
            rows.append(_row(range_key, th, en, value, count))
        now, before = Decimal(str(rows[0]["value"])), Decimal(str(rows[1]["value"]))
        difference = now - before
        percent = (difference / before * 100) if before > 0 else None
        moved_th = f"{'มากกว่า' if difference >= 0 else 'น้อยกว่า'}เดือนที่แล้ว {abs(difference):,.2f} บาท"
        if percent is not None:
            moved_th += f" ({abs(percent):,.0f}%)"
        return _envelope(
            "won_this_month", "ยอดปิดสำเร็จเดือนนี้", "Won this month", "money",
            _headline("ปิดได้เดือนนี้", "Won this month", now), rows,
            notes_th=[moved_th,
                      "นับตามวันที่ปิดจริง · ดีลที่ปิดก่อน 23 ก.ย. 2569 ใช้วันที่แก้ไขล่าสุดเป็นวันปิด"],
            notes_en=["By the real close date; deals closed before 23 Sep 2026 use their last-updated date."],
        )

    # ---------------------------------------------------------------- 3
    def _open_jobs_by_tech(self, scope: TenantScope, *, today: date | None = None) -> dict:
        found = self._s.execute(
            select(ServiceTicket.assigned_to_ref, func.count().label("n"))
            .where(
                ServiceTicket.license_id == scope.license_id,
                ServiceTicket.status.in_(OPEN_TICKET_STATUSES),
            )
            .group_by(ServiceTicket.assigned_to_ref)
            .order_by(func.count().desc())
        ).all()
        names = self._names(scope, [ref for ref, _n in found])
        rows = [
            _row(str(ref) if ref else "unassigned",
                 *(UNASSIGNED if ref is None
                   else names.get(ref, (str(ref)[:8], str(ref)[:8]))),
                 value=n, count=int(n))
            for ref, n in found
        ]
        return _envelope(
            "open_jobs_by_tech", "งานซ่อมค้างแยกตามช่าง", "Open jobs by technician", "count",
            _headline("งานค้างทั้งหมด", "Open jobs", sum(int(n) for _, n in found)), rows,
            notes_th=["นับสถานะ เปิดอยู่ · มอบหมายแล้ว · กำลังทำ"],
            notes_en=["Counting open, assigned and in progress."],
        )

    def _names(self, scope: TenantScope, refs) -> dict[uuid.UUID, tuple[str, str]]:
        """Every name this report shows, asked for once.

        A job is assigned to a person or to a team, and both are just a
        uuid in `assigned_to_ref` — so both are asked for together, in one
        round trip for the whole report instead of two per technician. A
        ref that matches neither is absent here and the caller shows it as
        an id rather than dropping the row, which is what `_who` did when
        it looked each ref up on its own.
        """
        wanted = {ref for ref in refs if isinstance(ref, uuid.UUID)}
        if not wanted:
            return {}
        people = (
            select(
                LicenseMember.id.label("ref"),
                literal("member").label("kind"),
                func.coalesce(
                    func.nullif(ChannIdentity.display_name, ""), LicenseMember.chann_uid,
                ).label("name"),
            )
            .join(ChannIdentity, ChannIdentity.chann_uid == LicenseMember.chann_uid, isouter=True)
            .where(
                LicenseMember.id.in_(wanted),
                LicenseMember.license_id == scope.license_id,
            )
        )
        teams = (
            select(
                TechnicianTeam.id.label("ref"),
                literal("team").label("kind"),
                TechnicianTeam.team_name.label("name"),
            )
            .where(
                TechnicianTeam.id.in_(wanted),
                TechnicianTeam.license_id == scope.license_id,
            )
        )
        found: dict[uuid.UUID, tuple[str, str]] = {}
        for ref, kind, name in self._s.execute(people.union_all(teams)):
            if kind == "member":
                found[ref] = (name, name)
            else:
                found.setdefault(ref, (f"ทีม {name}", f"Team {name}"))
        return found

    # ---------------------------------------------------------------- 4
    def _outstanding_invoices(self, scope: TenantScope, *, today: date | None = None) -> dict:
        day = today or datetime.now(BANGKOK).date()
        outstanding = Invoice.total - Invoice.paid_amount
        base = (
            select(func.coalesce(func.sum(outstanding), 0), func.count())
            .where(
                Invoice.license_id == scope.license_id,
                Invoice.archived_at.is_(None),
                Invoice.status.in_(OPEN_INVOICE_STATUSES),
            )
        )
        overdue_value, overdue_count = self._s.execute(
            base.where(Invoice.due_date.is_not(None), Invoice.due_date < day)).one()
        not_due_value, not_due_count = self._s.execute(
            base.where((Invoice.due_date.is_(None)) | (Invoice.due_date >= day))).one()
        rows = [
            _row("overdue", "เลยกำหนด", "Overdue", overdue_value, overdue_count),
            _row("not_due", "ยังไม่ถึงกำหนด", "Not yet due", not_due_value, not_due_count),
        ]
        total = Decimal(str(overdue_value or 0)) + Decimal(str(not_due_value or 0))
        return _envelope(
            "outstanding_invoices", "ยอดค้างชำระ", "Outstanding invoices", "money",
            _headline("ค้างชำระรวม", "Outstanding", total), rows,
            notes_th=["นับใบที่ออกแล้วและชำระบางส่วน · เลยกำหนดคิดจากวันครบกำหนดตามเวลาไทย"],
            notes_en=["Issued and partially paid bills; overdue is measured against Bangkok's today."],
        )

    # ---------------------------------------------------------------- 5
    def _satisfaction_avg(self, scope: TenantScope, *, today: date | None = None) -> dict:
        rows = []
        for range_key, th, en in (("this_month", "เดือนนี้", "This month"),
                                  ("last_month", "เดือนที่แล้ว", "Last month")):
            start, end = date_window(range_key, today=today)
            average, answered = self._s.execute(
                select(func.avg(SatisfactionSurvey.score), func.count())
                .where(
                    SatisfactionSurvey.license_id == scope.license_id,
                    SatisfactionSurvey.submitted_at.is_not(None),
                    SatisfactionSurvey.score.is_not(None),
                    SatisfactionSurvey.submitted_at >= start,
                    SatisfactionSurvey.submitted_at < end,
                )
            ).one()
            rows.append(_row(range_key, th, en,
                             round(float(average), 2) if average is not None else 0, answered))
        return _envelope(
            "satisfaction_avg", "คะแนนความพึงพอใจเฉลี่ย", "Average satisfaction", "score",
            _headline("เฉลี่ยเดือนนี้", "This month", rows[0]["value"]), rows,
            # A mean of two answers is not a verdict, and the reader should
            # be able to see that without opening the survey list.
            notes_th=[f"จากใบที่ตอบแล้ว {rows[0].get('count', 0)} ใบเดือนนี้ (เต็ม 3)"],
            notes_en=[f"From {rows[0].get('count', 0)} answered forms this month (out of 3)."],
        )
