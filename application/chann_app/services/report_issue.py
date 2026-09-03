"""Phase 13.4/13.5 — the Service Report PDF, as a recorded document.

The same discipline as `quote_issue.py`, and the same engine: a frozen
snapshot, the shop's own published `service_report` template or the
built-in one, SmartBrowz to render, object storage first, the
`generated_documents` row second, and the report linked back last.

When it is produced: at final approval (Phase 14), because 13.5 puts the
approver's signature on it and there is no approver before then. A
report that is approved but somehow has no document (the render failed
that day, the storage was down) can be issued again on demand —
"ออกรายงาน SR-…" in chat, the PDF button on the reports page — through
`issue_for_report`, which is also what the approval hook calls.

Per the owner (3 Sep): the warranty certificate PDF is not built; the
service report PDF is required.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..data_client import DataClient
from .documents.fill import fill_template
from .documents.report_html import render_service_report_html
from .documents.report_snapshot import build_service_report_snapshot
from .pdf.base import PdfOptions, get_renderer
from .storage.base import get_document_store, sha256_hex

log = logging.getLogger(__name__)

DOCUMENT_TYPE = "service_report"
BUILTIN_REPORT_TEMPLATE_CODE = "BUILTIN-SERVICE-REPORT"
BUILTIN_REPORT_TEMPLATE_NAME = "รายงานการซ่อม (แบบมาตรฐานของระบบ)"
BUILTIN_REPORT_TEMPLATE_VERSION = 1
# The renderer fetches a signature image while it renders; an hour is far
# longer than a render takes and far shorter than a link worth leaking.
SIGNATURE_LINK_TTL_SECONDS = 3600

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_-]+")


class ReportNotApproved(RuntimeError):
    """The document carries the approver's signature (13.5); until the
    report is approved there is nobody to sign it."""


class ReportAlreadyIssued(RuntimeError):
    """This report already has a generated document; a second one has to
    be asked for explicitly (same reasoning as QuoteAlreadyIssued)."""


def report_document_key(*, license_id: str, report_code: str, issued_at: datetime, sha256: str) -> str:
    safe_code = _SAFE_KEY.sub("-", report_code or "report")
    return (
        f"documents/{license_id}/service-reports/"
        f"{issued_at:%Y/%m}/{safe_code}-{sha256[:12]}.pdf"
    )


async def _resolve_template(
    client: DataClient, license_id: str, snapshot: dict, *, actor_id: str | None = None,
) -> tuple[str, str]:
    """(template_version_id, html): the shop's published service_report
    template if it has one, the built-in otherwise — the quote's rule."""
    try:
        templates = await client.list_document_templates(license_id, document_type=DOCUMENT_TYPE)
    except Exception:
        log.exception("could not read report templates; falling back to the built-in")
        templates = []

    for template in templates:
        if template.get("template_code") == BUILTIN_REPORT_TEMPLATE_CODE:
            continue
        if not template.get("is_active", True):
            continue
        try:
            versions = await client.list_document_template_versions(license_id, str(template["id"]))
        except Exception:
            log.exception("could not read versions for template %s", template.get("id"))
            continue
        published = [v for v in versions if v.get("status") == "published"]
        if not published:
            continue
        newest = max(published, key=lambda v: int(v.get("version") or 0))
        compiled = str(newest.get("compiled_template_path") or "")
        if not compiled or compiled.startswith("builtin://"):
            continue
        try:
            raw = await get_document_store().get(path=compiled)
            return str(newest["id"]), fill_template(raw.decode("utf-8"), snapshot)
        except Exception:
            log.exception("tenant report template %s could not be used; using the built-in", newest.get("id"))
            break

    version_id = await _ensure_builtin_template_version(client, license_id, actor_id=actor_id)
    return version_id, render_service_report_html(snapshot)


async def _ensure_builtin_template_version(
    client: DataClient, license_id: str, *, actor_id: str | None = None,
) -> str:
    templates = await client.list_document_templates(license_id, document_type=DOCUMENT_TYPE)
    template = next(
        (t for t in templates if t.get("template_code") == BUILTIN_REPORT_TEMPLATE_CODE), None
    )
    if template is None:
        template = await client.create_document_template(
            license_id,
            {
                "document_type": DOCUMENT_TYPE,
                "template_code": BUILTIN_REPORT_TEMPLATE_CODE,
                "template_name": BUILTIN_REPORT_TEMPLATE_NAME,
            },
            actor_id=actor_id,
        )
    template_id = str(template["id"])
    versions = await client.list_document_template_versions(license_id, template_id)
    existing = next(
        (v for v in versions if v.get("version") == BUILTIN_REPORT_TEMPLATE_VERSION), None
    )
    if existing is not None:
        return str(existing["id"])
    version = await client.create_document_template_version(
        license_id,
        template_id,
        {
            "source_docx_path": "builtin://none",
            "intermediate_model": {
                "kind": "builtin",
                "module": "chann_app.services.documents.report_html:render_service_report_html",
                "version": BUILTIN_REPORT_TEMPLATE_VERSION,
            },
            "mapping_schema": {"kind": "builtin"},
            "compiled_template_path": f"builtin://service_report/v{BUILTIN_REPORT_TEMPLATE_VERSION}",
        },
        actor_id=actor_id,
    )
    version_id = str(version["id"])
    await client.publish_document_template_version(license_id, version_id, actor_id=actor_id)
    return version_id


async def issue_service_report_document(
    client: DataClient, *, license_id: str, report: dict, ticket: dict, company: dict,
    technician: dict | None = None, approvals: list[dict] | None = None,
    actor_id: str | None = None, allow_reissue: bool = False,
) -> dict:
    """Render, store, record, link. Returns the generated_documents row.

    Raises ReportNotApproved / ReportAlreadyIssued for the caller to phrase,
    and lets provider and storage errors through untouched — a document
    that could not be produced must never look like one that was.
    """
    if str(report.get("status") or "") != "approved":
        raise ReportNotApproved(f"report {report.get('report_id')} is {report.get('status')}, not approved")
    if report.get("generated_document_id") and not allow_reissue:
        raise ReportAlreadyIssued(f"report {report.get('report_id')} already has an issued document")

    issued_at = datetime.now(timezone.utc)
    snapshot = build_service_report_snapshot(
        report=report, ticket=ticket, company=company, technician=technician,
        approvals=approvals, issued_at=issued_at,
    )
    template_version_id, html = await _resolve_template(client, license_id, snapshot, actor_id=actor_id)

    renderer = get_renderer("smartbrowz")
    result = await renderer.render(
        html, PdfOptions(), idempotency_key=f"service_report:{license_id}:{report.get('id')}",
    )
    if not result.content:
        raise RuntimeError("renderer returned no document content")

    digest = sha256_hex(result.content)
    key = report_document_key(
        license_id=license_id, report_code=str(report.get("report_id") or ""),
        issued_at=issued_at, sha256=digest,
    )
    store = get_document_store()
    stored = await store.put(key=key, content=result.content, content_type="application/pdf")

    document = await client.record_generated_document(
        license_id,
        {
            "document_type": DOCUMENT_TYPE,
            "source_entity_type": "service_report",
            "source_entity_id": str(report["id"]),
            "template_version_id": template_version_id,
            "data_snapshot": snapshot,
            "output_path": stored.path,
            "sha256": stored.sha256,
            "renderer": result.renderer,
        },
        actor_id=actor_id,
    )
    try:
        await client.attach_report_document(
            license_id, str(report["id"]), document_id=str(document["id"]),
            pdf_path=stored.path, actor_id=actor_id,
        )
    except Exception:
        log.exception(
            "document %s was issued for report %s but linking it back failed",
            document.get("id"), report.get("report_id"),
        )
    return document


async def issue_for_report(
    client: DataClient, *, license_id: str, report_id: str, actor_id: str | None = None,
    allow_reissue: bool = False,
) -> dict:
    """Everything `issue_service_report_document` needs, gathered from the
    Data Tier by report id — the one entry point chat, the approval hook
    and the reports page all use."""
    license_id = str(license_id)
    rows = await client.list_service_reports(license_id)
    report = next((r for r in rows if str(r.get("id")) == str(report_id)), None)
    if report is None:
        raise LookupError(f"report {report_id} not found")

    ticket = await client.get_ticket(license_id, str(report.get("ticket_id") or "")) or {}
    company = await client.get_company_profile(license_id) or {}

    members = await client.list_members(license_id)
    by_id = {str(m.get("id")): m for m in members}
    technician = None
    tech_member = by_id.get(str(report.get("technician_member_id") or ""))
    if tech_member:
        technician = await _person(client, tech_member)

    approvals: list[dict] = []
    try:
        steps = await client.approval_steps_for_entity(license_id, "service_report", str(report["id"]))
    except Exception:
        log.exception("could not read approval steps for %s", report.get("report_id"))
        steps = []
    for step in sorted(steps, key=lambda s: int(s.get("step_order") or 0)):
        if str(step.get("status") or "") != "approved":
            continue
        member = by_id.get(str(step.get("acted_by_member_id") or ""))
        person = await _person(client, member) if member else {}
        approvals.append({
            "name": person.get("name") or str(step.get("acted_by_member_id") or ""),
            "role": str((member or {}).get("role") or step.get("approver_ref") or ""),
            "acted_at": str(step.get("acted_at") or "")[:16].replace("T", " "),
            "signature_url": person.get("signature_url") or "",
        })

    return await issue_service_report_document(
        client, license_id=license_id, report=report, ticket=ticket, company=company,
        technician=technician, approvals=approvals, actor_id=actor_id,
        allow_reissue=allow_reissue,
    )


async def _person(client: DataClient, member: dict) -> dict:
    """Name, phone and (13.5) a renderer-fetchable signature link for one
    member. A signature is stored as an object path; the renderer needs a
    URL it can fetch during the render, so it is signed for an hour."""
    chann_uid = str(member.get("chann_uid") or "")
    try:
        profile = await client.get_profile(chann_uid) or {}
    except Exception:
        profile = {}
    name = " ".join(p for p in (profile.get("first_name"), profile.get("last_name")) if p)
    signature_url = ""
    try:
        path = await client.identity_signature(chann_uid)
        if path:
            signature_url = (
                path if path.startswith("http")
                else await get_document_store().signed_url(
                    path=path, expires_seconds=SIGNATURE_LINK_TTL_SECONDS,
                )
            )
    except Exception:
        log.warning("no usable signature for %s", chann_uid)
    return {
        "chann_uid": chann_uid, "name": name or chann_uid,
        "phone": profile.get("phone") or "", "signature_url": signature_url,
    }
