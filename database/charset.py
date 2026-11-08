"""Use the same full-Unicode connection charset in the bot and Alembic."""
from sqlalchemy.engine import URL, make_url


def unicode_database_url(value: str) -> URL:
    url = make_url(value)
    if url.get_backend_name() in {"mysql", "mariadb"}:
        url = url.update_query_dict({"charset": "utf8mb4"})
    return url
