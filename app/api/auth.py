from datetime import timedelta

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db, limiter
from ..models import ActivityLog, TwoFactorCode, User
from ..models.application import utcnow
from ..services.email import send_2fa_email
from ..services.tokens import revoke_token
from .security import require_access

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _audit(action: str, user: User, details: str | None = None) -> None:
    db.session.add(
        ActivityLog(
            user_id=user.id,
            username=user.name,
            action=action,
            details=details,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string[:512],
        )
    )


def _password_error(password: str) -> str | None:
    if len(password) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if not any(character.isalpha() for character in password):
        return "La contraseña debe incluir al menos una letra."
    if not any(character.isdigit() for character in password):
        return "La contraseña debe incluir al menos un número."
    return None


@auth_bp.post("/login")
@limiter.limit("5 per minute; 20 per hour")
def login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Email y contraseña son requeridos."}), 400
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return jsonify({"error": "Credenciales incorrectas."}), 401
    user = User.query.filter_by(email=email, is_active=True).one_or_none()
    if not user or not user.check_password(password):
        return jsonify({"error": "Credenciales incorrectas."}), 401

    TwoFactorCode.query.filter_by(user_id=user.id, consumed_at=None).delete(synchronize_session=False)
    code_entry, code = TwoFactorCode.generate(user)
    _audit("login_step1", user)
    try:
        db.session.commit()
        send_2fa_email(user.email, user.name, code)
    except Exception:
        db.session.rollback()
        if code_entry.id:
            code_entry = db.session.get(TwoFactorCode, code_entry.id)
            if code_entry:
                code_entry.consumed_at = utcnow()
                db.session.commit()
        current_app.logger.exception("No se pudo entregar el código 2FA.")
        return jsonify({"error": "No se pudo enviar el código de verificación."}), 503

    pending_token = create_access_token(
        identity=str(user.id),
        additional_claims={"type": "2fa_pending", "auth_version": user.auth_version},
        expires_delta=timedelta(minutes=10),
    )
    return jsonify({"requires_2fa": True, "pending_token": pending_token})


@auth_bp.post("/verify-2fa")
@jwt_required()
@limiter.limit("10 per minute; 30 per hour")
def verify_2fa():
    claims = get_jwt()
    if claims.get("type") != "2fa_pending":
        return jsonify({"error": "Token inválido para este endpoint."}), 403
    user = db.session.get(User, int(get_jwt_identity()))
    if not user or not user.is_active or claims.get("auth_version") != user.auth_version:
        return jsonify({"error": "La cuenta no está disponible."}), 401
    body = request.get_json(silent=True)
    code = str(body.get("code", "")).strip() if isinstance(body, dict) else ""
    if len(code) != 6 or not code.isdigit():
        return jsonify({"error": "El código debe contener seis dígitos."}), 400
    entry = TwoFactorCode.query.filter_by(user_id=user.id, consumed_at=None).order_by(TwoFactorCode.id.desc()).first()
    if not entry or not entry.verify(code):
        db.session.commit()
        return jsonify({"error": "Código inválido o expirado."}), 401
    user.last_login = utcnow()
    _audit("login_success", user)
    db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"type": "access", "role": user.role, "auth_version": user.auth_version},
    )
    return jsonify({"access_token": token, "user": user.to_dict()})


@auth_bp.get("/me")
@require_access("admin", "super_admin")
def me():
    return jsonify({"user": g.auth_user.to_dict()})


@auth_bp.post("/logout")
@require_access("admin", "super_admin")
def logout():
    claims = get_jwt()
    revoke_token(claims["jti"], claims["exp"])
    _audit("logout", g.auth_user)
    db.session.commit()
    return jsonify({"message": "Sesión cerrada."})


@auth_bp.put("/change-password")
@require_access("admin", "super_admin")
@limiter.limit("5 per hour")
def change_password():
    body = request.get_json(silent=True)
    current_password = str(body.get("current_password", "")) if isinstance(body, dict) else ""
    new_password = str(body.get("new_password", "")) if isinstance(body, dict) else ""
    error = _password_error(new_password)
    if error:
        return jsonify({"error": error}), 422
    user = g.auth_user
    if not user.check_password(current_password):
        return jsonify({"error": "La contraseña actual es incorrecta."}), 401
    user.set_password(new_password)
    user.auth_version += 1
    _audit("change_password", user)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "No se pudo actualizar la contraseña."}), 503
    return jsonify({"message": "Contraseña actualizada correctamente."})
