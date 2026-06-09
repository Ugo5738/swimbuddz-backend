"""Sessions Service schemas package."""

from services.sessions_service.schemas.booking import (
    AdminPoolFeeRefundRequest,
    AdminWalkInRequest,
    BookingConfirmRequest,
    BulkBookingItem,
    BulkBookingRequest,
    BulkBookingResponse,
    RunningLateRequest,
    SessionBookingCreate,
    SessionBookingResponse,
    UnpaidBookingResponse,
)
from services.sessions_service.schemas.main import (
    SessionBase,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from services.sessions_service.schemas.makeup import (
    BookableSlotResponse,
    BookableSlotsResponse,
    MakeupBookingCreate,
    MakeupBookingResponse,
    MakeupOpenSlotCreate,
    MakeupRequestCreate,
)
from services.sessions_service.schemas.templates import (
    GenerateSessionsRequest,
    SessionTemplateBase,
    SessionTemplateCreate,
    SessionTemplateResponse,
    SessionTemplateUpdate,
)

__all__ = [
    "AdminPoolFeeRefundRequest",
    "AdminWalkInRequest",
    "BookableSlotResponse",
    "BookableSlotsResponse",
    "BookingConfirmRequest",
    "BulkBookingItem",
    "BulkBookingRequest",
    "BulkBookingResponse",
    "GenerateSessionsRequest",
    "MakeupBookingCreate",
    "MakeupBookingResponse",
    "MakeupOpenSlotCreate",
    "MakeupRequestCreate",
    "RunningLateRequest",
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
    "UnpaidBookingResponse",
]
