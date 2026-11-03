import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request
from marshmallow import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db, limiter
from ..models import Application, ApplicationDocument, InstitutionSettings
from ..models.application import utcnow
from ..schemas import application_schema
from ..services import EncryptionService, StorageService
from ..services.captcha import create_captcha, verify_captcha
from ..services.documents import DOCUMENT_TYPES, DocumentProcessingError, object_key, process_document
from ..services.email import send_application_confirmation
from ..services.requirements import required_document_types

applications_bp = Blueprint("applications", __name__, url_prefix="/applications")


@applications_bp.get("/institution")
def institution_contact():
    settings = db.session.get(InstitutionSettings, 1)
    return jsonify({"goodConductEmail": settings.good_conduct_email if settings else ""})


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _draft_token() -> str:
    return secrets.token_urlsafe(32)


def _get_authorized_draft(application_id: str, *, lock=False) -> tuple[Application | None, tuple | None]:
    application = db.session.get(Application, application_id, with_for_update=lock)
    if application is None:
        return None, (jsonify({"error": "Borrador no encontrado."}), 404)
    token = request.headers.get("X-Draft-Token", "")
    if not token or not hmac.compare_digest(application.draft_token_hash, _hash_token(token)):
        return None, (jsonify({"error": "Credencial de borrador inválida."}), 403)
    if application.status == "draft" and application.expires_at < utcnow():
        return None, (jsonify({"error": "El borrador expiró."}), 410)
    return application, None


def _tracking_code(year: int) -> str:
    return f"HE{year % 100:02d}-{secrets.token_hex(5).upper()}"


def _submission_receipt(application):
    return {
        "id": application.id,
        "trackingCode": application.tracking_code,
        "submittedAt": application.submitted_at.isoformat() + "Z",
        "confirmationEmailSent": application.confirmation_email_sent_at is not None,
    }


def _send_confirmation(application_id):
    # Serialize delivery/retries so concurrent requests don't send the same confirmation twice.
    application = db.session.get(Application, application_id, with_for_update=True, populate_existing=True)
    if application.confirmation_email_sent_at:
        db.session.commit()
        return True
    payload = EncryptionService.decrypt(application.payload_encrypted) or {}
    if not payload.get("contactEmail"):
        db.session.rollback()
        return False
    try:
        send_application_confirmation(application, payload["contactEmail"])
        application.confirmation_email_sent_at = utcnow()
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("No se pudo enviar la confirmación de la solicitud %s", application_id)
        return False


@applications_bp.get("/drafts/<application_id>/captcha")
@limiter.limit("60 per hour")
def confirmation_captcha(application_id):
    application, error = _get_authorized_draft(application_id)
    if error:
        return error
    if application.status != "draft":
        return jsonify({"error": "La solicitud ya fue enviada."}), 409
    return jsonify(create_captcha(application.id))


@applications_bp.post("/drafts")
@limiter.limit("5 per hour; 20 per day")
def create_draft():
    token = _draft_token()
    expires_at = utcnow() + timedelta(days=current_app.config["DRAFT_TTL_DAYS"])
    application = Application(draft_token_hash=_hash_token(token), expires_at=expires_at)
    try:
        db.session.add(application)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("No se pudo crear el borrador.")
        return jsonify({"error": "No se pudo crear la solicitud."}), 503
    return jsonify({"id": application.id, "token": token, "expiresAt": expires_at.isoformat() + "Z"}), 201


@applications_bp.get("/drafts/<application_id>")
@limiter.limit("60 per hour")
def get_draft(application_id: str):
    application, error = _get_authorized_draft(application_id)
    if error:
        return error
    return jsonify(
        {
            "id": application.id,
            "status": application.status,
            "expiresAt": application.expires_at.isoformat() + "Z",
            "documents": [document.to_dict() for document in application.documents],
            "trackingCode": application.tracking_code,
        }
    )


@applications_bp.post("/drafts/<application_id>/documents")
@limiter.limit("40 per hour; 100 per day")
def upload_document(application_id: str):
    application, error = _get_authorized_draft(application_id)
    if error:
        return error
    if application.status != "draft":
        return jsonify({"error": "La solicitud ya fue enviada y no admite cambios."}), 409
    document_type = request.form.get("type", "").strip()
    if document_type == "good_conduct":
        return jsonify(
            {"error": "El certificado debe enviarlo el Ministerio del Interior directamente a la institución."}
        ), 422
    if document_type not in DOCUMENT_TYPES:
        return jsonify({"error": "Tipo de documento inválido."}), 422
    try:
        processed = process_document(request.files.get("file"))
    except DocumentProcessingError as exc:
        return jsonify({"error": str(exc)}), 422

    new_key = object_key(application.id, document_type, processed.media_type)
    old_document = ApplicationDocument.query.filter_by(
        application_id=application.id, document_type=document_type
    ).one_or_none()
    old_key = old_document.object_key if old_document else None
    storage = StorageService()
    try:
        encrypted_content = EncryptionService.encrypt_file(processed.content)
        storage.put(new_key, encrypted_content)
        document = old_document or ApplicationDocument(application_id=application.id, document_type=document_type)
        document.object_key = new_key
        document.original_name = processed.original_name
        document.stored_name = processed.stored_name
        document.media_type = processed.media_type
        document.size_bytes = len(processed.content)
        document.checksum_sha256 = processed.checksum
        document.width = processed.width
        document.height = processed.height
        db.session.add(document)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        try:
            storage.remove(new_key)
        except Exception:
            current_app.logger.exception("No se pudo compensar un archivo luego de un fallo de base de datos.")
        current_app.logger.exception("No se pudo registrar el documento.")
        return jsonify({"error": "No se pudo guardar el documento."}), 503
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló el almacenamiento del documento.")
        return jsonify({"error": "El almacenamiento no está disponible temporalmente."}), 503

    if old_key and old_key != new_key:
        try:
            storage.remove(old_key)
        except Exception:
            current_app.logger.exception("Quedó un objeto anterior pendiente de limpieza: %s", old_key)
    return jsonify({"document": document.to_dict()}), 201


@applications_bp.post("/drafts/<application_id>/submit")
@limiter.limit("10 per hour; 20 per day")
def submit_draft(application_id: str):
    application, error = _get_authorized_draft(application_id, lock=True)
    if error:
        return error
    if application.status == "submitted":
        return jsonify(_submission_receipt(application))
    if not request.is_json:
        return jsonify({"error": "El contenido debe ser application/json."}), 415
    try:
        raw_payload = request.get_json(silent=False)
        if not isinstance(raw_payload, dict):
            raise ValidationError({"_schema": ["Se esperaba un objeto JSON."]})
        if isinstance(raw_payload.get("contactEmail"), str):
            raw_payload["contactEmail"] = raw_payload["contactEmail"].strip().lower()
        payload = application_schema.load(raw_payload)
    except ValidationError as exc:
        return jsonify({"error": "La solicitud contiene datos inválidos.", "fields": exc.messages}), 422

    if not verify_captcha(
        application.id,
        request.headers.get("X-Captcha-Token", ""),
        request.headers.get("X-Captcha-Answer", ""),
    ):
        return jsonify(
            {
                "error": "La verificación numérica es incorrecta o expiró.",
                "fields": {
                    "captcha": ["Resolvé una nueva verificación para enviar la solicitud."],
                },
            }
        ), 422

    uploaded_types = {document.document_type for document in application.documents}
    missing = sorted(required_document_types(payload) - uploaded_types)
    if missing:
        return jsonify({"error": "Falta documentación obligatoria.", "missingDocuments": missing}), 422

    payload["documents"] = [document.to_dict() for document in application.documents]
    application.payload_encrypted = EncryptionService.encrypt(payload)
    application.home = payload["request"]["home"]
    application.application_type = payload["request"]["type"]
    application.status = "submitted"
    application.submitted_at = utcnow()
    application.tracking_code = _tracking_code(application.application_year)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("No se pudo finalizar la solicitud.")
        return jsonify({"error": "No se pudo finalizar la solicitud."}), 503
    _send_confirmation(application.id)
    return jsonify(_submission_receipt(application)), 201


@applications_bp.post("/drafts/<application_id>/confirmation-email")
@limiter.limit("3 per hour; 5 per day")
def retry_confirmation_email(application_id):
    application, error = _get_authorized_draft(application_id)
    if error:
        return error
    if application.status != "submitted":
        return jsonify({"error": "La solicitud todavía no fue enviada."}), 409
    if not _send_confirmation(application.id):
        return jsonify(
            {"error": "El trámite está registrado, pero no se pudo enviar el correo. Intentá más tarde."}
        ), 503
    return jsonify(_submission_receipt(application))


@applications_bp.post("/track")
@limiter.limit("10 per minute; 30 per hour")
def track_application():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Ingresá el código de seguimiento y el correo registrado."}), 400
    code, email = body.get("trackingCode"), body.get("email")
    if not isinstance(code, str) or not isinstance(email, str) or len(code) > 24 or len(email) > 254:
        return jsonify({"error": "Ingresá un código y un correo válidos."}), 422
    application = Application.query.filter_by(tracking_code=code.strip().upper(), status="submitted").one_or_none()
    payload = EncryptionService.decrypt(application.payload_encrypted) if application else None
    stored_email = (payload or {}).get("contactEmail", "")
    if not stored_email or not hmac.compare_digest(stored_email.lower().encode(), email.strip().lower().encode()):
        return jsonify({"error": "No encontramos un trámite con ese código y correo. Revisá ambos datos."}), 404
    return jsonify(
        {
            "trackingCode": application.tracking_code,
            "status": application.status,
            "statusLabel": "Recibida",
            "message": "Tu solicitud quedó registrada. La recepción no implica la adjudicación de la beca.",
            "submittedAt": application.submitted_at.isoformat() + "Z",
            "home": application.home,
            "applicationType": application.application_type,
            "applicationYear": application.application_year,
        }
    )


@applications_bp.get("/receipts/<tracking_code>")
@limiter.limit("30 per hour")
def receipt(tracking_code: str):
    application = Application.query.filter_by(tracking_code=tracking_code, status="submitted").one_or_none()
    if application is None:
        return jsonify({"error": "Comprobante no encontrado."}), 404
    token = request.headers.get("X-Draft-Token", "")
    if not token or not hmac.compare_digest(application.draft_token_hash, _hash_token(token)):
        return jsonify({"error": "Credencial inválida."}), 403
    return jsonify(
        {
            "trackingCode": application.tracking_code,
            "submittedAt": application.submitted_at.isoformat() + "Z",
            "home": application.home,
            "applicationType": application.application_type,
            "applicationYear": application.application_year,
        }
    )
