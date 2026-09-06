"""Phase 17 — the ad-hoc report engine's only door into the database.

The AI never writes SQL. It produces a small JSON spec; this module turns
that spec into one SQLAlchemy statement built only from the whitelist
below, always filtered by the caller's license_id, always parameterised.
Anything outside the whitelist is refused before a statement exists.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ChannIdentity, Customer, Deal, LicenseMember, Product, Quote, ServiceTicket, Warranty,
)
from .phase9 import DEAL_STAGES
from .phase10 import QUOTE_STATUSES
from .phase12 import TICKET_STATUSES
from .phase16 import WARRANTY_STATUSES
from .tenant_scope import TenantScope

BANGKOK = ZoneInfo("Asia/Bangkok")

# entity -> {field name the AI may use: column}
ENTITIES: dict[str, dict] = {
    "deals": {
        "model": Deal,
        "fields": {
            "stage": Deal.stage, "owner_member_id": Deal.owner_member_id,
            "created_at": Deal.created_at, "expected_close_date": Deal.expected_close_date,
        },
        "enums": {"stage": DEAL_STAGES},
        "date_fields": ("created_at", "expected_close_date"),
    },
    "customers": {
        "model": Customer,
        "fields": {
            "stage": Customer.stage, "owner_member_id": Customer.owner_member_id,
            "created_at": Customer.created_at,
        },
        "enums": {},
        "date_fields": ("created_at",),
    },
    "tickets": {
        "model": ServiceTicket,
        "fields": {
            "status": ServiceTicket.status, "assigned_to": ServiceTicket.assigned_to_ref,
            "created_at": ServiceTicket.created_at, "scheduled_date": ServiceTicket.scheduled_date,
        },
        "enums": {"status": TICKET_STATUSES},
        "date_fields": ("created_at", "scheduled_date"),
    },
    "quotes": {
        "model": Quote,
        "fields": {
            "status": Quote.status, "owner_member_id": Quote.owner_member_id,
            "created_at": Quote.created_at, "valid_until": Quote.valid_until,
        },
        "enums": {"status": QUOTE_STATUSES},
        "date_fields": ("created_at", "valid_until"),
    },
    "warranties": {
        "model": Warranty,
        "fields": {
            "status": Warranty.status, "product_id": Warranty.product_id,
            "warranty_end": Warranty.warranty_end, "created_at": Warranty.created_at,
        },
        "enums": {"status": WARRANTY_STATUSES},
        "date_fields": ("created_at", "warranty_end"),
    },
}
# Numeric columns a sum/avg/min/max may target. Small on purpose: every
# entry here is a number the whole tenant may see through a report.
NUMERIC_FIELDS: dict[str, dict] = {
    "deals": {"amount": Deal.amount},
    "quotes": {"discount_amount": Quote.discount_amount},
}
METRICS = ("count", "sum", "avg", "min", "max")
GROUP_BY = ("owner_member_id", "stage", "status", "product_id", "assigned_to")
DATE_RANGES = ("today", "yesterday", "last_7_days", "last_30_days", "this_month", "last_month", "last_3_months", "this_year", "last_year")
ID_FIELDS = ("owner_member_id", "product_id", "assigned_to")
_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class ReportSpecInvalid(ValueError):
    pass


def date_window(range_key: str, *, today: date | None = None) -> tuple[datetime, datetime]:
    """[start, end) in UTC for a Bangkok-local range name."""
    today = today or datetime.now(BANGKOK).date()
    if range_key == "today":
        start, end = today, today + timedelta(days=1)
    elif range_key == "yesterday":
        start, end = today - timedelta(days=1), today
    elif range_key == "last_7_days":
        start, end = today - timedelta(days=6), today + timedelta(days=1)
    elif range_key == "last_30_days":
        start, end = today - timedelta(days=29), today + timedelta(days=1)
    elif range_key == "this_month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    elif range_key == "last_month":
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    elif range_key == "last_3_months":
        start = (today.replace(day=1) - timedelta(days=62)).replace(day=1)
        end = today + timedelta(days=1)
    elif range_key == "this_year":
        start, end = today.replace(month=1, day=1), today + timedelta(days=1)
    elif range_key == "last_year":
        start, end = today.replace(year=today.year - 1, month=1, day=1), today.replace(month=1, day=1)
    else:
        raise ReportSpecInvalid(f"unknown date range '{range_key}'")
    to_utc = lambda d: datetime.combine(d, time.min, tzinfo=BANGKOK).astimezone(timezone.utc)  # noqa: E731
    return to_utc(start), to_utc(end)


def validate_spec(spec: dict) -> dict:
    """The whitelist, applied. Returns a normalised copy or raises."""
    if not isinstance(spec, dict):
        raise ReportSpecInvalid("spec must be an object")
    entity = spec.get("entity")
    if entity not in ENTITIES:
        raise ReportSpecInvalid(f"entity '{entity}' is not allowed")
    table = ENTITIES[entity]
    metric = spec.get("metric") or "count"
    if metric not in METRICS:
        raise ReportSpecInvalid(f"metric '{metric}' is not allowed")
    field = spec.get("field")
    if metric != "count":
        if field not in NUMERIC_FIELDS.get(entity, {}):
            raise ReportSpecInvalid(f"{metric} needs a numeric field of {entity}; '{field}' is not one")
    else:
        field = None
    filters_in = spec.get("filter") or {}
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
    if group_by is not None:
        if group_by not in GROUP_BY or group_by not in table["fields"]:
            raise ReportSpecInvalid(f"cannot group {entity} by '{group_by}'")
    date_range = spec.get("date_range") or spec.get("dateRange")
    if date_range is not None and date_range not in DATE_RANGES:
        raise ReportSpecInvalid(f"date range '{date_range}' is not allowed")
    date_field = spec.get("date_field") or spec.get("dateField") or table["date_fields"][0]
    if date_field not in table["date_fields"]:
        raise ReportSpecInvalid(f"'{date_field}' is not a date field of {entity}")
    return {
        "entity": entity, "metric": metric, "field": field, "filter": filters,
        "group_by": group_by, "date_range": date_range, "date_field": date_field,
    }


class ReportQueryRepository:
    def __init__(self, session: Session):
        self._s = session

    def build_statement(self, scope: TenantScope, spec: dict, *, today: date | None = None):
        """One SELECT from the whitelist, tenant-filtered. Public so a test
        can look at the SQL and see the license_id bind parameter."""
        spec = validate_spec(spec)
        table = ENTITIES[spec["entity"]]
        model = table["model"]
        if spec["metric"] == "count":
            measure = func.count()
        else:
            column = NUMERIC_FIELDS[spec["entity"]][spec["field"]]
            measure = getattr(func, spec["metric"])(column)
        stmt = select(measure.label("value"))
        group_col = table["fields"][spec["group_by"]] if spec["group_by"] else None
        if group_col is not None:
            stmt = select(group_col.label("key"), measure.label("value")).group_by(group_col).order_by(measure.desc())
        stmt = stmt.select_from(model).where(model.license_id == scope.license_id)
        if hasattr(model, "archived_at"):
            # Archived leads and deals are not in any list; they were still
            # counted in "ลูกค้าใหม่เดือนนี้" (review, 6 Sep 2026).
            stmt = stmt.where(model.archived_at.is_(None))
        for key, value in spec["filter"].items():
            stmt = stmt.where(table["fields"][key] == value)
        if spec["date_range"]:
            start, end = date_window(spec["date_range"], today=today)
            column = table["fields"][spec["date_field"]]
            if isinstance(column.type.python_type, type) and column.type.python_type is date:
                stmt = stmt.where(column >= start.astimezone(BANGKOK).date(), column < end.astimezone(BANGKOK).date())
            else:
                stmt = stmt.where(column >= start, column < end)
        return stmt, spec

    def run(self, scope: TenantScope, spec: dict, *, today: date | None = None) -> dict:
        stmt, spec = self.build_statement(scope, spec, today=today)
        rows = []
        if spec["group_by"]:
            for key, value in self._s.execute(stmt).all():
                rows.append({"key": str(key) if key is not None else "", "label": self._label(scope, spec["group_by"], key), "value": _num(value)})
            total = sum(r["value"] for r in rows) if spec["metric"] in ("count", "sum") else None
        else:
            value = self._s.execute(stmt).scalar_one()
            total = _num(value)
        return {
            **spec, "rows": rows, "total": total,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _label(self, scope: TenantScope, group_by: str, key) -> str:
        if key is None:
            return "—"
        if group_by in ("owner_member_id", "assigned_to"):
            member = self._s.get(LicenseMember, key) if isinstance(key, uuid.UUID) else None
            if member is not None and member.license_id == scope.license_id:
                identity = self._s.get(ChannIdentity, member.chann_uid)
                if identity is not None and identity.display_name:
                    return identity.display_name
                return member.chann_uid
            return str(key)[:8]
        if group_by == "product_id":
            product = self._s.get(Product, key) if isinstance(key, uuid.UUID) else None
            if product is not None and product.license_id == scope.license_id:
                return getattr(product, "name", None) or getattr(product, "product_name", None) or str(key)[:8]
            return str(key)[:8]
        return str(key)


def _num(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else round(f, 2)
