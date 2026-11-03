"""Isolated real HTTP API for browser registration tests; never uses production services."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import TestConfig  # noqa: E402

from app import create_app, health  # noqa: E402
from app.api import applications  # noqa: E402
from app.extensions import db  # noqa: E402


class MemoryStorage:
    """Stub only the external object store; production encryption/processing remain active."""

    objects = {}
    lock = RLock()

    def ensure_bucket(self):
        pass

    def ready(self):
        return True

    def put(self, key, content):
        with self.lock:
            self.objects[key] = bytes(content)

    def get(self, key):
        with self.lock:
            return self.objects[key]

    def remove(self, key):
        with self.lock:
            self.objects.pop(key, None)


def main():
    port = int(os.getenv("BROWSER_TEST_PORT", "5001"))
    origin = os.getenv("BROWSER_TEST_FRONTEND_ORIGIN", "http://127.0.0.1:4173")
    if urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("El servidor de pruebas solo acepta un frontend local.")
    with TemporaryDirectory(prefix="hogares-browser-tests-") as directory:
        database = Path(directory) / "browser.sqlite"
        app = create_app(TestConfig, {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
            "CORS_ALLOWED_ORIGINS": [origin],
            "APPLICATION_YEAR": 2027,
            "MAIL_SUPPRESS_SEND": True,
            "TRUST_PROXY": False,
            "FORCE_HTTPS": False,
            "SESSION_COOKIE_SECURE": False,
        })
        applications.StorageService = MemoryStorage
        health.StorageService = MemoryStorage
        with app.app_context():
            db.create_all()
        print(f"BROWSER_TEST_API_READY http://127.0.0.1:{port} (SQLite temporal, sin correo real)", flush=True)
        try:
            app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == "__main__":
    main()
