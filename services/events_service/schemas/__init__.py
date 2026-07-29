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
]
