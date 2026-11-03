import json
from copy import deepcopy
from pathlib import Path

import pytest
from marshmallow import ValidationError
from test_applications import captcha_headers, create_draft

from app.extensions import db
from app.models import Application, ApplicationDocument
from app.schemas import application_schema
from app.services import EncryptionService
from app.services.requirements import required_document_types

CONTRACT = json.loads((Path(__file__).parent / "contracts/documents.json").read_text(encoding="utf-8"))


def merge(base, changes):
    result = deepcopy(base)
    for key, value in changes.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) else value
    return result


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["name"])
def test_shared_document_contract(app, case):
    payload = application_schema.load(merge(CONTRACT["baseline"], case["changes"]))
    assert required_document_types(payload) == set(case["required"])


@pytest.mark.parametrize("situation", ["deceased", "unknown", "no_contact"])
def test_absent_parents_do_not_require_personal_records(app, payload, situation):
    payload["household"] = {"livesAlone": False, "motherSituation": situation, "fatherSituation": situation}
    result = application_schema.load(payload)
    assert result["outsideParents"] == []
    assert result["household"]["livesWithMother"] is False


def test_lives_alone_requires_no_convivients(app, payload):
    payload["household"] = {"livesAlone": True, "motherSituation": "deceased", "fatherSituation": "unknown"}
    with pytest.raises(ValidationError):
        application_schema.load(payload)
    payload["householdMembers"] = []
    assert application_schema.load(payload)["householdMembers"] == []
    payload["household"]["motherSituation"] = "lives_with"
    with pytest.raises(ValidationError):
        application_schema.load(payload)


@pytest.mark.parametrize("situation", ["lives_with", "deceased", "unknown", "no_contact"])
def test_inconsistent_parent_records_rejected(app, payload, situation):
    payload["household"]["motherSituation"] = situation
    payload["outsideParents"] = [
        {
            "id": "outside-mother",
            "role": "mother",
            "name": "Nombre",
            "surnames": "Apellido",
            "relationship": "Madre",
            "age": 40,
        }
    ]
    with pytest.raises(ValidationError):
        application_schema.load(payload)


def test_disability_requires_supports_and_clears_hidden_value(app, payload):
    payload["health"]["hasDisability"] = True
    payload["health"]["dailyLivingSupports"] = "   "
    with pytest.raises(ValidationError) as error:
        application_schema.load(payload)
    assert "dailyLivingSupports" in error.value.messages["health"]
    payload["health"]["dailyLivingSupports"] = "No necesita apoyos actualmente."
    assert application_schema.load(payload)["health"]["dailyLivingSupports"]
    payload["health"]["hasDisability"] = False
    assert application_schema.load(payload)["health"]["dailyLivingSupports"] == ""


def test_submission_stores_new_fields_without_unrelated_documents(client, payload):
    client.application.config["APPLICATION_YEAR"] = 2027
    payload["household"] = {"livesAlone": True, "motherSituation": "deceased", "fatherSituation": "unknown"}
    payload["householdMembers"] = []
    payload["health"].update(hasDisability=True, dailyLivingSupports="Apoyo para desplazarse.")
    draft = create_draft(client)
    normalized = application_schema.load(payload)
    for kind in required_document_types(normalized):
        db.session.add(
            ApplicationDocument(
                application_id=draft["id"],
                document_type=kind,
                object_key=f"test/{kind}",
                original_name="test.webp",
                stored_name="test.webp",
                media_type="image/webp",
                size_bytes=10,
                checksum_sha256="a" * 64,
            )
        )
    db.session.commit()
    response = client.post(
        f"/api/applications/drafts/{draft['id']}/submit", json=payload, headers=captcha_headers(client, draft)
    )
    assert response.status_code == 201, response.json
    assert response.json["trackingCode"].startswith("HE27-")
    item = db.session.get(Application, draft["id"])
    assert item.application_year == 2027
    assert (
        EncryptionService.decrypt(item.payload_encrypted)["health"]["dailyLivingSupports"] == "Apoyo para desplazarse."
    )


def test_next_convocation_accepts_year_but_rejects_future_year(app, payload):
    app.config["APPLICATION_YEAR"] = 2027
    payload["request"].update(type="renewal", renewalYear=2027, sameHome=True)
    application_schema.load(payload)
    payload["request"]["renewalYear"] = 2028
    with pytest.raises(ValidationError):
        application_schema.load(payload)


def test_readiness_reports_redis_failure(client, monkeypatch):
    from app import health

    class Storage:
        def ensure_bucket(self):
            pass

        def ready(self):
            return True

    monkeypatch.setattr(health, "StorageService", Storage)
    monkeypatch.setattr(health.limiter.storage, "check", lambda: False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json["checks"] == {"database": True, "storage": True, "redis": False}
