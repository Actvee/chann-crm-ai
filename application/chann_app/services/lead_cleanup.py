"""Inactive-lead cleanup (user review, 4 Sep 2026).

Off unless a tenant turns it on: `lead_auto_archive_days` in
license_settings (0 or absent = never). When on, the daily sweep archives
leads — stage "lead", never promoted — whose last activity is older than
that many days. "Activity" is the newest of the record's own update, its
deals, its tickets, its notes and its follow-ups; the Data tier computes
it in one query so the definition lives in one place.

Archiving is the platform's existing soft delete: the row stays, it
leaves every list, and the audit trail says the sweep did it.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient

log = logging.getLogger(__name__)

SETTING_KEY = "lead_auto_archive_days"
MAX_DAYS = 3650


def _days_from(rows: list[dict]) -> int:
    for row in rows or []:
        if row.get("setting_key") == SETTING_KEY:
            try:
                days = int(str(row.get("setting_value")).strip())
            except (TypeError, ValueError):
                return 0
            return days if 0 < days <= MAX_DAYS else 0
    return 0


async def lead_auto_archive_days(client: DataClient, license_id: str) -> int:
    try:
        rows = await client.list_license_settings(str(license_id))
    except Exception:
        log.exception("could not read the lead cleanup setting for %s", license_id)
        return 0
    return _days_from(rows)


async def sweep_inactive_leads(client: DataClient) -> dict:
    """Run once a day, from the platform sweep. Per tenant, per setting."""
    summary = {"tenants": 0, "enabled": 0, "archived": 0, "failed": 0}
    try:
        licenses = await client.list_licenses(exclude_status="suspended")
    except Exception:
        log.exception("lead cleanup could not list tenants")
        return summary
    for license_row in licenses:
        license_id = str(license_row["id"])
        summary["tenants"] += 1
        days = await lead_auto_archive_days(client, license_id)
        if days <= 0:
            continue
        summary["enabled"] += 1
        try:
            archived = await client.archive_inactive_leads(license_id, days, actor_id="lead_cleanup")
        except Exception:
            log.exception("lead cleanup failed for %s", license_id)
            summary["failed"] += 1
            continue
        summary["archived"] += len(archived or [])
    return summary
