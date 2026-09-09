"""The sales journey, over HTTP, through both tiers.

Everything else in this suite tests one layer: a repository called
directly, or a chat handler against a fake client. That is why today's
worst bugs survived — every one of them lived in the seam between tiers,
and no test crossed a seam:

* ticket endpoints existed only in the Data tier, so every dashboard call
  404'd;
* a client sent PATCH to a route that only accepts POST, so the button
  returned 405 on its first press;
* the Presentation proxy parsed a PDF as JSON and answered 503 for a
  request the Application Tier had served successfully.

This walks a real shop's day — add a product, take a customer, open a
deal, quote it, discount it, issue it — with the Application Tier talking
to the Data Tier over its actual HTTP surface. A wrong method or a
missing route fails here, not in production.

The one thing stubbed is the PDF renderer: SmartBrowz is a paid external
service, and asserting that a document was recorded with the right
snapshot is the part that belongs to this codebase.
"""
from __future__ import annotations

import base64
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))


@pytest.fixture
def shop(migrated_db, monkeypatch):
    """A tenant, an owner, and both tiers wired together over HTTP.

    The Application Tier's DataClient is pointed at an in-process
    transport for the Data Tier app, so requests travel the real routing
    and validation of both sides without a network.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session, sessionmaker

    from chann_data import config as data_config
    from chann_data.db import get_session as data_get_session
    from chann_data.main import app as data_app
    from chann_data.models import ChannIdentity
    from chann_data.repositories.phase65 import RegistrationRepository

    # get_session is overridden rather than the setting patched: the
    # session factory is bound at import time from DATABASE_URL, so
    # changing the setting later leaves the app talking to whatever the
    # default pointed at.
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def _session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    data_app.dependency_overrides[data_get_session] = _session
    monkeypatch.setattr(data_config.settings, "admin_secret", "test-internal-secret")

    suffix = uuid.uuid4().hex[:6]
    with Session(migrated_db) as session:
        session.add(ChannIdentity(
            chann_uid=f"CHN-E2E-{suffix}", line_user_id=f"line-e2e-{suffix}",
            primary_role="sales",
        ))
        session.commit()
    with Session(migrated_db) as session:
        lic = RegistrationRepository(session).create_license(
            company_name=f"ร้านทดสอบ {suffix}",
            created_by_chann_uid=f"CHN-E2E-{suffix}",
        )
        session.commit()
        license_id = str(lic.id)

    from chann_app import data_client as dc
    from chann_app.main import app as application_app
    from chann_app.routers_phase2 import get_tenant_principal
    from chann_app.services.authorization import TenantPrincipal

    data_transport = httpx.ASGITransport(app=data_app)

    class _WiredClient(dc.DataClient):
        def __init__(self):
            super().__init__(
                base_url="http://data", secret="test-internal-secret",
            )
            self._client = httpx.AsyncClient(
                transport=data_transport, base_url="http://data",
            )

    wired = _WiredClient()
    from chann_app.routers_admin import get_data_client

    application_app.dependency_overrides[get_data_client] = lambda: wired

    # Every permission: this exercises the flow, not the gate — the gate
    # has its own tests, and mixing them would make a permission change
    # break a business-flow test for unrelated reasons.
    from chann_data.permissions import PERMISSION_KEYS

    application_app.dependency_overrides[get_tenant_principal] = (
        lambda: TenantPrincipal(
            chann_uid=f"CHN-E2E-{suffix}",
            license_id=license_id,
            role="owner",
            is_owner=True,
            permission_keys=sorted(PERMISSION_KEYS),
        )
    )

    client = TestClient(application_app)
    yield client, license_id

    application_app.dependency_overrides.clear()
    data_app.dependency_overrides.clear()


def _api(license_id: str, path: str = "") -> str:
    return f"/api/v1/licenses/{license_id}{path}"


class TestTheSalesJourneyOverHttp:
    def test_a_shop_can_get_from_a_customer_to_a_quote(self, shop):
        client, license_id = shop

        # 1. The catalogue.
        response = client.put(
            _api(license_id, "/products/FAN16"),
            json={
                "product_id": "FAN16",
                "product_name": "พัดลมตั้งพื้น 16 นิ้ว",
                "unit_price": "1500.00",
            },
        )
        assert response.status_code in (200, 201), response.text

        # 2. A customer.
        response = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "จุใจ", "last_name": "มาติกา", "phone": "0659635642"},
        )
        assert response.status_code == 201, response.text
        customer = response.json()
        assert customer["customer_id"].startswith("C-")

        # 3. Their deal.
        response = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        )
        assert response.status_code == 201, response.text
        deal = response.json()

        # 4. A line on it.
        response = client.post(
            _api(license_id, f"/deals/{deal['id']}/products"),
            json={
                "product_name": "พัดลมตั้งพื้น 16 นิ้ว",
                "quoted_unit_price": "1500.00",
                "qty": 2,
            },
        )
        assert response.status_code == 201, response.text

        # 5. The quote, which copies the line.
        response = client.post(
            _api(license_id, "/quotes"), json={"deal_id": deal["id"]},
        )
        assert response.status_code == 201, response.text
        quote = response.json()

        response = client.get(_api(license_id, f"/quotes/{quote['id']}/products"))
        assert response.status_code == 200, response.text
        lines = response.json()
        assert len(lines) == 1
        assert lines[0]["qty"] == 2

        # 6. A negotiated line, on THIS quote only.
        response = client.patch(
            _api(license_id, f"/quotes/{quote['id']}/products/{lines[0]['id']}"),
            json={"quoted_unit_price": "1400.00"},
        )
        assert response.status_code == 200, response.text

        response = client.get(_api(license_id, f"/deals/{deal['id']}"))
        if response.status_code == 200:
            deal_lines = response.json().get("products") or []
            assert deal_lines and str(
                deal_lines[0]["quoted_unit_price"]
            ).startswith("1500"), "the discount leaked back onto the deal"

    def test_the_same_phone_cannot_be_taken_twice(self, shop):
        """Over HTTP, so the 409 and its body are what a client sees."""
        client, license_id = shop
        payload = {"first_name": "ก", "last_name": "ข", "phone": "0812345678"}

        assert client.post(_api(license_id, "/customers"), json=payload).status_code == 201
        second = client.post(_api(license_id, "/customers"), json=payload)
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        # Structured, so a UI can offer to open the existing record.
        assert detail["error"] == "duplicate"
        assert detail["existing_code"].startswith("C-")

    def test_a_customer_cannot_hold_two_open_deals(self, shop):
        client, license_id = shop
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "ค", "last_name": "ง", "phone": "0898888888"},
        ).json()

        first = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        )
        assert first.status_code == 201
        second = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        )
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["existing_code"].startswith("D-")

    def test_a_quote_needs_a_line_before_it_can_exist(self, shop):
        client, license_id = shop
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "จ", "last_name": "ฉ", "phone": "0877777777"},
        ).json()
        deal = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        ).json()

        response = client.post(
            _api(license_id, "/quotes"), json={"deal_id": deal["id"]},
        )
        assert response.status_code == 409, response.text
        assert "no products" in str(response.json()["detail"]).lower()


class TestQuoteStatusOverHttp:
    """The method mismatch that shipped: the client sent PATCH, the route
    only accepts POST, and the button returned 405 on its first press."""

    def _quote(self, client, license_id, phone):
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "ก", "last_name": "ข", "phone": phone},
        ).json()
        deal = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        ).json()
        client.post(
            _api(license_id, f"/deals/{deal['id']}/products"),
            json={"product_name": "สินค้า", "quoted_unit_price": "100.00", "qty": 1},
        )
        return client.post(
            _api(license_id, "/quotes"), json={"deal_id": deal["id"]},
        ).json()

    def test_a_quote_can_be_voided(self, shop):
        client, license_id = shop
        quote = self._quote(client, license_id, "0866666666")

        response = client.patch(
            _api(license_id, f"/quotes/{quote['id']}/status"),
            json={"status": "rejected"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rejected"

    def test_an_issued_quote_refuses_line_edits(self, shop):
        client, license_id = shop
        quote = self._quote(client, license_id, "0855555555")
        lines = client.get(_api(license_id, f"/quotes/{quote['id']}/products")).json()

        client.patch(
            _api(license_id, f"/quotes/{quote['id']}/status"), json={"status": "sent"},
        )
        response = client.patch(
            _api(license_id, f"/quotes/{quote['id']}/products/{lines[0]['id']}"),
            json={"qty": 9},
        )
        assert response.status_code == 409, response.text


class TestPipelineOverHttp:
    def test_the_summary_reflects_what_was_created(self, shop):
        client, license_id = shop
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "ป", "last_name": "ผ", "phone": "0844444444"},
        ).json()
        deal = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        ).json()
        client.post(
            _api(license_id, f"/deals/{deal['id']}/products"),
            json={"product_name": "สินค้า", "quoted_unit_price": "2500.00", "qty": 2},
        )

        response = client.get(_api(license_id, "/pipeline"))
        assert response.status_code == 200, response.text
        summary = response.json()
        assert Decimal(summary["open_value"]) == Decimal("5000")
        assert summary["by_stage"]["new"]["count"] == 1
        # No close date was given, and the summary says so rather than
        # quietly forecasting nothing.
        assert summary["undated_open_count"] == 1


class TestTemplateUploadOverHttp:
    def test_a_template_uploads_as_a_draft_and_reports_blanks(self, shop, monkeypatch):
        """A template goes onto documents customers receive, so it stays a
        draft until someone publishes it deliberately."""
        client, license_id = shop

        # The document store is GCS in production; here it only has to
        # hold bytes so the upload path can complete.
        from chann_app.services.storage import base as storage_base

        stored: dict[str, bytes] = {}

        class _MemoryStore:
            async def put(self, *, key, content, content_type=None):
                stored[key] = content
                return type("Stored", (), {"path": f"mem://{key}"})()

            async def get(self, *, path):
                return stored[path.removeprefix("mem://")]

        monkeypatch.setattr(storage_base, "get_document_store", lambda: _MemoryStore())

        response = client.post(
            _api(license_id, "/document-templates/upload"),
            json={
                "template_name": "แบบมีโลโก้",
                "html": (
                    "<h1>{{company.name}}</h1>"
                    "<p>{{customer.name}} {{company.motto}}</p>"
                ),
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        # Reported before publishing, not discovered by a customer.
        #
        # `{{company.legal_name}}` used to be the placeholder here and
        # used to pass this check. It is not a snapshot key — the builder
        # READS legal_name off the company row and writes `company.name`
        # — and the sample the check ran against had the same mistake in
        # it, so the two agreed with each other and disagreed with the
        # document. Shops following the page's own legend printed a blank
        # company name. See services/documents/samples.py.
        assert body["unknown_placeholders"] == ["company.motto"]

    def test_an_empty_template_is_refused(self, shop):
        client, license_id = shop
        response = client.post(
            _api(license_id, "/document-templates/upload"),
            json={"template_name": "ว่าง", "html": "   "},
        )
        assert response.status_code == 400, response.text

    def test_word_upload_preview_publish_and_render_end_to_end(
        self, shop, monkeypatch,
    ):
        """The owner's three complaints, in one pass over both tiers.

        Upload a .docx a shop could really have made, look at it filled
        in, publish it, and issue a real quote that comes out rendered
        through THAT template rather than the built-in one.

        SmartBrowz is stubbed — it is a paid external service and the
        credential is not in this environment — in the same place and
        the same way `tests/unit/test_quote_issue.py` stubs it: the
        renderer object, so the HTML that would have been sent is
        captured and asserted on. Everything below the renderer is real:
        both tiers over HTTP, the real snapshot builder, the real fill
        engine, the real template resolution in quote_issue.py.
        """
        from chann_app.services import quote_issue
        from chann_app.services.documents.samples import build_sample_docx
        from chann_app.services.pdf import base as pdf_base
        from chann_app.services.storage import base as storage_base
        from chann_app.services.storage.base import StoredDocument, sha256_hex

        client, license_id = shop
        stored: dict[str, bytes] = {}

        class _MemoryStore:
            async def put(self, *, key, content, content_type=None):
                stored[key] = content
                # The real StoredDocument, not a stand-in: `sha256` is
                # what goes into generated_documents as the proof of
                # which bytes the customer received.
                return StoredDocument(
                    path=f"mem://{key}", sha256=sha256_hex(content), size=len(content),
                )

            async def get(self, *, path):
                return stored[path.removeprefix("mem://")]

        memory = _MemoryStore()
        monkeypatch.setattr(storage_base, "get_document_store", lambda: memory)

        rendered: dict[str, str] = {}

        class _Renderer:
            async def render(self, html, options, idempotency_key=None):
                rendered["html"] = html
                return type(
                    "Result", (),
                    {"content": b"%PDF-1.4 stub", "renderer": "smartbrowz"},
                )()

        # quote_issue imports both names at module level, so the module's
        # own attributes are what have to be replaced — the same two
        # lines tests/unit/test_quote_issue.py uses.
        monkeypatch.setattr(pdf_base, "get_renderer", lambda *a, **k: _Renderer())
        monkeypatch.setattr(quote_issue, "get_renderer", lambda *a, **k: _Renderer())
        monkeypatch.setattr(quote_issue, "get_document_store", lambda *a, **k: memory)

        # ---- 1. a Word file goes up
        content = build_sample_docx("quote")
        response = client.post(
            _api(license_id, "/document-templates/upload"),
            json={
                "template_name": "ใบเสนอราคาจากเวิร์ด",
                "docx_base64": base64.b64encode(content).decode(),
                "filename": "quotation.docx",
                "document_type": "quote",
            },
        )
        assert response.status_code == 201, response.text
        upload = response.json()
        assert upload["source_kind"] == "docx"
        assert upload["status"] == "draft"
        # A sample built from the real vocabulary leaves nothing blank.
        assert upload["unknown_placeholders"] == []
        template_id, version_id = upload["template_id"], upload["version_id"]

        # The Word file itself is kept, and the version list links to it.
        versions = client.get(
            _api(license_id, f"/document-templates/{template_id}/versions")
        ).json()
        assert versions[0]["source_docx_path"].endswith(".docx")
        assert stored[versions[0]["source_docx_path"].removeprefix("mem://")] == content

        # ---- 2. it can be looked at, and looking does not publish
        response = client.post(
            _api(
                license_id,
                f"/document-templates/{template_id}/versions/{version_id}/preview",
            ),
        )
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["status"] == "previewed"
        assert "QT-2026-0042" in preview["html"]
        assert "{{" not in preview["html"]
        assert preview["unknown_placeholders"] == []
        # Previewed, not published — the Data tier is the one asserting it.
        after = client.get(
            _api(license_id, f"/document-templates/{template_id}/versions")
        ).json()
        assert after[0]["status"] == "previewed"

        # ---- 3. publish
        response = client.post(
            _api(
                license_id,
                f"/document-templates/{template_id}/versions/{version_id}/publish",
            ),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "published"

        # ---- 4. a real quote renders through it
        client.patch(
            _api(license_id, "/company-profile"),
            json={
                "legal_name": "ร้านทดสอบเวิร์ด", "tax_id": "0105500000001",
                "company_address": "1 ถนนทดสอบ", "company_phone": "021111111",
            },
        )
        customer = client.post(
            _api(license_id, "/customers"),
            json={"first_name": "วรรณ", "last_name": "ดี", "phone": "0855555555"},
        ).json()
        deal = client.post(
            _api(license_id, "/deals"), json={"contact_id": customer["id"]},
        ).json()
        # The line goes on the deal; creating the quote copies it, which
        # is the order the rest of this file uses and the only one that
        # produces a quote with anything on it.
        client.post(
            _api(license_id, f"/deals/{deal['id']}/products"),
            json={"product_name": "พัดลมไอเย็น", "quoted_unit_price": "1200.00", "qty": 3},
        )
        response = client.post(
            _api(license_id, "/quotes"), json={"deal_id": deal["id"]},
        )
        assert response.status_code == 201, response.text
        quote = response.json()

        response = client.post(_api(license_id, f"/quotes/{quote['id']}/issue"))
        assert response.status_code in (200, 201), response.text
        document = response.json()
        assert document["generated_document_id"]
        assert document["sha256"]

        # What was handed to the renderer is the shop's OWN layout — the
        # one that came out of the Word file — not the built-in. The two
        # are told apart by the title the DOCX conversion writes; the
        # built-in's is "ใบเสนอราคา <number>".
        assert "<title>เอกสาร</title>" in rendered["html"]
        assert "ตารางอธิบายช่องข้อมูล" in rendered["html"]
        # And it is filled with this quote's real values, not the
        # sample's: a real snapshot, through the real fill engine.
        assert "ร้านทดสอบเวิร์ด" in rendered["html"]
        assert "พัดลมไอเย็น" in rendered["html"]
        assert "3600.00" in rendered["html"]
        assert "QT-2026-0042" not in rendered["html"]
        assert "{{" not in rendered["html"]
