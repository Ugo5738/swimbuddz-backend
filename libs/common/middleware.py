"""Observability middleware for FastAPI.

Provides:
- Request ID generation and propagation
- Request/response timing
- Structured logging for all requests

Usage:
    from libs.common.middleware import add_observability_middleware

    app = FastAPI()
    add_observability_middleware(app)
"""
import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from libs.common.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    get_request_id,
    set_request_context,
)

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware that sets request context for tracing and logs request lifecycle.
    
    Features:
    - Generates or propagates X-Request-ID header
    - Logs request start with path/method
    - Logs request completion with status code and duration
    - Clears context after request completes
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID")
        request_id = set_request_context(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        start_time = time.perf_counter()

        # Log request start (skip noisy health checks)
        if request.url.path != "/health":
            logger.info(
                "Request started",
                extra={"extra_fields": {"query": str(request.url.query) if request.url.query else None}},
            )

        try:
            response = await call_next(request)
            
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log request completion (skip health checks)
            if request.url.path != "/health":
                log_level = "warning" if response.status_code >= 400 else "info"
                getattr(logger, log_level)(
                    "Request completed",
                    extra={"extra_fields": {
                        "status_code": response.status_code,
                        "duration_ms": round(duration_ms, 2),
                    }},
                )

            # Add request ID to response headers for client-side correlation
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "Request failed with unhandled exception",
                extra={"extra_fields": {
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                }},
            )
            raise

        finally:
            clear_request_context()


def add_observability_middleware(app: FastAPI) -> None:
    """
    Add observability middleware to a FastAPI app.
    
    Call this after creating the app but before adding routes.
    
    Usage:
        app = FastAPI()
        add_observability_middleware(app)
    """
    # Configure logging first
    configure_logging()
    
    # Add request context middleware
    app.add_middleware(RequestContextMiddleware)
    
    logger.info("Observability middleware initialized")
