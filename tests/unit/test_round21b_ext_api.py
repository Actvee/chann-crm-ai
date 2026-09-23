"""Round 21B — the external API: key resolver, ext routes, client calls."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.data_client import DataClient  # noqa: E402
from test_phase6_chat import FakeDataClient, LICENSE_ID  # noqa: E402


def _client_recording(status_code=200, body=None):
    """A real DataClient over a MockTransport that records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=body if body is not None else {})

    client = DataClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://data")
    client._base = "http://data"
    return client, seen


class TestTheClient:
    async def test_the_four_key_calls_hit_the_data_routes(self):
        client, seen = _client_recording(201, {"id": "k1", "key": "chann_live_x"})
        await client.create_api_key("L1", {"name": "ERP", "created_by_chann_uid": "CHN-OWNER"}, actor_id="CHN-OWNER")
        client, seen2 = _client_recording(200, [])
        await client.list_api_keys("L1")
        client, seen3 = _client_recording(200, {"id": "k1"})
        await client.revoke_api_key("L1", "k1", actor_id="CHN-OWNER")
        client, seen4 = _client_recording(404, {"detail": "api key not found"})
        assert await client.resolve_api_key("0" * 64) is None
        assert (seen[0].method, seen[0].url.path) == ("POST", "/internal/v1/licenses/L1/api-keys")
        assert seen[0].headers["X-Actor-Id"] == "CHN-OWNER"
        assert (seen2[0].method, seen2[0].url.path) == ("GET", "/internal/v1/licenses/L1/api-keys")
        assert (seen3[0].method, seen3[0].url.path) == ("POST", "/internal/v1/licenses/L1/api-keys/k1/revoke")
        assert (seen4[0].method, seen4[0].url.path) == ("POST", "/internal/v1/api-keys/resolve")
        assert json.loads(seen4[0].content) == {"key_hash": "0" * 64}

    async def test_updated_since_rides_as_an_iso_query_param(self):
        stamp = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
        for name in ("list_customers_with_total", "list_deals_with_total",
                     "list_tickets_with_total", "list_invoices_with_total"):
            client, seen = _client_recording(200, [])
            await getattr(client, name)("L1", updated_since=stamp)
            assert seen[0].url.params["updated_since"] == "2026-09-23T00:00:00+00:00", name


from chann_app.auth import api_key as api_key_auth  # noqa: E402


class _ResolvingFake(FakeDataClient):
    """A FakeDataClient that answers resolve_api_key from a table."""
    def __init__(self, *, resolved=None, **kw):
        super().__init__(**kw)
        self._resolved = resolved
        self.resolve_calls: list[str] = []

    async def resolve_api_key(self, key_hash):
        self.resolve_calls.append(key_hash)
        return self._resolved


RAW = "chann_live_" + "A" * 32
RESOLVED = {
    "key": {"id": "11111111-1111-1111-1111-111111111111", "license_id": LICENSE_ID, "name": "ERP",
            "key_prefix": "chann_live_AAAA", "created_by_chann_uid": "CHN-OWNER",
            "last_used_at": None, "revoked_at": None, "created_at": "2026-09-23T00:00:00+00:00"},
    "license_status": "active", "permission_keys": ["customer.read", "invoice.create"],
    "limit": 600, "remaining": 599,
}


def _probe_app(fake):
    from fastapi import Depends

    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request, principal=Depends(api_key_auth.api_principal)):
        return {"license_id": principal.license_id, "chann_uid": principal.chann_uid,
                "audience": principal.audience, "keys": sorted(principal.permission_keys),
                "rate": list(request.state.rate_limit)}

    @app.post("/probe")
    async def probe_write(principal=Depends(api_key_auth.api_principal)):
        return {"ok": True}

    async def override():
        yield fake

    from chann_app.routers_admin import get_data_client
    app.dependency_overrides[get_data_client] = override
    return TestClient(app)


class TestTheResolver:
    def test_the_hash_matches_the_data_tier(self):
        import hashlib
        assert api_key_auth.hash_api_key(RAW) == hashlib.sha256(RAW.encode()).hexdigest()

    def test_no_header_or_wrong_shape_is_401_and_never_asks_data(self):
        fake = _ResolvingFake(resolved=RESOLVED)
        http = _probe_app(fake)
        assert http.get("/probe").status_code == 401
        assert http.get("/probe", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert http.get("/probe", headers={"Authorization": RAW}).status_code == 401
        assert fake.resolve_calls == []
        body = http.get("/probe").json()
        assert body["detail"]["code"] == "missing_or_malformed_key"

    def test_non_alphanumeric_key_body_is_401_and_never_asks_data(self):
        fake = _ResolvingFake(resolved=RESOLVED)
        http = _probe_app(fake)
        bad_key = "chann_live_" + "!" * 32
        r = http.get("/probe", headers={"Authorization": f"Bearer {bad_key}"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "missing_or_malformed_key"
        assert fake.resolve_calls == []

    def test_a_good_key_becomes_an_api_principal_with_the_staff_set(self):
        fake = _ResolvingFake(resolved=RESOLVED)
        http = _probe_app(fake)
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 200, r.text
        assert r.json() == {"license_id": LICENSE_ID, "chann_uid": "api:11111111-1111-1111-1111-111111111111",
                            "audience": "api", "keys": ["customer.read", "invoice.create"], "rate": [600, 599]}
        assert fake.resolve_calls == [api_key_auth.hash_api_key(RAW)]

    def test_unknown_or_revoked_is_401(self):
        http = _probe_app(_ResolvingFake(resolved=None))
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "unknown_or_revoked_key"

    def test_over_the_window_is_429_with_retry_after(self):
        http = _probe_app(_ResolvingFake(resolved={**RESOLVED, "remaining": -1}))
        r = http.get("/probe", headers={"Authorization": f"Bearer {RAW}"})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
        assert 1 <= int(r.headers["Retry-After"]) <= 60

    def test_a_suspended_shop_reads_but_does_not_write(self):
        http = _probe_app(_ResolvingFake(resolved={**RESOLVED, "license_status": "suspended"}))
        assert http.get("/probe", headers={"Authorization": f"Bearer {RAW}"}).status_code == 200
        assert http.post("/probe", headers={"Authorization": f"Bearer {RAW}"}).status_code == 423


from chann_app import routers_ext  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402


@pytest.fixture(autouse=True)
def _no_leaked_ext_overrides():
    """`_ext()` overrides dependencies on the MODULE-LEVEL `ext_app`.

    Nothing here owns that object: it is the app the real process serves
    and the one `tests/boundary/test_tier_boundaries.py::TestExternalApi`
    asserts against. Left in place, the override of `api_principal`
    followed the app into the boundary run — in gate order
    (`pytest tests/unit tests/boundary`) every ext route answered 200
    without a key and the "refuses a request without a key" test failed
    (round 21B review C1).
    """
    yield
    routers_ext.ext_app.dependency_overrides.clear()

ALL_STAFF = ["customer.read", "customer.create", "customer.update", "deal.read", "deal.create",
             "deal.update", "quote.read", "invoice.read", "invoice.create", "invoice.update",
             "ticket.read", "ticket.create", "warranty.read", "warranty.create",
             "product.read", "product.manage"]


def _ext(keys=ALL_STAFF, fake=None):
    """The ext app with the resolver replaced by a ready-made api principal."""
    client = fake or FakeDataClient(role="sales", permission_keys=list(keys))

    async def override_client():
        yield client

    async def override_principal(request: Request):
        # Mirrors what the real api_principal (auth/api_key.py) stamps on
        # request.state after resolving the key, so /me's read of it is
        # exercised the same way it is in production.
        request.state.api_key = {"id": "k1", "name": "ERP", "key_prefix": "chann_live_AAAA"}
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="api:k1", role="api", is_owner=False,
            permission_keys=frozenset(keys), audience="api",
        )

    from chann_app.routers_admin import get_data_client
    routers_ext.ext_app.dependency_overrides[get_data_client] = override_client
    routers_ext.ext_app.dependency_overrides[api_key_auth.api_principal] = override_principal
    return TestClient(routers_ext.ext_app), client


class TestExtCustomersAndDeals:
    def test_me_names_the_shop_and_the_permissions(self):
        http, _ = _ext()
        r = http.get("/me")
        assert r.status_code == 200, r.text
        assert r.json()["shop"]["license_id"] == LICENSE_ID
        assert r.json()["key"] == {"id": "k1", "name": "ERP", "key_prefix": "chann_live_AAAA"}
        assert "customer.read" in r.json()["permissions"]

    def test_lists_are_items_plus_total_with_the_header(self):
        http, client = _ext()
        client._customers = [
            {"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "last_name": "ใจดี",
             "phone": "0812345678", "stage": "contact"},
            {"id": "c2", "customer_id": "C-2026-0002", "first_name": "สมหญิง", "last_name": "ดีใจ",
             "phone": "0898765432", "stage": "lead"},
        ]
        r = http.get("/customers?limit=1")
        assert r.status_code == 200, r.text
        assert r.headers["X-Total-Count"] == "2"
        assert r.json()["total"] == 2 and len(r.json()["items"]) == 1

    def test_limit_over_200_is_a_validation_error_in_the_ext_shape(self):
        http, _ = _ext()
        r = http.get("/customers?limit=201")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_a_missing_permission_is_forbidden_in_the_ext_shape(self):
        http, _ = _ext(keys=["deal.read"])
        r = http.get("/customers")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    def test_get_create_patch_customer(self):
        http, client = _ext()
        made = http.post("/customers", json={"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        assert made.status_code == 201, made.text
        # An ERP-created customer is a customer, not a lead sitting
        # unconfirmed until someone opens the dashboard.
        assert made.json()["stage"] == "contact"
        assert any(w[0] == "promote_customer" for w in client.recorded), client.recorded
        cid = made.json()["id"]
        assert http.get(f"/customers/{cid}").status_code == 200
        assert http.get("/customers/nope").status_code == 404
        assert http.get("/customers/nope").json()["error"]["code"] == "not_found"
        patched = http.patch(f"/customers/{cid}", json={"email": "a@b.co"})
        assert patched.status_code == 200
        assert [w for w in client.recorded if w[0] == "update_customer"], client.recorded
        # The audit actor is the key, not a person.
        assert any(w[0] == "create_customer" and w[-1] == "api:k1" for w in client.recorded)

    def test_a_customer_created_as_a_lead_skips_the_promote(self):
        http, client = _ext()
        made = http.post("/customers", json={"first_name": "สมหญิง", "phone": "0898765432", "stage": "lead"})
        assert made.status_code == 201, made.text
        assert made.json()["stage"] == "lead"
        assert not any(w[0] == "promote_customer" for w in client.recorded), client.recorded

    def test_deal_create_lines_and_stage(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        deal = http.post("/deals", json={"customer_id": customer["id"], "amount": "1500.00"})
        assert deal.status_code == 201, deal.text
        did = deal.json()["id"]
        got = http.get(f"/deals/{did}")
        assert got.status_code == 200 and "items" in got.json()
        moved = http.post(f"/deals/{did}/stage", json={"stage": "lost", "lost_reason": "ราคา"})
        assert moved.status_code == 200, moved.text
        assert http.get("/deals?updated_since=not-a-date").status_code == 422

    def test_a_refused_stage_move_is_409_in_the_ext_shape(self):
        from chann_app.data_client import DataTierError

        class _RefusingStage(FakeDataClient):
            async def transition_deal_stage(self, license_id, deal_id, stage, *,
                                            allow_reopen=False, actor_id=None, lost_reason=None):
                raise DataTierError(409, "cannot move a deal from new to won")

        fake = _RefusingStage(role="sales", permission_keys=list(ALL_STAFF))
        http, client = _ext(fake=fake)
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        deal = http.post("/deals", json={"customer_id": customer["id"], "amount": "1500.00"}).json()
        r = http.post(f"/deals/{deal['id']}/stage", json={"stage": "won"})
        assert r.status_code == 409, r.text
        assert r.json() == {"error": {"code": "conflict", "message": "cannot move a deal from new to won"}}

    def test_a_customer_created_through_the_api_asks_the_sales_rule(self):
        """Every other road that creates a customer asks sales_dispatch
        afterwards — the dashboard form, the CSV import, chat, onboarding.
        The API did not, so an ERP-pushed customer belonged to nobody
        (round 21B review C2)."""
        http, client = _ext()
        client._assignment_rules = [{"id": "r1", "scope": "sales", "is_active": True}]
        client._assignment_outcome = {"member_id": "m9", "reason": "least_load"}
        made = http.post("/customers", json={"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"})
        assert made.status_code == 201, made.text
        asked = [r for r in client.recorded if r[0] == "execute_assignment"]
        assert asked, client.recorded
        request = client.assignment_requests[-1]
        assert request["scope"] == "sales" and request["entity_type"] == "customer"
        assert request["context"]["customer"]["source"] == "api"

    def test_a_routing_failure_does_not_undo_the_customer(self):
        class _Exploding(FakeDataClient):
            async def get_assignment_rules(self, license_id):
                raise RuntimeError("assignment rules are down")

        http, client = _ext(fake=_Exploding(role="sales", permission_keys=list(ALL_STAFF)))
        made = http.post("/customers", json={"first_name": "สมหญิง", "phone": "0898765432"})
        assert made.status_code == 201, made.text

    def test_updated_since_without_an_offset_is_read_as_utc(self):
        """docs/API.md §5: a naive value is UTC, decided here rather than
        guessed by every tier below (round 21B review I4)."""
        http, client = _ext()
        r = http.get("/tickets?updated_since=2026-09-23T00:00:00")
        assert r.status_code == 200, r.text
        seen = client.tickets_updated_since
        assert seen is not None and seen.tzinfo is not None
        assert seen.utcoffset() == timezone.utc.utcoffset(None)
        assert seen == datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)

    def test_a_suspended_shop_reads_a_sentence_not_just_the_code(self):
        """423's body used to read `"message": "tenant_suspended"` — the
        code twice, no sentence for the ERP's operator (review I5)."""
        from chann_app.services.authorization import refuse_if_suspended

        async def override_principal():
            refuse_if_suspended("suspended", "POST")

        http, _ = _ext()
        routers_ext.ext_app.dependency_overrides[api_key_auth.api_principal] = override_principal
        r = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"})
        assert r.status_code == 423, r.text
        body = r.json()["error"]
        assert body["code"] == "tenant_suspended"
        assert body["message"] and body["message"] != "tenant_suspended"

    def test_a_dict_detail_with_no_message_reads_its_own_code_not_a_python_repr(self):
        from fastapi import HTTPException

        async def override_principal():
            raise HTTPException(status_code=423, detail={"error": "tenant_suspended"})

        http, _ = _ext()
        routers_ext.ext_app.dependency_overrides[api_key_auth.api_principal] = override_principal
        r = http.get("/me")
        assert r.status_code == 423
        assert r.json() == {"error": {"code": "tenant_suspended", "message": "tenant_suspended"}}


class TestExtBilling:
    def _billable(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        deal = http.post("/deals", json={"customer_id": customer["id"]}).json()
        return http, client, customer, deal

    def test_an_invoice_from_a_deal_without_lines_is_a_409_in_the_ext_shape(self):
        http, client, customer, deal = self._billable()
        r = http.post("/invoices", json={"deal_id": deal["id"]})
        assert r.status_code in (409, 422), r.text
        # The stable taxonomy code, not the deal id `_invoice_document_error`
        # also stuffs into `detail["code"]` for a dashboard to say "on D-…".
        assert r.json()["error"]["code"] == "invoice_state"

    def test_a_payment_on_a_draft_invoice_is_409_invoice_state_not_the_invoice_id(self):
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "draft", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None}]
        r = http.post("/invoices/INV-A/payments", json={"full": True})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "invoice_state"

    def test_invoice_list_get_and_pdf_link(self, monkeypatch):
        from chann_app.config import settings

        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None,
                             "generated_document_id": "doc-1", "receipt_document_id": None}]
        listed = http.get("/invoices?status=issued")
        assert listed.status_code == 200 and listed.json()["total"] == 1
        one = http.get("/invoices/INV-A")
        assert one.status_code == 200 and one.json()["invoice_id"] == "INV-2026-0001"
        pdf = http.get("/invoices/INV-A/pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.json()["url"].startswith("http") and "expires_at" in pdf.json()
        assert http.get("/invoices/INV-A/receipt-pdf").status_code == 404

    def test_a_payment_needs_an_amount_unless_full(self):
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None}]
        r = http.post("/invoices/INV-A/payments", json={"method": "transfer"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"

    def test_without_invoice_read_the_list_is_forbidden(self):
        http, _ = _ext(keys=["customer.read"])
        assert http.get("/invoices").status_code == 403

    def test_a_full_payment_needs_no_amount_and_settles_the_invoice(self):
        http, client = _ext()
        client._invoices = [{"id": "INV-A", "invoice_id": "INV-2026-0001", "status": "issued", "total": "100.00",
                             "paid_amount": "0.00", "outstanding": "100.00", "is_overdue": False,
                             "contact_id": "c1", "deal_id": "d1", "quote_id": None}]
        r = http.post("/invoices/INV-A/payments", json={"full": True, "method": "cash"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "paid"
        assert body["paid_amount"] == "100.00"

    def test_issuing_checks_invoice_update_before_writing_the_draft(self):
        """create-but-not-update must not leave an orphan draft on record."""
        http, client, customer, deal = self._billable()
        restricted, _ = _ext(keys=["invoice.create", "deal.read", "customer.read"], fake=client)
        r = restricted.post("/invoices", json={"deal_id": deal["id"], "issue": True})
        assert r.status_code == 403, r.text
        assert not any(rec[0] == "create_invoice" for rec in client.recorded)

    def test_quote_list_detail_and_pdf_link(self):
        http, client = _ext()
        client._customers = [{"id": "c1", "customer_id": "C-2026-0001", "first_name": "สมชาย", "phone": "0812345678"}]
        client._deals = [{"id": "d1", "deal_id": "D-2026-0001", "contact_id": "c1", "stage": "new",
                          "products": [{"id": "p1", "product_name": "แอร์ 12000 BTU", "qty": 1,
                                        "quoted_unit_price": "15000.00"}]}]
        client._quotes = [{"id": "q1", "quote_id": "Q-2026-0001", "deal_id": "d1", "status": "sent",
                           "generated_document_id": None}]
        listed = http.get("/quotes?deal_id=d1")
        assert listed.status_code == 200 and listed.json()["total"] == 1
        one = http.get("/quotes/q1")
        assert one.status_code == 200, one.text
        body = one.json()
        assert body["quote"]["quote_id"] == "Q-2026-0001"
        assert body["deal"]["id"] == "d1"
        assert body["customer"]["id"] == "c1"
        assert body["items"] == client._deals[0]["products"]
        pdf = http.get("/quotes/q1/pdf")
        assert pdf.status_code == 404, pdf.text
        assert pdf.json()["error"]["code"] == "not_issued"


class TestExtServiceAndCatalogue:
    def test_tickets_list_get_create(self):
        http, client = _ext()
        customer = http.post("/customers", json={"first_name": "สมชาย", "phone": "0812345678"}).json()
        made = http.post("/tickets", json={"customer_id": customer["id"], "problem": "แอร์ไม่เย็น"})
        assert made.status_code == 201, made.text
        assert http.get("/tickets").status_code == 200
        assert http.get(f"/tickets/{made.json()['id']}").status_code == 200
        assert http.get("/tickets/nope").status_code == 404

    def test_ticket_create_maps_ext_fields_onto_the_dashboard_payload(self):
        http, client = _ext()
        made = http.post("/tickets", json={
            "customer_id": "c1", "problem": "แอร์ไม่เย็น", "address": "123 ถ.สุขุมวิท",
            "appointment_at": "2026-09-25T09:30:00+07:00",
        })
        assert made.status_code == 201, made.text
        _, _, payload, actor_id = next(r for r in client.recorded if r[0] == "create_ticket")
        assert payload["contact_id"] == "c1"
        assert payload["issue_description"] == "แอร์ไม่เย็น"
        assert payload["service_address"] == "123 ถ.สุขุมวิท"
        assert payload["scheduled_date"] == "2026-09-25"
        assert payload["scheduled_time"] == "09:30"
        assert actor_id == "api:k1"

    def test_a_ticket_filed_through_the_api_tells_the_dispatchers(self):
        """The dashboard's ticket create announces the job; the API's did
        not, so an ERP-filed fault sat in the database until somebody
        opened the dashboard (round 21B review C2)."""
        # The fake answers authorization_context from its own key set, so
        # the dispatcher's ticket.assign lives there, not on the row.
        fake = FakeDataClient(role="cs", permission_keys=list(ALL_STAFF) + ["ticket.assign"])
        http, client = _ext(fake=fake)
        client._members = [
            {"id": "cs-1", "chann_uid": "CHN-CS", "role": "cs", "status": "active"},
        ]
        client._line_targets = {"CHN-CS": "U-cs"}
        made = http.post("/tickets", json={"customer_id": "c1", "problem": "แอร์ไม่เย็น"})
        assert made.status_code == 201, made.text
        sent = [r for r in client.recorded if r[0] == "create_notification"]
        assert sent, client.recorded

    def test_a_notification_failure_does_not_undo_the_ticket(self):
        class _Exploding(FakeDataClient):
            async def list_members(self, license_id, **kwargs):
                raise RuntimeError("members are down")

        http, client = _ext(fake=_Exploding(role="sales", permission_keys=list(ALL_STAFF)))
        made = http.post("/tickets", json={"customer_id": "c1", "problem": "แอร์ไม่เย็น"})
        assert made.status_code == 201, made.text

    def test_ticket_create_needs_ticket_create(self):
        http, _ = _ext(keys=["ticket.read"])
        r = http.post("/tickets", json={"customer_id": "c1", "problem": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    def test_ticket_list_needs_ticket_read_and_forwards_filters(self):
        http, client = _ext()
        http.post("/tickets", json={"customer_id": "c1", "problem": "แอร์ไม่เย็น"})
        r = http.get("/tickets?status=open&customer_id=c1&q=แอร์")
        assert r.status_code == 200 and r.json()["total"] == 1
        forbidden, _ = _ext(keys=["ticket.create"], fake=client)
        assert forbidden.get("/tickets").status_code == 403

    def test_warranties_list_and_register(self):
        http, client = _ext()
        made = http.post("/warranties", json={"serial_number": "SN-ERP-1", "product_id": None})
        assert made.status_code == 201, made.text
        assert http.get("/warranties?q=SN-ERP").status_code == 200

    def test_warranty_create_maps_customer_id_and_purchase_date(self):
        http, client = _ext()
        made = http.post("/warranties", json={
            "serial_number": "SN-2", "customer_id": "c9",
            "purchase_date": "2026-01-01", "warranty_months": 24,
        })
        assert made.status_code == 201, made.text
        _, _, payload, _ = next(r for r in client.recorded if r[0] == "register_warranty")
        assert payload["contact_id"] == "c9"
        assert payload["warranty_start"] == "2026-01-01"
        assert payload["warranty_months"] == 24
        restricted, _ = _ext(keys=["warranty.read"], fake=client)
        assert restricted.post("/warranties", json={"serial_number": "SN-3"}).status_code == 403

    def test_warranty_get_by_id_and_404(self):
        http, client = _ext()
        made = http.post("/warranties", json={"serial_number": "SN-9"})
        wid = made.json()["id"]
        assert http.get(f"/warranties/{wid}").status_code == 200
        assert http.get("/warranties/nope").status_code == 404

    def test_products_list_and_create_need_the_manage_key_to_write(self):
        http, client = _ext(keys=["product.read"])
        assert http.get("/products").status_code == 200
        assert http.post("/products", json={"name": "แอร์", "unit_price": "15900"}).status_code == 403

    def test_creating_a_product_twice_is_a_conflict_not_a_silent_overwrite(self):
        """upsert_product is a PUT keyed by the shop's own code, so a
        repeated POST used to rewrite the row and still answer 201
        (round 21B review I3). PATCH is the edit road."""
        http, client = _ext()
        first = http.post("/products", json={"name": "แอร์ 12000 BTU", "product_id": "AC-12000",
                                             "unit_price": "15900"})
        assert first.status_code == 201, first.text
        again = http.post("/products", json={"name": "ชื่ออื่น", "product_id": "AC-12000",
                                             "unit_price": "1"})
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "conflict"
        assert "AC-12000" in again.json()["error"]["message"]
        # and the row the ERP would have trampled is untouched
        still = http.get("/products/AC-12000").json()
        assert still["product_name"] == "แอร์ 12000 BTU"
        assert still["unit_price"] == "15900"

    def test_product_create_get_and_patch(self):
        http, client = _ext()
        made = http.post("/products", json={"name": "แอร์ 12000 BTU", "sku": "AC-12000", "unit_price": "15900"})
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["product_name"] == "แอร์ 12000 BTU"
        pid = body["id"]
        assert http.get(f"/products/{pid}").status_code == 200
        assert http.get("/products/nope").status_code == 404
        patched = http.patch(f"/products/{pid}", json={"unit_price": "16900"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["unit_price"] == "16900"
        assert patched.json()["product_name"] == "แอร์ 12000 BTU"


from chann_app import routers_phase2  # noqa: E402


class _KeyFake(FakeDataClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._api_keys: list[dict] = []

    async def create_api_key(self, license_id, payload, actor_id=None):
        self.recorded.append(("create_api_key", license_id, payload, actor_id))
        row = {"id": f"k{len(self._api_keys) + 1}", "license_id": license_id, "name": payload["name"],
               "key_prefix": "chann_live_ab12", "created_by_chann_uid": actor_id, "last_used_at": None,
               "revoked_at": None, "created_at": "2026-09-23T00:00:00+00:00"}
        self._api_keys.append(row)
        return {**row, "key": "chann_live_" + "ab12" + "x" * 28}

    async def list_api_keys(self, license_id):
        self.recorded.append(("list_api_keys", license_id))
        return list(self._api_keys)

    async def revoke_api_key(self, license_id, key_id, actor_id=None):
        self.recorded.append(("revoke_api_key", license_id, key_id, actor_id))
        for row in self._api_keys:
            if row["id"] == key_id:
                row["revoked_at"] = "2026-09-23T01:00:00+00:00"
                return row
        from chann_app.data_client import DataTierError
        raise DataTierError(404, "api key not found")


def _liff(is_owner=True, keys=("setting.manage",)):
    client = _KeyFake(role="sales", permission_keys=list(keys))

    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(license_id=LICENSE_ID, chann_uid="CHN-OWNER", role="owner",
                               is_owner=is_owner, permission_keys=frozenset(keys), audience="sales")

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app), client


class TestOwnerKeyRoutes:
    def test_the_owner_makes_sees_and_revokes_a_key(self, monkeypatch):
        from chann_app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "https://app.example")
        http, client = _liff()
        made = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert made.status_code == 201, made.text
        assert made.json()["key"].startswith("chann_live_")
        listed = http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys")
        assert listed.status_code == 200
        assert listed.json()["docs_url"] == "https://app.example/api/ext/v1/docs"
        assert [k["id"] for k in listed.json()["keys"]] == ["k1"] and "key" not in listed.json()["keys"][0]
        gone = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys/k1/revoke")
        assert gone.status_code == 200 and gone.json()["revoked_at"]
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").json()["keys"] == []

    def test_an_admin_with_setting_manage_but_not_owner_is_refused(self):
        http, client = _liff(is_owner=False)
        r = http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert r.status_code == 403 and r.json()["detail"]["reason_code"] == "owner_only"
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").status_code == 403
        assert client.recorded == []

    def test_without_setting_manage_it_is_the_ordinary_403(self):
        http, _ = _liff(keys=("customer.read",))
        assert http.get(f"/api/v1/licenses/{LICENSE_ID}/api-keys").status_code == 403

    def test_a_blank_name_is_422(self):
        http, _ = _liff()
        assert http.post(f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "   "}).status_code == 422
