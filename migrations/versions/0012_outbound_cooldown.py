"""Persist Telegram flood cooldown across restart."""
from alembic import op
import sqlalchemy as sa
revision = "0012_outbound_cooldown"
down_revision = "0011_broadcast"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("outbound_cooldown",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("blocked_until", sa.DateTime(), nullable=False),
                    mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")


def downgrade():
    op.drop_table("outbound_cooldown")
