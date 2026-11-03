import uuid
from datetime import UTC, datetime

from flask import current_app
from sqlalchemy import CheckConstraint, UniqueConstraint

from ..extensions import db


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Application(db.Model):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("tracking_code"),
        CheckConstraint("status IN ('draft', 'submitted')", name="ck_application_status"),
        CheckConstraint("home IS NULL OR home IN ('minas', 'mariscala', 'montevideo')", name="ck_application_home"),
        CheckConstraint(
            "application_type IS NULL OR application_type IN ('new', 'renewal')", name="ck_application_type"
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tracking_code = db.Column(db.String(24), index=True)
    draft_token_hash = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="draft", index=True)
    application_year = db.Column(
        db.SmallInteger, nullable=False, default=lambda: current_app.config["APPLICATION_YEAR"]
    )
    home = db.Column(db.String(20))
    application_type = db.Column(db.String(20))
    payload_encrypted = db.Column(db.LargeBinary)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    submitted_at = db.Column(db.DateTime)
    confirmation_email_sent_at = db.Column(db.DateTime)
    good_conduct_received_at = db.Column(db.DateTime)
    good_conduct_received_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    archived_at = db.Column(db.DateTime, index=True)
    archive_name = db.Column(db.String(120), index=True)
    archived_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    documents = db.relationship(
        "ApplicationDocument", back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )


class ApplicationDocument(db.Model):
    __tablename__ = "application_documents"
    __table_args__ = (UniqueConstraint("application_id", "document_type", name="uq_application_document_type"),)

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = db.Column(
        db.String(36), db.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type = db.Column(db.String(50), nullable=False)
    object_key = db.Column(db.String(512), nullable=False, unique=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum_sha256 = db.Column(db.String(64), nullable=False)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    application = db.relationship("Application", back_populates="documents")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.document_type,
            "name": self.original_name,
            "storedName": self.stored_name,
            "mimeType": self.media_type,
            "size": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "status": "uploaded",
        }
