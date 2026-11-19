"""Fast, database-free checks for next-swim recommendation ordering.

These tests do not exercise cross-service access; the route delegates that
decision to the existing Sessions access service.
"""

from services.sessions_service.routers.next_swim import _ranking_key


def test_included_home_session_is_preferred_over_paid_visiting_session():
    paid = {
        "id": "b",
        "starts_at": "2026-11-21T09:00:00+01:00",
        "access": {"access_source": "club_visit", "fee_amount_kobo": 1500000},
    }
    included = {
        "id": "a",
        "starts_at": "2026-11-28T09:00:00+01:00",
        "access": {"access_source": "plan_included", "fee_amount_kobo": 0},
    }
    assert sorted([paid, included], key=_ranking_key)[0]["id"] == "a"


def test_earliest_slot_wins_at_equal_member_access():
    later = {
        "id": "b",
        "starts_at": "2026-11-28T09:00:00+01:00",
        "access": {"access_source": "club_visit", "fee_amount_kobo": 1500000},
    }
    earlier = {
        "id": "a",
        "starts_at": "2026-11-21T09:00:00+01:00",
        "access": {"access_source": "club_visit", "fee_amount_kobo": 1500000},
    }
    assert sorted([later, earlier], key=_ranking_key)[0]["id"] == "a"


def test_missing_pricing_does_not_rank_as_included():
    unpriced = {"id": "b", "starts_at": "2026-11-21T09:00:00+01:00", "access": {}}
    included = {
        "id": "a",
        "starts_at": "2026-11-21T09:00:00+01:00",
        "access": {"access_source": "active_club", "fee_amount_kobo": 0},
    }
    assert sorted([unpriced, included], key=_ranking_key)[0]["id"] == "a"
