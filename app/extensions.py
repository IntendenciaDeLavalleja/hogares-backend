import smtplib

from flask import current_app
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Connection, Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman

db = SQLAlchemy()
migrate = Migrate()
limiter = Limiter(key_func=get_remote_address)
talisman = Talisman()
jwt = JWTManager()


class BoundedMailConnection(Connection):
    def configure_host(self):
        timeout = current_app.config["MAIL_TIMEOUT"]
        if self.mail.use_ssl:
            host = smtplib.SMTP_SSL(self.mail.server, self.mail.port, timeout=timeout)
        else:
            host = smtplib.SMTP(self.mail.server, self.mail.port, timeout=timeout)
        host.set_debuglevel(int(self.mail.debug))
        if self.mail.use_tls:
            host.starttls()
        if self.mail.username and self.mail.password:
            host.login(self.mail.username, self.mail.password)
        return host


class BoundedMail(Mail):
    def connect(self):
        application = getattr(self, "app", None) or current_app
        return BoundedMailConnection(application.extensions["mail"])


mail = BoundedMail()
