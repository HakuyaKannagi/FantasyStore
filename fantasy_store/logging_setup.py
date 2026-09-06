from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fantasy_store.config import LOG_BACKUP_COUNT, LOG_MAX_BYTES


class _EventCodeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "event_code"):
            record.event_code = "-"
        if not hasattr(record, "operation_id"):
            record.operation_id = "-"
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


def setup_logging(log_file: Path, level: int = logging.INFO) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("fantasy_store")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.addFilter(_EventCodeFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s event=%(event_code)s operation=%(operation_id)s request=%(request_id)s %(message)s"
    ))
    logger.addHandler(handler)
    return logger
