"""The guide as a public file (liff-files-v1): no session, inline HTML
or an attached markdown, per OA."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application"))

from chann_app import routers_admin  # noqa: E402


def _http():
    app = FastAPI()
    app.include_router(routers_admin.router)
    return TestClient(app)


def test_html_is_shown_inline_and_needs_no_token():
    response = _http().get("/api/v1/guides/customer/file?format=html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("inline")
    assert "<h1>" in response.text and 'class="slot"' in response.text


def test_markdown_is_an_attachment():
    response = _http().get("/api/v1/guides/technician/file?format=md")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert "[IMAGE:" in response.text


def test_unknown_audience_is_404():
    assert _http().get("/api/v1/guides/nobody/file").status_code == 404
