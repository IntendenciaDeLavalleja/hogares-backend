"""Archive applications by convocatoria."""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0006"
down_revision = "20260909_0005"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("archive_name", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("archived_by", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_application_archived_user", "users", ["archived_by"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_applications_archived_at", "applications", ["archived_at"])
    op.create_index("ix_applications_archive_name", "applications", ["archive_name"])


def downgrade():
    op.drop_index("ix_applications_archive_name", table_name="applications")
    op.drop_index("ix_applications_archived_at", table_name="applications")
    with op.batch_alter_table("applications") as batch:
        batch.drop_constraint("fk_application_archived_user", type_="foreignkey")
        batch.drop_column("archived_by")
        batch.drop_column("archive_name")
        batch.drop_column("archived_at")
