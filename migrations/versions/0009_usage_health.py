"""Privacy-safe logical AI request accounting."""
from alembic import op
import sqlalchemy as sa

revision = "0009_usage_health"
down_revision = "0008_reply_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_requests",
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("request_key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.tg_user_id"]),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
    op.create_index("ix_ai_requests_owner_created", "ai_requests", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_ai_requests_owner_created", table_name="ai_requests")
    op.drop_table("ai_requests")
