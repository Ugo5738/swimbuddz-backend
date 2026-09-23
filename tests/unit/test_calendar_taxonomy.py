"""Calendar audience, visibility, and event access must remain independent."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from services.events_service.routers.member import (
    EventActor,
    _can_attend_event,
    _can_view_event,
    _event_response_dict,
)
from services.events_service.schemas import EventCreate
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
            "primary_audience": "academy",
            "audiences": ["academy"],
            "audience": "academy",
            "visibility": "public",
            "tier_access": "public",
            "start_time": datetime(2026, 9, 12, 9, tzinfo=timezone.utc),
        }
    )

    assert item is not None
    assert item.primary_audience == "academy"
    assert item.audiences == ["academy"]
    assert item.audience == "academy"
    assert item.visibility == "public"
    assert item.access_level == "public"


def test_anonymous_visitor_can_view_public_but_cannot_rsvp_without_account():
    actor = EventActor(None, frozenset(), False, False)
    event = _event()

    assert _can_view_event(event, actor, invited=False)
    assert not _can_attend_event(event, actor, invited=False)


def test_invite_only_event_requires_explicit_invite():
    actor = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)
    event = _event(visibility="invite_only", tier_access="invite_only")

    assert not _can_view_event(event, actor, invited=False)
    assert _can_view_event(event, actor, invited=True)
    assert _can_attend_event(event, actor, invited=True)


def test_invite_visibility_cannot_be_bypassed_by_malformed_public_tier():
    actor = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)
    malformed = _event(visibility="invite_only", tier_access="public")

    assert not _can_view_event(malformed, actor, invited=False)
    assert not _can_attend_event(malformed, actor, invited=False)
    assert _can_attend_event(malformed, actor, invited=True)


def test_public_discovery_is_independent_from_club_attendance():
    event = _event(visibility="public", tier_access="club")
    anonymous = EventActor(None, frozenset(), False, False)
    member = EventActor(uuid.uuid4(), frozenset(), True, False)
    club_member = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)

    assert _can_view_event(event, anonymous, invited=False)
    assert _can_view_event(event, member, invited=False)
    assert not _can_attend_event(event, anonymous, invited=False)
    assert not _can_attend_event(event, member, invited=False)
    assert _can_attend_event(event, club_member, invited=False)


def test_members_only_discovery_requires_profile_not_programme_entitlement():
    event = _event(visibility="members_only", tier_access="club")
    anonymous = EventActor(None, frozenset(), False, False)
    auth_without_profile = EventActor(None, frozenset({"club"}), True, False)
    member = EventActor(uuid.uuid4(), frozenset(), True, False)
    club_member = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)

    assert not _can_view_event(event, anonymous, invited=False)
    assert not _can_view_event(event, auth_without_profile, invited=False)
    assert _can_view_event(event, member, invited=False)
    assert not _can_attend_event(event, member, invited=False)
    assert _can_view_event(event, club_member, invited=False)
    assert _can_attend_event(event, club_member, invited=False)


def test_community_event_requires_community_entitlement():
    event = _event(visibility="public", tier_access="community")
    unpaid = EventActor(uuid.uuid4(), frozenset(), True, False)
    club_only = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)
    academy_only = EventActor(uuid.uuid4(), frozenset({"academy"}), True, False)
    community = EventActor(uuid.uuid4(), frozenset({"community"}), True, False)

    assert not _can_attend_event(event, unpaid, invited=False)
    assert not _can_attend_event(event, club_only, invited=False)
    assert not _can_attend_event(event, academy_only, invited=False)
    assert _can_attend_event(event, community, invited=False)


def test_private_location_is_redacted_until_viewer_can_attend():
    starts_at = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
    event = SimpleNamespace(
        id=uuid.uuid4(),
        community_experience_offering_id=None,
        title="Members-only Club Swim",
        description="A private venue test",
        event_type="community_swim",
        primary_audience="club",
        audiences=["club"],
        visibility="members_only",
        status="published",
        location_type="physical",
        timezone="Africa/Lagos",
        location_area="Yaba",
        is_location_private=True,
        location="Private Pool",
        start_time=starts_at,
        end_time=starts_at.replace(hour=11),
        max_capacity=20,
        tier_access="club",
        cost_kobo=None,
        pricing_mode="free",
        pricing_expected_attendees=20,
        cost_lines=[],
        estimated_total_cost=0,
        estimated_cost_per_attendee=0,
        margin_type="fixed_per_attendee",
        margin_value=0,
        margin_amount_per_attendee=0,
        email_reminder_hours=[],
        pool_id=uuid.uuid4(),
        pool_fee_kobo=None,
        organizer_surcharge_kobo=0,
        created_by=uuid.uuid4(),
        created_at=starts_at,
        updated_at=starts_at,
    )
    ordinary_member = EventActor(uuid.uuid4(), frozenset(), True, False)
    club_member = EventActor(uuid.uuid4(), frozenset({"club"}), True, False)

    redacted = _event_response_dict(event, actor=ordinary_member)
    eligible = _event_response_dict(event, actor=club_member)

    assert redacted["location"] == "Venue shared after RSVP"
    assert redacted["pool_id"] is None
    assert eligible["location"] == "Private Pool"
    assert eligible["pool_id"] == event.pool_id


def test_event_schema_rejects_mismatched_invite_visibility_and_access():
    with pytest.raises(ValidationError):
        EventCreate(
            title="Private swim",
            event_type="community_swim",
            visibility="invite_only",
            tier_access="public",
            start_time=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        )
