"""Regression against native MariaDB MVCC, deliberately not emulated with SQLite."""

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Event

import pytest
from conftest import TestConfig as AppTestConfig
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.api import applications
from app.extensions import db
from app.models import Application
from app.models.application import utcnow
from app.services import EncryptionService

pytestmark = pytest.mark.skipif(
    not os.getenv("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL habilita MariaDB local aislada"
)


@pytest.fixture(scope="module")
def native_app():
    uri = make_url(os.environ["MARIADB_TEST_URL"])
    if uri.host not in {"127.0.0.1", "localhost", "::1"} or uri.drivername != "mariadb+mariadbconnector":
        raise ValueError("La regresión solo admite MariaDB Connector en un servidor local de pruebas.")
    schema = "hogares_test_" + uuid.uuid4().hex
    if not re.fullmatch(r"hogares_test_[a-f0-9]{32}", schema):
        raise ValueError("Nombre de esquema de pruebas inválido.")
    administrative_engine = create_engine(uri.set(database=None), connect_args={"connect_timeout": 5})
    with administrative_engine.begin() as connection:
        connection.execute(text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    application = create_app(AppTestConfig, {
        "SQLALCHEMY_DATABASE_URI": uri.set(database=schema).render_as_string(hide_password=False),
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "pool_recycle": 280, "hide_parameters": True},
    })
    try:
        with application.app_context():
            db.create_all()
            with db.engine.connect() as connection:
                assert connection.execute(text("SELECT @@tx_isolation")).scalar_one() == "REPEATABLE-READ"
                version = connection.execute(text("SELECT VERSION()")).scalar_one()
                major, minor = (int(part) for part in version.split(".")[:2])
                assert (major, minor) >= (11, 8), "La regresión necesita MariaDB 11.8 o posterior."
        yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        # Only the fresh, validated UUID schema owned by this fixture is ever removed.
        with administrative_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE `{schema}`"))
        administrative_engine.dispose()


def _pdf(large):
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    if large:
        content = DecodedStreamObject()
        content.set_data((b"%" + b"X" * 1023 + b"\n") * 7168)
        writer.pages[0][NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["property_tax", "household_ids"])
@pytest.mark.parametrize("large", [False, True], ids=["small-pdf", "7mb-pdf"])
def test_autosave_during_upload_does_not_reuse_a_stale_mariadb_snapshot(native_app, monkeypatch, kind, large):
    stored = {}
    storage_started, finish_storage = Event(), Event()

    class DeferredStorage:
        def put(self, key, content):
            storage_started.set()
            if not finish_storage.wait(timeout=15):
                raise TimeoutError("La prueba no liberó el almacenamiento aislado.")
            stored[key] = bytes(content)

        def remove(self, key):
            stored.pop(key, None)

    monkeypatch.setattr(applications, "StorageService", DeferredStorage)
    with native_app.test_client() as client:
        created = client.post("/api/applications/drafts", json={
            "request": {"home": "minas", "type": "new"}, "applicant": {"fullName": "Prueba Aislada"},
        })
        assert created.status_code == 201
        draft = created.json
    url = f"/api/applications/drafts/{draft['id']}"
    headers = {"X-Draft-Token": draft["token"]}
    document = _pdf(large)
    if large:
        assert 7 * 1024 * 1024 < len(document) < 10 * 1024 * 1024

    def upload():
        with native_app.test_client() as client:
            response = client.post(url + "/documents", headers=headers, data={
                "type": kind, "file": (BytesIO(document), "native-test.pdf", "application/pdf"),
            })
            return response.status_code, response.json

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(upload)
        try:
            assert storage_started.wait(timeout=10), "La subida no llegó al almacenamiento."
            # This commits after upload authorization but before its final SELECT FOR UPDATE.
            for number in range(3):
                with native_app.test_client() as client:
                    saved = client.put(url, headers=headers, json={"socioeconomic": {"notes": f"avance-{number}"}})
                    assert saved.status_code == 200, saved.json
        finally:
            finish_storage.set()
        status, body = pending.result(timeout=15)
    assert status == 201, body
    assert body["document"]["type"] == kind
    assert body["document"]["size"] == len(document)
    with native_app.test_client() as client:
        restored = client.get(url, headers=headers)
    assert restored.status_code == 200
    assert restored.json["data"]["socioeconomic"]["notes"] == "avance-2"
    assert len(restored.json["documents"]) == 1
    assert restored.json["documents"][0]["type"] == kind
    assert len(stored) == 1


def test_concurrent_confirmation_retries_use_fresh_locks_and_send_once(native_app, monkeypatch):
    with native_app.test_client() as client:
        created = client.post("/api/applications/drafts")
        assert created.status_code == 201
        draft = created.json
    with native_app.app_context():
        application = db.session.get(Application, draft["id"])
        application.status = "submitted"
        application.submitted_at = utcnow()
        application.tracking_code = "HE26-" + uuid.uuid4().hex[:10].upper()
        application.payload_encrypted = EncryptionService.encrypt({"contactEmail": "native-test@example.uy"})
        db.session.commit()

    sent = []
    ready = Barrier(2)
    original_confirmation = applications._send_confirmation

    def synchronized_confirmation(application_id):
        ready.wait(timeout=10)
        return original_confirmation(application_id)

    monkeypatch.setattr(applications, "_send_confirmation", synchronized_confirmation)
    monkeypatch.setattr(applications, "send_application_confirmation", lambda _application, email: sent.append(email))

    def retry():
        with native_app.test_client() as client:
            response = client.post(
                f"/api/applications/drafts/{draft['id']}/confirmation-email", headers={"X-Draft-Token": draft["token"]}
            )
            return response.status_code, response.json

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(retry), pool.submit(retry)
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert [status for status, _body in responses] == [200, 200]
    assert all(body["confirmationEmailSent"] is True for _status, body in responses)
    assert len({body["trackingCode"] for _status, body in responses}) == 1
    assert sent == ["native-test@example.uy"]
