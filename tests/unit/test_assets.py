"""Review E2 (6 Sep 2026): stored objects are served by this tier through
one-object, time-limited asset links — never GCS signed URLs, which this
deployment cannot produce."""
from __future__ import annotations

import sys
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_admin  # noqa: E402
from chann_app.auth import document_link  # noqa: E402
from chann_app.config import settings  # noqa: E402
from chann_app.services import assets  # noqa: E402
from chann_app.services.storage import base as storage_base  # noqa: E402


class _Store:
    def __init__(self, objects=None, error=None):
        self.objects = objects or {}
        self.error = error

    async def get(self, *, path):
        if self.error:
            raise self.error
        if path not in self.objects:
            raise storage_base.DocumentStoreError(f"no stored document at {path}")
        return self.objects[path]


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://app.example")


@pytest.fixture
def app(monkeypatch, secret):
    store = _Store({"gs://b/documents/lic/tickets/t1/photos/a.jpg": b"JPEGBYTES"})
    monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: store)
    fastapi_app = FastAPI()
    fastapi_app.include_router(routers_admin.router)
    return fastapi_app, store


class TestTokens:
    def test_round_trip_carries_path_type_and_name(self, secret):
        token = document_link.issue_asset_token("gs://b/x.png", "image/png", 60, filename="sig.png")
        assert document_link.decode_asset_token(token) == ("gs://b/x.png", "image/png", "sig.png")

    def test_a_document_token_is_not_an_asset_token(self, secret):
        token = document_link.issue_document_token("lic", "doc")
        with pytest.raises(document_link.DocumentLinkInvalid):
            document_link.decode_asset_token(token)

    def test_an_asset_token_is_not_a_document_token(self, secret):
        token = document_link.issue_asset_token("gs://b/x.png", "image/png")
        with pytest.raises(document_link.DocumentLinkInvalid):
            document_link.decode_document_token(token)

    def test_expiry_is_enforced(self, secret):
        token = document_link.issue_asset_token("gs://b/x.png", "image/png", ttl_seconds=-1)
        with pytest.raises(document_link.DocumentLinkInvalid):
            document_link.decode_asset_token(token)

    def test_a_token_from_another_secret_is_refused(self, secret):
        token = jwt.encode({"path": "gs://b/x", "ct": "image/png", "purpose": "asset.download"}, "other", algorithm="HS256")
        with pytest.raises(document_link.DocumentLinkInvalid):
            document_link.decode_asset_token(token)


class TestLinks:
    def test_a_link_is_absolute_under_the_public_base(self, secret):
        url = assets.asset_link("gs://b/documents/a.jpg", ttl_seconds=3600)
        assert url.startswith("https://app.example/api/v1/assets/")
        assert document_link.decode_asset_token(url.rsplit("/", 1)[-1])[:2] == ("gs://b/documents/a.jpg", "image/jpeg")

    def test_content_type_comes_from_the_extension_unless_given(self, secret):
        assert assets.content_type_for("gs://b/a.PNG") == "image/png"
        assert assets.content_type_for("gs://b/a.pdf") == "application/pdf"
        assert assets.content_type_for("gs://b/noext") == "application/octet-stream"
        url = assets.asset_link("gs://b/a.bin", content_type="text/csv")
        assert document_link.decode_asset_token(url.rsplit("/", 1)[-1])[1] == "text/csv"

    def test_an_http_path_is_already_a_link(self, secret):
        assert assets.asset_link("https://cdn/x.png") == "https://cdn/x.png"

    def test_no_base_means_none(self, monkeypatch, secret):
        monkeypatch.setattr(settings, "public_base_url", "")
        assert assets.asset_link("gs://b/a.jpg") is None
        assert assets.asset_link("gs://b/a.jpg", base_url="https://req.example/") .startswith("https://req.example/api/v1/assets/")

    async def test_render_image_inlines_when_there_is_no_base(self, monkeypatch, secret):
        monkeypatch.setattr(settings, "public_base_url", "")
        monkeypatch.setattr(assets, "get_document_store", lambda *a, **k: _Store({"gs://b/s.png": b"PNG!"}))
        assert await assets.image_for_render("gs://b/s.png") == "data:image/png;base64,UE5HIQ=="

    async def test_render_image_links_when_there_is_a_base(self, secret):
        assert (await assets.image_for_render("gs://b/s.png")).startswith("https://app.example/api/v1/assets/")


class TestRoute:
    def test_a_valid_link_streams_the_bytes_with_the_right_type(self, app):
        fastapi_app, _ = app
        url = assets.asset_link("gs://b/documents/lic/tickets/t1/photos/a.jpg", ttl_seconds=60)
        response = TestClient(fastapi_app).get(url.replace("https://app.example", ""))
        assert response.status_code == 200
        assert response.content == b"JPEGBYTES"
        assert response.headers["content-type"] == "image/jpeg"
        assert 'filename="a.jpg"' in response.headers["content-disposition"]

    def test_a_forged_or_expired_link_is_404_not_401(self, app):
        fastapi_app, _ = app
        assert TestClient(fastapi_app).get("/api/v1/assets/not-a-token").status_code == 404
        expired = document_link.issue_asset_token("gs://b/documents/lic/tickets/t1/photos/a.jpg", "image/jpeg", ttl_seconds=-1)
        assert TestClient(fastapi_app).get(f"/api/v1/assets/{expired}").status_code == 404

    def test_a_document_token_cannot_fetch_an_asset(self, app):
        fastapi_app, _ = app
        token = document_link.issue_document_token("lic", "doc")
        assert TestClient(fastapi_app).get(f"/api/v1/assets/{token}").status_code == 404

    def test_a_missing_object_is_404(self, app):
        fastapi_app, _ = app
        token = document_link.issue_asset_token("gs://b/gone.jpg", "image/jpeg")
        assert TestClient(fastapi_app).get(f"/api/v1/assets/{token}").status_code == 404

    def test_unconfigured_storage_is_503(self, app, monkeypatch):
        fastapi_app, _ = app
        monkeypatch.setattr(storage_base, "get_document_store", lambda *a, **k: storage_base.NullDocumentStore())
        token = document_link.issue_asset_token("gs://b/x.jpg", "image/jpeg")
        assert TestClient(fastapi_app).get(f"/api/v1/assets/{token}").status_code == 503
