"""Track submission confirmation email delivery."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0003"
down_revision = "20260903_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("applications", sa.Column("confirmation_email_sent_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("applications", "confirmation_email_sent_at")
