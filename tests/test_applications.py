from io import BytesIO

from PIL import Image

from app.extensions import db
from app.models import Application, ApplicationDocument


class FakeStorage:
    objects = {}

    def put(self, key, content):
        self.objects[key] = content

    def remove(self, key):
        self.objects.pop(key, None)


def create_draft(client):
    response = client.post("/api/applications/drafts")
    assert response.status_code == 201
    return response.get_json()


def captcha_headers(client, draft):
    headers = {"X-Draft-Token": draft["token"]}
    challenge = client.get(f"/api/applications/drafts/{draft['id']}/captcha", headers=headers).get_json()
    a, b = challenge["question"].split(" + ")
    return {**headers, "X-Captcha-Token": challenge["token"], "X-Captcha-Answer": str(int(a) + int(b))}


def test_liveness(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_draft_requires_opaque_token(client):
    draft = create_draft(client)
    assert client.get(f"/api/applications/drafts/{draft['id']}").status_code == 403
    response = client.get(f"/api/applications/drafts/{draft['id']}", headers={"X-Draft-Token": draft["token"]})
    assert response.status_code == 200
    with client.application.app_context():
        application = db.session.get(Application, draft["id"])
        assert application.draft_token_hash != draft["token"]


def test_image_is_reencoded_as_webp(client, monkeypatch):
    from app.api import applications as module

    monkeypatch.setattr(module, "StorageService", FakeStorage)
    draft = create_draft(client)
    image = Image.new("RGB", (2500, 1200), color="red")
    source = BytesIO()
    image.save(source, format="PNG")
    source.seek(0)
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/documents",
        headers={"X-Draft-Token": draft["token"]},
        data={"type": "applicant_id", "file": (source, "../cedula.png", "image/png")},
    )
    assert response.status_code == 201
    document = response.get_json()["document"]
    assert document["mimeType"] == "image/webp"
    assert document["storedName"].endswith(".webp")
    assert document["width"] <= 1920
    from app.services import EncryptionService

    stored = EncryptionService.decrypt_file(next(iter(FakeStorage.objects.values())))
    assert stored.startswith(b"RIFF") and b"WEBP" in stored[:16]


def test_submission_validates_payload_and_required_documents(client, payload):
    draft = create_draft(client)
    headers = captcha_headers(client, draft)
    invalid = {**payload, "applicant": {**payload["applicant"], "phone": "letters"}}
    response = client.post(f"/api/applications/drafts/{draft['id']}/submit", headers=headers, json=invalid)
    assert response.status_code == 422

    with client.application.app_context():
        required = {
            "applicant_id",
            "household_ids",
            "address",
            "school_records",
            "health_card",
            "ute_receipt",
            "passport_photo",
            "income_proofs",
            "transit_certificate",
        }
        for document_type in required:
            db.session.add(
                ApplicationDocument(
                    application_id=draft["id"],
                    document_type=document_type,
                    object_key=f"test/{document_type}",
                    original_name="test.webp",
                    stored_name="test.webp",
                    media_type="image/webp",
                    size_bytes=10,
                    checksum_sha256="a" * 64,
                )
            )
        db.session.commit()

    response = client.post(f"/api/applications/drafts/{draft['id']}/submit", headers=headers, json=payload)
    assert response.status_code == 201
    receipt = response.get_json()
    assert receipt["trackingCode"].startswith("HE26-")
    with client.application.app_context():
        application = db.session.get(Application, draft["id"])
        assert application.status == "submitted"
        assert b"Persona Aspirante" not in application.payload_encrypted


def test_unknown_payload_fields_are_rejected(client, payload):
    draft = create_draft(client)
    payload["unexpected"] = "value"
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/submit", headers={"X-Draft-Token": draft["token"]}, json=payload
    )
    assert response.status_code == 422


def test_invalid_uruguayan_id_is_rejected(client, payload):
    draft = create_draft(client)
    payload["applicant"]["nationalId"] = "4.567.890-1"
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/submit",
        headers={"X-Draft-Token": draft["token"]},
        json=payload,
    )
    assert response.status_code == 422
    assert "nationalId" in response.get_json()["fields"]["applicant"]


def test_non_convivient_parent_data_is_required(client, payload):
    draft = create_draft(client)
    payload["household"]["livesWithMother"] = False
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/submit",
        headers={"X-Draft-Token": draft["token"]},
        json=payload,
    )
    assert response.status_code == 422
    assert "outsideParents" in response.get_json()["fields"]


def test_spoofed_image_content_is_rejected(client, monkeypatch):
    from app.api import applications as module

    monkeypatch.setattr(module, "StorageService", FakeStorage)
    draft = create_draft(client)
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/documents",
        headers={"X-Draft-Token": draft["token"]},
        data={"type": "applicant_id", "file": (BytesIO(b"not-an-image"), "fake.png", "image/png")},
    )
    assert response.status_code == 422
