from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from .extensions import db, limiter
from .services import StorageService

health_bp = Blueprint("health", __name__)


@health_bp.get("/health/live")
@limiter.exempt
def live():
    return jsonify({"status": "ok"})


@health_bp.get("/health/ready")
@limiter.exempt
def ready():
    checks = {"database": False, "storage": False, "redis": False}
    try:
        checks["redis"] = limiter.storage.check()
    except Exception:
        current_app.logger.exception("Falló el readiness de Redis.")
    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        current_app.logger.exception("Falló el readiness de base de datos.")
    try:
        storage = StorageService()
        storage.ensure_bucket()
        checks["storage"] = storage.ready()
    except Exception:
        current_app.logger.exception("Falló el readiness de MinIO.")
    status = 200 if all(checks.values()) else 503
    return jsonify({"status": "ready" if status == 200 else "unavailable", "checks": checks}), status
