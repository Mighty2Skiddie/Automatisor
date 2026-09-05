"""Structured JSON logging.

One JSON object per line, so logs are greppable locally and ingestible by anything
that reads JSON. Every request carries a ``request_id`` that also goes back in the
response header, which is what makes a user-reported problem traceable to its log
line.

The raw query is never logged when the input guard found PII. The redacted form is
logged instead — the guard already produced it, so there is no reason to persist the
original anywhere.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set once per request by the API middleware and read by the formatter, so call sites
# do not have to thread a request id through every log call.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Attributes LogRecord always carries; anything else was passed via `extra=` and is
# therefore application context worth emitting.
_STANDARD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        current_request = request_id_var.get()
        if current_request:
            payload["request_id"] = current_request

        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: uvicorn's own handlers are replaced rather than added to, so a
    reload does not produce every line twice.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False

    # These are chatty at INFO and say nothing about our own behaviour.
    for name in ("httpx", "httpcore", "urllib3", "google_genai", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
