"""Phase 14-B — the approval executor and survey sender.

ONE domain service, called by chat and by the dashboard routes alike
(Master Spec 14.6 `test_approval_chat_vs_dashboard`): whichever surface a
CS person approves from, the same function runs the same Data Tier call,
sends the same notifications and, on the last step, the same survey.

The Data Tier already owns the rules (Phase 14-A): which step is current,
who may act on it, and that the report becomes `approved` and the survey
row is created in the same transaction as the last step. This module
adds what only the Application tier can do — find LINE accounts, push
messages, turn a policy sentence into a workflow — and never restates a
rule the Data Tier enforces.

Every LINE side effect is best-effort: a push that fails must not undo a
committed approval (the same rule `notify.py` follows).
"""
from __future__ import annotations

import logging

from ..data_client import DataClient, DataTierError
from ..line.client import LineReplyError, push_messages, quick_reply_item, text_message
from .notify import send_notification

log = logging.getLogger(__name__)

ENTITY_TYPE = "service_report"

# The default scale the Data Tier seeds; kept here only as the fallback
# when a survey row somehow carries none.
DEFAULT_SCALE = {"1": "ไม่ดี", "2": "พอใช้", "3": "ดีเยี่ยม"}

APPROVAL_REQUEST = {
    "th": "รายงานบริการ {report} ({ticket}{customer}) รอคุณตรวจ\nพิมพ์ \"อนุมัติ {report}\" หรือ \"ไม่อนุมัติ {report} <เหตุผล>\"",
    "en": "Service report {report} ({ticket}{customer}) is waiting for your review.\nReply \"approve {report}\" or \"reject {report} <reason>\".",
}
REJECTED_TO_SUBMITTER = {
    "th": "รายงาน {report} ถูกตีกลับ{reason}\nแก้แล้วปิดงานส่งใหม่ได้เลย",
    "en": "Report {report} was rejected{reason}. Fix it and check out again.",
}
SURVEY_PROMPT = {
    "th": "งาน {ticket} เสร็จเรียบร้อยแล้วครับ\nช่วยประเมินความพึงพอใจหน่อยครับ (กดเลือกได้เลย)",
    "en": "Job {ticket} is complete. How was it? (tap to answer)",
}


def _t(table: dict, language: str) -> str:
    return table.get(language) or table["th"]


# ----------------------------------------------------------------- actors

async def actor_of(client: DataClient, license_id: str, chann_uid: str) -> dict:
    """member_id + the role names the Data Tier should match steps against.

    An owner passes as `owner` AND `admin`: the default workflow falls
    back to the admin role when a ticket has no owner, and a shop whose
    owner does the approving would otherwise be told it is not their
    step.
    """
    context = await client.authorization_context(license_id, chann_uid) or {}
    roles = [r for r in (context.get("role"),) if r]
    if context.get("is_owner"):
        roles += [r for r in ("owner", "admin") if r not in roles]
    return {
        "member_id": str(context.get("member_id") or ""),
        "roles": roles,
        "chann_uid": chann_uid,
    }


def approvers_for(step: dict, members: list[dict]) -> list[dict]:
    """The members a pending step is waiting on."""
    ref = str(step.get("approver_ref") or "")
    if step.get("approver_type") == "user":
        return [m for m in members if str(m.get("id")) == ref]
    return [
        m for m in members
        if str(m.get("role") or "") == ref and str(m.get("status") or "active") == "active"
    ]


# ------------------------------------------------------------- submission

async def on_report_submitted(
    client: DataClient, *, license_id: str, report: dict, language: str = "th",
) -> list[dict]:
    """Check-out happened: open the steps and tell the first approver now.

    Owner decision 2 (PHASE14_PLAN): the request reaches the CS the second
    the technician closes the job, not on the next digest.
    """
    license_id = str(license_id)
    steps = await client.open_approval_steps(license_id, str(report["id"]))
    if not report.get("ticket_id"):
        # A caller that only has the id (the check-out reply, say) — the
        # full row names the ticket the request must mention.
        try:
            report = {**await _report_by_id(client, license_id, str(report["id"])), **report}
        except Exception:
            log.exception("could not load report %s", report.get("id"))
    await _notify_current_approvers(client, license_id, report, steps, language)
    return steps


async def _notify_current_approvers(
    client: DataClient, license_id: str, report: dict, steps: list[dict], language: str,
) -> None:
    pending = [s for s in steps if s.get("status") == "pending"]
    if not pending:
        return
    current = min(pending, key=lambda s: int(s.get("step_order") or 0))
    try:
        members = await client.list_members(license_id)
        ticket = await client.get_ticket(license_id, str(report.get("ticket_id") or ""))
    except Exception:
        log.exception("could not resolve approvers for %s", report.get("report_id"))
        return
    ticket = ticket or {}
    fields = dict(
        report=report.get("report_id") or "",
        ticket=ticket.get("ticket_number") or "",
        customer=f" · {ticket['customer_name']}" if ticket.get("customer_name") else "",
    )
    text = _t(APPROVAL_REQUEST, "th").format(**fields)
    text_en = _t(APPROVAL_REQUEST, "en").format(**fields)
    for member in approvers_for(current, members):
        uid = str(member.get("chann_uid") or "")
        if not uid:
            continue
        try:
            line_uid = await client.line_target_of(uid)
            await send_notification(
                client, license_id=license_id, target_chann_uid=uid,
                target_line_user_id=line_uid, type="approval_pending", message=text,
                message_en=text_en,
                entity_type=ENTITY_TYPE, entity_id=str(report["id"]),
                language=language, oa="sales",
            )
        except Exception:
            log.exception("approval notification to %s failed", uid)


# ------------------------------------------------------------------ acting

async def pending_for_actor(client: DataClient, *, license_id: str, chann_uid: str) -> list[dict]:
    actor = await actor_of(client, str(license_id), chann_uid)
    return await client.pending_approval_steps(
        str(license_id), member_id=actor["member_id"] or None, roles=actor["roles"],
    )


async def act(
    client: DataClient, *, license_id: str, step_id: str, approve: bool,
    actor_chann_uid: str, reason: str | None = None, language: str = "th",
) -> dict:
    """Approve or reject one step, then do what the outcome calls for.

    Returns the Data Tier's answer — {step, report_status, survey} — with
    `survey_sent` added so the caller can say whether the customer was
    asked. Raises DataTierError untouched (409 = not your step / already
    acted; 404 = not found), for the caller to phrase.
    """
    license_id = str(license_id)
    actor = await actor_of(client, license_id, actor_chann_uid)
    result = await client.act_on_approval_step(
        license_id, step_id, approve=approve,
        member_id=actor["member_id"] or None, roles=actor["roles"],
        reason=reason, actor_id=actor_chann_uid,
    )
    result = dict(result or {})
    result["survey_sent"] = False
    step = result.get("step") or {}
    report_id = str(step.get("entity_id") or "")

    try:
        report = await _report_by_id(client, license_id, report_id)
    except Exception:
        log.exception("could not load report %s after acting", report_id)
        report = {"id": report_id, "report_id": ""}

    status = result.get("report_status")
    result["document_url"] = None
    if status == "rejected":
        await _notify_submitter(client, license_id, report, reason, language)
    elif status == "approved":
        # 13.4/13.5: the report becomes paper now — with this approver's
        # signature line on it. Best effort: a render or storage failure
        # must not undo the approval; the PDF can be issued again from
        # "ออกรายงาน SR-…" or the reports page.
        result["document_url"] = await issue_report_document(
            client, license_id=license_id, report=report, actor_id=actor_chann_uid,
        )
        await _notify_submitter_of_document(client, license_id, report, result["document_url"], language)
        if result.get("survey"):
            result["survey_sent"] = await send_survey(
                client, license_id=license_id, survey=result["survey"], language=language,
            )
    elif status == "submitted":
        # More steps: the next approver hears about it now.
        try:
            steps = await client.approval_steps_for_entity(license_id, ENTITY_TYPE, report_id)
            await _notify_current_approvers(client, license_id, report, steps, language)
        except Exception:
            log.exception("could not notify the next approver for %s", report_id)
    return result


async def issue_report_document(
    client: DataClient, *, license_id: str, report: dict, actor_id: str | None,
) -> str | None:
    """The approved report's PDF link, issuing it if the report has none.
    Never raises: the approval already happened."""
    from .chat import document_download_url
    from .report_issue import ReportAlreadyIssued, issue_for_report

    report_id = str(report.get("id") or "")
    if not report_id:
        return None
    try:
        document = await issue_for_report(
            client, license_id=license_id, report_id=report_id, actor_id=actor_id,
        )
    except ReportAlreadyIssued:
        document_id = str(report.get("generated_document_id") or "")
        return document_download_url(license_id, document_id) if document_id else None
    except Exception:
        log.exception("service report PDF for %s could not be produced", report.get("report_id"))
        return None
    return document_download_url(license_id, str(document.get("id") or ""))


REPORT_APPROVED_TO_SUBMITTER = {
    "th": "รายงาน {report} ผ่านการอนุมัติแล้ว{link}",
    "en": "Report {report} has been approved{link}",
}


async def _notify_submitter_of_document(
    client: DataClient, license_id: str, report: dict, url: str | None, language: str,
) -> None:
    """The technician hears the outcome — and gets the paper."""
    member_id = str(report.get("technician_member_id") or "")
    if not member_id:
        return
    try:
        members = await client.list_members(license_id)
        member = next((m for m in members if str(m.get("id")) == member_id), None)
        if not member or not member.get("chann_uid"):
            return
        uid = str(member["chann_uid"])
        line_uid = await client.line_target_of(uid)
        link = f"\nPDF (7 วัน): {url}" if url else ""
        await send_notification(
            client, license_id=license_id, target_chann_uid=uid,
            target_line_user_id=line_uid, type="approval_pending",
            message=_t(REPORT_APPROVED_TO_SUBMITTER, "th").format(
                report=report.get("report_id") or "", link=link,
            ),
            message_en=_t(REPORT_APPROVED_TO_SUBMITTER, "en").format(
                report=report.get("report_id") or "", link=link,
            ),
            entity_type=ENTITY_TYPE, entity_id=str(report.get("id") or ""),
            language=language, oa="technician",
        )
    except Exception:
        log.exception("could not tell the technician the report was approved")


async def _report_by_id(client: DataClient, license_id: str, report_id: str) -> dict:
    rows = await client.list_service_reports(license_id)
    return next((r for r in rows if str(r.get("id")) == report_id), {"id": report_id})


async def _notify_submitter(
    client: DataClient, license_id: str, report: dict, reason: str | None, language: str,
) -> None:
    """on_reject = notify_submitter: the technician hears why, on their OA."""
    member_id = str(report.get("technician_member_id") or "")
    if not member_id:
        return
    try:
        members = await client.list_members(license_id)
        member = next((m for m in members if str(m.get("id")) == member_id), None)
        if not member or not member.get("chann_uid"):
            return
        uid = str(member["chann_uid"])
        line_uid = await client.line_target_of(uid)
        await send_notification(
            client, license_id=license_id, target_chann_uid=uid,
            target_line_user_id=line_uid, type="approval_rejected",
            message=_t(REJECTED_TO_SUBMITTER, "th").format(
                report=report.get("report_id") or "", reason=f": {reason}" if reason else "",
            ),
            message_en=_t(REJECTED_TO_SUBMITTER, "en").format(
                report=report.get("report_id") or "", reason=f": {reason}" if reason else "",
            ),
            entity_type=ENTITY_TYPE, entity_id=str(report.get("id") or ""),
            language=language, oa="technician",
        )
    except Exception:
        log.exception("could not tell the technician about a rejection")


# ----------------------------------------------------------------- survey

async def send_survey(
    client: DataClient, *, license_id: str, survey: dict, language: str = "th",
) -> bool:
    """Push the 1–3 quick-reply survey to the ticket's customer.

    Message actions, not postbacks: tapping sends the digit as text, which
    the existing text-only webhook already delivers to chat, where the
    customer branch records it. False when the customer has no LINE
    account on file (a walk-in ticket) — the survey row still exists.
    """
    license_id = str(license_id)
    try:
        ticket = await client.get_ticket(license_id, str(survey.get("ticket_id") or "")) or {}
        uid = str(ticket.get("customer_chann_uid") or "")
        line_uid = await client.line_target_of(uid) if uid else None
    except Exception:
        log.exception("could not find the customer for survey %s", survey.get("id"))
        return False
    if not line_uid:
        log.info("survey %s recorded but the customer has no LINE target", survey.get("id"))
        return False
    # The CUSTOMER's language, not the approver's (principle 7; review, 6 Sep 2026).
    try:
        prefs = await client.get_display_preferences(uid) or {}
        language = str(prefs.get("language") or language or "th")
    except Exception:  # noqa: BLE001
        pass

    scale = survey.get("scale_config_json") or DEFAULT_SCALE
    items = [
        quick_reply_item(f"{score} {label}", str(score))
        for score, label in sorted(scale.items(), key=lambda kv: str(kv[0]))
    ]
    message = text_message(
        _t(SURVEY_PROMPT, language).format(ticket=ticket.get("ticket_number") or ""),
        quick_reply=items,
    )
    try:
        await push_messages("customer", line_uid, [message])
    except LineReplyError as exc:
        log.error("survey push failed for %s: %s", survey.get("id"), exc)
        return False
    try:
        await client.mark_survey_sent(license_id, str(survey["id"]))
    except Exception:
        log.exception("could not mark survey %s as sent", survey.get("id"))
    return True


async def pending_survey_for_customer(
    client: DataClient, *, license_id: str, customer_chann_uid: str,
) -> tuple[dict | None, dict | None]:
    """(survey, ticket) the customer still owes an answer for, newest first."""
    license_id = str(license_id)
    tickets = await client.list_tickets(license_id)
    mine = [
        t for t in tickets
        if str(t.get("customer_chann_uid") or "") == customer_chann_uid
        and str(t.get("status") or "") == "completed"
    ]
    mine.sort(key=lambda t: str(t.get("updated_at") or t.get("created_at") or ""), reverse=True)
    for ticket in mine[:5]:
        survey = await client.pending_survey_for_ticket(license_id, str(ticket["id"]))
        if survey:
            return survey, ticket
    return None, None


async def answer_survey(
    client: DataClient, *, license_id: str, survey_id: str, score: int,
    comment: str | None, actor_chann_uid: str,
) -> dict:
    return await client.answer_survey(
        str(license_id), survey_id, score=score, comment=comment, actor_id=actor_chann_uid,
    )


# --------------------------------------------------------------- workflow

STEP_LABELS = {
    "ticket_owner": {"th": "CS เจ้าของงาน", "en": "the CS who owns the ticket"},
}


def describe_workflow(rules_json: dict, language: str = "th") -> str:
    """The flow in words, generated from the structure (never from the
    model's own summary — the person is confirming what will run)."""
    steps = sorted(rules_json.get("steps") or [], key=lambda s: int(s.get("order") or 0))
    if not steps:
        return "ไม่มีขั้นอนุมัติ" if language == "th" else "No approval steps."
    lines = []
    for step in steps:
        ref = str(step.get("approver_ref") or "")
        if step.get("approver_type") == "user":
            who = STEP_LABELS.get(ref, {}).get(language) or STEP_LABELS.get(ref, {}).get("th") or ref
        else:
            who = (f"บทบาท {ref}" if language == "th" else f"role {ref}")
        lines.append((f"ขั้น {step.get('order')}: {who}") if language == "th" else f"Step {step.get('order')}: {who}")
    tail = (
        "อนุมัติครบทุกขั้น → รายงานเป็น 'อนุมัติแล้ว' และส่งแบบประเมินให้ลูกค้าทันที\nไม่อนุมัติขั้นใด → แจ้งช่างให้แก้"
        if language == "th"
        else "All steps approved → report approved and the customer survey is sent.\nAny rejection → the technician is told."
    )
    return "\n".join(lines) + "\n" + tail


async def replace_workflow(
    client: DataClient, *, license_id: str, rules_json: dict, actor_chann_uid: str,
    entity_type: str = ENTITY_TYPE,
) -> dict:
    actor = await actor_of(client, str(license_id), actor_chann_uid)
    return await client.replace_approval_workflow(
        str(license_id), entity_type, rules_json,
        updated_by=actor["member_id"] or None, actor_id=actor_chann_uid,
    )


async def current_workflow(client: DataClient, *, license_id: str, entity_type: str = ENTITY_TYPE) -> dict:
    return await client.get_approval_workflow(str(license_id), entity_type)


__all__ = [
    "DataTierError", "ENTITY_TYPE", "act", "actor_of", "answer_survey", "approvers_for",
    "current_workflow", "describe_workflow", "on_report_submitted", "pending_for_actor",
    "pending_survey_for_customer", "replace_workflow", "send_survey",
]
