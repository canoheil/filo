"""Persist resumable setup and response review metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0003_setup_progress"
down_revision = "0002_unicode_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("setup_progress",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("completed", sa.JSON(), nullable=False),
        sa.Column("tested_hash", sa.String(64), nullable=True),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.tg_user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")


def downgrade():
    op.drop_table("setup_progress")
