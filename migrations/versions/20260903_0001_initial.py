"""Initial student homes application schema.

Revision ID: 20260903_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "20260903_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tracking_code", sa.String(length=24), nullable=True),
        sa.Column("draft_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("application_year", sa.SmallInteger(), nullable=False),
        sa.Column("home", sa.String(length=20), nullable=True),
        sa.Column("application_type", sa.String(length=20), nullable=True),
        sa.Column("payload_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "application_type IS NULL OR application_type IN ('new', 'renewal')", name="ck_application_type"
        ),
        sa.CheckConstraint("home IS NULL OR home IN ('minas', 'mariscala', 'montevideo')", name="ck_application_home"),
        sa.CheckConstraint("status IN ('draft', 'submitted')", name="ck_application_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracking_code"),
    )
    op.create_index(op.f("ix_applications_expires_at"), "applications", ["expires_at"])
    op.create_index(op.f("ix_applications_status"), "applications", ["status"])
    op.create_index(op.f("ix_applications_tracking_code"), "applications", ["tracking_code"])
    op.create_table(
        "application_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "document_type", name="uq_application_document_type"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(op.f("ix_application_documents_application_id"), "application_documents", ["application_id"])


def downgrade():
    op.drop_index(op.f("ix_application_documents_application_id"), table_name="application_documents")
    op.drop_table("application_documents")
    op.drop_index(op.f("ix_applications_tracking_code"), table_name="applications")
    op.drop_index(op.f("ix_applications_status"), table_name="applications")
    op.drop_index(op.f("ix_applications_expires_at"), table_name="applications")
    op.drop_table("applications")
