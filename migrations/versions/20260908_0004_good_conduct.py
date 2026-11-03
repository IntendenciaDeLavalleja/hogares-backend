"""Institution contact and receipt of certificates."""

import sqlalchemy as sa
from alembic import op

revision = "20260908_0004"
down_revision = "20260907_0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("good_conduct_received_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("good_conduct_received_by", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_application_good_conduct_user", "users", ["good_conduct_received_by"], ["id"], ondelete="SET NULL"
        )
    op.create_table(
        "institution_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("good_conduct_email", sa.String(254), nullable=False),
    )
    op.execute(sa.text("INSERT INTO institution_settings (id, good_conduct_email) VALUES (1, '')"))


def downgrade():
    op.drop_table("institution_settings")
    with op.batch_alter_table("applications") as batch:
        batch.drop_constraint("fk_application_good_conduct_user", type_="foreignkey")
        batch.drop_column("good_conduct_received_by")
        batch.drop_column("good_conduct_received_at")
