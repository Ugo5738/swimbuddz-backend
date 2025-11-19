import logging
import sys
from typing import Any

from libs.common.config import get_settings


class JsonFormatter(logging.Formatter):
    """
    Simple JSON-like formatter for structured logging.
    For a real production app, consider using structlog or python-json-logger.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Basic JSON structure
        log_record = {
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "timestamp": self.formatTime(record, self.datefmt),
        }
        
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            
        # In a real implementation, we'd use json.dumps(log_record)
        # For now, a simple string representation is fine for the MVP
        return str(log_record)


def configure_logging() -> None:
    """
    Configure global logging settings.
    """
    settings = get_settings()
    
    # Determine log level
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    
    # Use standard formatter for local dev, JSON for prod could be added here
    if settings.ENVIRONMENT == "local":
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    else:
        # Simple placeholder for structured logging in non-local envs
        formatter = JsonFormatter()

    handler.setFormatter(formatter)
    
    # Remove existing handlers to avoid duplication
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(handler)
    
    # Set third-party loggers to warning to reduce noise
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger for a specific module.
    """
    return logging.getLogger(name)
