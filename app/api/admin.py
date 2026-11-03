from io import BytesIO

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db, limiter
from ..models import ActivityLog, Application, ApplicationDocument, User
from ..services import EncryptionService, StorageService
from ..services.admin_applications import filtered_applications
from .auth import _password_error
from .security import require_access

admin_bp = Blueprint("admin_api", __name__, url_prefix="/admin")


def _audit(action: str, details: str | None = None) -> None:
    user = g.auth_user
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


@admin_bp.get("/applications")
@require_access("admin", "super_admin")
@limiter.limit("120 per hour")
def list_applications():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))
    query = filtered_applications(request.args).order_by(Application.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        {
            "items": [
                {
                    "id": item.id,
                    "trackingCode": item.tracking_code,
                    "status": item.status,
                    "home": item.home,
                    "applicationType": item.application_type,
                    "applicationYear": item.application_year,
                    "createdAt": item.created_at.isoformat() + "Z",
                    "submittedAt": item.submitted_at.isoformat() + "Z" if item.submitted_at else None,
                    "documentCount": len(item.documents),
                }
                for item in pagination.items
            ],
            "page": pagination.page,
            "pages": pagination.pages,
            "total": pagination.total,
        }
    )


@admin_bp.get("/applications/<application_id>")
@require_access("admin", "super_admin")
@limiter.limit("120 per hour")
def application_detail(application_id: str):
    application = db.session.get(Application, application_id)
    if not application:
        return jsonify({"error": "Solicitud no encontrada."}), 404
    payload = EncryptionService.decrypt(application.payload_encrypted)
    if application.status == "submitted" and payload is None:
        return jsonify({"error": "No se pudo descifrar la solicitud."}), 500
    _audit("view_application", application.id)
    db.session.commit()
    return jsonify(
        {
            "id": application.id,
            "trackingCode": application.tracking_code,
            "status": application.status,
            "data": payload,
            "documents": [document.to_dict() for document in application.documents],
        }
    )


@admin_bp.get("/applications/<application_id>/documents/<document_id>")
@require_access("admin", "super_admin")
@limiter.limit("60 per hour")
def download_document(application_id: str, document_id: str):
    document = ApplicationDocument.query.filter_by(id=document_id, application_id=application_id).one_or_none()
    if not document:
        return jsonify({"error": "Documento no encontrado."}), 404
    try:
        encrypted = StorageService().get(document.object_key)
        content = EncryptionService.decrypt_file(encrypted)
    except Exception:
        current_app.logger.exception("No se pudo recuperar el documento %s", document.id)
        return jsonify({"error": "No se pudo recuperar el documento."}), 503
    _audit("download_document", document.id)
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype=document.media_type,
        as_attachment=True,
        download_name=document.stored_name,
        max_age=0,
    )


@admin_bp.get("/users")
@require_access("super_admin")
@limiter.limit("60 per hour")
def list_users():
    return jsonify({"users": [user.to_dict() for user in User.query.order_by(User.name).all()]})


@admin_bp.post("/users")
@require_access("super_admin")
@limiter.limit("20 per hour")
def create_user():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Datos inválidos."}), 400
    name = str(body.get("name", "")).strip()
    password = str(body.get("password", ""))
    role = str(body.get("role", "admin"))
    try:
        email = validate_email(str(body.get("email", "")).strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return jsonify({"error": "Email inválido."}), 422
    if len(name) < 2 or len(name) > 100 or role not in {"admin", "super_admin"}:
        return jsonify({"error": "Nombre o rol inválido."}), 422
    password_error = _password_error(password)
    if password_error:
        return jsonify({"error": password_error}), 422
    user = User(name=name, email=email, role=role, is_active=True)
    user.set_password(password)
    try:
        db.session.add(user)
        db.session.flush()
        _audit("create_user", f"user_id={user.id};role={role}")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Ya existe un usuario con ese email."}), 409
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "No se pudo crear el usuario."}), 503
    return jsonify({"user": user.to_dict()}), 201


@admin_bp.put("/users/<int:user_id>/password")
@require_access("super_admin")
@limiter.limit("10 per hour")
def reset_user_password(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "Usuario no encontrado."}), 404
    body = request.get_json(silent=True)
    password = str(body.get("new_password", "")) if isinstance(body, dict) else ""
    password_error = _password_error(password)
    if password_error:
        return jsonify({"error": password_error}), 422
    user.set_password(password)
    user.auth_version += 1
    _audit("reset_password", f"user_id={user.id}")
    db.session.commit()
    return jsonify({"message": "Contraseña actualizada."})


@admin_bp.put("/users/<int:user_id>/active")
@require_access("super_admin")
@limiter.limit("20 per hour")
def set_user_active(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "Usuario no encontrado."}), 404
    if user.id == g.auth_user.id:
        return jsonify({"error": "No puede desactivar su propia cuenta."}), 409
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("is_active"), bool):
        return jsonify({"error": "is_active debe ser booleano."}), 422
    user.is_active = body["is_active"]
    user.auth_version += 1
    _audit("set_user_active", f"user_id={user.id};active={user.is_active}")
    db.session.commit()
    return jsonify({"user": user.to_dict()})
