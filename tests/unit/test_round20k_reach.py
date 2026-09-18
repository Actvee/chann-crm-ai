"""Round 20k — three things that existed and could not be reached.

`POST /reports/ai/run` accepted an edited spec and nothing ever called it,
so the model's first answer was the only answer a person could get from
the dashboard. `archive_document_template_version` existed in the Data
tier and in the client with no caller at all, so a published layout could
go into use and never come back out. Both are end-to-end route tests,
because the class of bug that hides here is a route that raises the moment
it is actually called (publish passed `template_id` to a client method
that takes `version_id` and had never once run).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for tier in ("application", "data"):
    if str(ROOT / tier) not in sys.path:
        sys.path.insert(0, str(ROOT / tier))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from chann_app import routers_phase2  # noqa: E402
from chann_app.services import reports_ai  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402

LICENSE_ID = "11111111-1111-1111-1111-111111111111"
VERSION_ID = "22222222-2222-2222-2222-222222222222"
TEMPLATE_ID = "33333333-3333-3333-3333-333333333333"


def _http(client, keys=("view_reports", "setting.manage")):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="admin", is_owner=False,
            permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


# ----------------------------------------------------- the spec editor's menu

class TestSpecOptions:
    """The editor may only offer what the validator accepts.

    Restating the whitelist in TypeScript is how `Warranty.purchase_date`
    happened — a field the dashboard believed in and the Data tier had
    never heard of. So the page asks, and this pins that the answer and
    the validator agree.
    """

    def test_every_offered_combination_validates(self):
        options = reports_ai.spec_options("th")
        for entity in options["entities"]:
            for group in [None] + [g["value"] for g in entity["group_by"]]:
                for date_field in [d["value"] for d in entity["date_fields"]]:
                    spec = reports_ai.validate_query_spec({
                        "entity": entity["value"], "metric": "count",
                        "group_by": group, "date_range": "last_30_days",
                        "date_field": date_field,
                    })
                    assert spec["entity"] == entity["value"]
                    assert spec["group_by"] == group

    def test_every_offered_numeric_field_validates(self):
        for entity in reports_ai.spec_options("en")["entities"]:
            for field in entity["numeric_fields"]:
                for metric in ("sum", "avg", "min", "max"):
                    spec = reports_ai.validate_query_spec({
                        "entity": entity["value"], "metric": metric,
                        "field": field["value"],
                    })
                    assert spec["field"] == field["value"]

    def test_every_offered_range_validates(self):
        for rng in reports_ai.spec_options("th")["date_ranges"]:
            spec = reports_ai.validate_query_spec(
                {"entity": "deals", "metric": "count", "date_range": rng["value"]}
            )
            assert spec["date_range"] == rng["value"]

    def test_entities_with_no_numeric_field_offer_none(self):
        # The editor hides sum/avg for these; if the whitelist ever grows
        # one, the page has to learn about it here rather than by a 422.
        by_name = {e["value"]: e for e in reports_ai.spec_options("th")["entities"]}
        assert by_name["customers"]["numeric_fields"] == []
        assert [f["value"] for f in by_name["deals"]["numeric_fields"]] == ["amount"]

    def test_labels_are_not_raw_keys(self):
        # Rule 4: no raw key ever reaches a person's eyes.
        options = reports_ai.spec_options("th")
        for entity in options["entities"]:
            assert entity["label"] != entity["value"]
            for group in entity["group_by"]:
                assert group["label"] != group["value"]
            for field in entity["date_fields"] + entity["numeric_fields"]:
                assert field["label"] != field["value"]

    def test_english_differs_from_thai(self):
        th = reports_ai.spec_options("th")
        en = reports_ai.spec_options("en")
        assert th["entities"][0]["label"] != en["entities"][0]["label"]

    def test_the_route_answers(self):
        class _Client:
            async def aclose(self):
                pass

        http = _http(_Client())
        response = http.get(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/options?language=th"
        )
        assert response.status_code == 200
        assert [e["value"] for e in response.json()["entities"]] == list(
            reports_ai.ALLOWED_ENTITIES
        )

    def test_the_route_needs_the_permission(self):
        class _Client:
            async def aclose(self):
                pass

        http = _http(_Client(), keys=())
        assert http.get(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/options"
        ).status_code == 403


# ------------------------------------------------------- running an edited spec

class _ReportClient:
    def __init__(self):
        self.ran: list[dict] = []

    async def aclose(self):
        pass

    async def run_report_query(self, license_id, spec, actor_id=None):
        self.ran.append(spec)
        return {"entity": spec["entity"], "metric": spec["metric"],
                "group_by": spec["group_by"], "date_range": spec["date_range"],
                "rows": [{"key": "won", "label": "won", "value": 3}], "total": 3}


class TestRunEditedSpec:
    def test_an_edited_range_runs_without_asking_the_model_again(self):
        client = _ReportClient()
        http = _http(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run",
            json={"spec": {"entity": "deals", "metric": "count",
                           "group_by": "stage", "date_range": "this_year"},
                  "language": "th"},
        )
        assert response.status_code == 200, response.text
        assert client.ran[0]["date_range"] == "this_year"
        assert response.json()["spec"]["date_range"] == "this_year"
        assert response.json()["text"]

    def test_a_spec_off_the_whitelist_is_refused_not_run(self):
        client = _ReportClient()
        http = _http(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run",
            json={"spec": {"entity": "payroll", "metric": "count"}},
        )
        assert response.status_code == 422
        assert client.ran == []

    def test_grouping_an_entity_cannot_group_by_is_refused(self):
        client = _ReportClient()
        http = _http(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run",
            json={"spec": {"entity": "customers", "metric": "count",
                           "group_by": "assigned_to"}},
        )
        assert response.status_code == 422
        assert client.ran == []

    def test_it_needs_the_permission(self):
        http = _http(_ReportClient(), keys=())
        assert http.post(
            f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run",
            json={"spec": {"entity": "deals", "metric": "count"}},
        ).status_code == 403


# ------------------------------------------------------ retiring a template version

class _TemplateClient:
    def __init__(self, versions):
        self.versions = versions
        self.archived: list[str] = []

    async def aclose(self):
        pass

    async def list_document_template_versions(self, license_id, template_id):
        return self.versions

    async def archive_document_template_version(self, license_id, version_id, actor_id=None):
        self.archived.append(version_id)
        return {"id": version_id, "status": "archived"}


class TestArchiveTemplateVersion:
    def test_it_archives(self):
        client = _TemplateClient([{"id": VERSION_ID, "version": 2, "status": "published"}])
        http = _http(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/document-templates/{TEMPLATE_ID}"
            f"/versions/{VERSION_ID}/archive"
        )
        assert response.status_code == 200, response.text
        assert client.archived == [VERSION_ID]
        assert response.json()["status"] == "archived"

    def test_a_version_of_another_template_is_not_reachable(self):
        # The same guard publish needed: the Data tier addresses a version
        # by its own id, so the template in the path proves nothing.
        client = _TemplateClient([{"id": "44444444-4444-4444-4444-444444444444",
                                   "version": 1, "status": "published"}])
        http = _http(client)
        response = http.post(
            f"/api/v1/licenses/{LICENSE_ID}/document-templates/{TEMPLATE_ID}"
            f"/versions/{VERSION_ID}/archive"
        )
        assert response.status_code == 404
        assert client.archived == []

    def test_it_needs_setting_manage(self):
        client = _TemplateClient([{"id": VERSION_ID, "version": 1, "status": "published"}])
        http = _http(client, keys=("view_reports",))
        assert http.post(
            f"/api/v1/licenses/{LICENSE_ID}/document-templates/{TEMPLATE_ID}"
            f"/versions/{VERSION_ID}/archive"
        ).status_code == 403
        assert client.archived == []


class TestWhatRendersAfterArchiving:
    """The sentence the confirmation shows has to be true.

    `usable_version` takes the highest PUBLISHED version, so archiving it
    hands over to the next published one — or to the built-in layout when
    there is none. The page says which; these pin the rule it says it by.
    """

    def test_the_next_published_version_takes_over(self):
        from chann_app.services.documents import selection

        left = [{"id": "a", "version": 1, "status": "published",
                 "compiled_template_path": "gs://x/1.html"}]
        assert selection.usable_version(left)["version"] == 1

    def test_with_none_left_it_falls_back_to_the_builtin(self):
        from chann_app.services.documents import selection

        left = [{"id": "a", "version": 1, "status": "archived",
                 "compiled_template_path": "gs://x/1.html"}]
        assert selection.usable_version(left) is None
