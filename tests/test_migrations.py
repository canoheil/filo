from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from core.config import get_settings


def test_initial_migration_roundtrip(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migration.db"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(url)
        with engine.connect() as conn:
            assert {"users", "personas", "business_connections", "ai_message_log"} <= set(inspect(conn).get_table_names())
        engine.dispose()
        command.downgrade(config, "base")
        engine = create_engine(url)
        with engine.connect() as conn:
            assert set(inspect(conn).get_table_names()) == {"alembic_version"}
        engine.dispose()
    finally:
        get_settings.cache_clear()
