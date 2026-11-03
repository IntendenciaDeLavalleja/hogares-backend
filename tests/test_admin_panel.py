import secrets
import time
from datetime import timedelta

import pytest
from test_auth import create_user

from app.extensions import db
from app.models import ActivityLog, Application, ApplicationDocument, TwoFactorCode, User
from app.models.application import utcnow
from app.services import EncryptionService


def form(client, path, data=None):
    with client.session_transaction() as session:
        token = session.get("admin_csrf", "")
    return client.post(path, data={"csrf_token": token, **(data or {})})


def start_login(client, monkeypatch):
    from app.admin import routes

    captured = {}
    monkeypatch.setattr(routes, "send_2fa_email", lambda _email, _name, code: captured.update(code=code))
    assert client.get("/admin/login").status_code == 200
    with client.session_transaction() as session:
        answer = session["captcha_answer"]
    result = form(
        client,
        "/admin/login",
        {
            "email": "admin@example.uy",
            "password": "SecurePassword!2026",
            "captcha": answer,
        },
    )
    assert result.location.endswith("/admin/2fa")
    assert client.get("/admin/2fa").status_code == 200
    return captured["code"]


def login(client, monkeypatch):
    code = start_login(client, monkeypatch)
    assert form(client, "/admin/2fa", {"code": code}).location.endswith("/admin/dashboard")
    assert client.get("/admin/dashboard").status_code == 200


@pytest.mark.parametrize("path", ["/", "/missing", "/admin/missing", "/admin/permits", "/admin/pages"])
def test_unavailable_web_routes_are_html_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert "No encontramos la página" in response.text


@pytest.mark.parametrize("path", ["/api", "/api/missing", "/auth", "/auth/missing"])
def test_unavailable_api_routes_are_json_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json == {"error": "No encontrado."}


@pytest.mark.parametrize("path", ["/admin", "/admin/", "/admin/dashboard", "/admin/applications", "/admin/users"])
def test_web_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert response.location.endswith("/admin/login")


def test_login_assets_cookie_and_csrf(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "style-src 'self'" in response.headers["Content-Security-Policy"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    assert "Secure;" not in response.headers["Set-Cookie"]
    assert client.get("/static/admin.css").status_code == 200
    assert client.get("/favicon.ico").status_code == 200
    assert client.post("/admin/login", data={}).status_code == 400
    assert client.post("/admin/login", data={"csrf_token": "ñ"}).status_code == 400
    assert client.get("/admin/logout").status_code == 405


def test_captcha_is_required_before_email(app, client, monkeypatch):
    from app.admin import routes

    with app.app_context():
        create_user()
    monkeypatch.setattr(routes, "send_2fa_email", lambda *_: pytest.fail("Must not send email"))
    client.get("/admin/login")
    result = form(client, "/admin/login", {"email": "admin@example.uy", "password": "SecurePassword!2026"})
    assert "Verificación de seguridad incorrecta" in result.text
    with app.app_context():
        assert TwoFactorCode.query.count() == 0


def test_login_logout_and_cookie_replay(app, client, monkeypatch):
    with app.app_context():
        create_user()
    login(client, monkeypatch)
    cookie = client.get_cookie("hogares_admin").value
    assert client.get("/api/admin/applications").status_code == 401
    assert client.post("/admin/logout").status_code == 400
    assert form(client, "/admin/logout").location.endswith("/admin/login")
    client.set_cookie("hogares_admin", cookie)
    assert client.get("/admin/dashboard").location.endswith("/admin/login")
    with app.app_context():
        assert TwoFactorCode.query.one().consumed_at is not None
        assert ActivityLog.query.filter_by(action="admin:logout").count() == 1


def test_five_bad_codes_and_expired_pending(app, client, monkeypatch):
    with app.app_context():
        create_user()
    code = start_login(client, monkeypatch)
    for _ in range(5):
        assert "Código inválido" in form(client, "/admin/2fa", {"code": "000000"}).text
    assert form(client, "/admin/2fa", {"code": code}).location.endswith("/admin/login")
    assert client.get("/admin/dashboard").status_code == 302


def test_expired_code_and_disabled_pending_user(app, client, monkeypatch):
    with app.app_context():
        user_id = create_user().id
    start_login(client, monkeypatch)
    with app.app_context():
        TwoFactorCode.query.one().expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.get("/admin/2fa").location.endswith("/admin/login")
    start_login(client, monkeypatch)
    with app.app_context():
        db.session.get(User, user_id).is_active = False
        db.session.commit()
    assert client.get("/admin/2fa").location.endswith("/admin/login")


def test_failed_email_does_not_allow_second_factor(app, client, monkeypatch):
    from app.admin import routes

    with app.app_context():
        create_user()

    def fail_email(*_args):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(routes, "send_2fa_email", fail_email)
    client.get("/admin/login")
    with client.session_transaction() as session:
        answer = session["captcha_answer"]
    result = form(
        client,
        "/admin/login",
        {
            "email": "admin@example.uy",
            "password": "SecurePassword!2026",
            "captcha": answer,
        },
    )
    assert "No se pudo enviar" in result.text
    assert client.get("/admin/2fa").location.endswith("/admin/login")
    with app.app_context():
        assert TwoFactorCode.query.one().consumed_at is not None


def test_admin_cannot_access_superadmin_routes(app, client, monkeypatch):
    with app.app_context():
        create_user()
    login(client, monkeypatch)
    for path in ["/admin/users", "/admin/logs"]:
        response = client.get(path)
        assert response.status_code == 404
        assert response.mimetype == "text/html"
    assert form(client, "/admin/users", {"name": "Forbidden"}).status_code == 404
    assert client.get("/api/admin/users").status_code == 401


@pytest.mark.parametrize("change", ["disabled", "password", "expired"])
def test_sessions_are_invalidated(app, client, monkeypatch, change):
    with app.app_context():
        user_id = create_user().id
    login(client, monkeypatch)
    if change == "expired":
        with client.session_transaction() as session:
            session["admin_expires"] = int(time.time()) - 1
    else:
        with app.app_context():
            user = db.session.get(User, user_id)
            if change == "disabled":
                user.is_active = False
            else:
                user.auth_version += 1
            db.session.commit()
    assert client.get("/admin/dashboard").location.endswith("/admin/login")


def test_applications_filters_detail_download_and_audit(app, client, monkeypatch, payload):
    from app.admin import routes

    with app.app_context():
        create_user()
        payload["applicant"]["fullName"] = "<script>alert(1)</script>"
        item = Application(
            status="submitted",
            home="minas",
            tracking_code="HE-TEST-001",
            draft_token_hash=secrets.token_hex(32),
            expires_at=utcnow() + timedelta(days=1),
            payload_encrypted=EncryptionService.encrypt(payload),
        )
        db.session.add(item)
        db.session.flush()
        doc = ApplicationDocument(
            application_id=item.id,
            document_type="applicant_id",
            object_key="test/object",
            original_name="cedula.pdf",
            stored_name="documento.pdf",
            media_type="application/pdf",
            size_bytes=8,
            checksum_sha256="x",
        )
        db.session.add(doc)
        db.session.commit()
        item_id, doc_id = item.id, doc.id
        encrypted = EncryptionService.encrypt_file(b"test-pdf")
    login(client, monkeypatch)
    assert "HE-TEST-001" in client.get("/admin/applications?status=submitted&home=minas&q=TEST").text
    assert "HE-TEST-001" not in client.get("/admin/applications?home=montevideo").text
    detail = client.get(f"/admin/applications/{item_id}")
    assert detail.status_code == 200
    assert "&lt;script&gt;" in detail.text
    assert "Nombre completo" in detail.text
    monkeypatch.setattr(routes.StorageService, "get", lambda _self, _key: encrypted)
    result = client.get(f"/admin/applications/{item_id}/documents/{doc_id}")
    assert result.data == b"test-pdf"
    assert "attachment" in result.headers["Content-Disposition"]
    assert result.headers["Cache-Control"] == "no-store"
    assert client.get(f"/admin/applications/other/documents/{doc_id}").status_code == 404
    with app.app_context():
        assert ActivityLog.query.filter_by(action="admin:view_application").count() == 1
        assert ActivityLog.query.filter_by(action="admin:download_document").count() == 1


def test_superadmin_users_logs_and_password(app, client, monkeypatch):
    with app.app_context():
        user_id = create_user(role="super_admin").id
    login(client, monkeypatch)
    assert client.get("/admin/users").status_code == 200
    result = form(
        client,
        "/admin/users",
        {
            "name": "Nuevo admin",
            "email": "new@example.uy",
            "password": "Password1234",
            "role": "admin",
        },
    )
    assert result.status_code == 302
    with app.app_context():
        new_id = User.query.filter_by(email="new@example.uy").one().id
    assert form(client, f"/admin/users/{user_id}/active", {"is_active": "false"}).status_code == 409
    assert form(client, f"/admin/users/{new_id}/active", {"is_active": "false"}).status_code == 302
    assert form(client, f"/admin/users/{new_id}/password", {"new_password": "Updated1234"}).status_code == 302
    with app.app_context():
        user = db.session.get(User, new_id)
        assert not user.is_active
        assert user.check_password("Updated1234")
    assert "admin:create_user" in client.get("/admin/logs?action=create_user").text
    assert client.get("/admin/password").status_code == 200
    result = form(
        client,
        "/admin/password",
        {
            "current_password": "SecurePassword!2026",
            "new_password": "Changed1234",
        },
    )
    assert result.location.endswith("/admin/login")
    assert client.get("/admin/dashboard").status_code == 302


def test_pagination_preserves_filters(app, client, monkeypatch):
    with app.app_context():
        create_user()
        for index in range(26):
            db.session.add(
                Application(
                    home="minas",
                    tracking_code=f"PAGE-{index}",
                    draft_token_hash=secrets.token_hex(32),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        db.session.commit()
    login(client, monkeypatch)
    response = client.get("/admin/applications?home=minas&status=draft")
    assert response.status_code == 200
    assert "page=2" in response.text
    assert "home=minas" in response.text
    assert "26 registros" in client.get("/admin/applications?home=minas&status=draft&page=2").text
