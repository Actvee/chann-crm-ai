"""Phase 13.4/13.5 — the service report data snapshot.

Same contract as the quote snapshot (`snapshot.py`): everything the PDF
prints is frozen here first, so a `generated_documents` row can be
re-rendered byte-for-byte later even after the ticket's address, the
technician's name or the shop's phone number changed. Nothing in it is
produced by the AI layer.

Unlike a quote, a service report is not a legal-tender document: a shop
whose company profile is missing its tax ID can still hand a customer a
record of what was done. So the company block degrades to whatever is on
file instead of refusing — the refusal rule (10.6) exists to stop a
fabricated document, and a report with a blank tax-ID line is not that.
"""
from __future__ import annotations

from datetime import datetime, timezone

SNAPSHOT_VERSION = 1


def _name_of(profile: dict | None, fallback: str = "") -> str:
    profile = profile or {}
    name = " ".join(p for p in (profile.get("first_name"), profile.get("last_name")) if p)
    return name or fallback


def build_service_report_snapshot(
    *,
    report: dict,
    ticket: dict,
    company: dict,
    technician: dict | None = None,
    approvals: list[dict] | None = None,
    issued_at: datetime | None = None,
    photos: list[str] | None = None,
) -> dict:
    """The complete, frozen input to a service report render.

    approvals: [{"name", "role", "acted_at", "signature_url"}] for every
    approved step, in order — 13.5 puts the approver's signature on the
    paper, and the URL is resolved by the caller (a signed, short-lived
    link the renderer can fetch) rather than here.
    """
    stamped_at = issued_at or datetime.now(timezone.utc)
    data = dict(report.get("report_data") or {})
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "issued_at": stamped_at.isoformat(),
        "issued_on": stamped_at.date().isoformat(),
        "company": {
            "name": company.get("legal_name") or company.get("company_name") or "",
            "trading_name": company.get("company_name") or "",
            "tax_id": company.get("tax_id") or "",
            "address": company.get("company_address") or "",
            "phone": company.get("company_phone") or "",
            "email": company.get("company_email") or "",
        },
        "report": {
            "report_id": report.get("report_id") or "",
            "status": report.get("status") or "",
            "created_at": str(report.get("created_at") or ""),
            "found_issue": str(data.get("found_issue") or ""),
            "work_done": str(data.get("work_done") or ""),
            "parts_changed": str(data.get("parts_changed") or ""),
            "notes": str(data.get("notes") or ""),
        },
        "ticket": {
            "ticket_number": ticket.get("ticket_number") or "",
            "customer_name": ticket.get("customer_name") or "",
            "customer_phone": ticket.get("customer_phone") or "",
            "service_address": ticket.get("service_address") or "",
            "serial_number": ticket.get("serial_number") or "",
            "issue_description": ticket.get("issue_description") or "",
            "scheduled_date": str(ticket.get("scheduled_date") or ""),
            "scheduled_time": str(ticket.get("scheduled_time") or ""),
        },
        "technician": {
            # Either an already-resolved {"name", "phone"} or a profile row.
            "name": str((technician or {}).get("name") or "")
            or _name_of(technician, str((technician or {}).get("chann_uid") or "")),
            "phone": str((technician or {}).get("phone") or ""),
        },
        # 13.1: evidence from the visit — fetchable links the renderer
        # resolves during the render (signed by the caller).
        "photos": [str(u) for u in (photos or []) if u][:4],
        "approvals": [
            {
                "name": str(a.get("name") or ""),
                "role": str(a.get("role") or ""),
                "acted_at": str(a.get("acted_at") or ""),
                "signature_url": str(a.get("signature_url") or ""),
            }
            for a in (approvals or [])
        ],
    }
