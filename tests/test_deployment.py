from pathlib import Path

from conftest import TestConfig as AppTestConfig
from flask import jsonify
from flask_migrate import check, downgrade, upgrade
from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def test_migrations_upgrade_downgrade_and_model_match(tmp_path):
    app = create_app(AppTestConfig, {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"})
    directory = str(Path(__file__).parents[1] / "migrations")
    with app.app_context():
        upgrade(directory=directory)
        assert "good_conduct_received_at" in {
            column["name"] for column in inspect(db.engine).get_columns("applications")
        }
        check(directory=directory)
        downgrade(directory=directory, revision="base")
        assert "applications" not in inspect(db.engine).get_table_names()
        upgrade(directory=directory)
        check(directory=directory)
        db.session.remove()
        db.engine.dispose()


def test_secure_admin_cookie_even_when_proxy_handles_https():
    app = create_app(AppTestConfig, {"SESSION_COOKIE_SECURE": True, "FORCE_HTTPS": False})
    response = app.test_client().get("/admin/login")
    cookie = response.headers["Set-Cookie"]
    assert "Secure;" in cookie and "HttpOnly;" in cookie and "SameSite=Lax" in cookie


def test_service_check_authenticates_smtp_without_sending(app, monkeypatch):
    from app import commands, health

    monkeypatch.setattr(
        health, "ready", lambda: (jsonify(checks={"database": True, "storage": True, "redis": True}), 200)
    )
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, **kwargs):
            calls.append("tls")

        def login(self, *args):
            calls.append("login")

        def noop(self):
            return 250, b"OK"

    app.config.update({"MAIL_USE_TLS": True, "MAIL_USE_SSL": False, "MAIL_USERNAME": "test", "MAIL_PASSWORD": "test"})
    monkeypatch.setattr(commands.smtplib, "SMTP", SMTP)
    result = app.test_cli_runner().invoke(args=["check-services", "--smtp"])
    assert result.exit_code == 0, result.output
    assert calls == ["ehlo", "tls", "ehlo", "login"]
    assert "no se enviaron correos" in result.output
