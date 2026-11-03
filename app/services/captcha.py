import secrets

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer


def _signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="application-confirmation-captcha-v1")


def create_captcha(application_id):
    a, b = secrets.randbelow(20) + 1, secrets.randbelow(20) + 1
    return {
        "question": f"{a} + {b}",
        "token": _signer().dumps({"application_id": application_id, "a": a, "b": b}),
        "expiresIn": 600,
    }


def verify_captcha(application_id, token, answer):
    if not isinstance(token, str) or len(token) > 1024 or not isinstance(answer, str):
        return False
    if not answer.isascii() or not answer.isdigit() or len(answer) > 2:
        return False
    try:
        data = _signer().loads(token, max_age=600)
    except BadData:
        return False
    return data.get("application_id") == application_id and int(answer) == data["a"] + data["b"]
