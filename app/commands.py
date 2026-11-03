import smtplib
import ssl

import click
from email_validator import EmailNotValidError, validate_email
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import Application
from .models.application import utcnow
from .services import StorageService


@click.command("check-services")
@click.option("--smtp", is_flag=True, help="Comprueba conexión, TLS y autenticación SMTP sin enviar mensajes.")
@with_appcontext
def check_services(smtp: bool) -> None:
    """Verify production dependencies without sending messages or creating applications."""
    from .health import ready

    response, status = ready()
    for name, success in response.get_json()["checks"].items():
        click.echo(f"{name}: {'OK' if success else 'ERROR'}")
    if status != 200:
        raise click.ClickException("Hay servicios no disponibles.")
    if smtp:
        config = current_app.config
        smtp_class = smtplib.SMTP_SSL if config["MAIL_USE_SSL"] else smtplib.SMTP
        try:
            with smtp_class(config["MAIL_SERVER"], config["MAIL_PORT"], timeout=15) as connection:
                connection.ehlo()
                if config["MAIL_USE_TLS"]:
                    connection.starttls(context=ssl.create_default_context())
                    connection.ehlo()
                if config["MAIL_USERNAME"]:
                    connection.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
                code, _message = connection.noop()
                if code != 250:
                    raise smtplib.SMTPException("Respuesta NOOP inválida.")
        except (OSError, smtplib.SMTPException) as error:
            raise click.ClickException(f"SMTP no disponible ({type(error).__name__}).") from error
        click.echo("smtp: OK (no se enviaron correos)")


@click.command("create-admin")
@click.argument("name")
@click.argument("email")
@click.argument("password")
@click.argument("super_admin", default="false")
@with_appcontext
def create_admin(name: str, email: str, password: str, super_admin: str) -> None:
    """Create an admin. SUPER_ADMIN accepts true or false."""
    from .api.auth import _password_error
    from .models import User

    name = name.strip()
    try:
        email = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise click.ClickException("Email inválido.") from exc
    if len(name) < 2 or len(name) > 100:
        raise click.ClickException("El nombre debe tener entre 2 y 100 caracteres.")
    password_error = _password_error(password)
    if password_error:
        raise click.ClickException(password_error)
    value = super_admin.strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise click.ClickException("SUPER_ADMIN debe ser true o false.")
    role = "super_admin" if value in {"true", "1", "yes"} else "admin"
    user = User(name=name, email=email, role=role, is_active=True)
    user.set_password(password)
    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise click.ClickException("Ya existe un usuario con ese email.") from exc
    click.echo(f'{role} "{name}" <{email}> creado correctamente (id={user.id}).')


@click.command("cleanup-drafts")
@click.option("--limit", default=100, type=click.IntRange(1, 1000), show_default=True)
def cleanup_drafts(limit: int) -> None:
    """Remove expired drafts and their private objects."""
    expired = (
        Application.query.filter(Application.status == "draft", Application.expires_at < utcnow()).limit(limit).all()
    )
    storage = StorageService()
    removed = 0
    for application in expired:
        try:
            for document in application.documents:
                storage.remove(document.object_key)
            db.session.delete(application)
            db.session.commit()
            removed += 1
        except Exception:
            db.session.rollback()
            current_app.logger.exception("No se pudo limpiar el borrador %s", application.id)
    click.echo(f"Borradores eliminados: {removed}")
