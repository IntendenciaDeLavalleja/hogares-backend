import secrets
import time
from collections import defaultdict
from functools import wraps
from io import BytesIO

from email_validator import EmailNotValidError, validate_email
from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from sqlalchemy.exc import IntegrityError

from ..api.auth import _password_error
from ..extensions import db, limiter
from ..models import (
    ActivityLog,
    Application,
    ApplicationDocument,
    InstitutionSettings,
    PonderadorSettings,
    TwoFactorCode,
    User,
)
from ..models.application import utcnow
from ..services import EncryptionService, StorageService
from ..services.admin_applications import (
    archived_applications,
    comparison_row,
    export_csv,
    export_review_csv,
    filtered_applications,
    review_applications,
    suggested_archive_name,
)
from ..services.email import send_2fa_email
from ..services.ponderador import (
    build_ranking,
    effective_weights,
    export_ranking_csv,
    load_ponderador_settings,
    parse_weights,
    weight_text,
)
from ..services.tokens import revoke_token, token_is_revoked
from . import admin_bp


def audit(action, details=None):
    user = g.admin_user
    db.session.add(
        ActivityLog(
            user_id=user.id,
            username=user.name,
            action=f"admin:{action}",
            details=details,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string[:512],
        )
    )


def csrf_token():
    if "admin_csrf" not in session:
        session["admin_csrf"] = secrets.token_urlsafe(32)
    return session["admin_csrf"]


@admin_bp.before_request
def load_session():
    g.admin_user = None
    user_id = session.get("admin_user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if (
            user
            and user.is_active
            and user.role in {"admin", "super_admin"}
            and user.auth_version == session.get("admin_auth_version")
            and session.get("admin_expires", 0) > time.time()
            and not token_is_revoked(session.get("admin_session_id", ""))
        ):
            g.admin_user = user
        else:
            session.clear()
    if request.method == "POST":
        expected = session.get("admin_csrf", "")
        if not expected or not secrets.compare_digest(expected.encode(), request.form.get("csrf_token", "").encode()):
            abort(400, description="El formulario expiró. Recargá la página e intentá nuevamente.")


@admin_bp.context_processor
def template_context():
    return {"admin_user": g.admin_user, "csrf_token": csrf_token}


def require_admin(super_admin=False):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not g.admin_user:
                return redirect(url_for("admin.login"))
            if super_admin and not g.admin_user.is_super_admin:
                abort(403)
            return function(*args, **kwargs)

        return wrapped

    return decorator


def captcha():
    a, b = secrets.randbelow(20) + 1, secrets.randbelow(20) + 1
    session["captcha_answer"] = str(a + b)
    session["captcha_question"] = f"{a} + {b}"


@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def login():
    if g.admin_user:
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        answer = session.pop("captcha_answer", None)
        email = request.form.get("email", "").strip().lower()
        if answer is None or request.form.get("captcha", "").strip() != answer:
            flash("Verificación de seguridad incorrecta.", "error")
        else:
            user = User.query.filter_by(email=email, is_active=True).one_or_none()
            if (
                not user
                or user.role not in {"admin", "super_admin"}
                or not user.check_password(request.form.get("password", ""))
            ):
                flash("Credenciales incorrectas.", "error")
            else:
                session.pop("admin_pending", None)
                TwoFactorCode.query.filter_by(user_id=user.id, consumed_at=None).delete(synchronize_session=False)
                entry, code = TwoFactorCode.generate(user)
                db.session.commit()
                try:
                    send_2fa_email(user.email, user.name, code)
                except Exception:
                    entry.consumed_at = utcnow()
                    db.session.commit()
                    current_app.logger.exception("No se pudo enviar el código del panel.")
                    flash("No se pudo enviar el código. Intentá nuevamente más tarde.", "error")
                else:
                    session.clear()
                    session["admin_pending"] = {
                        "user_id": user.id,
                        "entry_id": entry.id,
                        "version": user.auth_version,
                    }
                    return redirect(url_for("admin.verify_2fa"))
    captcha()
    return render_template("admin/login.html")


@admin_bp.route("/2fa", methods=["GET", "POST"])
@limiter.limit("10 per minute; 30 per hour", methods=["POST"])
def verify_2fa():
    pending = session.get("admin_pending")
    if not pending:
        return redirect(url_for("admin.login"))
    user = db.session.get(User, pending["user_id"])
    entry = db.session.get(TwoFactorCode, pending["entry_id"])
    if (
        not user
        or not user.is_active
        or user.role not in {"admin", "super_admin"}
        or user.auth_version != pending["version"]
        or not entry
        or entry.user_id != user.id
        or entry.consumed_at
        or entry.expires_at < utcnow()
        or entry.attempts >= 5
    ):
        session.clear()
        flash("La verificación expiró. Iniciá sesión nuevamente.", "error")
        return redirect(url_for("admin.login"))
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not entry.verify(code if len(code) == 6 and code.isascii() and code.isdigit() else ""):
            db.session.commit()
            flash("Código inválido o expirado.", "error")
        else:
            user.last_login = utcnow()
            g.admin_user = user
            audit("login_success")
            db.session.commit()
            session.clear()
            session.permanent = True
            session.update(
                admin_user_id=user.id,
                admin_auth_version=user.auth_version,
                admin_session_id=secrets.token_urlsafe(32),
                admin_expires=int(time.time() + current_app.permanent_session_lifetime.total_seconds()),
            )
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/verify_2fa.html")


@admin_bp.post("/logout")
@require_admin()
def logout():
    revoke_token(session["admin_session_id"], session["admin_expires"])
    audit("logout")
    db.session.commit()
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("")
@admin_bp.get("/")
@admin_bp.get("/dashboard")
@require_admin()
def dashboard():
    active = Application.archived_at.is_(None)
    totals = {
        "Solicitudes": Application.query.filter(active).count(),
        "Enviadas": Application.query.filter(active, Application.status == "submitted").count(),
        "Borradores": Application.query.filter(active, Application.status == "draft").count(),
        "Documentos": ApplicationDocument.query.join(Application).filter(active).count(),
        "Archivadas": Application.query.filter(Application.archived_at.is_not(None)).count(),
    }
    recent = Application.query.filter(active).order_by(Application.created_at.desc()).limit(10).all()
    return render_template("admin/dashboard.html", totals=totals, items=recent)


@admin_bp.get("/applications")
@require_admin()
def applications():
    query = filtered_applications(request.args)
    pagination = query.order_by(Application.created_at.desc()).paginate(
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=25,
        error_out=False,
    )
    return render_template(
        "admin/applications.html",
        pagination=pagination,
        items=pagination.items,
        suggested_archive_name=suggested_archive_name(request.args),
    )


@admin_bp.get("/applications/export.csv")
@require_admin()
@limiter.limit("20 per hour")
def applications_csv():
    items = filtered_applications(request.args).order_by(Application.created_at.desc()).all()
    content = export_csv(items)
    audit("export_applications_csv", f"count={len(items)}")
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="inscripciones.csv",
        max_age=0,
    )


@admin_bp.post("/applications/archive")
@require_admin()
@limiter.limit("10 per hour")
def archive_applications():
    scope = request.form.get("scope")
    if scope not in {"selected", "filtered"}:
        abort(400)
    archive_name = request.form.get("archive_name", "").strip()
    if not 3 <= len(archive_name) <= 120:
        flash("Ingresá un nombre de convocatoria de entre 3 y 120 caracteres.", "error")
        return redirect(url_for("admin.applications", **request.args.to_dict(flat=False)))
    if scope == "selected":
        application_ids = request.form.getlist("application_id")
        if not application_ids:
            flash("Seleccioná al menos una solicitud enviada para archivar.", "error")
            return redirect(url_for("admin.applications", **request.args.to_dict(flat=False)))
        query = Application.query.filter(
            Application.id.in_(application_ids),
            Application.status == "submitted",
            Application.archived_at.is_(None),
        )
    else:
        query = filtered_applications(request.args).filter(Application.status == "submitted")
    items = query.all()
    if not items:
        flash("No hay solicitudes enviadas para archivar con los filtros seleccionados.", "error")
        return redirect(url_for("admin.applications", **request.args.to_dict(flat=False)))
    now = utcnow()
    for item in items:
        item.archived_at = now
        item.archive_name = archive_name
        item.archived_by = g.admin_user.id
    audit("archive_applications", f"count={len(items)};name={archive_name};scope={scope}")
    db.session.commit()
    flash(
        f"{len(items)} {'solicitud archivada' if len(items) == 1 else 'solicitudes archivadas'} en «{archive_name}».",
        "success",
    )
    return redirect(url_for("admin.archived", archive=archive_name))


@admin_bp.get("/archived")
@require_admin()
def archived():
    query = archived_applications(request.args)
    items = query.order_by(
        Application.archive_name,
        Application.application_year,
        Application.home,
        Application.application_type,
        Application.submitted_at,
        Application.id,
    ).all()
    grouped = defaultdict(list)
    for item in items:
        grouped[item.archive_name or "Sin nombre"].append(comparison_row(item))
    groups = []
    for name, rows in grouped.items():
        groups.append(
            {
                "name": name,
                "rows": rows,
                "count": len(rows),
                "archived_at": max(row["item"].archived_at for row in rows),
            }
        )
    groups.sort(key=lambda group: group["archived_at"], reverse=True)
    archive_names = [
        row[0]
        for row in db.session.query(Application.archive_name)
        .filter(Application.archived_at.is_not(None), Application.archive_name.is_not(None))
        .distinct()
        .order_by(Application.archive_name)
        .all()
    ]
    return render_template(
        "admin/archived.html",
        groups=groups,
        archive_names=archive_names,
        result_count=len(items),
        export_args=request.args.to_dict(flat=False),
    )


@admin_bp.get("/archived/export.csv")
@require_admin()
@limiter.limit("20 per hour")
def archived_csv():
    items = archived_applications(request.args).order_by(
        Application.archive_name,
        Application.submitted_at,
        Application.id,
    ).all()
    content = export_csv(items, include_archive=True)
    audit("export_archived_csv", f"count={len(items)}")
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="solicitudes-archivadas.csv",
        max_age=0,
    )


@admin_bp.post("/archived/restore")
@require_admin()
@limiter.limit("10 per hour")
def restore_archive():
    archive_name = request.form.get("archive_name", "").strip()
    if not archive_name or len(archive_name) > 120:
        abort(400)
    items = Application.query.filter(
        Application.archived_at.is_not(None),
        Application.archive_name == archive_name,
    ).all()
    if not items:
        flash("No se encontraron solicitudes archivadas en esa convocatoria.", "error")
        return redirect(url_for("admin.archived"))
    for item in items:
        item.archived_at = None
        item.archive_name = None
        item.archived_by = None
    audit("restore_archive", f"count={len(items)};name={archive_name}")
    db.session.commit()
    flash(
        f"{len(items)} {'solicitud restaurada' if len(items) == 1 else 'solicitudes restauradas'} de «{archive_name}».",
        "success",
    )
    return redirect(url_for("admin.archived"))


@admin_bp.get("/comparison")
@require_admin()
def comparison():
    return render_household_review("admin/comparison.html")


def render_household_review(template):
    query, homes, kinds = review_applications(request.args)
    items = query.order_by(
        Application.application_year,
        Application.home,
        Application.application_type,
        Application.created_at,
        Application.id,
    ).all()
    groups = defaultdict(list)
    for item in items:
        groups[(item.application_year, item.home, item.application_type)].append(comparison_row(item))
    settings = load_ponderador_settings()
    income_weight, schooling_weight = effective_weights(settings)
    ranking, excluded = build_ranking(items, income_weight, schooling_weight)
    return render_template(
        template,
        groups=groups,
        ranking=ranking,
        excluded=excluded,
        rankable_count=len(ranking),
        excluded_count=len(excluded),
        income_weight=income_weight,
        schooling_weight=schooling_weight,
        selected_homes=homes,
        selected_types=kinds,
        result_count=len(items),
        export_args=request.args.to_dict(flat=False),
        export_endpoint="admin.comparison_ranking_csv",
        export_label="Aplicar ponderación y descargar CSV",
        secondary_endpoint="admin.comparison_csv",
        secondary_label="Descargar datos descriptivos",
    )


@admin_bp.get("/comparison/export.csv")
@require_admin()
@limiter.limit("20 per hour")
def comparison_csv():
    query, _homes, _kinds = review_applications(request.args)
    items = query.order_by(
        Application.application_year,
        Application.home,
        Application.application_type,
        Application.created_at,
        Application.id,
    ).all()
    content = export_review_csv(items)
    audit("export_household_review_csv", f"count={len(items)}")
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="comparacion-ingresos-escolaridad.csv",
        max_age=0,
    )


def _ranking_csv_response():
    settings = load_ponderador_settings()
    income_weight, schooling_weight = effective_weights(settings)
    query, _homes, _kinds = review_applications(request.args)
    items = query.order_by(
        Application.application_year,
        Application.home,
        Application.application_type,
        Application.created_at,
        Application.id,
    ).all()
    ranking, _excluded = build_ranking(items, income_weight, schooling_weight)
    content = export_ranking_csv(ranking, income_weight, schooling_weight)
    audit(
        "export_ponderador_csv",
        f"count={len(ranking)};income={income_weight};schooling={schooling_weight}",
    )
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="ranking-ponderado.csv",
        max_age=0,
    )


@admin_bp.get("/comparison/ranking.csv")
@require_admin()
@limiter.limit("20 per hour")
def comparison_ranking_csv():
    return _ranking_csv_response()


@admin_bp.get("/ponderador/export.csv")
@admin_bp.get("/ponderador/ranking.csv")
@require_admin()
@limiter.limit("20 per hour")
def ponderador_csv():
    return _ranking_csv_response()


@admin_bp.route("/ponderador", methods=["GET", "POST"])
@require_admin()
@limiter.limit("30 per hour", methods=["POST"])
def ponderador():
    settings = load_ponderador_settings()
    income_input = schooling_input = None
    if request.method == "POST":
        income_input = request.form.get("income_weight", "")
        schooling_input = request.form.get("schooling_weight", "")
        try:
            income_weight, schooling_weight = parse_weights(income_input, schooling_input)
        except ValueError as error:
            flash(str(error), "error")
        else:
            if settings is None:
                settings = PonderadorSettings(id=1)
                db.session.add(settings)
            settings.income_weight = income_weight
            settings.schooling_weight = schooling_weight
            audit("update_ponderador_weights", f"income={income_weight};schooling={schooling_weight}")
            db.session.commit()
            flash("Ponderación guardada.", "success")
            return redirect(url_for("admin.ponderador"))
    if income_input is None:
        income_weight, schooling_weight = effective_weights(settings)
        income_input = weight_text(income_weight)
        schooling_input = weight_text(schooling_weight)
    return render_template(
        "admin/ponderador.html",
        settings=settings,
        income_input=income_input,
        schooling_input=schooling_input,
    )


@admin_bp.post("/applications/<application_id>/good-conduct")
@require_admin()
def good_conduct(application_id):
    item = db.get_or_404(Application, application_id)
    if item.status != "submitted":
        abort(409, description="Solo se puede registrar la recepción en solicitudes enviadas.")
    value = request.form.get("received")
    if value not in {"true", "false"}:
        abort(400)
    if value == "true" and not item.good_conduct_received_at:
        item.good_conduct_received_at = utcnow()
        item.good_conduct_received_by = g.admin_user.id
    elif value == "false":
        item.good_conduct_received_at = None
        item.good_conduct_received_by = None
    audit("good_conduct_receipt", f"application_id={item.id};received={value}")
    db.session.commit()
    flash("Recepción del certificado actualizada.", "success")
    return redirect(url_for("admin.application_detail", application_id=item.id))


@admin_bp.route("/settings", methods=["GET", "POST"])
@require_admin(super_admin=True)
def settings():
    settings = db.session.get(InstitutionSettings, 1)
    if request.method == "POST":
        try:
            email = validate_email(
                request.form.get("good_conduct_email", "").strip(), check_deliverability=False
            ).normalized.lower()
        except EmailNotValidError:
            flash("Ingresá un correo institucional válido.", "error")
        else:
            if settings is None:
                settings = InstitutionSettings(id=1)
                db.session.add(settings)
            settings.good_conduct_email = email
            audit("update_institution_contact")
            db.session.commit()
            flash("Correo institucional actualizado.", "success")
            return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", settings=settings)


@admin_bp.get("/applications/<application_id>/file.pdf")
@require_admin()
@limiter.limit("20 per hour")
def application_pdf(application_id):
    from ..services.application_pdf import build_application_pdf

    item = db.get_or_404(Application, application_id)
    if item.status != "submitted":
        abort(409, description="La ficha está disponible para solicitudes enviadas.")
    try:
        content = build_application_pdf(item)
    except Exception:
        current_app.logger.exception("No se pudo generar la ficha PDF %s", item.id)
        abort(503, description="No se pudo generar la ficha completa con sus adjuntos. Intentá nuevamente más tarde.")
    audit("export_application_pdf", item.id)
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"ficha-{item.tracking_code or item.id}.pdf",
        max_age=0,
    )


@admin_bp.get("/applications/<application_id>")
@require_admin()
@limiter.limit("120 per hour")
def application_detail(application_id):
    item = db.get_or_404(Application, application_id)
    data = EncryptionService.decrypt(item.payload_encrypted)
    if item.payload_encrypted and data is None:
        abort(503, description="No se pudo descifrar la solicitud.")
    audit("view_application", item.id)
    db.session.commit()
    receiver = db.session.get(User, item.good_conduct_received_by) if item.good_conduct_received_by else None
    return render_template(
        "admin/application.html",
        item=item,
        data=data or {},
        receiver=receiver,
        institution=db.session.get(InstitutionSettings, 1),
    )


@admin_bp.get("/applications/<application_id>/documents/<document_id>")
@require_admin()
@limiter.limit("60 per hour")
def download_document(application_id, document_id):
    document = ApplicationDocument.query.filter_by(id=document_id, application_id=application_id).first_or_404()
    try:
        content = EncryptionService.decrypt_file(StorageService().get(document.object_key))
    except Exception:
        current_app.logger.exception("No se pudo recuperar el documento %s", document.id)
        abort(503, description="No se pudo recuperar el documento. Intentá nuevamente más tarde.")
    audit("download_document", document.id)
    db.session.commit()
    return send_file(
        BytesIO(content),
        mimetype=document.media_type,
        as_attachment=True,
        download_name=document.stored_name,
        max_age=0,
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@require_admin(super_admin=True)
@limiter.limit("20 per hour", methods=["POST"])
def users():
    if request.method == "POST":
        name, role = request.form.get("name", "").strip(), request.form.get("role", "admin")
        password = request.form.get("password", "")
        error = _password_error(password)
        try:
            email = validate_email(request.form.get("email", ""), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            error = "Email inválido."
        if not 2 <= len(name) <= 100 or role not in {"admin", "super_admin"}:
            error = "Nombre o rol inválido."
        if error:
            flash(error, "error")
        else:
            user = User(name=name, email=email, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.flush()
                audit("create_user", f"user_id={user.id};role={role}")
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Ya existe un usuario con ese email.", "error")
            else:
                flash("Usuario creado.", "success")
                return redirect(url_for("admin.users"))
    pagination = User.query.order_by(User.name).paginate(
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=20,
        error_out=False,
    )
    return render_template("admin/users.html", pagination=pagination)


@admin_bp.post("/users/<int:user_id>/active")
@require_admin(super_admin=True)
@limiter.limit("20 per hour")
def user_active(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == g.admin_user.id:
        abort(409, description="No podés desactivar tu propia cuenta.")
    value = request.form.get("is_active")
    if value not in {"true", "false"}:
        abort(400)
    user.is_active = value == "true"
    user.auth_version += 1
    audit("set_user_active", f"user_id={user.id};active={user.is_active}")
    db.session.commit()
    flash("Estado del usuario actualizado.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/password")
@require_admin(super_admin=True)
@limiter.limit("10 per hour")
def user_password(user_id):
    user = db.get_or_404(User, user_id)
    password = request.form.get("new_password", "")
    error = _password_error(password)
    if error:
        flash(error, "error")
    else:
        user.set_password(password)
        user.auth_version += 1
        audit("reset_password", f"user_id={user.id}")
        db.session.commit()
        flash("Contraseña actualizada. Las sesiones anteriores quedaron invalidadas.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/password", methods=["GET", "POST"])
@require_admin()
@limiter.limit("5 per hour", methods=["POST"])
def password():
    if request.method == "POST":
        value = request.form.get("new_password", "")
        error = _password_error(value)
        if not g.admin_user.check_password(request.form.get("current_password", "")):
            error = "La contraseña actual es incorrecta."
        if error:
            flash(error, "error")
        else:
            g.admin_user.set_password(value)
            g.admin_user.auth_version += 1
            audit("change_password")
            db.session.commit()
            session.clear()
            flash("Contraseña actualizada. Iniciá sesión nuevamente.", "success")
            return redirect(url_for("admin.login"))
    return render_template("admin/password.html")


@admin_bp.get("/logs")
@require_admin(super_admin=True)
def logs():
    query = ActivityLog.query
    action = request.args.get("action", "").strip()[:100]
    if action:
        query = query.filter(ActivityLog.action.contains(action, autoescape=True))
    pagination = query.order_by(ActivityLog.created_at.desc()).paginate(
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=50,
        error_out=False,
    )
    return render_template("admin/logs.html", pagination=pagination)
