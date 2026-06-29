"""Events Service schemas package."""

from services.events_service.schemas.main import (
    EventBase,
    EventCreate,
    EventResponse,
    EventUpdate,
    OpenSwimCreate,
    OpenSwimUpdate,
    RSVPCreate,
    RSVPResponse,
)

__all__ = [
    "EventBase",
    "EventCreate",
    "EventResponse",
    "EventUpdate",
    "OpenSwimCreate",
    "OpenSwimUpdate",
    "RSVPCreate",
    "RSVPResponse",
]
