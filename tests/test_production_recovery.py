from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NullObject, TextStringObject
from test_applications import captcha_headers, create_draft

from app.extensions import BoundedMailConnection, db
from app.models import Application, ApplicationDocument
from app.models.application import utcnow
from app.schemas import application_schema
from app.services import EncryptionService
from app.services.requirements import required_document_types

ORIGIN = "http://localhost:5173"


@pytest.fixture()
def memory_storage(monkeypatch):
    from app.api import applications

    class Storage:
        objects = {}

        def put(self, key, content):
            self.objects[key] = content

        def remove(self, key):
            self.objects.pop(key, None)

    monkeypatch.setattr(applications, "StorageService", Storage)
    return Storage


def upload(client, draft, source, name, mime, kind="applicant_id"):
    return client.post(
        f"/api/applications/drafts/{draft['id']}/documents",
        headers={"X-Draft-Token": draft["token"], "Origin": ORIGIN},
        data={"type": kind, "file": (source, name, mime)},
    )


def image_file(format="PNG", **options):
    output = BytesIO()
    Image.new("RGB", options.pop("size", (40, 30)), color="blue").save(output, format=format, **options)
    output.seek(0)
    return output


def pdf_file(open_action=None):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    if open_action == "view":
        writer.root_object[NameObject("/OpenAction")] = ArrayObject([
            writer.pages[0].indirect_reference, NameObject("/XYZ"), NullObject(), NullObject(), NullObject(),
        ])
    elif open_action == "javascript":
        writer.root_object[NameObject("/OpenAction")] = DictionaryObject({
            NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("alert('test')"),
        })
    writer.write(output)
    output.seek(0)
    return output


def attach_required_metadata(draft, payload):
    for kind in required_document_types(application_schema.load(payload)):
        db.session.add(ApplicationDocument(
            application_id=draft["id"], document_type=kind, object_key=f"test/{kind}",
            original_name="document.pdf", stored_name="document.pdf", media_type="application/pdf",
            size_bytes=100, checksum_sha256="a" * 64,
        ))
    db.session.commit()


def test_creation_saves_incomplete_snapshot_encrypted(client):
    snapshot = {
        "request": {"home": "minas", "type": "new", "sameHome": None, "renewalYear": ""},
        "applicant": {"fullName": "Nombre Parcial", "nationalId": ""},
        "contactEmail": "", "acceptedDeclaration": False,
    }
    response = client.post("/api/applications/drafts", json=snapshot)
    assert response.status_code == 201
    draft = response.json
    application = db.session.get(Application, draft["id"])
    assert application.status == "draft" and application.home == "minas" and application.application_type == "new"
    assert b"Nombre Parcial" not in application.payload_encrypted
    saved = client.get(f"/api/applications/drafts/{draft['id']}", headers={"X-Draft-Token": draft["token"]})
    assert saved.json["data"] == snapshot
    assert saved.json["documents"] == []


def test_partial_snapshot_merges_existing_data_without_submitting(client, memory_storage):
    draft = create_draft(client)
    headers = {"X-Draft-Token": draft["token"]}
    url = f"/api/applications/drafts/{draft['id']}"
    initial = {"applicant": {"fullName": "Nombre", "nationalId": "123"}}
    changed = {"applicant": {"phone": ""}, "health": {"hasDisability": None}}
    assert client.put(url, headers=headers, json=initial).status_code == 200
    assert client.put(url, headers=headers, json=changed).status_code == 200
    assert upload(client, draft, pdf_file(), "scanner.pdf", "application/pdf").status_code == 201
    saved = client.get(url, headers=headers).json
    assert saved["data"]["applicant"] == {"fullName": "Nombre", "nationalId": "123", "phone": ""}
    assert saved["status"] == "draft" and saved["trackingCode"] is None
    assert len(saved["documents"]) == 1


@pytest.mark.parametrize("body", [
    {"applicant": {"unknown": "x"}}, {"unknown": "x"}, {"health": []},
    {"applicant": {"fullName": {"injection": "x"}}}, {"health": {"monthlySalary": float("nan")}},
])
def test_malformed_snapshot_does_not_replace_previous_data(client, body):
    draft = create_draft(client)
    url, headers = f"/api/applications/drafts/{draft['id']}", {"X-Draft-Token": draft["token"]}
    assert client.put(url, json={"applicant": {"fullName": "Guardado"}}, headers=headers).status_code == 200
    assert client.put(url, json=body, headers=headers).status_code == 422
    assert client.get(url, headers=headers).json["data"]["applicant"]["fullName"] == "Guardado"


def test_snapshot_requires_credential_and_draft_state(client, payload):
    draft = create_draft(client)
    url = f"/api/applications/drafts/{draft['id']}"
    assert client.put(url, json=payload).status_code == 403
    application = db.session.get(Application, draft["id"])
    application.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    headers = {"X-Draft-Token": draft["token"]}
    assert client.put(url, json=payload, headers=headers).status_code == 410


def test_submission_and_timeout_recovery_keep_one_receipt(client, payload):
    draft = create_draft(client)
    url = f"/api/applications/drafts/{draft['id']}"
    headers = {"X-Draft-Token": draft["token"]}
    assert client.put(url, json=payload, headers=headers).status_code == 200
    attach_required_metadata(draft, payload)
    first = client.post(url + "/submit", json=payload, headers=captcha_headers(client, draft))
    assert first.status_code == 201
    recovered = client.get(url, headers=headers).json
    assert recovered["status"] == "submitted"
    assert recovered["trackingCode"] == first.json["trackingCode"]
    assert recovered["submittedAt"] == first.json["submittedAt"]
    assert "confirmationEmailSent" in recovered
    assert client.put(url, json={"contactEmail": "other@example.uy"}, headers=headers).status_code == 409
    repeated = client.post(url + "/submit", json={}, headers=headers)
    assert repeated.status_code == 200 and repeated.json["trackingCode"] == first.json["trackingCode"]


@pytest.mark.parametrize("mime", ["application/pdf", "application/octet-stream", "application/x-pdf"])
def test_pdf_with_normal_opening_view_is_accepted(client, memory_storage, mime):
    draft = create_draft(client)
    response = upload(client, draft, pdf_file("view"), "documento del escaner.PDF", mime)
    assert response.status_code == 201
    assert response.json["document"]["mimeType"] == "application/pdf"


def test_pdf_javascript_is_still_rejected(client, memory_storage):
    draft = create_draft(client)
    response = upload(client, draft, pdf_file("javascript"), "document.pdf", "application/pdf")
    assert response.status_code == 422 and "acciones" in response.json["error"]
    assert not memory_storage.objects


@pytest.mark.parametrize("format,mime,name", [
    ("PNG", "application/octet-stream", "foto.png"), ("HEIF", "image/heic", "IMG_0001.HEIC"),
    ("HEIF", "application/octet-stream", "IMG_0002.HEIC"),
])
def test_mobile_images_are_detected_and_reencoded(client, memory_storage, format, mime, name):
    draft = create_draft(client)
    response = upload(client, draft, image_file(format), name, mime)
    assert response.status_code == 201
    document = response.json["document"]
    assert document["mimeType"] == "image/webp" and document["storedName"].endswith(".webp")
    assert document["width"] == 40 and document["height"] == 30


def test_large_original_image_is_accepted_before_optimization(client, memory_storage):
    source = image_file("PNG", size=(1500, 1500), compress_level=0)
    assert source.getbuffer().nbytes > 5 * 1024 * 1024
    response = upload(client, create_draft(client), source, "photo.png", "image/png")
    assert response.status_code == 201 and response.json["document"]["size"] < 5 * 1024 * 1024


def test_camera_orientation_is_preserved_and_metadata_is_removed(client, memory_storage):
    source = BytesIO()
    image = Image.new("RGB", (40, 30), color="blue")
    metadata = Image.Exif()
    metadata[274] = 6
    image.save(source, format="JPEG", exif=metadata)
    source.seek(0)
    response = upload(client, create_draft(client), source, "camera.jpg", "image/jpeg")
    assert response.status_code == 201
    assert (response.json["document"]["width"], response.json["document"]["height"]) == (30, 40)
    stored = EncryptionService.decrypt_file(next(iter(memory_storage.objects.values())))
    with Image.open(BytesIO(stored)) as sanitized:
        assert not sanitized.getexif()


def test_failed_replacement_keeps_existing_document_and_cors(client, memory_storage, monkeypatch):
    draft = create_draft(client)
    first = upload(client, draft, pdf_file(), "original.pdf", "application/pdf").json["document"]

    def unavailable(_self, _key, _content):
        raise TimeoutError("Storage unavailable")

    monkeypatch.setattr(memory_storage, "put", unavailable)
    failed = upload(client, draft, pdf_file(), "replacement.pdf", "application/pdf")
    assert failed.status_code == 503
    assert failed.headers["Access-Control-Allow-Origin"] == ORIGIN
    saved = client.get(f"/api/applications/drafts/{draft['id']}", headers={"X-Draft-Token": draft["token"]}).json
    assert saved["documents"][0]["id"] == first["id"] and saved["documents"][0]["name"] == "original.pdf"


def test_upload_finishing_after_submission_cannot_replace_a_confirmed_document(client, memory_storage, monkeypatch):
    draft = create_draft(client)
    first = upload(client, draft, pdf_file(), "original.pdf", "application/pdf").json["document"]
    original_document = db.session.get(ApplicationDocument, first["id"])
    original_key = original_document.object_key
    original_content = memory_storage.objects[original_key]
    new_objects = []

    def store_then_submit(_self, key, content):
        memory_storage.objects[key] = content
        new_objects.append(key)
        application = db.session.get(Application, draft["id"])
        application.status = "submitted"
        application.submitted_at = utcnow()
        application.tracking_code = "HE26-CONFIRMED"
        db.session.commit()

    monkeypatch.setattr(memory_storage, "put", store_then_submit)
    response = upload(client, draft, pdf_file(), "replacement.pdf", "application/pdf")
    assert response.status_code == 409
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    saved = db.session.get(ApplicationDocument, first["id"])
    assert saved.object_key == original_key and saved.original_name == "original.pdf"
    assert memory_storage.objects[original_key] == original_content
    assert all(key not in memory_storage.objects for key in new_objects)
    assert db.session.get(Application, draft["id"]).status == "submitted"


def test_storage_configuration_error_is_recoverable_json(client, monkeypatch):
    from app.api import applications

    def unavailable():
        raise ValueError("Invalid endpoint")

    monkeypatch.setattr(applications, "StorageService", unavailable)
    failed = upload(client, create_draft(client), pdf_file(), "file.pdf", "application/pdf")
    assert failed.status_code == 503 and "temporalmente" in failed.json["error"]
    assert failed.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_oversized_request_preserves_readable_cors_error(client, app):
    draft = create_draft(client)
    app.config["MAX_CONTENT_LENGTH"] = 100
    response = upload(client, draft, pdf_file(), "file.pdf", "application/pdf")
    assert response.status_code == 413 and "tamaño" in response.json["error"]
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_shared_mobile_ip_does_not_share_document_quota_between_drafts(client):
    first, second = create_draft(client), create_draft(client)
    for _attempt in range(120):
        response = client.post(
            f"/api/applications/drafts/{first['id']}/documents",
            headers={"X-Draft-Token": first["token"], "Origin": ORIGIN}, data={"type": "invalid"},
        )
        assert response.status_code == 422
    blocked = client.post(
        f"/api/applications/drafts/{first['id']}/documents",
        headers={"X-Draft-Token": first["token"], "Origin": ORIGIN}, data={"type": "invalid"},
    )
    assert blocked.status_code == 429 and blocked.headers["Access-Control-Allow-Origin"] == ORIGIN
    other = client.post(
        f"/api/applications/drafts/{second['id']}/documents", headers={"X-Draft-Token": second["token"]},
        data={"type": "invalid"},
    )
    assert other.status_code == 422


def test_shared_mobile_ip_can_start_more_than_five_families(client):
    for _attempt in range(6):
        assert client.post("/api/applications/drafts").status_code == 201


def test_random_invalid_tokens_share_one_ip_rate_limit_key(app, client):
    from app.api.applications import _draft_limit_key

    draft = create_draft(client)
    keys = []
    for token in [None, "random-one", "random-two", "x" * 200]:
        headers = {"X-Draft-Token": token} if token is not None else {}
        with app.test_request_context(
            f"/api/applications/drafts/{draft['id']}/documents", method="POST", headers=headers,
            environ_base={"REMOTE_ADDR": "203.0.113.4"},
        ):
            keys.append(_draft_limit_key())
    assert keys == ["ip:203.0.113.4"] * 4


def test_verified_tokens_have_distinct_quotas_and_cannot_be_reused_for_other_drafts(app, client):
    from app.api.applications import _draft_limit_key

    first, second = create_draft(client), create_draft(client)
    keys = []
    for draft in [first, second]:
        with app.test_request_context(
            f"/api/applications/drafts/{draft['id']}/documents", method="POST",
            headers={"X-Draft-Token": draft["token"]}, environ_base={"REMOTE_ADDR": "203.0.113.4"},
        ):
            keys.append(_draft_limit_key())
    assert keys[0] != keys[1]
    assert all(not key.startswith("ip:") for key in keys)
    with app.test_request_context(
        f"/api/applications/drafts/{second['id']}/documents", method="POST",
        headers={"X-Draft-Token": first["token"]}, environ_base={"REMOTE_ADDR": "203.0.113.4"},
    ):
        assert _draft_limit_key() == "ip:203.0.113.4"


def test_hidden_stale_values_do_not_prevent_final_validation(payload):
    previous = deepcopy(payload)
    payload["request"].update(renewalYear=9999, sameHome=True)
    payload["studies"]["pendingSubjectsCount"] = -2
    payload["health"].update(monthlySalary=-100, dailyLivingSupports="hidden", inTreatment="invalid")
    payload["socioeconomic"].update(rentAmount=-5, installmentAmount=-5)
    payload["socioeconomic"]["car"].update(quantity=0, year=9999, brand="hidden")
    payload["socioeconomic"]["rural"]["leaseAmount"] = -5
    normalized = application_schema.load(payload)
    assert normalized["request"]["renewalYear"] is None
    assert normalized["health"]["monthlySalary"] is None
    assert normalized["socioeconomic"]["car"]["year"] is None
    assert required_document_types(normalized) == required_document_types(application_schema.load(previous))


def test_smtp_connection_has_finite_timeout(app, monkeypatch):
    from app import extensions

    recorded = {}

    class SMTP:
        def __init__(self, _server, _port, **kwargs):
            recorded.update(kwargs)

        def set_debuglevel(self, _level):
            pass

        def starttls(self, **_kwargs):
            pass

        def login(self, _user, _password):
            pass

    monkeypatch.setattr(extensions.smtplib, "SMTP", SMTP)
    BoundedMailConnection(app.extensions["mail"]).configure_host()
    assert recorded["timeout"] == 10
