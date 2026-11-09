"""Version draft approvals so stale buttons cannot send edited text."""
from alembic import op
import sqlalchemy as sa

revision = "0005_draft_revision"
down_revision = "0004_recent_context"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_message_log", sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade():
    op.drop_column("ai_message_log", "revision")
