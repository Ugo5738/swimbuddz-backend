"""Async Redis client for SwimBuddz.

Provides a shared Redis connection pool for cross-service validation cache.

Usage:
    from libs.common.redis import get_redis, close_redis

    redis = await get_redis()
    await redis.set("key", "value")
    value = await redis.get("key")

    # On shutdown:
    await close_redis()
"""
from typing import Optional

from redis.asyncio import Redis, from_url

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

_redis_client: Optional[Redis] = None


async def get_redis() -> Redis:
    """
    Get the shared Redis client.

    Creates a new connection on first call, reuses thereafter.
    Connection uses the REDIS_URL from settings.
    """
    global _redis_client

    if _redis_client is None:
        settings = get_settings()
        _redis_client = from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(f"Redis client connected to {settings.REDIS_URL}")

    return _redis_client


async def close_redis() -> None:
    """
    Close the Redis connection.

    Call this on application shutdown.
    """
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis client closed")


async def ping_redis() -> bool:
    """
    Check if Redis is available.

    Returns True if Redis responds to ping, False otherwise.
    """
    try:
        redis = await get_redis()
        await redis.ping()
        return True
    except Exception as e:
        logger.warning(f"Redis ping failed: {e}")
        return False
