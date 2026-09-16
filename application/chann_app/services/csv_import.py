"""CSV import for the two lists a shop builds in bulk (owner, 4 Sep):
the product catalogue and the register of sold units. A file the owner
exports from a spreadsheet, headers in Thai or English, one row per
item; every row is applied on its own so one bad line does not sink the
other four hundred, and the reply names each row that was refused.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

from ..data_client import DataClient, DataTierError

log = logging.getLogger(__name__)

MAX_ROWS = 500

PRODUCT_COLUMNS = {
    "product_id": ("product_id", "รหัสสินค้า", "code", "รหัส", "id"),
    "product_name": ("product_name", "ชื่อสินค้า", "name", "สินค้า"),
    "unit_price": ("unit_price", "ราคา", "price", "ราคาต่อหน่วย"),
    "warranty_months": ("warranty_months", "ประกัน(เดือน)", "ระยะประกัน", "warranty months"),
    "category": ("category", "หมวด", "หมวดหมู่", "ประเภท"),
    "sku": ("sku", "รหัสภายใน"),
    "description": ("description", "รายละเอียด", "คำอธิบาย"),
}
WARRANTY_COLUMNS = {
    "serial_number": ("serial_number", "serial", "s/n", "sn", "หมายเลขเครื่อง", "ซีเรียล"),
    "product_id": ("product_id", "รหัสสินค้า", "code"),
    "product_name": ("product_name", "ชื่อสินค้า", "สินค้า", "name"),
    "warranty_start": ("warranty_start", "วันที่ซื้อ", "วันเริ่มประกัน", "purchase_date", "start"),
    "warranty_months": ("warranty_months", "ประกัน(เดือน)", "ประกันเดือน", "months", "เดือน"),
}

PRODUCT_SAMPLE = (
    "product_id,product_name,unit_price,category,sku,description\n"
    "FAN001,พัดลมไอเย็น 20 ลิตร,3500,พัดลม,F-20L,ถังน้ำ 20 ลิตร รีโมต\n"
    "AC12K,แอร์ติดผนัง 12000 BTU,15900,เครื่องปรับอากาศ,AC-12,Inverter รวมติดตั้ง\n"
    "WM8,เครื่องซักผ้าฝาบน 8 กก.,8900,เครื่องซักผ้า,,\n"
)
WARRANTY_SAMPLE = (
    "serial_number,product_id,product_name,warranty_start,warranty_months\n"
    "SN-AC12K-000123,AC12K,แอร์ติดผนัง 12000 BTU,2026-09-01,24\n"
    "SN-FAN-000045,FAN001,พัดลมไอเย็น 20 ลิตร,01/09/2026,12\n"
    "SN-WM8-000007,,เครื่องซักผ้าฝาบน 8 กก.,,12\n"
)


class CsvRejected(Exception):
    """The file as a whole cannot be read — wrong headers, empty, too big."""


def _rows(text: str, columns: dict[str, tuple[str, ...]], required: tuple[str, ...]) -> list[dict]:
    text = (text or "").lstrip("﻿").strip()
    if not text:
        raise CsvRejected("empty file")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise CsvRejected("empty file")
    aliases = {alias.lower(): field for field, names in columns.items() for alias in names}
    mapping: dict[int, str] = {}
    for i, raw in enumerate(header):
        key = (raw or "").strip().lower().lstrip("﻿")
        if key in aliases:
            mapping[i] = aliases[key]
    missing = [field for field in required if field not in mapping.values()]
    if missing:
        raise CsvRejected("missing columns: " + ", ".join(missing))
    out = []
    for n, row in enumerate(reader, start=2):
        if not any((c or "").strip() for c in row):
            continue
        item = {"_row": n}
        for i, field in mapping.items():
            item[field] = (row[i] if i < len(row) else "").strip()
        out.append(item)
        if len(out) > MAX_ROWS:
            raise CsvRejected(f"more than {MAX_ROWS} rows")
    if not out:
        raise CsvRejected("no data rows")
    return out


def _iso_date(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            # A Buddhist-era year typed by hand (2569) → Gregorian.
            if parsed.year > 2400:
                parsed = date(parsed.year - 543, parsed.month, parsed.day)
            return parsed.isoformat()
        except ValueError:
            continue
    raise ValueError(f"bad date: {value}")


async def import_products(client: DataClient, *, license_id: str, text: str, actor_id: str) -> dict:
    rows = _rows(text, PRODUCT_COLUMNS, ("product_id", "product_name"))
    results = []
    saved = 0
    for item in rows:
        code = item.get("product_id") or ""
        try:
            if not code or not item.get("product_name"):
                raise ValueError("product_id and product_name are required")
            # product_id belongs in the BODY as well as the URL
            # (schemas.ProductIn requires it). Without it every row came
            # back 422 and an import saved nothing at all — invisible here
            # because the test fake accepted any payload (DEV log,
            # 16 ก.ย. 2569).
            payload = {"product_id": code, "product_name": item["product_name"]}
            for key in ("category", "sku", "description"):
                if item.get(key):
                    payload[key] = item[key]
            if item.get("unit_price"):
                payload["unit_price"] = str(float(item["unit_price"].replace(",", "")))
            # PRODUCT_COLUMNS has taken "ระยะประกัน" since the column list was
            # written, and nothing ever sent it on: a shop could fill the
            # column in and the period stayed unset.
            if item.get("warranty_months"):
                payload["warranty_months"] = int(str(item["warranty_months"]).strip())
            await client.upsert_product(license_id, code, payload, actor_id=actor_id)
            saved += 1
            results.append({"row": item["_row"], "key": code, "status": "saved", "message": ""})
        except DataTierError as exc:
            results.append({"row": item["_row"], "key": code, "status": "error", "message": str(exc.detail)[:200]})
        except (ValueError, KeyError) as exc:
            results.append({"row": item["_row"], "key": code, "status": "error", "message": str(exc)[:200]})
    return {"kind": "products", "total": len(rows), "saved": saved, "failed": len(rows) - saved, "rows": results}


async def _registered_already(client: DataClient, license_id: str, serial: str) -> dict | None:
    """The unit with exactly this serial, or None. Exact, not a search:
    the lookup is by serial and a near-match would back-fill the purchase
    date of somebody else's machine."""
    try:
        rows = await client.list_warranties(license_id, serial_number=serial)
    except DataTierError:
        return None
    return next(
        (r for r in rows or [] if str(r.get("serial_number") or "").upper() == serial
         and str(r.get("status") or "") != "void"),
        None,
    )


async def import_warranties(client: DataClient, *, license_id: str, text: str, actor_id: str) -> dict:
    """Register sold units — and fill in the purchase date of ones already
    registered.

    Owner, 16 ก.ย. 2569: "ใน Sale OA ต้องรองรับการอัพโหลดไฟ csv เข้ามาเพื่อ
    อัปเดตวันที่ซื้อกับทะเบียนสินค้าเก่าๆด้วยและคำนวณวันหมดสิ้นสุดมาเลยตาม
    แต่ละสินค้า". Since round 19g a unit may be registered with no purchase
    date at all, so a shop ends up with a register full of units whose
    warranty has no start; the spreadsheet they already keep is how the
    dates arrive. A row whose serial is already on file used to come back
    "duplicate serial" and change nothing.

    The end date is never in the file: the Data Tier computes it from the
    start and the product's own warranty period (0030), so one shop-wide
    rule decides it rather than whatever was typed in a column.
    """
    rows = _rows(text, WARRANTY_COLUMNS, ("serial_number",))
    results = []
    saved = 0
    updated = 0
    for item in rows:
        serial = (item.get("serial_number") or "").upper()
        try:
            if not serial:
                raise ValueError("serial_number is required")
            months = item.get("warranty_months") or ""
            start = _iso_date(item.get("warranty_start") or "")
            period = int(float(months)) if months else None
            existing = await _registered_already(client, license_id, serial)
            if existing is not None:
                if not start:
                    # Nothing to add: the row names a unit already on file
                    # and carries no date. Said as its own verdict, not as
                    # an error — the shop re-uploading last month's file
                    # has done nothing wrong.
                    results.append({
                        "row": item["_row"], "key": serial, "status": "skipped",
                        "message": "already registered",
                    })
                    continue
                await client.update_warranty(
                    license_id, str(existing.get("id") or ""),
                    {"warranty_start": start, "warranty_months": period},
                    actor_id=actor_id,
                )
                updated += 1
                results.append({"row": item["_row"], "key": serial, "status": "updated", "message": ""})
                continue
            payload = {
                "serial_number": serial,
                "product_id": item.get("product_id") or None,
                "product_name": item.get("product_name") or None,
                "warranty_start": start,
                "warranty_months": period,
            }
            await client.register_warranty(license_id, payload, actor_id=actor_id)
            saved += 1
            results.append({"row": item["_row"], "key": serial, "status": "saved", "message": ""})
        except DataTierError as exc:
            message = "duplicate serial" if exc.status_code == 409 else str(exc.detail)[:200]
            results.append({"row": item["_row"], "key": serial, "status": "error", "message": message})
        except (ValueError, KeyError) as exc:
            results.append({"row": item["_row"], "key": serial, "status": "error", "message": str(exc)[:200]})
    failed = len([r for r in results if r["status"] == "error"])
    return {
        "kind": "warranties", "total": len(rows), "saved": saved, "updated": updated,
        "skipped": len([r for r in results if r["status"] == "skipped"]),
        "failed": failed, "rows": results,
    }


# ------------------------------------------------ customers (user review, 4 Sep 2026)
CUSTOMER_COLUMNS = {
    "first_name": ("first_name", "ชื่อ", "firstname", "name"),
    "last_name": ("last_name", "นามสกุล", "lastname", "surname"),
    "phone": ("phone", "เบอร์", "เบอร์โทร", "โทร", "tel", "mobile"),
    "email": ("email", "อีเมล", "e-mail"),
    "address": ("address", "ที่อยู่"),
    "notes": ("notes", "note", "บันทึก", "หมายเหตุ"),
}


async def import_customers(client: DataClient, *, license_id: str, text: str, actor_id: str) -> dict:
    """Leads from a spreadsheet: one verdict per row. A duplicate phone or
    email is refused naming the existing record; a phone with letters is
    refused with the reason; the rest are created."""
    from .phone import phone_problem

    rows = _rows(text, CUSTOMER_COLUMNS, ("first_name", "phone"))
    results = []
    saved = 0
    for item in rows:
        key = item.get("phone") or ""
        try:
            if not item.get("first_name"):
                raise ValueError("first_name is required")
            if not item.get("phone"):
                raise ValueError("phone is required")
            problem = phone_problem(item["phone"])
            if problem == "letters":
                raise ValueError("phone must contain digits only")
            if problem == "length":
                raise ValueError("phone must have 9-15 digits")
            payload = {k: item[k] for k in ("first_name", "last_name", "phone", "email", "address", "notes") if item.get(k)}
            row = await client.create_customer(license_id, payload, actor_id=actor_id)
            saved += 1
            # Imported customers are new work for the sales team (round 18).
            from .sales_dispatch import route_new_customer

            await route_new_customer(client, license_id, row, source="csv", actor_chann_uid=actor_id)
            results.append({"row": item["_row"], "key": row.get("customer_id") or key, "status": "saved", "message": ""})
        except DataTierError as exc:
            structured = getattr(exc, "structured", None) or {}
            if structured.get("error") == "duplicate":
                results.append({"row": item["_row"], "key": key, "status": "error",
                                "message": f"already exists: {structured.get('existing_code', '')} ({structured.get('field', 'phone')})"})
            else:
                results.append({"row": item["_row"], "key": key, "status": "error", "message": str(exc.detail)[:200]})
        except (ValueError, KeyError) as exc:
            results.append({"row": item["_row"], "key": key, "status": "error", "message": str(exc)[:200]})
    return {"kind": "customers", "total": len(rows), "saved": saved, "failed": len(rows) - saved, "rows": results}
