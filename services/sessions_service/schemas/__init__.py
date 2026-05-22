"""Sessions Service schemas package."""

from services.sessions_service.schemas.booking import (
    AdminWalkInRequest,
    BookingConfirmRequest,
    BulkBookingItem,
    BulkBookingRequest,
    BulkBookingResponse,
    SessionBookingCreate,
    SessionBookingResponse,
)
from services.sessions_service.schemas.main import (
    SessionBase,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from services.sessions_service.schemas.templates import (
    GenerateSessionsRequest,
    SessionTemplateBase,
    SessionTemplateCreate,
    SessionTemplateResponse,
    SessionTemplateUpdate,
)

__all__ = [
    "AdminWalkInRequest",
    "BookingConfirmRequest",
    "BulkBookingItem",
    "BulkBookingRequest",
    "BulkBookingResponse",
    "GenerateSessionsRequest",
    "SessionBase",
    "SessionBookingCreate",
    "SessionBookingResponse",
    "SessionCreate",
    "SessionResponse",
    "SessionTemplateBase",
    "SessionTemplateCreate",
    "SessionTemplateResponse",
    "SessionTemplateUpdate",
    "SessionUpdate",
]
