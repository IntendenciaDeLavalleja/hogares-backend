from decimal import Decimal

from ..extensions import db
from .application import utcnow


class InstitutionSettings(db.Model):
    __tablename__ = "institution_settings"

    id = db.Column(db.Integer, primary_key=True)
    good_conduct_email = db.Column(db.String(254), nullable=False, default="")


class PonderadorSettings(db.Model):
    """Porcentajes globales usados para ordenar las solicitudes enviadas."""

    __tablename__ = "ponderador_settings"

    id = db.Column(db.Integer, primary_key=True)
    income_weight = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal("50.00"))
    schooling_weight = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal("50.00"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
