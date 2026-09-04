"""Phase 17 — ad-hoc reports asked for in plain language.

The model's only job is to turn "ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด" into a
small JSON spec. Everything after that is code: the whitelist decides
whether the spec is allowed, the Data tier builds one parameterised,
tenant-filtered statement, and this module writes the summary, the
table page and the files. The model never sees SQL and never sees data.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from ..data_client import DataClient, DataTierError
from .ai.client import AINotConfigured, AIUnavailable, complete
from .storage.base import DocumentStoreNotConfigured, get_document_store

log = logging.getLogger(__name__)

FILE_LINK_SECONDS = 7 * 24 * 3600

# The same whitelist the Data tier enforces (chann_data.repositories.phase17).
# Kept here too so a bad spec is refused before a network call — and so the
# prompt below is generated from one source of truth.
ALLOWED_ENTITIES: dict[str, dict] = {
    "deals": {"fields": ("stage", "owner_member_id", "created_at", "expected_close_date"),
              "enums": {"stage": ("new", "proposed", "won", "lost")}, "date_fields": ("created_at", "expected_close_date")},
    "customers": {"fields": ("stage", "owner_member_id", "created_at"), "enums": {}, "date_fields": ("created_at",)},
    "tickets": {"fields": ("status", "assigned_to", "created_at", "scheduled_date"),
                "enums": {"status": ("open", "assigned", "in_progress", "completed", "cancelled")},
                "date_fields": ("created_at", "scheduled_date")},
    "quotes": {"fields": ("status", "owner_member_id", "created_at", "valid_until"),
               "enums": {"status": ("draft", "sent", "accepted", "rejected", "expired")},
               "date_fields": ("created_at", "valid_until")},
    "warranties": {"fields": ("status", "product_id", "warranty_end", "created_at"),
                   "enums": {"status": ("active", "expired", "void")}, "date_fields": ("created_at", "warranty_end")},
}
NUMERIC_FIELDS = {"deals": ("amount",), "quotes": ("discount_amount",)}
ALLOWED_METRICS = ("count", "sum", "avg", "min", "max")
ALLOWED_GROUP_BY = ("owner_member_id", "stage", "status", "product_id", "assigned_to")
ALLOWED_DATE_RANGES = ("today", "yesterday", "last_7_days", "last_30_days", "this_month", "last_month", "last_3_months", "this_year", "last_year")
ID_FIELDS = ("owner_member_id", "product_id", "assigned_to")
_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

ENTITY_LABEL = {
    "deals": {"th": "ดีล", "en": "deals"}, "customers": {"th": "ลูกค้า", "en": "customers"},
    "tickets": {"th": "งานซ่อม", "en": "tickets"}, "quotes": {"th": "ใบเสนอราคา", "en": "quotes"},
    "warranties": {"th": "การรับประกัน", "en": "warranties"},
}
RANGE_LABEL = {
    "today": {"th": "วันนี้", "en": "today"}, "yesterday": {"th": "เมื่อวาน", "en": "yesterday"},
    "last_7_days": {"th": "7 วันล่าสุด", "en": "last 7 days"}, "last_30_days": {"th": "30 วันล่าสุด", "en": "last 30 days"},
    "this_month": {"th": "เดือนนี้", "en": "this month"}, "last_month": {"th": "เดือนที่แล้ว", "en": "last month"},
    "last_3_months": {"th": "3 เดือนล่าสุด", "en": "last 3 months"}, "this_year": {"th": "ปีนี้", "en": "this year"},
    "last_year": {"th": "ปีที่แล้ว", "en": "last year"},
}
GROUP_LABEL = {
    "owner_member_id": {"th": "ผู้ดูแล", "en": "owner"}, "assigned_to": {"th": "ช่าง", "en": "technician"},
    "stage": {"th": "สถานะ", "en": "stage"}, "status": {"th": "สถานะ", "en": "status"},
    "product_id": {"th": "สินค้า", "en": "product"},
}
METRIC_LABEL = {
    "count": {"th": "จำนวน", "en": "count"}, "sum": {"th": "ผลรวม", "en": "total"}, "avg": {"th": "ค่าเฉลี่ย", "en": "average"},
    "min": {"th": "ต่ำสุด", "en": "minimum"}, "max": {"th": "สูงสุด", "en": "maximum"},
}
NO_DATA = {"th": "ไม่มีข้อมูลในช่วงที่ขอ", "en": "No data in that range"}
INVALID = {
    "th": "สร้างรายงานนี้ไม่ได้ครับ ({reason}) ลองถามแบบนี้: \"ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด\" หรือ \"สรุปงานค้างแยกตามช่าง\"",
    "en": "I can't build that report ({reason}). Try: \"won deals in the last 3 months\" or \"open tickets by technician\".",
}
FILES_LINE = {"th": "ไฟล์ (ใช้ได้ 7 วัน): CSV {csv} · หน้าเว็บ {html}", "en": "Files (valid 7 days): CSV {csv} · web page {html}"}
PDF_LINE = {"th": " · PDF {pdf}", "en": " · PDF {pdf}"}


class ReportSpecInvalid(ValueError):
    pass


def _t(table: dict, language: str) -> str:
    return table.get(language) or table["th"]


# ----------------------------------------------------------------- validate

def validate_query_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ReportSpecInvalid("spec must be an object")
    entity = spec.get("entity")
    if entity not in ALLOWED_ENTITIES:
        raise ReportSpecInvalid(f"entity '{entity}' is not allowed")
    table = ALLOWED_ENTITIES[entity]
    metric = spec.get("metric") or "count"
    if metric not in ALLOWED_METRICS:
        raise ReportSpecInvalid(f"metric '{metric}' is not allowed")
    field = spec.get("field")
    if metric != "count":
        if field not in NUMERIC_FIELDS.get(entity, ()):
            raise ReportSpecInvalid(f"{metric} needs a numeric field of {entity}")
    else:
        field = None
    filters_in = spec.get("filter") or spec.get("filters") or {}
    if not isinstance(filters_in, dict):
        raise ReportSpecInvalid("filter must be an object")
    filters: dict[str, str] = {}
    for key, value in filters_in.items():
        if key not in table["fields"] or key in table["date_fields"]:
            raise ReportSpecInvalid(f"field '{key}' cannot be filtered on {entity}")
        value = str(value).strip()
        if key in table["enums"]:
            if value not in table["enums"][key]:
                raise ReportSpecInvalid(f"'{value}' is not a valid {key}")
        elif key in ID_FIELDS:
            try:
                value = str(uuid.UUID(value))
            except ValueError:
                raise ReportSpecInvalid(f"{key} must be an id")
        elif not _VALUE_RE.match(value):
            raise ReportSpecInvalid(f"'{value}' is not an acceptable value for {key}")
        filters[key] = value
    group_by = spec.get("group_by") or spec.get("groupBy")
    if group_by is not None and (group_by not in ALLOWED_GROUP_BY or group_by not in table["fields"]):
        raise ReportSpecInvalid(f"cannot group {entity} by '{group_by}'")
    date_range = spec.get("date_range") or spec.get("dateRange")
    if date_range is not None and date_range not in ALLOWED_DATE_RANGES:
        raise ReportSpecInvalid(f"date range '{date_range}' is not allowed")
    date_field = spec.get("date_field") or spec.get("dateField") or table["date_fields"][0]
    if date_field not in table["date_fields"]:
        raise ReportSpecInvalid(f"'{date_field}' is not a date field of {entity}")
    return {"entity": entity, "metric": metric, "field": field, "filter": filters,
            "group_by": group_by, "date_range": date_range, "date_field": date_field}


# ----------------------------------------------------------------- the model

def _whitelist_text() -> str:
    lines = []
    for entity, table in ALLOWED_ENTITIES.items():
        enums = "; ".join(f"{k} in {list(v)}" for k, v in table["enums"].items())
        lines.append(f"- {entity}: fields {list(table['fields'])}" + (f" ({enums})" if enums else "")
                     + f"; date fields {list(table['date_fields'])}"
                     + (f"; numeric {list(NUMERIC_FIELDS[entity])}" if entity in NUMERIC_FIELDS else ""))
    return "\n".join(lines)


def build_system_prompt() -> str:
    return (
        "You turn a shop staff member's request (Thai or English) into ONE report query spec as JSON. "
        "You never write SQL and never invent fields.\n\n"
        "Allowed entities and fields:\n" + _whitelist_text() + "\n"
        f"metric: one of {list(ALLOWED_METRICS)} (count unless a numeric field is named)\n"
        f"group_by: one of {list(ALLOWED_GROUP_BY)} that exists on the entity, or null\n"
        f"date_range: one of {list(ALLOWED_DATE_RANGES)} or null\n"
        "date_field: which date field the range applies to (default created_at)\n\n"
        "Thai hints: ดีล/ยอดขาย = deals; ยอดขายรวม/มูลค่ารวม = metric sum of field amount on deals; ปิดสำเร็จ/ชนะ = stage won; แพ้ = lost; ลูกค้า = customers; "
        "งาน/ใบงาน/ticket = tickets; งานค้าง = status open or assigned or in_progress (pick 'open' if they say ค้าง and no other clue, "
        "or omit the status filter and group by status); ช่าง = assigned_to; ใบเสนอราคา = quotes; รับประกัน = warranties; "
        "เดือนนี้ = this_month; 3 เดือน = last_3_months; ปีนี้ = this_year; แยกตาม = group_by.\n\n"
        "Reply with JSON only, no prose:\n"
        '{"entity": "...", "metric": "count", "field": null, "filter": {}, "group_by": null, "date_range": null, "date_field": null}\n'
        'If the request is not a report about these entities, or is too vague to pick an entity, reply {"clarify": "<one short question in the user\'s language>"}.'
    )


def extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.split("```", 1)[0].strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ReportSpecInvalid("the model did not return JSON")
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportSpecInvalid(f"the model returned unreadable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportSpecInvalid("the model did not return an object")
    return data


async def generate_query_spec(message: str, *, language: str = "th", client=None) -> dict:
    """The model's JSON, parsed but NOT yet validated — the caller decides
    between a spec, a clarifying question, and a refusal."""
    prompt = build_system_prompt()
    user = f"Language: {language}\nRequest: {message.strip()}"
    try:
        raw = await complete(system_prompt=prompt, user_message=user, thinking=True, max_tokens=600, client=client)
    except AINotConfigured:
        # No reasoning model configured: the chat model does the same job
        # with the same whitelist; nothing downstream trusts it more.
        raw = await complete(system_prompt=prompt, user_message=user, thinking=False, max_tokens=600, client=client)
    return extract_json(raw)


# ----------------------------------------------------------------- outputs

def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{int(value):,}" if isinstance(value, int) else str(value)


def describe(spec: dict, language: str) -> str:
    entity = _t(ENTITY_LABEL[spec["entity"]], language)
    metric = _t(METRIC_LABEL[spec["metric"]], language)
    parts = [f"{metric}{entity}" if language != "en" else f"{metric} of {entity}"]
    for key, value in (spec.get("filter") or {}).items():
        parts.append(f"{key}={value}")
    if spec.get("date_range"):
        parts.append(_t(RANGE_LABEL[spec["date_range"]], language))
    if spec.get("group_by"):
        parts.append(("แยกตาม" if language != "en" else "by ") + _t(GROUP_LABEL[spec["group_by"]], language))
    return " · ".join(parts)


def report_text(spec: dict, result: dict, language: str) -> str:
    head = describe(spec, language)
    rows = result.get("rows") or []
    if spec.get("group_by"):
        if not rows:
            return f"{head}\n{_t(NO_DATA, language)}"
        lines = [f"{head}"]
        for row in rows[:15]:
            lines.append(f"• {row['label']}: {_fmt(row['value'])}")
        if len(rows) > 15:
            lines.append(f"… +{len(rows) - 15}")
        if result.get("total") is not None:
            lines.append(("รวม " if language != "en" else "Total ") + _fmt(result["total"]))
        return "\n".join(lines)
    total = result.get("total")
    return f"{head}\n{('รวม ' if language != 'en' else 'Total ')}{_fmt(total if total is not None else 0)}"


def report_csv(spec: dict, result: dict, language: str) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if spec.get("group_by"):
        writer.writerow([_t(GROUP_LABEL[spec["group_by"]], language), _t(METRIC_LABEL[spec["metric"]], language)])
        for row in result.get("rows") or []:
            writer.writerow([row["label"], row["value"]])
        if result.get("total") is not None:
            writer.writerow(["รวม" if language != "en" else "Total", result["total"]])
    else:
        writer.writerow([_t(METRIC_LABEL[spec["metric"]], language)])
        writer.writerow([result.get("total") if result.get("total") is not None else 0])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def report_html(spec: dict, result: dict, language: str, *, company_name: str = "") -> str:
    e = html.escape
    rows = result.get("rows") or []
    peak = max([r["value"] for r in rows] + [0]) or 1
    body = []
    if spec.get("group_by"):
        body.append("<table><thead><tr><th>%s</th><th class=\"num\">%s</th><th></th></tr></thead><tbody>" % (
            e(_t(GROUP_LABEL[spec["group_by"]], language)), e(_t(METRIC_LABEL[spec["metric"]], language))))
        for row in rows:
            width = max(2, int(100 * float(row["value"]) / float(peak)))
            body.append(f"<tr><td>{e(str(row['label']))}</td><td class=\"num\">{e(_fmt(row['value']))}</td>"
                        f"<td class=\"bar\"><span style=\"width:{width}%\"></span></td></tr>")
        if result.get("total") is not None:
            body.append(f"<tr class=\"total\"><td>{'รวม' if language != 'en' else 'Total'}</td><td class=\"num\">{e(_fmt(result['total']))}</td><td></td></tr>")
        body.append("</tbody></table>")
        if not rows:
            body.append(f"<p class=\"empty\">{e(_t(NO_DATA, language))}</p>")
    else:
        body.append(f"<p class=\"big\">{e(_fmt(result.get('total') if result.get('total') is not None else 0))}</p>")
    generated = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
    return (
        "<!doctype html><html lang=\"th\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{e(describe(spec, language))}</title>"
        "<style>body{font-family:'IBM Plex Sans Thai','Noto Sans Thai',system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px;color:#1a2030;line-height:1.55}"
        "h1{font-size:20px;margin:0 0 4px}.meta{color:#5a6478;font-size:13px;margin:0 0 18px}"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #e5e0d8;padding:8px 10px;text-align:left;vertical-align:middle}"
        "th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#8b93a3}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}"
        ".bar{width:40%}.bar span{display:block;height:10px;border-radius:5px;background:#178a50}.total td{font-weight:600;border-top:2px solid #cfc8bc}"
        ".big{font-size:44px;font-weight:600;margin:12px 0}.empty{color:#8b93a3}@media print{body{padding:0}}</style></head><body>"
        f"<h1>{e(describe(spec, language))}</h1>"
        f"<p class=\"meta\">{e(company_name)}{' · ' if company_name else ''}{e(generated[:16].replace('T', ' '))} UTC</p>"
        + "".join(body) + "</body></html>"
    )


async def publish_files(spec: dict, result: dict, language: str, *, license_id: str, company_name: str = "") -> dict:
    """CSV and a printable page in the document store (signed, 7 days);
    a PDF as well when the renderer is configured. Missing storage is
    not an error: the text answer stands on its own."""
    files: dict[str, str | None] = {"csv": None, "html": None, "pdf": None}
    try:
        store = get_document_store()
    except DocumentStoreNotConfigured:
        return files
    stamp = uuid.uuid4().hex
    page = report_html(spec, result, language, company_name=company_name)
    try:
        stored = await store.put(key=f"reports/{license_id}/{stamp}.csv", content=report_csv(spec, result, language), content_type="text/csv; charset=utf-8")
        files["csv"] = await store.signed_url(path=stored.path, expires_seconds=FILE_LINK_SECONDS)
        stored = await store.put(key=f"reports/{license_id}/{stamp}.html", content=page.encode("utf-8"), content_type="text/html; charset=utf-8")
        files["html"] = await store.signed_url(path=stored.path, expires_seconds=FILE_LINK_SECONDS)
    except DocumentStoreNotConfigured:
        return files
    except Exception:  # noqa: BLE001
        log.exception("could not store report files")
        return files
    try:
        from .pdf import PdfOptions, get_renderer

        rendered = await get_renderer("smartbrowz").render(page, PdfOptions(), idempotency_key=f"report:{license_id}:{stamp}")
        if rendered.content:
            stored = await store.put(key=f"reports/{license_id}/{stamp}.pdf", content=rendered.content, content_type="application/pdf")
            files["pdf"] = await store.signed_url(path=stored.path, expires_seconds=FILE_LINK_SECONDS)
    except Exception:  # noqa: BLE001
        log.info("report PDF skipped (renderer not available)")
    return files


# ----------------------------------------------------------------- the flow

async def run_spec(client: DataClient, *, license_id: str, spec: dict, actor_id: str | None = None) -> dict:
    spec = validate_query_spec(spec)
    return await client.run_report_query(license_id, spec, actor_id=actor_id)


async def handle_report_request(
    client: DataClient, *, license_id: str, message: str, language: str = "th",
    actor_id: str | None = None, ai_client=None, company_name: str = "", with_files: bool = True,
) -> dict:
    """The whole path for one request. Returns one of:
    {"clarify": question} · {"spec", "result", "text", "files"} · raises
    ReportSpecInvalid / AIUnavailable / AINotConfigured / DataTierError."""
    data = await generate_query_spec(message, language=language, client=ai_client)
    if data.get("clarify"):
        return {"clarify": str(data["clarify"])[:300]}
    spec = validate_query_spec(data)
    result = await client.run_report_query(license_id, spec, actor_id=actor_id)
    text = report_text(spec, result, language)
    files = await publish_files(spec, result, language, license_id=license_id, company_name=company_name) if with_files else {}
    return {"spec": spec, "result": result, "text": text, "files": files}


def files_line(files: dict, language: str) -> str:
    if not files or not files.get("csv"):
        return ""
    line = _t(FILES_LINE, language).format(csv=files["csv"], html=files.get("html") or "-")
    if files.get("pdf"):
        line += _t(PDF_LINE, language).format(pdf=files["pdf"])
    return line
