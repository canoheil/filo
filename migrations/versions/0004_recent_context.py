"""Opt-in flag only. Conversation content is never stored in this table."""
from alembic import op
import sqlalchemy as sa

revision = "0004_recent_context"
down_revision = "0003_setup_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("personas", sa.Column("context_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade():
    op.drop_column("personas", "context_enabled")
