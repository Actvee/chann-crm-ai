"""Word templates: converting one, looking at one, and starting from one.

The owner's report, 9 Sep 2026, was three complaints in one sentence —
a template that cannot be opened to look at, no sample file to download,
and an upload that only takes HTML. This covers all three, plus the bug
found while fixing them: the placeholder vocabulary the page advertised
(`{{company.legal_name}}`, `{{item.name}}`) is not the vocabulary the
snapshot builder produces, so a shop following the instructions got a
quotation with a blank company name and no warning anywhere.
"""
from __future__ import annotations

import base64
import io
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app import routers_admin, routers_phase2  # noqa: E402
from chann_app.services.authorization import TenantPrincipal  # noqa: E402
from chann_app.services.documents.docx import (  # noqa: E402
    MAX_DOCX_BYTES,
    DocxConversionError,
    convert_docx_to_html,
    polish_converted_html,
)
from chann_app.services.documents.fill import (  # noqa: E402
    fill_template,
    placeholders_in,
    unknown_placeholders,
)
from chann_app.services.documents.samples import (  # noqa: E402
    LEGEND,
    LINE_ITEM_LEGEND,
    SAMPLE_DOCUMENT_TYPES,
    build_sample_docx,
    placeholders_used_by_samples,
    sample_snapshot,
)

LICENSE_ID = "11111111-1111-1111-1111-111111111111"
TEMPLATE_ID = "22222222-2222-2222-2222-222222222222"
VERSION_ID = "33333333-3333-3333-3333-333333333333"


# ------------------------------------------------------- a real .docx
#
# Built here rather than checked in: the point of these tests is that a
# file Word would actually produce survives the trip, and a fixture in
# the repo would freeze one shape of it forever.

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(body_xml: str, *, parts: dict[str, str] | None = None) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body_xml}</w:body></w:document>'
    )
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        "word/document.xml": document,
    }
    files.update(parts or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _p(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def _r(text: str, *, bold: bool = False) -> str:
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


class TestConvertingAWordFile:
    def test_a_placeholder_typed_in_word_survives_the_conversion(self):
        html = convert_docx_to_html(
            _docx(_p(_r("เรียน {{customer.name}}"))), filename="q.docx",
        )
        assert "{{customer.name}}" in html
        assert "customer.name" in placeholders_in(html)

    def test_a_placeholder_word_split_across_runs_is_rejoined(self):
        """Word breaks a typed word into runs whenever anything about it
        changes — a proofing mark, a Thai/Latin switch, one bolded
        character. Every one of those arrives as its own element, and
        fill.py's regex correctly does not match the pieces."""
        html = convert_docx_to_html(
            _docx(_p(_r("{{cus"), _r("tomer", bold=True), _r(".name}}"))),
            filename="q.docx",
        )
        assert "{{customer.name}}" in html
        assert "customer.name" in placeholders_in(html)

    def test_a_table_survives_as_a_table(self):
        table = (
            "<w:tbl>"
            f"<w:tr><w:tc>{_p(_r('รายการ'))}</w:tc><w:tc>{_p(_r('ราคา'))}</w:tc></w:tr>"
            f"<w:tr><w:tc>{_p(_r('พัดลม'))}</w:tc><w:tc>{_p(_r('1500'))}</w:tc></w:tr>"
            "</w:tbl>" + _p(_r(""))
        )
        html = convert_docx_to_html(_docx(table), filename="q.docx")
        assert html.count("<tr>") == 2
        assert "พัดลม" in html and "1500" in html

    def test_line_item_markers_typed_into_cells_wrap_the_whole_row(self):
        """Someone building a quotation in Word puts {{#line_items}} in
        the first cell of the row that should repeat. Left there, the
        block repeats the cell CONTENTS and every quote prints one row."""
        table = (
            "<w:tbl>"
            "<w:tr>"
            f"<w:tc>{_p(_r('{{#line_items}}{{item.index}}'))}</w:tc>"
            f"<w:tc>{_p(_r('{{item.product_name}}'))}</w:tc>"
            f"<w:tc>{_p(_r('{{item.line_total}}{{/line_items}}'))}</w:tc>"
            "</w:tr>"
            "</w:tbl>" + _p(_r(""))
        )
        html = convert_docx_to_html(_docx(table), filename="q.docx")
        assert "{{#line_items}}<tr>" in html
        assert "</tr>{{/line_items}}" in html

        filled = fill_template(html, sample_snapshot("quote"))
        # Two line items in the sample, so two rows — not one row with
        # the contents doubled inside it.
        assert filled.count("<tr>") == 2

    def test_the_result_is_a_complete_document_with_a_thai_font(self):
        """A fragment renders as an unstyled page with no Thai font, and
        the failure is silent: the PDF is valid and full of boxes."""
        html = convert_docx_to_html(_docx(_p(_r("สวัสดี"))), filename="q.docx")
        assert html.startswith("<!DOCTYPE html>")
        assert "Noto Sans Thai" in html
        assert "@page" in html

    def test_the_polish_step_does_not_swallow_text_after_a_stray_brace(self):
        """A "{{" someone typed in prose must not consume the rest of the
        document looking for a closing pair."""
        out = polish_converted_html("<p>ราคา {{ พิเศษ</p><p>{{customer.name}}</p>")
        assert "ราคา {{ พิเศษ" in out
        assert "{{customer.name}}" in out


class TestRefusingAFileTheShopCannotUse:
    def test_the_old_doc_format_says_to_save_as_docx(self):
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", filename="q.doc")
        assert ".docx" in caught.value.message_th
        assert "Save As" in caught.value.message_th or "บันทึกเป็น" in caught.value.message_th

    def test_a_password_protected_docx_is_named_as_such(self):
        """An encrypted .docx is an OLE container, exactly like a .doc —
        only the filename tells the two apart."""
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(
                b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", filename="q.docx",
            )
        assert "รหัสผ่าน" in caught.value.message_th
        assert "password" in caught.value.message_en

    def test_a_corrupt_file_is_refused(self):
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(b"PK\x03\x04 and then nonsense", filename="q.docx")
        assert "corrupt" in caught.value.message_en

    def test_a_zip_that_is_not_a_word_document_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("sheet1.xml", "<x/>")
        with pytest.raises(DocxConversionError):
            convert_docx_to_html(buffer.getvalue(), filename="q.docx")

    def test_an_unsupported_extension_is_refused(self):
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(_docx(_p(_r("x"))), filename="q.pdf")
        assert ".docx" in caught.value.message_en

    def test_the_size_cap_is_enforced_before_anything_is_parsed(self):
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(b"PK" + b"\0" * MAX_DOCX_BYTES, filename="q.docx")
        assert "too large" in caught.value.message_en

    def test_an_empty_document_is_refused(self):
        with pytest.raises(DocxConversionError) as caught:
            convert_docx_to_html(_docx(_p()), filename="q.docx")
        assert "no text" in caught.value.message_en


class TestTheSampleFiles:
    @pytest.mark.parametrize("document_type", SAMPLE_DOCUMENT_TYPES)
    def test_every_placeholder_in_a_sample_resolves_against_a_real_snapshot(
        self, document_type,
    ):
        """The assertion this module exists for.

        The hand-written sample the upload used to check against had
        drifted from the snapshot it imitated, so the page told shops
        `{{company.legal_name}}` was fine and their quotes printed a
        blank company name. A sample that is generated from the legend
        and checked against the real builder cannot drift again without
        this failing."""
        html = convert_docx_to_html(
            build_sample_docx(document_type), filename="sample.docx",
        )
        assert unknown_placeholders(html, sample_snapshot(document_type)) == []

    @pytest.mark.parametrize("document_type", SAMPLE_DOCUMENT_TYPES)
    def test_a_sample_contains_every_placeholder_its_legend_promises(
        self, document_type,
    ):
        html = convert_docx_to_html(
            build_sample_docx(document_type), filename="sample.docx",
        )
        found = placeholders_in(html)
        assert set(placeholders_used_by_samples(document_type)) <= found

    @pytest.mark.parametrize("document_type", SAMPLE_DOCUMENT_TYPES)
    def test_a_sample_is_a_valid_word_package(self, document_type):
        content = build_sample_docx(document_type)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            assert archive.testzip() is None
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names

    def test_the_same_bytes_every_time(self):
        """Deterministic, so the download is cacheable and a diff in a
        test failure means the content changed, not the clock."""
        assert build_sample_docx("quote") == build_sample_docx("quote")

    def test_the_quote_sample_fills_into_a_real_looking_document(self):
        html = convert_docx_to_html(build_sample_docx("quote"), filename="s.docx")
        filled = fill_template(html, sample_snapshot("quote"))
        assert "บริษัท ชาญ แอร์ เซอร์วิส จำกัด" in filled
        assert "QT-2026-0042" in filled
        # Both line items, and the VAT the snapshot computed.
        assert "18500.00" in filled and "3500.00" in filled
        assert "3045.00" in filled and "46545.00" in filled
        assert "{{" not in filled

    def test_the_legend_is_in_thai_and_explains_each_field(self):
        for document_type in SAMPLE_DOCUMENT_TYPES:
            entries = LEGEND[document_type]
            assert entries
            for name, label in entries:
                assert label.strip(), name
                assert any("฀" <= ch <= "๿" for ch in label), name
        for name, label in LINE_ITEM_LEGEND:
            assert any("฀" <= ch <= "๿" for ch in label), name


class TestTheSampleDownload:
    def _http(self):
        app = FastAPI()
        app.include_router(routers_admin.router)
        return TestClient(app)

    @pytest.mark.parametrize("document_type", SAMPLE_DOCUMENT_TYPES)
    def test_a_sample_downloads_as_a_word_file_with_no_session(self, document_type):
        """No token, like /guides/{audience}/file: there is no tenant
        data in it, and the LINE in-app browser can only save a file
        when the URL needs no header on it."""
        response = self._http().get(f"/api/v1/document-template-samples/{document_type}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml"
        )
        assert response.headers["content-disposition"].startswith("attachment")
        assert response.content[:2] == b"PK"
        # It is the same file the generator makes, not a re-encoding.
        assert response.content == build_sample_docx(document_type)

    def test_an_unknown_document_type_is_404(self):
        assert self._http().get("/api/v1/document-template-samples/invoice").status_code == 404

    def test_only_docx_is_offered(self):
        response = self._http().get(
            "/api/v1/document-template-samples/quote?format=pdf"
        )
        assert response.status_code == 400


# ------------------------------------------------- upload and preview

class _Store:
    """The document store, in memory. Production is GCS; here it only has
    to hold bytes so the upload and preview paths can complete."""

    def __init__(self, seed: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(seed or {})

    async def put(self, *, key, content, content_type=None):
        self.objects[key] = content
        return type("Stored", (), {"path": f"mem://{key}"})()

    async def get(self, *, path):
        key = path.removeprefix("mem://")
        if key not in self.objects:
            from chann_app.services.storage.base import DocumentStoreError

            raise DocumentStoreError(f"no stored document at {path}")
        return self.objects[key]


class _Client:
    """Only the methods the template routes reach for."""

    def __init__(self, templates=None, versions=None):
        self.templates = templates if templates is not None else []
        self.versions = versions if versions is not None else []
        self.calls: list[tuple] = []

    async def aclose(self):
        pass

    async def list_document_templates(self, license_id, document_type=None):
        self.calls.append(("list_templates", license_id, document_type))
        return [
            t for t in self.templates
            if document_type is None or t["document_type"] == document_type
        ]

    async def create_document_template(self, license_id, payload, actor_id=None):
        row = {"id": TEMPLATE_ID, **payload}
        self.templates.append(row)
        self.calls.append(("create_template", payload))
        return row

    async def list_document_template_versions(self, license_id, template_id):
        return [v for v in self.versions if str(v["template_id"]) == str(template_id)]

    async def create_document_template_version(
        self, license_id, template_id, payload, actor_id=None,
    ):
        row = {
            "id": str(uuid.uuid4()), "template_id": template_id,
            "version": len(self.versions) + 1, "status": "draft", **payload,
        }
        self.versions.append(row)
        self.calls.append(("create_version", payload))
        return row

    async def preview_document_template_version(self, license_id, version_id, actor_id=None):
        self.calls.append(("preview", version_id, actor_id))
        row = next(v for v in self.versions if str(v["id"]) == str(version_id))
        row["status"] = "previewed"
        return dict(row)

    async def publish_document_template_version(self, license_id, template_id, version_id, actor_id=None):
        self.calls.append(("publish", version_id))
        row = next(v for v in self.versions if str(v["id"]) == str(version_id))
        row["status"] = "published"
        return dict(row)


def _app(client, *, keys=("setting.manage",), license_id=LICENSE_ID):
    async def override_client():
        yield client

    async def override_principal():
        return TenantPrincipal(
            license_id=license_id, chann_uid="CHN-S-000001", role="admin",
            is_owner=True, permission_keys=frozenset(keys), audience="sales",
        )

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


def _url(*parts: str) -> str:
    return f"/api/v1/licenses/{LICENSE_ID}/document-templates" + "".join(parts)


@pytest.fixture
def store(monkeypatch):
    from chann_app.services.storage import base as storage_base

    memory = _Store()
    monkeypatch.setattr(storage_base, "get_document_store", lambda: memory)
    return memory


class TestUploadingAWordFile:
    def test_a_docx_uploads_as_a_draft_and_keeps_the_original(self, store):
        client = _Client()
        http = _app(client)
        content = build_sample_docx("quote")

        response = http.post(_url("/upload"), json={
            "template_name": "ใบเสนอราคาของร้าน",
            "docx_base64": base64.b64encode(content).decode(),
            "filename": "quotation.docx",
        })
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["source_kind"] == "docx"
        # The sample is built from the real vocabulary, so nothing in it
        # comes out blank.
        assert body["unknown_placeholders"] == []

        version = client.versions[-1]
        # The compiled HTML renders; the Word file is kept beside it so
        # the shop can open their own layout again.
        assert version["compiled_template_path"].endswith(".html")
        assert version["source_docx_path"].endswith(".docx")
        assert version["intermediate_model"]["kind"] == "docx_upload"
        assert version["intermediate_model"]["filename"] == "quotation.docx"
        assert store.objects[version["source_docx_path"].removeprefix("mem://")] == content

    def test_an_html_upload_is_unchanged(self, store):
        """The rule for this whole change: additive. An HTML template
        that worked before still works, byte for byte."""
        client = _Client()
        http = _app(client)
        response = http.post(_url("/upload"), json={
            "template_name": "เดิม", "html": "<h1>{{company.name}}</h1>",
        })
        assert response.status_code == 201, response.text
        assert response.json()["source_kind"] == "html"
        version = client.versions[-1]
        assert version["source_docx_path"] == "upload://html"
        assert version["intermediate_model"] == {"kind": "html_upload"}
        stored = store.objects[version["compiled_template_path"].removeprefix("mem://")]
        assert stored == b"<h1>{{company.name}}</h1>"

    def test_a_doc_is_refused_with_the_advice_to_save_as_docx(self, store):
        http = _app(_Client())
        response = http.post(_url("/upload"), json={
            "template_name": "เก่า",
            "docx_base64": base64.b64encode(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x").decode(),
            "filename": "quotation.doc",
        })
        assert response.status_code == 400
        assert ".docx" in response.json()["detail"]

    def test_a_corrupt_file_is_refused_with_a_thai_sentence(self, store):
        http = _app(_Client())
        response = http.post(_url("/upload"), json={
            "template_name": "เสีย",
            "docx_base64": base64.b64encode(b"PK\x03\x04nonsense").decode(),
            "filename": "quotation.docx",
        })
        assert response.status_code == 400
        assert "ไฟล์" in response.json()["detail"]

    def test_a_body_that_is_not_base64_is_refused(self, store):
        http = _app(_Client())
        response = http.post(_url("/upload"), json={
            "template_name": "x", "docx_base64": "not base64 at all!!",
            "filename": "q.docx",
        })
        assert response.status_code == 400

    def test_upload_needs_setting_manage(self, store):
        http = _app(_Client(), keys=("quote.read",))
        response = http.post(_url("/upload"), json={
            "template_name": "x", "html": "<p>{{company.name}}</p>",
        })
        assert response.status_code == 403

    def test_the_blank_placeholder_warning_now_uses_the_real_vocabulary(self, store):
        """`company.legal_name` is what the page used to recommend and is
        not a snapshot key. It must be reported, not blessed."""
        http = _app(_Client())
        response = http.post(_url("/upload"), json={
            "template_name": "เก่า",
            "html": "<p>{{company.legal_name}} {{item.name}} {{company.name}}</p>",
        })
        assert response.status_code == 201, response.text
        assert "company.legal_name" in response.json()["unknown_placeholders"]
        assert "company.name" not in response.json()["unknown_placeholders"]


def _draft_version(store, *, status="draft", html=None):
    key = f"{LICENSE_ID}/templates/{TEMPLATE_ID}/abc.html"
    body = html or convert_docx_to_html(
        build_sample_docx("quote"), filename="s.docx",
    )
    store.objects[key] = body.encode("utf-8")
    return {
        "id": VERSION_ID, "template_id": TEMPLATE_ID, "version": 1,
        "status": status, "compiled_template_path": f"mem://{key}",
        "source_docx_path": f"mem://{LICENSE_ID}/templates/{TEMPLATE_ID}/abc.docx",
        "intermediate_model": {"kind": "docx_upload", "filename": "q.docx"},
    }


@pytest.fixture
def previewable(store):
    version = _draft_version(store)
    client = _Client(
        templates=[{
            "id": TEMPLATE_ID, "template_name": "ของร้าน",
            "template_code": "tenant-abc", "document_type": "quote",
            "is_active": True,
        }],
        versions=[version],
    )
    return _app(client), client, version


class TestPreviewingAVersion:
    def test_a_version_renders_with_representative_data(self, previewable):
        http, _client, _version = previewable
        response = http.post(_url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview"))
        assert response.status_code == 200, response.text
        body = response.json()
        html = body["html"]
        # Filled, through the same path a real document takes: values in,
        # no placeholders left behind.
        assert "บริษัท ชาญ แอร์ เซอร์วิส จำกัด" in html
        assert "QT-2026-0042" in html
        assert "46545.00" in html
        assert "{{" not in html
        assert body["unknown_placeholders"] == []

    def test_preview_does_not_publish(self, previewable):
        """10.7's rule, and the one thing a preview button must never
        get wrong: looking at a draft must not put it on documents."""
        http, client, _version = previewable
        body = http.post(
            _url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview")
        ).json()
        assert body["status"] == "previewed"
        assert ("preview", VERSION_ID, "CHN-S-000001") in client.calls
        assert not any(call[0] == "publish" for call in client.calls)

    def test_an_already_published_version_still_renders_and_is_not_re_marked(
        self, store,
    ):
        """A shop asking "what does the layout my customers are getting
        look like" must get an answer, not a status error."""
        version = _draft_version(store, status="published")
        client = _Client(
            templates=[{
                "id": TEMPLATE_ID, "template_name": "ของร้าน",
                "template_code": "tenant-abc", "document_type": "quote",
                "is_active": True,
            }],
            versions=[version],
        )
        response = _app(client).post(
            _url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview")
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "published"
        assert not any(call[0] == "preview" for call in client.calls)

    def test_preview_is_gated_by_setting_manage(self, previewable, store):
        http, client, _ = previewable
        http = _app(client, keys=("quote.read",))
        response = http.post(_url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview"))
        assert response.status_code == 403

    def test_a_caller_from_another_tenant_is_refused(self, previewable):
        _http, client, _ = previewable
        other = "99999999-9999-9999-9999-999999999999"
        http = _app(client, license_id=other)
        response = http.post(_url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview"))
        assert response.status_code in (403, 404)

    def test_a_version_belonging_to_another_template_is_404(self, previewable):
        http, _client, _ = previewable
        other_template = "44444444-4444-4444-4444-444444444444"
        response = http.post(
            _url(f"/{other_template}/versions/{VERSION_ID}/preview")
        )
        assert response.status_code == 404

    def test_a_missing_stored_file_says_so_rather_than_500(self, store):
        version = _draft_version(store)
        store.objects.clear()
        client = _Client(
            templates=[{
                "id": TEMPLATE_ID, "template_name": "ของร้าน",
                "template_code": "tenant-abc", "document_type": "quote",
                "is_active": True,
            }],
            versions=[version],
        )
        response = _app(client).post(
            _url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview")
        )
        assert response.status_code == 404
        assert "แบบฟอร์ม" in response.json()["detail"]

    def test_a_service_report_template_previews_with_report_data(self, store):
        key = f"{LICENSE_ID}/templates/{TEMPLATE_ID}/sr.html"
        store.objects[key] = convert_docx_to_html(
            build_sample_docx("service_report"), filename="s.docx",
        ).encode("utf-8")
        client = _Client(
            templates=[{
                "id": TEMPLATE_ID, "template_name": "ใบซ่อม",
                "template_code": "tenant-sr", "document_type": "service_report",
                "is_active": True,
            }],
            versions=[{
                "id": VERSION_ID, "template_id": TEMPLATE_ID, "version": 1,
                "status": "draft", "compiled_template_path": f"mem://{key}",
                "source_docx_path": "upload://html", "intermediate_model": {},
            }],
        )
        body = _app(client).post(
            _url(f"/{TEMPLATE_ID}/versions/{VERSION_ID}/preview")
        ).json()
        assert body["document_type"] == "service_report"
        assert "SR-2026-0088" in body["html"]
        assert "คอยล์เย็นอุดตัน" in body["html"]
        assert body["unknown_placeholders"] == []


class TestTheVersionListLinksBackToTheWordFile:
    def test_a_docx_version_offers_its_original(self, previewable, monkeypatch):
        from chann_app import config as app_config

        monkeypatch.setattr(app_config.settings, "public_base_url", "https://x.test")
        monkeypatch.setattr(app_config.settings, "jwt_secret", "unit-test-secret")
        http, _client, _ = previewable
        rows = http.get(_url(f"/{TEMPLATE_ID}/versions")).json()
        assert rows[0]["source_docx_url"].startswith("https://x.test/api/v1/assets/")

    def test_an_html_version_offers_nothing_to_download(self, store):
        client = _Client(
            templates=[],
            versions=[{
                "id": VERSION_ID, "template_id": TEMPLATE_ID, "version": 1,
                "status": "draft", "compiled_template_path": "mem://x",
                "source_docx_path": "upload://html", "intermediate_model": {},
            }],
        )
        rows = _app(client).get(_url(f"/{TEMPLATE_ID}/versions")).json()
        assert rows[0]["source_docx_url"] is None
