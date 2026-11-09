"""Per-conversation pause and opt-in owner takeover metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0007_chat_controls"
down_revision = "0006_draft_lifecycle_index"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("personas", sa.Column("handoff_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.create_table("chat_controls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("handoff_until", sa.DateTime(), nullable=True),
        sa.Column("routing_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner_message_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_reason", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.tg_user_id"]),
        sa.UniqueConstraint("user_id", "connection_id", "chat_id", name="uq_chat_control_scope"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
    op.create_index("ix_chat_controls_user_id", "chat_controls", ["user_id"])


def downgrade():
    op.drop_index("ix_chat_controls_user_id", table_name="chat_controls")
    op.drop_table("chat_controls")
    op.drop_column("personas", "handoff_minutes")
