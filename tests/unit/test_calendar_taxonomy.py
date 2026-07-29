"""Calendar audience, visibility, and event access must remain independent."""

from datetime import datetime, timezone
from types import SimpleNamespace

from services.events_service.routers.member import (
    EventActor,
    _can_attend_event,
    _can_view_event,
)
from services.gateway_service.app.routers.calendar import _event_item


def _event(**overrides):
    values = {
        "status": "published",
        "visibility": "public",
        "tier_access": "public",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_academy_assessment_keeps_academy_audience():
    item = _event_item(
        {
            "id": "assessment-1",
            "title": "Free Intro-to-Water Assessment",
            "event_type": "assessment",
            "audience": "academy",
            "visibility": "public",
            "tier_access": "public",
            "start_time": datetime(2026, 9, 12, 9, tzinfo=timezone.utc),
        }
    )

    assert item is not None
    assert item.audience == "academy"
    assert item.visibility == "public"
    assert item.access_level == "public"


def test_anonymous_visitor_can_view_public_but_cannot_rsvp_without_account():
    actor = EventActor(None, frozenset(), False, False)
    event = _event()

    assert _can_view_event(event, actor, invited=False)
    assert not _can_attend_event(event, actor, invited=False)


def test_invite_only_event_requires_explicit_invite():
    actor = EventActor(None, frozenset({"club"}), True, False)
    event = _event(visibility="invite_only", tier_access="invite_only")

    assert not _can_view_event(event, actor, invited=False)
    assert _can_view_event(event, actor, invited=True)
    assert _can_attend_event(event, actor, invited=True)
