"""Custom exception classes for SwimBuddz.

Provides a hierarchy of domain-specific exceptions that can be caught
by the global exception handler for consistent API error responses.

Usage:
    from libs.common.exceptions import NotFoundError, ValidationError

    raise NotFoundError("Member not found", resource="member", resource_id=str(member_id))
"""

from typing import Any, Optional


class SwimBuddzError(Exception):
    """
    Base exception for all SwimBuddz application errors.

    Subclass this for domain-specific exceptions that should return
    structured JSON error responses.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(SwimBuddzError):
    """Resource not found."""

    code = "NOT_FOUND"
    status_code = 404

    def __init__(
        self,
        message: str = "Resource not found",
        *,
        resource: Optional[str] = None,
        resource_id: Optional[str] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        if resource:
            details["resource"] = resource
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(message, details=details, **kwargs)


class ValidationError(SwimBuddzError):
    """Request validation failed."""

    code = "VALIDATION_ERROR"
    status_code = 400

    def __init__(
        self,
        message: str = "Validation failed",
        *,
        field: Optional[str] = None,
        errors: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        if field:
            details["field"] = field
        if errors:
            details["errors"] = errors
        super().__init__(message, details=details, **kwargs)


class AuthenticationError(SwimBuddzError):
    """Authentication failed or credentials invalid."""

    code = "UNAUTHORIZED"
    status_code = 401


class AuthorizationError(SwimBuddzError):
    """User lacks permission for this action."""

    code = "FORBIDDEN"
    status_code = 403

    def __init__(
        self,
        message: str = "Permission denied",
        *,
        required_role: Optional[str] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        if required_role:
            details["required_role"] = required_role
        super().__init__(message, details=details, **kwargs)


class ConflictError(SwimBuddzError):
    """Resource already exists or state conflict."""

    code = "CONFLICT"
    status_code = 409


class RateLimitError(SwimBuddzError):
    """Rate limit exceeded."""

    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: Optional[int] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__(message, details=details, **kwargs)


class ExternalServiceError(SwimBuddzError):
    """External service (Supabase, Paystack, etc.) failed."""

    code = "EXTERNAL_SERVICE_ERROR"
    status_code = 502

    def __init__(
        self,
        message: str = "External service error",
        *,
        service: Optional[str] = None,
        **kwargs,
    ):
        details = kwargs.pop("details", {})
        if service:
            details["service"] = service
        super().__init__(message, details=details, **kwargs)
