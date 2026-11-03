import time

import pytest
import redis
from test_admin_panel import form, login
from test_auth import create_user, login_with_2fa

from app.services import tokens


def test_real_development_session_load_and_logout(app, client, monkeypatch):
    app.config.update(TESTING=False, ENV_NAME="development")
    with app.app_context():
        create_user(role="super_admin")
    # Exercise the actual login -> 2FA -> dashboard sequence without the TESTING branch.
    login(client, monkeypatch)
    for path in ["/admin/dashboard", "/admin/applications", "/admin/users", "/admin/logs"]:
        assert client.get(path).status_code == 200
    cookie = client.get_cookie("hogares_admin").value
    assert form(client, "/admin/logout").status_code == 302
    client.set_cookie("hogares_admin", cookie)
    assert client.get("/admin/dashboard").location.endswith("/admin/login")


def test_real_development_jwt_revocation(app, client, monkeypatch):
    app.config.update(TESTING=False, ENV_NAME="development")
    with app.app_context():
        create_user()
    token = login_with_2fa(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_memory_revocations_expire(app):
    with app.app_context():
        assert not tokens.token_is_revoked("expiry-test")
        tokens.revoke_token("expiry-test", int(time.time()) + 60)
        assert tokens.token_is_revoked("expiry-test")
        store = app.extensions["auth_memory_blocklist"]
        store.expirations["auth:revoked:expiry-test"] = time.time() - 1
        assert not tokens.token_is_revoked("expiry-test")


@pytest.mark.parametrize("failure", [redis.ConnectionError("Unavailable"), ValueError("Invalid URI")])
def test_redis_errors_do_not_allow_access(app, monkeypatch, failure):
    app.config["RATELIMIT_STORAGE_URI"] = "redis://localhost:6379/0"

    def fail():
        raise failure

    monkeypatch.setattr(tokens, "_client", fail)
    with app.app_context():
        assert tokens.token_is_revoked("session-id")


def test_redis_uses_shared_revocations_even_in_tests(app, monkeypatch):
    app.config["RATELIMIT_STORAGE_URI"] = "redis://localhost:6379/0"
    values = {}

    class FakeRedis:
        def setex(self, key, ttl, value):
            assert 0 < ttl <= 60
            values[key] = value

        def exists(self, key):
            return key in values

    monkeypatch.setattr(tokens, "_client", FakeRedis)
    with app.app_context():
        assert not tokens.token_is_revoked("redis-session")
        tokens.revoke_token("redis-session", int(time.time()) + 60)
        assert tokens.token_is_revoked("redis-session")


def test_production_cannot_use_process_local_revocations(app):
    app.config.update(TESTING=False, ENV_NAME="production")
    with pytest.raises(RuntimeError, match="Redis es obligatorio"):
        tokens.init_token_store(app)
