"""Persistent admin-managed membership and metadata-only audit."""
from alembic import op
import sqlalchemy as sa
revision = "0010_admin_channels"
down_revision = "0009_usage_health"
branch_labels = None
depends_on = None


def upgrade():
    opts = dict(mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
    op.create_table("membership_policy", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("revision", sa.Integer(), nullable=False), **opts)
    op.create_table("required_channels", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column("chat_id", sa.String(64), nullable=False, unique=True),
                    sa.Column("join_url", sa.String(255), nullable=False),
                    sa.Column("enabled", sa.Boolean(), nullable=False),
                    sa.Column("version", sa.Integer(), nullable=False), **opts)
    op.create_table("admin_audit", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column("admin_id", sa.BigInteger(), nullable=False),
                    sa.Column("action", sa.String(32), nullable=False),
                    sa.Column("target_id", sa.Integer(), nullable=True),
                    sa.Column("created_at", sa.DateTime(), nullable=False), **opts)


def downgrade():
    op.drop_table("admin_audit")
    op.drop_table("required_channels")
    op.drop_table("membership_policy")
