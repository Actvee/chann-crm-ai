"""The platform's calendar day.

Every tenant is a Thai shop and every scheduled job runs on Asia/Bangkok
time (infrastructure/terraform/scheduler.tf). A "today" taken from the
UTC clock is yesterday's date for the first seven hours of a Bangkok
day — which is exactly when the 00:30 sweeps run — so a quote valid
until yesterday stayed "sent" one more day and a warranty that ended
yesterday still read "active" (review E4, 6 Sep 2026).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")


def bangkok_today() -> date:
    return datetime.now(BANGKOK).date()


def bangkok_date(moment: datetime | None) -> date | None:
    """The Bangkok calendar date of a timestamp (a naive one is taken as UTC)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        from datetime import timezone

        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BANGKOK).date()
