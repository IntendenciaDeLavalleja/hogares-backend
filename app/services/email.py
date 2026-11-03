from html import escape

from flask import current_app
from flask_mail import Message

from ..extensions import mail


def send_2fa_email(to_email: str, user_name: str, code: str) -> None:
    app_name = current_app.config["APP_NAME"]
    safe_name = escape(user_name)
    safe_app_name = escape(app_name)
    message = Message(subject=f"[{app_name}] Código de verificación", recipients=[to_email])
    message.body = (
        f"Hola {user_name},\n\nTu código de verificación de doble factor es: {code}\n"
        "Expira en 10 minutos.\n\nSi no iniciaste sesión, ignorá este correo."
    )
    message.html = (
        '<!doctype html><html lang="es"><body style="margin:0;background:#f4f6f8;padding:32px 16px;'
        'font-family:Arial,sans-serif"><table role="presentation" width="100%"><tr><td align="center">'
        '<table role="presentation" width="520" style="max-width:520px;width:100%;background:#fff;'
        f'border:1px solid #dce3e8"><tr><td style="padding:24px;background:#143c5a;color:#fff;'
        f'font-size:20px;font-weight:700">{safe_app_name}</td></tr><tr><td style="padding:32px">'
        f"<p>Hola <strong>{safe_name}</strong>,</p><p>Tu código de verificación de doble factor es:</p>"
        f'<p style="padding:18px;background:#eef3f6;text-align:center;font:700 36px monospace;'
        f'letter-spacing:8px">{code}</p><p style="color:#64748b;font-size:13px">Expira en 10 minutos. '
        "Si no iniciaste sesión, ignorá este mensaje.</p></td></tr></table></td></tr></table></body></html>"
    )
    mail.send(message)


def send_application_confirmation(application, to_email: str) -> None:
    app_name = current_app.config["APP_NAME"]
    code = application.tracking_code
    message = Message(subject=f"[{app_name}] Recibimos tu solicitud · {code}", recipients=[to_email])
    message.body = (
        f"Hola,\n\nRecibimos tu solicitud de alojamiento en Hogares Estudiantiles.\n\n"
        f"Código de seguimiento: {code}\n"
        f"Fecha de recepción: {application.submitted_at.strftime('%d/%m/%Y %H:%M')} UTC\n\n"
        "Podés consultar el estado en la sección «Seguí tu trámite» de la página principal, "
        "con este código y el correo al que recibiste esta confirmación.\n\n"
        "Esta confirmación acredita la recepción de la solicitud; no implica la adjudicación de una beca.\n\n"
        f"— {app_name}"
    )
    message.html = (
        '<!doctype html><html lang="es"><body style="background:#f4f6f8;padding:24px;font-family:Arial,sans-serif">'
        '<div style="max-width:520px;margin:auto;background:white;padding:32px;border:1px solid #dce3e8">'
        f'<h1 style="font-size:22px;color:#143c5a">{escape(app_name)}</h1>'
        '<h2 style="font-size:18px">Recibimos tu solicitud</h2>'
        "<p>Tu solicitud de alojamiento quedó registrada correctamente.</p>"
        "<p>Código de seguimiento:</p>"
        f'<p style="background:#eef3f6;padding:18px;font:700 24px monospace">{escape(code)}</p>'
        "<p>Consultá el estado en <strong>Seguí tu trámite</strong>, en la página principal, "
        "con este código y tu correo electrónico.</p>"
        '<p style="font-size:13px;color:#64748b">La recepción de la solicitud no implica '
        "la adjudicación de una beca.</p></div></body></html>"
    )
    mail.send(message)
