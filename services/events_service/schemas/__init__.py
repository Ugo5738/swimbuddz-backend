"""Events Service schemas package."""

from services.events_service.schemas.main import (
    EventBase,
    EventCreate,
    EventInviteCreate,
    EventInviteResponse,
    EventResponse,
    EventUpdate,
    OpenSwimCreate,
    OpenSwimUpdate,
    RSVPCreate,
    RSVPResponse,
)
from services.events_service.schemas.planning import (
    CalendarImportCommitRequest,
    CalendarImportCommitResponse,
    CalendarImportPreviewResponse,
    EventGenerationResponse,
    EventOccurrence,
    EventOccurrenceRange,
    EventTemplateCreate,
    EventTemplateResponse,
    EventTemplateUpdate,
)

__all__ = [
    "EventBase",
    "EventCreate",
    "EventInviteCreate",
    "EventInviteResponse",
    "EventResponse",
    "EventUpdate",
    "OpenSwimCreate",
    "OpenSwimUpdate",
    "RSVPCreate",
    "RSVPResponse",
    "CalendarImportCommitRequest",
    "CalendarImportCommitResponse",
    "CalendarImportPreviewResponse",
    "EventGenerationResponse",
    "EventOccurrence",
    "EventOccurrenceRange",
    "EventTemplateCreate",
    "EventTemplateResponse",
    "EventTemplateUpdate",
]
