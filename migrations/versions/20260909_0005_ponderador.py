"""Persist the weights used by the application ranking."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0005"
down_revision = "20260908_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ponderador_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("income_weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("schooling_weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO ponderador_settings (id, income_weight, schooling_weight, updated_at) "
            "VALUES (1, 50.00, 50.00, CURRENT_TIMESTAMP)"
        )
    )


def downgrade():
    op.drop_table("ponderador_settings")
