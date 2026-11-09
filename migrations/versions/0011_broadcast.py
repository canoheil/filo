"""Durable broadcast outbox and private-bot audience."""
from alembic import op
import sqlalchemy as sa
revision = "0011_broadcast"
down_revision = "0010_admin_channels"
branch_labels = None
depends_on = None


def upgrade():
    opts = dict(mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
    op.create_table("broadcast_subscribers", sa.Column("user_id", sa.BigInteger(), primary_key=True),
                    sa.Column("enabled", sa.Boolean(), nullable=False), **opts)
    op.create_table("broadcast_jobs", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column("admin_id", sa.BigInteger(), nullable=False),
                    sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
                    sa.Column("source_message_id", sa.BigInteger(), nullable=False),
                    sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False),
                    sa.Column("retry_at", sa.DateTime(), nullable=True), **opts)
    op.create_index("ix_broadcast_jobs_state", "broadcast_jobs", ["state"])
    op.create_table("broadcast_targets", sa.Column("job_id", sa.Integer(), sa.ForeignKey("broadcast_jobs.id"), primary_key=True),
                    sa.Column("user_id", sa.BigInteger(), primary_key=True),
                    sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False), **opts)
    op.create_index("ix_broadcast_targets_user_id", "broadcast_targets", ["user_id"])
    op.create_index("ix_broadcast_target_state", "broadcast_targets", ["job_id", "state", "user_id"])


def downgrade():
    op.drop_table("broadcast_targets")
    op.drop_table("broadcast_jobs")
    op.drop_table("broadcast_subscribers")
