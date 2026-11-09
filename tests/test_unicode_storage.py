from io import StringIO

from alembic import command
from alembic.config import Config
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql

from core.config import get_settings
from database.base import Base
from database.charset import unicode_database_url
import database.models  # noqa: F401


def test_connection_charset_is_forced_without_losing_url_components():
    url = unicode_database_url("mysql+asyncmy://user:p%40ss@db/name?charset=utf8&other=yes")
    assert url.query["charset"] == "utf8mb4"
    assert url.password == "p@ss" and url.query["other"] == "yes"
    assert unicode_database_url("sqlite:///test.db").query == {}


def test_all_model_tables_have_explicit_mysql_unicode_defaults():
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert "CHARSET=utf8mb4" in sql
        assert "COLLATE utf8mb4_unicode_ci" in sql


def test_mysql_migration_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        config = Config("alembic.ini", output_buffer=output)
        command.upgrade(config, "0001_initial:0002_unicode_storage", sql=True)
        sql = output.getvalue()
        assert sql.count("ALTER TABLE") == 2
        assert "ALTER TABLE `personas`" in sql
        assert "ALTER TABLE `ai_message_log`" in sql
        assert "MODIFY COLUMN `draft_text` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL" in sql
        assert "MODIFY COLUMN `name` VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL" in sql
        assert "latin1" not in sql
        output = StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output), "0001_initial", sql=True)
        assert output.getvalue().count("CHARSET=utf8mb4") == 4
    finally:
        get_settings.cache_clear()


def test_setup_table_migration_has_explicit_mysql_unicode(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output),
                        "0002_unicode_storage:0003_setup_progress", sql=True)
        sql = output.getvalue()
        assert "CREATE TABLE setup_progress" in sql
        assert "CHARSET=utf8mb4" in sql
        assert "COLLATE utf8mb4_unicode_ci" in sql
        assert "FOREIGN KEY(user_id) REFERENCES users" in sql
    finally:
        get_settings.cache_clear()


def test_usage_migration_mysql_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output), "0008_reply_schedule:0009_usage_health", sql=True)
        sql = output.getvalue()
        assert "CREATE TABLE ai_requests" in sql
        assert "CHARSET=utf8mb4" in sql
        assert "ix_ai_requests_owner_created" in sql
        assert "request_key" in sql and "FOREIGN KEY(user_id) REFERENCES users" in sql
    finally:
        get_settings.cache_clear()


def test_admin_migration_mysql_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output), "0009_usage_health:0010_admin_channels", sql=True)
        sql = output.getvalue()
        for table in ("membership_policy", "required_channels", "admin_audit"):
            assert f"CREATE TABLE {table}" in sql
        assert sql.count("CHARSET=utf8mb4") == 3
        assert sql.count("COLLATE utf8mb4_unicode_ci") == 3
    finally:
        get_settings.cache_clear()


def test_broadcast_migration_mysql_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output), "0010_admin_channels:0011_broadcast", sql=True)
        sql = output.getvalue()
        for table in ("broadcast_subscribers", "broadcast_jobs", "broadcast_targets"):
            assert f"CREATE TABLE {table}" in sql
        assert sql.count("CHARSET=utf8mb4") == 3
        assert sql.count("COLLATE utf8mb4_unicode_ci") == 3
        assert "ix_broadcast_target_state" in sql
    finally:
        get_settings.cache_clear()
