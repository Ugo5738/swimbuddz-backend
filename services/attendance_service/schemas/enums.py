import enum


class RideShareOption(str, enum.Enum):
    """Local copy — avoids cross-service import from transport_service."""

    NONE = "none"
    LEAD = "lead"
    JOIN = "join"
