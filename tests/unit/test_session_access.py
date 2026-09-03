from datetime import datetime, timedelta, timezone

import pytest

from libs.common.session_access import (
    active_paid_tiers,
    default_booking_prompt_tier,
    evaluate_session_access,
    has_paid_session_access,
    is_unpaid_community_prospect,
    required_tier_for_session_type,
)

NOW = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=30)).isoformat()
PAST = (NOW - timedelta(days=1)).isoformat()


def _member(**overrides):
    data = {
        "id": "member-1",
        "member_id": "member-1",
        "active_tiers": ["community"],
        "primary_tier": "community",
        "community_paid_until": None,
        "club_paid_until": None,
        "academy_paid_until": None,
        "post_academy_club_until": None,
    }
    data.update(overrides)
    return data


def _session(**overrides):
    data = {
        "id": "session-1",
        "session_type": "community",
        "status": "scheduled",
        "starts_at": (NOW + timedelta(days=1)).isoformat(),
        "ends_at": (NOW + timedelta(days=1, hours=1)).isoformat(),
        "cohort_id": None,
        "pod_id": None,
    }
    data.update(overrides)
    return data


def test_required_tier_by_session_type():
    assert required_tier_for_session_type("cohort_class") == "academy"
    assert required_tier_for_session_type("academy") == "academy"
    assert required_tier_for_session_type("club") == "club"
    assert required_tier_for_session_type("community") == "community"
    assert required_tier_for_session_type("event") == "community"


def test_paid_products_do_not_expand_a_tier_hierarchy():
    member = _member(
        active_tiers=["academy", "club", "community"],
        primary_tier="academy",
        academy_paid_until=FUTURE,
    )

    assert active_paid_tiers(member, NOW) == {"academy"}


def test_unpaid_baseline_community_is_prospect_not_bookable():
    member = _member()
    decision = evaluate_session_access(member, _session(), now=NOW)

    assert is_unpaid_community_prospect(member, NOW)
    assert not has_paid_session_access(member, "community", NOW)
    assert default_booking_prompt_tier(member, NOW) == "prospect"
    assert not decision.bookable
    assert not decision.digest_eligible
    assert decision.reason == "membership_required"


def test_legacy_academy_prompt_access_is_scoped_elsewhere():
    assert has_paid_session_access(_member(), "academy", NOW)


def test_paid_community_can_book_community_and_event_sessions():
    member = _member(community_paid_until=FUTURE)

    assert evaluate_session_access(member, _session(), now=NOW).bookable
    assert evaluate_session_access(
        member,
        _session(session_type="event"),
        now=NOW,
    ).bookable


def test_paid_club_member_can_book_club_and_community():
    member = _member(
        active_tiers=["club", "community"],
        primary_tier="club",
        club_paid_until=FUTURE,
        community_paid_until=FUTURE,
    )

    assert evaluate_session_access(
        member, _session(session_type="club"), now=NOW
    ).bookable
    assert evaluate_session_access(member, _session(), now=NOW).bookable


def test_paid_academy_member_does_not_inherit_club_access():
    member = _member(
        active_tiers=["academy", "club", "community"],
        primary_tier="academy",
        academy_paid_until=FUTURE,
    )

    decision = evaluate_session_access(member, _session(session_type="club"), now=NOW)

    assert not decision.bookable
    assert not decision.digest_eligible
    assert decision.reason == "club_required"


def test_authoritative_club_product_result_overrides_legacy_dates():
    member = _member(club_paid_until=FUTURE)
    session = _session(session_type="club")

    denied = evaluate_session_access(
        member,
        session,
        now=NOW,
        club_product_access=False,
    )
    allowed = evaluate_session_access(
        _member(),
        session,
        now=NOW,
        club_product_access=True,
    )

    assert not denied.bookable
    assert denied.reason == "club_required"
    assert allowed.bookable


@pytest.mark.parametrize(
    ("club_access_result", "expected_source", "expected_fee"),
    [
        (
            {
                "allowed": True,
                "source": "club_enrollment",
                "payment_mode": "quarterly_prepaid",
                "fee_amount_kobo": 0,
            },
            "club_enrollment",
            0,
        ),
        (
            {
                "allowed": True,
                "source": "club_transition",
                "payment_mode": "transition_per_session",
                "fee_amount_kobo": 500_000,
            },
            "club_transition",
            500_000,
        ),
    ],
)
def test_club_enrollment_mode_controls_the_authoritative_member_fee(
    club_access_result, expected_source, expected_fee
):
    decision = evaluate_session_access(
        _member(),
        _session(session_type="club", pool_fee=9_000),
        now=NOW,
        club_access_result=club_access_result,
    )

    assert decision.bookable
    assert decision.access_source == expected_source
    assert decision.fee_amount_kobo == expected_fee


def test_community_dropin_requires_explicit_session_opt_in_and_annual_membership():
    session = _session(
        session_type="club",
        allows_community_dropins=True,
        community_dropin_fee_kobo=650_000,
    )
    denied = evaluate_session_access(
        _member(), session, now=NOW, club_access_result={"allowed": False}
    )
    allowed = evaluate_session_access(
        _member(community_paid_until=FUTURE),
        session,
        now=NOW,
        club_access_result={"allowed": False},
    )
    disabled = evaluate_session_access(
        _member(community_paid_until=FUTURE),
        {**session, "allows_community_dropins": False},
        now=NOW,
        club_access_result={"allowed": False},
    )

    assert denied.reason == "membership_required"
    assert allowed.bookable
    assert allowed.access_source == "community_dropin"
    assert allowed.fee_amount_kobo == 650_000
    assert disabled.reason == "community_dropins_disabled"


def test_post_academy_bridge_grants_club_access_and_prompt_tier():
    member = _member(
        active_tiers=["club", "community"],
        primary_tier="club",
        post_academy_club_until=FUTURE,
    )

    decision = evaluate_session_access(member, _session(session_type="club"), now=NOW)

    assert active_paid_tiers(member, NOW) == {"club"}
    assert default_booking_prompt_tier(member, NOW) == "club"
    assert decision.bookable
    assert decision.prompt_eligible


def test_cohort_class_requires_enrollment_and_not_suspended():
    member = _member(
        active_tiers=["academy", "club", "community"],
        primary_tier="academy",
        academy_paid_until=FUTURE,
    )
    session = _session(session_type="cohort_class", cohort_id="cohort-1")

    not_enrolled = evaluate_session_access(
        member,
        session,
        now=NOW,
        cohort_enrollment={"enrolled": False, "access_suspended": False},
    )
    suspended = evaluate_session_access(
        member,
        session,
        now=NOW,
        cohort_enrollment={"enrolled": True, "access_suspended": True},
    )
    enrolled = evaluate_session_access(
        member,
        session,
        now=NOW,
        cohort_enrollment={"enrolled": True, "access_suspended": False},
    )

    assert not not_enrolled.bookable
    assert not_enrolled.reason == "cohort_required"
    assert not suspended.bookable
    assert suspended.reason == "cohort_access_suspended"
    assert enrolled.bookable


def test_pod_scoped_club_session_requires_pod_membership():
    member = _member(
        id="member-1",
        active_tiers=["club", "community"],
        primary_tier="club",
        club_paid_until=FUTURE,
    )
    session = _session(session_type="club", pod_id="pod-1")

    denied = evaluate_session_access(
        member,
        session,
        now=NOW,
        pod_member_ids=["other-member"],
    )
    allowed = evaluate_session_access(
        member,
        session,
        now=NOW,
        pod_member_ids=["member-1"],
    )

    assert not denied.bookable
    assert denied.reason == "pod_required"
    assert allowed.bookable


@pytest.mark.parametrize("starts_at", [PAST, NOW.isoformat()])
def test_started_sessions_are_digest_visible_but_not_newly_bookable(starts_at):
    member = _member(community_paid_until=FUTURE)
    session = _session(
        starts_at=starts_at,
        ends_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    decision = evaluate_session_access(member, session, now=NOW)

    assert not decision.bookable
    assert decision.digest_eligible
    assert decision.reason == "session_unavailable"


def test_confirmed_booking_preserves_sign_in_even_after_tier_expires():
    member = _member(community_paid_until=PAST)
    session = _session(
        starts_at=(NOW - timedelta(minutes=15)).isoformat(),
        ends_at=(NOW + timedelta(minutes=45)).isoformat(),
    )
    decision = evaluate_session_access(
        member,
        session,
        now=NOW,
        confirmed_booking=True,
    )

    assert decision.sign_in_allowed
    assert not decision.bookable


@pytest.mark.parametrize(
    "session",
    [
        _session(status="cancelled"),
        _session(
            starts_at=(NOW + timedelta(hours=2)).isoformat(),
            ends_at=(NOW + timedelta(hours=3)).isoformat(),
        ),
        _session(
            status="completed",
            starts_at=(NOW - timedelta(hours=3)).isoformat(),
            ends_at=(NOW - timedelta(hours=2)).isoformat(),
        ),
    ],
)
def test_confirmed_booking_does_not_bypass_session_sign_in_window(session):
    decision = evaluate_session_access(
        _member(community_paid_until=PAST),
        session,
        now=NOW,
        confirmed_booking=True,
    )

    assert not decision.sign_in_allowed
    assert decision.reason == "session_unavailable"


def test_cancelled_session_is_not_sign_in_eligible_even_with_booking():
    session = _session(
        status="cancelled",
        starts_at=(NOW - timedelta(minutes=15)).isoformat(),
        ends_at=(NOW + timedelta(minutes=45)).isoformat(),
    )

    confirmed = evaluate_session_access(
        _member(community_paid_until=PAST),
        session,
        now=NOW,
        confirmed_booking=True,
    )
    unbooked = evaluate_session_access(
        _member(community_paid_until=FUTURE),
        session,
        now=NOW,
    )

    assert not confirmed.sign_in_eligible
    assert not confirmed.sign_in_allowed
    assert not unbooked.sign_in_eligible
    assert not unbooked.sign_in_allowed
