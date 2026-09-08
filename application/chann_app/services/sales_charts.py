"""The sales pictures the chat can send — "ขอกราฟยอดขาย" and its cousins.

Phase 17 wants three output shapes for a report; this is the "ตาราง/กราฟ"
one for the four questions a shop actually asks in the chat:

  * the pipeline by stage        (`pipeline`)
  * sales over the last months   (`monthly`)
  * best-selling products        (`products`)
  * sales per person             (`owner`)

Nothing here queries the database. Every number comes from an endpoint the
Application tier already calls — `pipeline_summary` (the same one the
dashboard card and the text "สรุปการขาย" use, so the picture and the
sentence beside it can never disagree) and `list_deals`, which is
tenant-scoped by its path. A deal's value is read the way the pipeline
repository reads it: the line items when there are any, otherwise the
amount the salesperson stated (0024).

Drawing lives in charts.py and knows nothing about deals; storing lives in
reports_ai.publish_chart and knows nothing about drawing. This module is
the only place that knows a "ยอดขาย" chart is won deals by month.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..data_client import DataClient
from . import charts

log = logging.getLogger(__name__)

# Charts are tenant numbers sitting behind a bearer link, so the link is
# short — an hour, like a ticket photo (services/photos.py). LINE fetches
# the picture the moment the message is delivered and keeps its own copy,
# so the person still sees it when they scroll back next week; what
# expires is the ability of anyone the link is forwarded to to fetch the
# original.
CHART_LINK_TTL_SECONDS = 3600

# The shop's own calendar. "6 เดือนล่าสุด" must mean the same six months the
# Data tier's report windows mean (chann_data.repositories.phase17 works in
# Asia/Bangkok), or a deal closed at 01:00 Bangkok on the 1st lands in last
# month's bar. Same fixed offset the reminders and trials services use —
# Thailand has no DST.
BANGKOK_TZ = timezone(timedelta(hours=7))

MONTHS_DEFAULT = 6
TOP_PRODUCTS_DEFAULT = 5

STAGE_ORDER = ("new", "proposed", "won", "lost")
STAGE_LABEL = {
    "new": {"th": "ใหม่", "en": "New"},
    "proposed": {"th": "เสนอราคาแล้ว", "en": "Proposed"},
    "won": {"th": "ปิดสำเร็จ", "en": "Won"},
    "lost": {"th": "ไม่สำเร็จ", "en": "Lost"},
}
MONTH_ABBR = {
    "th": ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
           "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}
UNNAMED = {"th": "ไม่ระบุ", "en": "Unassigned"}

TITLE = {
    "pipeline": {"th": "ยอดขายตามสถานะดีล", "en": "Pipeline by stage"},
    "monthly": {"th": "ยอดขายรายเดือน", "en": "Sales by month"},
    "products": {"th": "สินค้าขายดี", "en": "Best sellers"},
    "owner": {"th": "ยอดขายรายคน", "en": "Sales by person"},
}
SUBTITLE = {
    "pipeline": {"th": "ดีลทั้งหมด · บาท", "en": "All deals · THB"},
    "monthly": {"th": "{n} เดือนล่าสุด · บาท (ดีลปิดสำเร็จ)", "en": "Last {n} months · THB (won deals)"},
    "products": {"th": "จากดีลที่ปิดสำเร็จ · บาท", "en": "From won deals · THB"},
    "owner": {"th": "ดีลปิดสำเร็จ · บาท", "en": "Won deals · THB"},
}
SUMMARY = {
    "pipeline": {
        "th": "กราฟยอดขายตามสถานะดีล — เปิดอยู่ {open_count} ดีล มูลค่า {open_value} บาท · ปิดสำเร็จ {won_count} ดีล มูลค่า {won_value} บาท",
        "en": "Pipeline by stage — {open_count} open worth {open_value} THB · {won_count} won worth {won_value} THB",
    },
    "monthly": {
        "th": "กราฟยอดขาย {n} เดือนล่าสุด (ดีลปิดสำเร็จ) — รวม {total} บาท · เดือนนี้ {latest} บาท",
        "en": "Sales for the last {n} months (won deals) — {total} THB in total · {latest} THB this month",
    },
    "products": {
        "th": "กราฟสินค้าขายดีจากดีลที่ปิดสำเร็จ — อันดับ 1 คือ {top} {top_value} บาท",
        "en": "Best sellers from won deals — {top} leads with {top_value} THB",
    },
    "owner": {
        "th": "กราฟยอดขายรายคน (ดีลปิดสำเร็จ) — {top} ทำได้มากที่สุด {top_value} บาท",
        "en": "Sales per person (won deals) — {top} leads with {top_value} THB",
    },
}
EMPTY_SUMMARY = {
    "pipeline": {"th": "ยังไม่มีดีลในระบบ กราฟจึงยังว่างอยู่", "en": "There are no deals yet, so the chart is empty."},
    "monthly": {"th": "ยังไม่มีดีลที่ปิดสำเร็จใน {n} เดือนล่าสุด กราฟจึงยังว่างอยู่", "en": "No won deals in the last {n} months, so the chart is empty."},
    "products": {"th": "ยังไม่มีสินค้าในดีลที่ปิดสำเร็จ กราฟจึงยังว่างอยู่", "en": "No products on won deals yet, so the chart is empty."},
    "owner": {"th": "ยังไม่มีดีลที่ปิดสำเร็จ กราฟจึงยังว่างอยู่", "en": "No won deals yet, so the chart is empty."},
}
TOTAL_FOOTER = {"th": "รวม {total} บาท · {company}", "en": "Total {total} THB · {company}"}
COUNT_FOOTER = {"th": "รวม {count} ดีล · มูลค่า {total} บาท", "en": "{count} deals · {total} THB"}


def _t(table: dict, language: str) -> str:
    return table.get(language) or table["th"]


def _money(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):,.0f}"
    except (InvalidOperation, ValueError):
        return "0"


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def deal_value(deal: dict) -> Decimal:
    """What one deal is worth — the line items when it has any, otherwise
    the stated amount. Exactly what DealRepository.pipeline_summary does;
    a chart that valued deals differently from the card on the dashboard
    would be a second, quieter source of truth."""
    lines = deal.get("products") or []
    if lines:
        total = Decimal("0")
        for row in lines:
            total += _decimal(row.get("quoted_unit_price")) * int(row.get("qty") or 0)
        if total:
            return total
    return _decimal(deal.get("amount"))


def _as_date(value) -> date | None:
    """A calendar day in Bangkok. `expected_close_date` is already a plain
    day and is taken as it is; `created_at` is a UTC timestamp and is moved
    to Bangkok first, so an evening deal is not filed under the next day."""
    if isinstance(value, datetime):
        return (value.astimezone(BANGKOK_TZ) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    text = str(value or "")
    if "T" in text or " " in text.strip():
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            moment = None
        if moment is not None:
            return (moment.astimezone(BANGKOK_TZ) if moment.tzinfo else moment).date()
    text = text[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def deal_closed_on(deal: dict) -> date | None:
    """When a won deal counts towards a month. The expected close date is
    the shop's own answer to that question and is what the pipeline
    forecast already uses; created_at is the fallback for the deals nobody
    dated (a shop that never fills the date would otherwise have an empty
    chart forever)."""
    return _as_date(deal.get("expected_close_date")) or _as_date(deal.get("created_at"))


def month_labels(today: date, months: int, language: str) -> list[tuple[tuple[int, int], str]]:
    """The last `months` calendar months ending with today's, newest last.
    The year rides along on every label: "ม.ค." on its own is ambiguous the
    moment the window crosses a new year, which it does every January."""
    abbr = MONTH_ABBR.get(language) or MONTH_ABBR["th"]
    out = []
    year, month = today.year, today.month
    for _ in range(months):
        out.append(((year, month), f"{abbr[month - 1]} {(year + (543 if language != 'en' else 0)) % 100:02d}"))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


@dataclass
class ChartAnswer:
    """A drawn chart and the sentence that goes with it. The text is a
    complete answer on its own — the picture may fail to store, and a
    notification preview never shows an image."""

    chart: charts.Chart
    summary: str
    empty: bool = False


# ------------------------------------------------------------------ builders

async def pipeline_chart(
    client: DataClient, *, license_id: str, language: str = "th", company_name: str = "",
) -> ChartAnswer:
    summary = await client.pipeline_summary(str(license_id))
    by_stage = summary.get("by_stage") or {}
    points: list[tuple[str, float]] = []
    total = Decimal("0")
    count = 0
    for stage in STAGE_ORDER:
        bucket = by_stage.get(stage) or {}
        value = _decimal(bucket.get("value"))
        points.append((_t(STAGE_LABEL[stage], language), float(value)))
        total += value
        count += int(bucket.get("count") or 0)
    if not count:
        points = []
    open_count = sum(int((by_stage.get(s) or {}).get("count") or 0) for s in ("new", "proposed"))
    won = by_stage.get("won") or {}
    chart = charts.Chart(
        title=_t(TITLE["pipeline"], language), subtitle=_t(SUBTITLE["pipeline"], language),
        points=points, kind="bar", money=True, language=language,
        footer=_t(COUNT_FOOTER, language).format(count=count, total=_money(total)) if count else "",
    )
    if not count:
        return ChartAnswer(chart, _t(EMPTY_SUMMARY["pipeline"], language), empty=True)
    return ChartAnswer(chart, _t(SUMMARY["pipeline"], language).format(
        open_count=open_count, open_value=_money(summary.get("open_value")),
        won_count=int(won.get("count") or 0), won_value=_money(won.get("value")),
    ))


async def monthly_chart(
    client: DataClient, *, license_id: str, language: str = "th", months: int = MONTHS_DEFAULT,
    company_name: str = "", today: date | None = None,
) -> ChartAnswer:
    months = max(2, min(int(months or MONTHS_DEFAULT), charts.MAX_POINTS))
    today = today or datetime.now(BANGKOK_TZ).date()
    deals = await client.list_deals(str(license_id), stage="won")
    buckets: dict[tuple[int, int], Decimal] = {}
    for deal in deals or []:
        when = deal_closed_on(deal)
        if when is None:
            continue
        key = (when.year, when.month)
        buckets[key] = buckets.get(key, Decimal("0")) + deal_value(deal)
    window = month_labels(today, months, language)
    points = [(label, float(buckets.get(key, Decimal("0")))) for key, label in window]
    total = sum((buckets.get(key, Decimal("0")) for key, _ in window), Decimal("0"))
    # "Was there anything to plot" is about deals landing in the window, not
    # about the total: a shop whose only won deal is worth 0 asked a real
    # question and gets a real chart with a zero on it.
    plotted = any(key in buckets for key, _ in window)
    chart = charts.Chart(
        title=_t(TITLE["monthly"], language),
        subtitle=_t(SUBTITLE["monthly"], language).format(n=months),
        points=points if plotted else [], kind="line", money=True, language=language,
        footer=_t(TOTAL_FOOTER, language).format(total=_money(total), company=company_name).strip(" ·"),
    )
    if not plotted:
        return ChartAnswer(chart, _t(EMPTY_SUMMARY["monthly"], language).format(n=months), empty=True)
    return ChartAnswer(chart, _t(SUMMARY["monthly"], language).format(
        n=months, total=_money(total), latest=_money(buckets.get(window[-1][0], Decimal("0"))),
    ))


async def product_chart(
    client: DataClient, *, license_id: str, language: str = "th", top: int = TOP_PRODUCTS_DEFAULT,
    company_name: str = "",
) -> ChartAnswer:
    top = max(1, min(int(top or TOP_PRODUCTS_DEFAULT), charts.MAX_HBARS))
    deals = await client.list_deals(str(license_id), stage="won")
    totals: dict[str, Decimal] = {}
    for deal in deals or []:
        for row in deal.get("products") or []:
            name = str(row.get("product_name") or "").strip() or _t(UNNAMED, language)
            value = _decimal(row.get("quoted_unit_price")) * int(row.get("qty") or 0)
            totals[name] = totals.get(name, Decimal("0")) + value
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    points = [(name, float(value)) for name, value in ranked]
    chart = charts.Chart(
        # "สินค้าขายดี 5 อันดับ" only when there are five to rank: a shop
        # with one product on a won deal must not be told it has a top five.
        title=_t(TITLE["products"], language) + (
            f" {len(ranked)} อันดับ" if len(ranked) > 1 and language != "en" else ""),
        subtitle=_t(SUBTITLE["products"], language), points=points, kind="hbar",
        money=True, language=language,
        footer=_t(TOTAL_FOOTER, language).format(
            total=_money(sum((v for _, v in ranked), Decimal("0"))), company=company_name).strip(" ·"),
    )
    if not ranked:
        chart.title = _t(TITLE["products"], language)
        return ChartAnswer(chart, _t(EMPTY_SUMMARY["products"], language), empty=True)
    return ChartAnswer(chart, _t(SUMMARY["products"], language).format(
        top=ranked[0][0], top_value=_money(ranked[0][1]),
    ))


async def owner_chart(
    client: DataClient, *, license_id: str, language: str = "th", company_name: str = "",
) -> ChartAnswer:
    deals = await client.list_deals(str(license_id), stage="won")
    totals: dict[str, Decimal] = {}
    for deal in deals or []:
        totals[str(deal.get("owner_member_id") or "")] = (
            totals.get(str(deal.get("owner_member_id") or ""), Decimal("0")) + deal_value(deal)
        )
    names: dict[str, str] = {}
    try:
        for member in await client.list_members(str(license_id)) or []:
            display = str(member.get("display_name") or "").strip() or " ".join(
                p for p in (member.get("first_name"), member.get("last_name")) if p
            ).strip()
            names[str(member.get("id"))] = display or str(member.get("chann_uid") or "")
    except Exception:  # noqa: BLE001
        # A name is a nicety; the numbers are the answer. Falling back to
        # the member id would be worse than "ไม่ระบุ", so an unresolved
        # owner keeps that label.
        log.exception("could not resolve member names for the sales chart")
    ranked = sorted(
        ((names.get(ref) or _t(UNNAMED, language), value) for ref, value in totals.items()),
        key=lambda kv: (-kv[1], kv[0]),
    )
    points = [(name, float(value)) for name, value in ranked]
    chart = charts.Chart(
        title=_t(TITLE["owner"], language), subtitle=_t(SUBTITLE["owner"], language),
        points=points, kind="bar", money=True, language=language,
        footer=_t(TOTAL_FOOTER, language).format(
            total=_money(sum((v for _, v in ranked), Decimal("0"))), company=company_name).strip(" ·"),
    )
    if not ranked:
        return ChartAnswer(chart, _t(EMPTY_SUMMARY["owner"], language), empty=True)
    return ChartAnswer(chart, _t(SUMMARY["owner"], language).format(
        top=ranked[0][0], top_value=_money(ranked[0][1]),
    ))


BUILDERS = {
    "pipeline": pipeline_chart,
    "monthly": monthly_chart,
    "products": product_chart,
    "owner": owner_chart,
}


async def build(
    kind: str, client: DataClient, *, license_id: str, language: str = "th",
    company_name: str = "", **options,
) -> ChartAnswer:
    builder = BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"unknown sales chart: {kind!r}")
    return await builder(
        client, license_id=str(license_id), language=language,
        company_name=company_name, **options,
    )
