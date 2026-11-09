"""Index periodic draft deadline/retention maintenance."""
from alembic import op

revision = "0006_draft_lifecycle_index"
down_revision = "0005_draft_revision"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_ai_draft_lifecycle", "ai_message_log", ["direction", "status", "created_at"])


def downgrade():
    op.drop_index("ix_ai_draft_lifecycle", table_name="ai_message_log")
