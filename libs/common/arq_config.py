"""ARQ (Async Redis Queue) configuration utilities.

Provides helpers for parsing Redis connection settings from the
application config into ARQ-compatible RedisSettings.
"""

from urllib.parse import urlparse

from arq.connections import RedisSettings
from libs.common.config import get_settings


def get_redis_settings() -> RedisSettings:
    """Parse REDIS_URL from application settings into ARQ RedisSettings."""
    settings = get_settings()
    parsed = urlparse(settings.REDIS_URL)

    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "0"),
        password=parsed.password,
    )
