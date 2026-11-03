import time

import pytest
from itsdangerous import TimestampSigner
from test_applications import captcha_headers, create_draft

from app.api import applications
from app.extensions import db, mail
from app.models import Application
from app.services import EncryptionService
from app.services.captcha import create_captcha, verify_captcha


@pytest.fixture()
def submission(client, monkeypatch):
    monkeypatch.setattr(applications, "required_document_types", lambda _payload: set())
    draft = create_draft(client)
    return draft, captcha_headers(client, draft)


@pytest.mark.parametrize("email", [None, "", "invalid", "name@", "a" * 255 + "@example.uy"])
def test_confirmation_email_is_required(client, payload, submission, email):
    draft, headers = submission
    if email is None:
        payload.pop("contactEmail")
    else:
        payload["contactEmail"] = email
    response = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers)
    assert response.status_code == 422
    assert "contactEmail" in response.json["fields"]


@pytest.mark.parametrize("change", ["missing", "wrong", "tampered", "other_draft"])
def test_captcha_cannot_be_skipped_or_forged(client, payload, submission, change):
    draft, headers = submission
    if change == "missing":
        headers.pop("X-Captcha-Token")
    elif change == "wrong":
        headers["X-Captcha-Answer"] = "99"
    elif change == "tampered":
        headers["X-Captcha-Token"] += "invalid"
    else:
        other = create_draft(client)
        headers.update({key: value for key, value in captcha_headers(client, other).items() if key != "X-Draft-Token"})
    response = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers)
    assert response.status_code == 422
    assert "captcha" in response.json["fields"]
    with client.application.app_context():
        assert db.session.get(Application, draft["id"]).status == "draft"


def test_captcha_expires(app, monkeypatch):
    with app.app_context():
        with monkeypatch.context() as patch:
            patch.setattr(TimestampSigner, "get_timestamp", lambda _self: int(time.time()) - 601)
            challenge = create_captcha("draft-id")
        a, b = challenge["question"].split(" + ")
        assert not verify_captcha("draft-id", challenge["token"], str(int(a) + int(b)))


def test_confirmation_mail_content_and_idempotent_submission(client, payload, submission):
    draft, headers = submission
    payload["contactEmail"] = "  Aspirante@Example.UY "
    with client.application.app_context(), mail.record_messages() as messages:
        response = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers)
        assert response.status_code == 201
        assert response.json["confirmationEmailSent"] is True
        assert len(messages) == 1
        assert messages[0].recipients == ["aspirante@example.uy"]
        assert response.json["trackingCode"] in messages[0].body
        assert response.json["trackingCode"] in messages[0].html
        assert "Persona Aspirante" not in messages[0].body
        assert payload["applicant"]["nationalId"] not in messages[0].html
        repeated = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers)
        assert repeated.json == response.json
        assert len(messages) == 1
        retry = client.post(f"/api/applications/drafts/{draft['id']}/confirmation-email", headers=headers)
        assert retry.json["confirmationEmailSent"] is True
        assert len(messages) == 1
        application = db.session.get(Application, draft["id"])
        assert b"aspirante@example.uy" not in application.payload_encrypted
        assert EncryptionService.decrypt(application.payload_encrypted)["contactEmail"] == "aspirante@example.uy"
        assert application.confirmation_email_sent_at is not None


def test_failed_email_keeps_submission_and_can_retry(client, payload, submission, monkeypatch):
    draft, headers = submission

    def fail(*_args):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(applications, "send_application_confirmation", fail)
    response = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers)
    assert response.status_code == 201
    assert response.json["confirmationEmailSent"] is False
    with client.application.app_context():
        assert db.session.get(Application, draft["id"]).status == "submitted"
    path = f"/api/applications/drafts/{draft['id']}/confirmation-email"
    assert client.post(path).status_code == 403
    assert client.post(path, headers=headers).status_code == 503
    calls = []
    monkeypatch.setattr(applications, "send_application_confirmation", lambda _app, email: calls.append(email))
    retried = client.post(path, headers=headers)
    assert retried.status_code == 200
    assert retried.json["confirmationEmailSent"] is True
    assert retried.json["trackingCode"] == response.json["trackingCode"]
    assert calls == [payload["contactEmail"]]


def test_tracking_from_another_device_is_private(client, payload, submission):
    draft, headers = submission
    receipt = client.post(f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=headers).json
    other_client = client.application.test_client()
    code = receipt["trackingCode"]
    response = other_client.post(
        "/api/applications/track", json={"trackingCode": code.lower(), "email": " ASPIRANTE@example.uy "}
    )
    assert response.status_code == 200
    assert response.json["statusLabel"] == "Recibida"
    assert "data" not in response.json
    assert "documents" not in response.json
    assert payload["applicant"]["nationalId"] not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    wrong_email = other_client.post("/api/applications/track", json={"trackingCode": code, "email": "other@example.uy"})
    wrong_code = other_client.post(
        "/api/applications/track", json={"trackingCode": "UNKNOWN", "email": payload["contactEmail"]}
    )
    assert wrong_email.status_code == wrong_code.status_code == 404
    assert wrong_email.json == wrong_code.json


def test_unsubmitted_draft_cannot_send_confirmation(client, submission):
    draft, headers = submission
    assert client.post(f"/api/applications/drafts/{draft['id']}/confirmation-email", headers=headers).status_code == 409
    assert client.get(f"/api/applications/drafts/{draft['id']}/captcha").status_code == 403


def test_cors_allows_confirmation_headers(client):
    response = client.options(
        "/api/applications/drafts/id/submit",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Draft-Token,X-Captcha-Token,X-Captcha-Answer,Content-Type",
        },
    )
    assert response.status_code == 200
    assert "X-Captcha-Token" in response.headers["Access-Control-Allow-Headers"]
