from functools import wraps

from flask import g, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from ..extensions import db
from ..models import User


def require_access(*roles: str):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            if claims.get("type") != "access":
                return jsonify({"error": "Token de acceso inválido."}), 403
            try:
                user_id = int(get_jwt_identity())
            except (TypeError, ValueError):
                return jsonify({"error": "Token de acceso inválido."}), 403
            user = db.session.get(User, user_id)
            if not user or not user.is_active or claims.get("auth_version") != user.auth_version:
                return jsonify({"error": "La sesión ya no es válida."}), 401
            if roles and user.role not in roles:
                return jsonify({"error": "Acceso denegado."}), 403
            g.auth_user = user
            return function(*args, **kwargs)

        return wrapped

    return decorator
