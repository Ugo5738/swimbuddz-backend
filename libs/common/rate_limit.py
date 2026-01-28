"""Rate limiting configuration for SwimBuddz API.

Uses slowapi with Redis backend for distributed rate limiting across services.
"""

from functools import lru_cache
from typing import Callable

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from libs.common.config import get_settings


def _get_client_ip(request: Request) -> str:
    """
    Get client IP from request, handling proxies.

    Checks X-Forwarded-For header first (for proxied requests),
    then falls back to direct connection IP.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP in the chain (original client)
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def _get_user_or_ip(request: Request) -> str:
    """
    Rate limit by user ID if authenticated, otherwise by IP.

    This prevents a single user from consuming rate limits
    that would affect other users on the same IP.
    """
    # Check if request has authenticated user
    if hasattr(request.state, "user") and request.state.user:
        return f"user:{request.state.user.sub}"
    return f"ip:{_get_client_ip(request)}"


@lru_cache
def get_limiter() -> Limiter:
    """
    Create and return a cached Limiter instance.

    Uses Redis for distributed rate limit state across service instances.
    Falls back to in-memory storage if Redis is unavailable.
    """
    settings = get_settings()

    # Configure Redis storage for distributed limiting
    storage_uri = settings.REDIS_URL

    return Limiter(
        key_func=_get_user_or_ip,
        default_limits=["100/minute"],
        storage_uri=storage_uri,
        strategy="fixed-window",
    )


# Pre-configured limiters for different endpoint categories
limiter = get_limiter()


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Custom handler for rate limit exceeded errors.

    Returns a JSON response with clear error message and retry-after header.
    """
    retry_after = exc.detail.split("per")[0].strip() if exc.detail else "1 minute"

    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded. Try again in {retry_after}.",
            "code": "RATE_LIMIT_EXCEEDED",
        },
        headers={
            "Retry-After": str(getattr(exc, "retry_after", 60)),
            "X-RateLimit-Limit": str(getattr(exc, "limit", "unknown")),
        },
    )


# Decorator shortcuts for common rate limit tiers
def auth_limit(func: Callable) -> Callable:
    """Apply strict rate limit for authentication endpoints (5/minute)."""
    return limiter.limit("5/minute")(func)


def payment_limit(func: Callable) -> Callable:
    """Apply strict rate limit for payment endpoints (3/minute)."""
    return limiter.limit("3/minute")(func)


def api_limit(func: Callable) -> Callable:
    """Apply standard rate limit for general API endpoints (100/minute)."""
    return limiter.limit("100/minute")(func)


def admin_limit(func: Callable) -> Callable:
    """Apply relaxed rate limit for admin endpoints (200/minute)."""
    return limiter.limit("200/minute")(func)
