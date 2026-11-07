import logging
import sys


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    # HTTP request URLs can contain credentials; SQL parameters can contain drafts.
    for name in ("httpx", "httpcore", "sqlalchemy.engine", "sqlalchemy.pool"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return logging.getLogger("secretary")
