"""Repair Unicode storage for personas and AI drafts.

Stop writers and take a tested backup first: MariaDB DDL implicitly commits.
Explicit MODIFY preserves TEXT/VARCHAR types (CONVERT TO can widen TEXT).
Already-replaced question marks cannot be recovered by this migration.
"""
from alembic import op

revision = "0002_unicode_storage"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Freeze the schema here; never import the evolving ORM models into migrations.
_COLUMNS = {
    "personas": (
        ("name", "VARCHAR(120)", "NOT NULL"),
        ("role", "VARCHAR(255)", "NOT NULL"),
        ("tone", "VARCHAR(120)", "NOT NULL"),
        ("language", "VARCHAR(8)", "NOT NULL"),
        ("knowledge_base", "TEXT", "NOT NULL"),
        ("instructions", "TEXT", "NOT NULL"),
        ("signature", "VARCHAR(120)", "NOT NULL"),
    ),
    "ai_message_log": (
        ("connection_id", "VARCHAR(255)", "NOT NULL"),
        ("direction", "VARCHAR(8)", "NOT NULL"),
        ("status", "VARCHAR(16)", "NOT NULL"),
        ("draft_text", "TEXT", "NULL"),
        ("model", "VARCHAR(64)", "NULL"),
    ),
}


def upgrade() -> None:
    if op.get_context().dialect.name not in {"mysql", "mariadb"}:
        return  # SQLite already supports Unicode; no MySQL-specific ALTER there.
    for table, columns in _COLUMNS.items():
        clauses = ["DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"]
        clauses.extend(
            f"MODIFY COLUMN `{name}` {type_} CHARACTER SET utf8mb4 "
            f"COLLATE utf8mb4_unicode_ci {nullable}"
            for name, type_, nullable in columns
        )
        op.execute(f"ALTER TABLE `{table}` " + ", ".join(clauses))


def downgrade() -> None:
    if op.get_context().dialect.name in {"mysql", "mariadb"}:
        raise RuntimeError(
            "Refusing lossy utf8mb4-to-latin1 downgrade. Restore a verified backup "
            "or plan an explicit data-safe rollback instead."
        )
