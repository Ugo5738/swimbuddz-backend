"""Cross-service validation cache using Redis.

Provides functions to track valid entity IDs across services:
- Cohort IDs from academy_service
- Event IDs from events_service

Usage:
    from libs.common.validation_cache import (
        COHORT_IDS_KEY,
        add_valid_id,
        remove_valid_id,
        is_valid_id,
    )

    # When creating a cohort
    await add_valid_id(COHORT_IDS_KEY, str(cohort.id))

    # When deleting a cohort
    await remove_valid_id(COHORT_IDS_KEY, str(cohort_id))

    # When creating a session that references cohort
    if not await is_valid_id(COHORT_IDS_KEY, str(cohort_id)):
        raise HTTPException(400, "Invalid cohort_id")
"""
from typing import Optional

from libs.common.logging import get_logger
from libs.common.redis import get_redis, ping_redis

logger = get_logger(__name__)

# Cache keys for different entity types
COHORT_IDS_KEY = "valid:cohort_ids"
EVENT_IDS_KEY = "valid:event_ids"
PROGRAM_IDS_KEY = "valid:program_ids"


async def add_valid_id(key: str, entity_id: str) -> bool:
    """
    Add an ID to the validation cache.

    Args:
        key: Cache key (e.g., COHORT_IDS_KEY)
        entity_id: UUID string to add

    Returns:
        True if added successfully, False if Redis unavailable
    """
    try:
        redis = await get_redis()
        await redis.sadd(key, entity_id)
        logger.debug(f"Added {entity_id} to {key}")
        return True
    except Exception as e:
        logger.warning(f"Failed to add {entity_id} to {key}: {e}")
        return False


async def remove_valid_id(key: str, entity_id: str) -> bool:
    """
    Remove an ID from the validation cache.

    Args:
        key: Cache key (e.g., COHORT_IDS_KEY)
        entity_id: UUID string to remove

    Returns:
        True if removed successfully, False if Redis unavailable
    """
    try:
        redis = await get_redis()
        await redis.srem(key, entity_id)
        logger.debug(f"Removed {entity_id} from {key}")
        return True
    except Exception as e:
        logger.warning(f"Failed to remove {entity_id} from {key}: {e}")
        return False


async def is_valid_id(key: str, entity_id: str, allow_missing_redis: bool = True) -> bool:
    """
    Check if an ID exists in the validation cache.

    Args:
        key: Cache key (e.g., COHORT_IDS_KEY)
        entity_id: UUID string to check
        allow_missing_redis: If True, return True when Redis is unavailable
                           (fail-open for availability). Set to False for strict validation.

    Returns:
        True if ID is valid (or if Redis unavailable and allow_missing_redis=True)
    """
    try:
        redis = await get_redis()
        exists = await redis.sismember(key, entity_id)
        return bool(exists)
    except Exception as e:
        logger.warning(f"Redis check failed for {entity_id} in {key}: {e}")
        return allow_missing_redis


async def get_all_valid_ids(key: str) -> set[str]:
    """
    Get all valid IDs for a given key.

    Returns empty set if Redis is unavailable.
    """
    try:
        redis = await get_redis()
        return await redis.smembers(key)
    except Exception as e:
        logger.warning(f"Failed to get all IDs from {key}: {e}")
        return set()


async def populate_cache(key: str, ids: list[str]) -> bool:
    """
    Populate the cache with a list of IDs.

    Useful for warming the cache on service startup.

    Args:
        key: Cache key
        ids: List of UUID strings to add

    Returns:
        True if successful
    """
    if not ids:
        return True

    try:
        redis = await get_redis()
        await redis.sadd(key, *ids)
        logger.info(f"Populated {key} with {len(ids)} IDs")
        return True
    except Exception as e:
        logger.warning(f"Failed to populate {key}: {e}")
        return False


async def clear_cache(key: str) -> bool:
    """
    Clear all IDs from a cache key.

    Args:
        key: Cache key to clear

    Returns:
        True if successful
    """
    try:
        redis = await get_redis()
        await redis.delete(key)
        logger.info(f"Cleared cache key {key}")
        return True
    except Exception as e:
        logger.warning(f"Failed to clear {key}: {e}")
        return False
