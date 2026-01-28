"""Structured logging utilities for SwimBuddz.

Provides:
- JSON-formatted logs for production environments
- Request context (request_id, path, method) via contextvars
- Consistent log format across all services

Usage:
    from libs.common.logging import configure_logging, get_logger, set_request_context

    configure_logging()
    logger = get_logger(__name__)

    # In middleware or request handler:
    set_request_context(request_id="abc-123", path="/api/v1/members", method="GET")

    logger.info("Processing request")  # Automatically includes request context
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Optional

from libs.common.config import get_settings

# Context variables for request tracing
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_request_path: ContextVar[Optional[str]] = ContextVar("request_path", default=None)
_request_method: ContextVar[Optional[str]] = ContextVar("request_method", default=None)


def set_request_context(
    request_id: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
) -> str:
    """
    Set request context for the current async context.

    Returns the request_id (generated if not provided).
    """
    rid = request_id or str(uuid.uuid4())
    _request_id.set(rid)
    if path:
        _request_path.set(path)
    if method:
        _request_method.set(method)
    return rid


def clear_request_context() -> None:
    """Clear request context after request completes."""
    _request_id.set(None)
    _request_path.set(None)
    _request_method.set(None)


def get_request_id() -> Optional[str]:
    """Get current request ID from context."""
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Output format:
    {
        "timestamp": "2024-01-15T10:30:00",
        "level": "INFO",
        "logger": "services.members_service.router",
        "message": "Request processed",
        "request_id": "abc-123",
        "path": "/api/v1/members",
        "method": "GET"
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add request context if available
        request_id = _request_id.get()
        if request_id:
            log_record["request_id"] = request_id

        request_path = _request_path.get()
        if request_path:
            log_record["path"] = request_path

        request_method = _request_method.get()
        if request_method:
            log_record["method"] = request_method

        # Add exception info if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Add any extra fields passed to the log call
        if hasattr(record, "extra_fields"):
            log_record.update(record.extra_fields)

        return json.dumps(log_record)


class DevFormatter(logging.Formatter):
    """
    Human-readable formatter for local development.

    Output format:
    2024-01-15 10:30:00 | INFO | services.members_service.router | [abc-123] Request processed
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)

        # Add request ID prefix if available
        request_id = _request_id.get()
        rid_part = f"[{request_id[:8]}] " if request_id else ""

        base = f"{timestamp} | {record.levelname:5} | {record.name} | {rid_part}{record.getMessage()}"

        if record.exc_info:
            base += f"\n{self.formatException(record.exc_info)}"

        return base


def configure_logging() -> None:
    """
    Configure global logging settings.

    Uses JSON format in production, human-readable in development.
    """
    settings = get_settings()

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # Use human-readable format for local dev, JSON for everything else
    if settings.ENVIRONMENT == "local":
        formatter = DevFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    else:
        formatter = JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")

    handler.setFormatter(formatter)

    # Remove existing handlers to avoid duplication
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger for a specific module.

    Usage:
        logger = get_logger(__name__)
        logger.info("Something happened")
    """
    return logging.getLogger(name)
