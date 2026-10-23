"""Structured logging: one JSON object per line, scoped to the request in flight."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Everything LogRecord sets on itself. Whatever a caller passed as `extra` is
# what remains once these are removed.
# `color_message` is uvicorn's ANSI copy of its own message, which a JSON line
# has no use for.
_RESERVED_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        current_request = request_id.get()
        if current_request is not None:
            entry["request_id"] = current_request

        entry.update({key: value for key, value in record.__dict__.items() if key not in _RESERVED_RECORD_FIELDS})

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def logging_config(level: str) -> dict[str, Any]:
    """A dictConfig that routes uvicorn's own loggers through the same formatter.

    Uvicorn's access log is left off: the request middleware emits one line per
    request with the fields that log actually lacks.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JSONFormatter}},
        "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
        },
    }
