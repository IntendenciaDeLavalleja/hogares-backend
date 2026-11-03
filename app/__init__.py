import uuid

from flask import Flask, g, jsonify, render_template, request
from flask_cors import CORS
from flask_limiter.errors import RateLimitExceeded
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import db, jwt, limiter, mail, migrate, talisman
from .services.tokens import init_token_store, token_is_revoked


def create_app(config_class=Config, config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)
    if config_overrides:
        app.config.update(config_overrides)
    config_class.validate()
    init_token_store(app)
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)
    jwt.init_app(app)
    mail.init_app(app)
    talisman.init_app(
        app,
        force_https=app.config.get("FORCE_HTTPS", False),
        session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", False),
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        content_security_policy={
            "default-src": "'none'", "style-src": "'self'", "img-src": "'self'",
            "form-action": "'self'", "base-uri": "'none'", "frame-ancestors": "'none'",
        },
        referrer_policy="no-referrer",
    )
    CORS(
        app,
        resources={
            r"/api/*": {"origins": app.config["CORS_ALLOWED_ORIGINS"]},
            r"/auth/*": {"origins": app.config["CORS_ALLOWED_ORIGINS"]},
        },
        supports_credentials=False,
        allow_headers=["Authorization", "Content-Type", "X-Draft-Token", "X-Captcha-Token", "X-Captcha-Answer"],
        methods=["GET", "POST", "PUT", "OPTIONS"],
        max_age=600,
    )

    @jwt.token_in_blocklist_loader
    def jwt_blocklist(_header, payload):
        return token_is_revoked(payload.get("jti", ""))

    @jwt.unauthorized_loader
    def jwt_missing(_reason):
        return jsonify({"error": "Token requerido."}), 401

    @jwt.invalid_token_loader
    def jwt_invalid(_reason):
        return jsonify({"error": "Token inválido."}), 422

    @jwt.expired_token_loader
    def jwt_expired(_header, _payload):
        return jsonify({"error": "Token expirado."}), 401

    @jwt.revoked_token_loader
    def jwt_revoked(_header, _payload):
        return jsonify({"error": "La sesión fue cerrada."}), 401

    from .api import api_bp
    from .api.auth import auth_bp
    from .health import health_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp, name="auth_public")
    app.register_blueprint(health_bp)
    from .admin import admin_bp

    app.register_blueprint(admin_bp)

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.ico")
    from . import models  # noqa: F401
    from .commands import check_services, cleanup_drafts, create_admin

    app.cli.add_command(cleanup_drafts)
    app.cli.add_command(create_admin)
    app.cli.add_command(check_services)

    @app.before_request
    def request_context():
        g.request_id = request.headers.get("X-Request-ID", "")[:64] or str(uuid.uuid4())

    @app.after_request
    def security_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.errorhandler(RateLimitExceeded)
    def rate_limited(_error):
        return error_response("Demasiadas solicitudes. Intente nuevamente más tarde.", 429)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return error_response("La solicitud supera el tamaño máximo permitido.", 413)

    @app.errorhandler(ValidationError)
    def invalid_payload(error):
        return jsonify({"error": "Datos inválidos.", "fields": error.messages}), 422

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code in {403, 404}:
            if not is_api_request():
                return render_template("errors/not_found.html"), 404
            return jsonify({"error": "No encontrado." if error.code == 404 else "Acceso denegado."}), error.code
        return error_response(error.description, error.code)

    def is_api_request():
        return request.path in {"/api", "/auth"} or request.path.startswith(("/api/", "/auth/"))

    def error_response(message, code):
        if not is_api_request() and request.path != "/health" and not request.path.startswith("/health/"):
            return render_template("errors/error.html", message=message, code=code), code
        return jsonify({"error": message}), code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        db.session.rollback()
        app.logger.exception("Error no controlado. request_id=%s", getattr(g, "request_id", "unknown"), exc_info=error)
        if not is_api_request() and not request.path.startswith("/health/"):
            return error_response("No se pudo completar la operación. Intentá nuevamente más tarde.", 500)
        return jsonify({"error": "Error interno.", "requestId": getattr(g, "request_id", None)}), 500

    return app
