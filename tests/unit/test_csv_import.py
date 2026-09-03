"""4 Sep owner list, part 3 (csv-import-v1): the catalogue and the register
of sold units accept a spreadsheet export, Thai or English headers, one
verdict per row; the guide can be taken away as a file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app import routers_phase2  # noqa: E402
from chann_app.data_client import DataTierError  # noqa: E402
from chann_app.services import csv_import  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_app.services.guides import GUIDES, guide_as_html, guide_as_markdown  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


class TestProducts:
    async def test_thai_headers_and_prices_with_commas(self):
        client = FakeDataClient(permission_keys=["product.manage"])
        text = "﻿รหัสสินค้า,ชื่อสินค้า,ราคา,หมวด\nFAN001,พัดลมไอเย็น,\"3,500\",พัดลม\nAC12K,แอร์ 12000 BTU,15900,\n"
        result = await csv_import.import_products(client, license_id=LICENSE_ID, text=text, actor_id="CHN-X")
        assert result["saved"] == 2 and result["failed"] == 0
        calls = [r for r in client.recorded if r[0] == "upsert_product"]
        assert calls[0][2] == "FAN001" and calls[0][3]["unit_price"] == "3500.0"
        assert calls[0][3]["category"] == "พัดลม" and "category" not in calls[1][3]

    async def test_a_bad_row_does_not_sink_the_others(self):
        client = FakeDataClient(permission_keys=["product.manage"])
        text = "product_id,product_name,unit_price\nA1,ดี,10\n,ไม่มีรหัส,5\nA3,ราคาเพี้ยน,abc\n"
        result = await csv_import.import_products(client, license_id=LICENSE_ID, text=text, actor_id="CHN-X")
        assert result["saved"] == 1 and result["failed"] == 2
        statuses = [(r["row"], r["status"]) for r in result["rows"]]
        assert statuses == [(2, "saved"), (3, "error"), (4, "error")]

    async def test_missing_columns_reject_the_file(self):
        client = FakeDataClient(permission_keys=["product.manage"])
        with pytest.raises(csv_import.CsvRejected):
            await csv_import.import_products(client, license_id=LICENSE_ID, text="name,price\nx,1\n", actor_id="CHN-X")
        with pytest.raises(csv_import.CsvRejected):
            await csv_import.import_products(client, license_id=LICENSE_ID, text="", actor_id="CHN-X")


class TestWarranties:
    async def test_dates_in_either_shape_and_duplicates_named(self):
        client = FakeDataClient(permission_keys=["warranty.create"])
        seen = set()

        async def register(license_id, payload, actor_id=None):
            client.recorded.append(("register_warranty", license_id, payload))
            if payload["serial_number"] in seen:
                raise DataTierError(409, "duplicate")
            seen.add(payload["serial_number"])
            return {"id": "w", **payload}

        client.register_warranty = register
        text = (
            "serial_number,product_name,warranty_start,warranty_months\n"
            "sn-1,แอร์,2026-09-01,24\n"
            "SN-2,พัดลม,01/09/2569,12\n"
            "SN-1,ซ้ำ,,\n"
        )
        result = await csv_import.import_warranties(client, license_id=LICENSE_ID, text=text, actor_id="CHN-X")
        assert result["saved"] == 2 and result["failed"] == 1
        calls = [r[2] for r in client.recorded if r[0] == "register_warranty"]
        assert calls[0]["serial_number"] == "SN-1" and calls[0]["warranty_start"] == "2026-09-01"
        assert calls[1]["warranty_start"] == "2026-09-01" and calls[1]["warranty_months"] == 12
        assert result["rows"][2]["message"] == "duplicate serial"


def _harness(keys):
    client = FakeDataClient(permission_keys=list(keys))

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-OWNER", role="owner", is_owner=True,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


class TestRoutes:
    def test_products_import_needs_the_key_and_reports_rows(self):
        http, client = _harness(["product.manage"])
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/products/import",
            json={"csv": csv_import.PRODUCT_SAMPLE},
        )
        assert response.status_code == 200, response.text
        assert response.json()["saved"] == 3
        http2, _ = _harness(["product.read"])
        assert http2.post(f"/api/v1/licenses/{LICENSE_ID}/products/import", json={"csv": "x"}).status_code == 403

    def test_a_broken_file_is_a_422_with_the_reason(self):
        http, _ = _harness(["warranty.create"])
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/warranties/import", json={"csv": "a,b\n1,2\n"})
        assert response.status_code == 422
        assert "missing columns" in response.json()["detail"]["message"]

    def test_the_samples_match_the_parser(self):
        http, _ = _harness(["warranty.create", "product.manage"])
        response = http.post(f"/api/v1/licenses/{LICENSE_ID}/warranties/import", json={"csv": csv_import.WARRANTY_SAMPLE})
        assert response.status_code == 200 and response.json()["saved"] == 3


class TestGuideFiles:
    def test_every_oa_renders_markdown_and_html_with_image_slots(self):
        for oa in GUIDES:
            md = guide_as_markdown(oa)
            html = guide_as_html(oa)
            assert "[IMAGE:" in md and GUIDES[oa]["title"]["th"] in md
            assert "<h1>" in html and 'class="slot"' in html
            for step in GUIDES[oa]["steps"]:
                assert step["title"]["th"] in html and step["image"] in html

    def test_the_sample_files_on_disk_are_the_parsers_samples(self):
        root = Path(__file__).resolve().parents[2] / "presentation" / "public" / "samples"
        assert (root / "products.csv").read_text(encoding="utf-8") == csv_import.PRODUCT_SAMPLE
        assert (root / "warranties.csv").read_text(encoding="utf-8") == csv_import.WARRANTY_SAMPLE
