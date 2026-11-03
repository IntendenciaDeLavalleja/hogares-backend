from flask import Blueprint

from .admin import admin_bp
from .applications import applications_bp
from .auth import auth_bp

api_bp = Blueprint("api", __name__, url_prefix="/api")
api_bp.register_blueprint(applications_bp)
api_bp.register_blueprint(auth_bp)
api_bp.register_blueprint(admin_bp)
