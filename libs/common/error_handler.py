"""Global exception handlers for FastAPI applications.

Provides consistent JSON error responses for all SwimBuddz exceptions.

Usage:
    from libs.common.error_handler import add_exception_handlers

    app = FastAPI()
    add_exception_handlers(app)
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from libs.common.exceptions import SwimBuddzError
from libs.common.logging import get_logger, get_request_id

logger = get_logger(__name__)


def add_exception_handlers(app: FastAPI) -> None:
    """
    Add global exception handlers to a FastAPI application.
    
    Handles:
    - SwimBuddzError subclasses → structured JSON with code
    - Starlette HTTPException → standard format
    - RequestValidationError → validation error format
    - Unhandled exceptions → 500 with request ID for debugging
    """

    @app.exception_handler(SwimBuddzError)
    async def swimbuddz_error_handler(request: Request, exc: SwimBuddzError) -> JSONResponse:
        """Handle all SwimBuddz custom exceptions."""
        request_id = get_request_id()
        
        content = {
            "detail": exc.message,
            "code": exc.code,
        }
        
        if exc.details:
            content.update(exc.details)
        
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        
        logger.warning(
            f"Application error: {exc.code}",
            extra={"extra_fields": {
                "error_code": exc.code,
                "status_code": exc.status_code,
                "message": exc.message,
                "details": exc.details,
            }},
        )
        
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Handle Starlette/FastAPI HTTPException."""
        request_id = get_request_id()
        
        content = {
            "detail": exc.detail,
            "code": _status_to_code(exc.status_code),
        }
        
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle Pydantic validation errors."""
        request_id = get_request_id()
        
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })
        
        content = {
            "detail": "Validation failed",
            "code": "VALIDATION_ERROR",
            "errors": errors,
        }
        
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        
        return JSONResponse(
            status_code=422,
            content=content,
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unhandled exceptions with safe error message."""
        request_id = get_request_id()
        
        logger.exception(
            "Unhandled exception",
            extra={"extra_fields": {
                "exception_type": type(exc).__name__,
            }},
        )
        
        content = {
            "detail": "An unexpected error occurred",
            "code": "INTERNAL_ERROR",
        }
        
        if request_id:
            content["request_id"] = request_id
        
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        
        return JSONResponse(
            status_code=500,
            content=content,
            headers=headers,
        )


def _status_to_code(status_code: int) -> str:
    """Map HTTP status codes to error codes."""
    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        410: "GONE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
    }
    return mapping.get(status_code, "ERROR")
