"""Opt-in weekly reply hours, with explicit IANA timezone."""
from alembic import op
import sqlalchemy as sa

revision = "0008_reply_schedule"
down_revision = "0007_chat_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reply_schedules",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("days", sa.JSON(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["users.tg_user_id"]),
        sa.PrimaryKeyConstraint("user_id"), mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")


def downgrade():
    op.drop_table("reply_schedules")
