from app.extensions import db
from app.models import TwoFactorCode, User


def create_user(role="admin", email="admin@example.uy", password="SecurePassword!2026"):
    user = User(name="Admin de prueba", email=email, role=role, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def login_with_2fa(client, monkeypatch, email="admin@example.uy", password="SecurePassword!2026"):
    captured = {}

    def capture_email(_email, _name, code):
        captured["code"] = code

    from app.api import auth

    monkeypatch.setattr(auth, "send_2fa_email", capture_email)
    first = client.post("/api/auth/login", json={"email": email, "password": password})
    assert first.status_code == 200
    pending = first.get_json()["pending_token"]
    second = client.post(
        "/api/auth/verify-2fa",
        json={"code": captured["code"]},
        headers={"Authorization": f"Bearer {pending}"},
    )
    assert second.status_code == 200
    return second.get_json()["access_token"]


def test_create_admin_command_has_expected_signature(app):
    result = app.test_cli_runner().invoke(
        args=["create-admin", "Super Admin", "super@example.uy", "SecurePassword!2026", "true"]
    )
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = User.query.filter_by(email="super@example.uy").one()
        assert user.role == "super_admin"
        assert user.password_hash != "SecurePassword!2026"


def test_login_requires_email_2fa_and_issues_access_token(app, client, monkeypatch):
    with app.app_context():
        create_user()
    token = login_with_2fa(client, monkeypatch)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "admin"


def test_public_auth_alias_matches_sample_backend(app, client, monkeypatch):
    with app.app_context():
        create_user()
    captured = {}
    from app.api import auth

    monkeypatch.setattr(auth, "send_2fa_email", lambda _email, _name, code: captured.update(code=code))
    response = client.post("/auth/login", json={"email": "admin@example.uy", "password": "SecurePassword!2026"})
    assert response.status_code == 200
    assert captured["code"].isdigit()


def test_two_factor_code_is_blocked_after_five_failures(app, client, monkeypatch):
    with app.app_context():
        create_user()
    captured = {}
    from app.api import auth

    monkeypatch.setattr(auth, "send_2fa_email", lambda _email, _name, code: captured.update(code=code))
    login = client.post("/api/auth/login", json={"email": "admin@example.uy", "password": "SecurePassword!2026"})
    pending = login.get_json()["pending_token"]
    headers = {"Authorization": f"Bearer {pending}"}
    for _ in range(5):
        assert client.post("/api/auth/verify-2fa", json={"code": "000000"}, headers=headers).status_code == 401
    assert client.post("/api/auth/verify-2fa", json={"code": captured["code"]}, headers=headers).status_code == 401
    with app.app_context():
        entry = TwoFactorCode.query.one()
        assert entry.attempts == 5
        assert entry.consumed_at is not None


def test_logout_revokes_access_token(app, client, monkeypatch):
    with app.app_context():
        create_user()
    token = login_with_2fa(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_only_super_admin_can_manage_users(app, client, monkeypatch):
    with app.app_context():
        create_user()
    admin_token = login_with_2fa(client, monkeypatch)
    assert client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 403
