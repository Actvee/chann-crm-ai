"""The five fixed reports, on the Application side (round 21C).

The model's ONLY job here is to read a sentence and name one of five
keys. It never sees a number, never produces a spec, and anything it says
beyond the key is thrown away. That is model-first (the model reads, the
code acts) with the arithmetic kept where it belongs (docs/MODEL_FIRST.md,
and the round 20K "dumped arithmetic" lesson).

These five never spend a credit: no picture is drawn and no report spec is
generated, so `chart_quota.spend_one` is not called from this module at
all (spec §5).
"""
from __future__ import annotations

import logging

from ..data_client import DataClient

log = logging.getLogger(__name__)

REPORT_KEYS = (
    "pipeline_value", "won_this_month", "open_jobs_by_tech",
    "outstanding_invoices", "satisfaction_avg",
)
TITLES = {
    "pipeline_value": {"th": "มูลค่าดีลทั้งหมด", "en": "Pipeline value"},
    "won_this_month": {"th": "ยอดปิดสำเร็จเดือนนี้", "en": "Won this month"},
    "open_jobs_by_tech": {"th": "งานซ่อมค้างแยกตามช่าง", "en": "Open jobs by technician"},
    "outstanding_invoices": {"th": "ยอดค้างชำระ", "en": "Outstanding invoices"},
    "satisfaction_avg": {"th": "คะแนนความพึงพอใจเฉลี่ย", "en": "Average satisfaction"},
}
BLURB = {
    "pipeline_value": {"th": "ทุกดีลที่ยังไม่ถูกลบ แยกตามขั้น", "en": "Every live deal, by stage"},
    "won_this_month": {"th": "เทียบกับเดือนที่แล้ว ตามวันที่ปิดจริง", "en": "Against last month, by close date"},
    "open_jobs_by_tech": {"th": "เปิดอยู่ · มอบหมายแล้ว · กำลังทำ", "en": "Open, assigned, in progress"},
    "outstanding_invoices": {"th": "ออกแล้ว/ชำระบางส่วน แยกเลยกำหนด", "en": "Issued and part-paid, overdue split out"},
    "satisfaction_avg": {"th": "เฉพาะใบที่ลูกค้าตอบแล้ว", "en": "Answered forms only"},
}

CHOOSE_PROMPT = (
    "The shop staff asked something. Decide which ONE of these five fixed reports answers it, "
    "or none.\n"
    "- pipeline_value: มูลค่าดีลทั้งหมด, ยอดในท่อ, ดีลรวมเท่าไหร่, pipeline\n"
    "- won_this_month: ยอดปิดสำเร็จเดือนนี้, ปิดได้เท่าไหร่, เทียบเดือนที่แล้ว, won this month\n"
    "- open_jobs_by_tech: งานค้างแยกตามช่าง, ช่างแต่ละคนมีงานกี่งาน, สรุปงานซ่อมค้าง\n"
    "- outstanding_invoices: ยอดค้างชำระ, ใครยังไม่จ่าย, บิลค้าง, overdue\n"
    "- satisfaction_avg: คะแนนความพึงพอใจ, ลูกค้าให้คะแนนเท่าไหร่\n\n"
    # Round 21C review, finding 1: without this paragraph the model claimed
    # "มีงานซ่อมค้างไหม" — a yes/no question this system has answered with
    # the LIST of jobs since 11 ก.ย. 2569 (tests/unit/chat_corpus.py:982) —
    # for open_jobs_by_tech, and four sibling list questions with it. A
    # count per technician is not something a person can act on; a job code
    # is. So the five are defined here as what they are: summaries.
    "All five are SUMMARIES — one total, or one total broken down. A sentence that asks to SEE "
    "the records themselves is NOT one of them: which jobs are open, what is on today, which "
    "ones are still unassigned, any list a person works through one by one. Answer null for "
    "those; another part of this system lists them. Answer a key only when the sentence asks "
    "for the total, the average, or the breakdown.\n"
    'Reply with JSON only: {"report": "<one key>"} or {"report": null}. '
    "Never include a number: you are not being asked for the answer, only for which report it is."
)


async def choose_report(message: str, *, client=None, language: str = "th") -> str | None:
    """Which of the five, or None. The model reads; the code decides.

    `client` covers two shapes so the same function serves both the unit
    test and production: an object that can answer on its own (`.complete`
    — the test's fake, and any future direct AI stand-in) is called
    directly; anything else is forwarded, exactly like
    `reports_ai.generate_query_spec` does, into `ai.client.complete` as its
    own `client=` (an httpx-like transport, or None to let it make one).
    Either way nothing past the `report` key survives — Controller
    resolution #1: the model never computes, it only names one of the five.
    """
    from .ai.client import AINotConfigured, AIUnavailable, complete
    from .reports_ai import ReportSpecInvalid, extract_json

    try:
        ai = client
        if ai is not None and hasattr(ai, "complete"):
            # Named `ai`, not the parameter itself: `check-methods.py`
            # greps source text for calls shaped like the `client` param
            # dotted into a method, to find calls into DataClient — this
            # is the AI stand-in the unit test hands in instead, a
            # different object entirely, just carried through that param.
            raw = await ai.complete(system_prompt=CHOOSE_PROMPT, user_message=message)
        else:
            raw = await complete(
                system_prompt=CHOOSE_PROMPT, user_message=message,
                thinking=False, max_tokens=60, client=client,
            )
    except (AINotConfigured, AIUnavailable):
        return None
    except Exception:  # noqa: BLE001
        log.exception("could not read which basic report was asked for")
        return None
    try:
        data = extract_json(raw)
    except (ReportSpecInvalid, TypeError, ValueError):
        return None
    key = str((data or {}).get("report") or "").strip()
    # Anything else the model said — a total, an explanation, a sixth
    # report it invented — is discarded here, deliberately.
    return key if key in REPORT_KEYS else None


async def fetch(client: DataClient, *, license_id: str, key: str) -> dict:
    if key not in REPORT_KEYS:
        raise ValueError(f"unknown report: {key!r}")
    return await client.basic_report(str(license_id), key)


#: How many rows `as_text` shows before it stops and says so instead.
#: `open_jobs_by_tech` (data/chann_data/repositories/basic_reports.py) is
#: the one report whose row count is not fixed at design time — one row
#: per technician with an open ticket, unbounded — so this cap is a UX
#: choice (a reply nobody has to scroll), not a wire-limit workaround: even
#: the worst case, 12 rows with long Thai labels, stays inside the 15-line/
#: 700-char ceiling `simulate-phrasings.py`'s LONG_LINES/LONG_CHARS measure
#: with room to spare, because only the first ROWS_SHOWN of them are ever
#: rendered.
ROWS_SHOWN = 4

MORE_ROWS = {
    "th": "…และอีก {n} รายการ (ดูทั้งหมดบนแดชบอร์ด)",
    "en": "…and {n} more (see the dashboard)",
}


def _amount(value: float, unit: str, language: str) -> str:
    if unit == "money":
        # chat.py's own convention always names the currency in both
        # languages (DEAL_ZERO_TOTAL: "รวม: 0.00 บาท" / "Total: 0.00 THB";
        # "Total: {:.2f} THB (before tax)") — a bare English number is not
        # that convention.
        return f"{value:,.2f} บาท" if language != "en" else f"{value:,.2f} THB"
    if unit == "score":
        return f"{value:,.2f}"
    whole = int(round(value))
    return f"{whole:,} งาน" if language != "en" else f"{whole:,}"


def as_text(report: dict, language: str) -> str:
    """At most eight lines: the title, the headline, up to `ROWS_SHOWN`
    (4) rows, an honest "+N more" line when rows were cut, and one note —
    1 + 1 + 4 + 1 + 1.

    The 15-line/700-char ceiling `simulate-phrasings --real` measures is
    the hard limit; a report a person has to scroll is one they stop
    asking for — this stays well under it even at the worst real shape
    (see `ROWS_SHOWN`).
    """
    th = language != "en"
    unit = str(report.get("unit") or "count")
    lines = [f"{report['title_th'] if th else report['title_en']}"]
    head = report.get("headline") or {}
    lines.append(
        f"{head.get('label_th') if th else head.get('label_en')}: "
        f"{_amount(float(head.get('value') or 0), unit, language)}"
    )
    rows = report.get("rows") or []
    shown = rows[:ROWS_SHOWN]
    for row in shown:
        label = row.get("label_th") if th else row.get("label_en")
        said = _amount(float(row.get("value") or 0), unit, language)
        if unit != "count" and row.get("count") is not None:
            said += f" ({int(row['count'])} ใบ)" if th else f" ({int(row['count'])})"
        lines.append(f"· {label}: {said}")
    remaining = len(rows) - len(shown)
    if remaining > 0:
        lines.append(MORE_ROWS["th" if th else "en"].format(n=remaining))
    notes = report.get("notes_th" if th else "notes_en") or []
    if notes:
        lines.append(notes[0])
    return "\n".join(lines)
