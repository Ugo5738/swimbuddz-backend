"""Fast, database-free checks for next-swim recommendation ordering.

These tests do not exercise cross-service access; the route delegates that
decision to the existing Sessions access service.
"""

import uuid

from services.sessions_service.routers.next_swim import _is_rebookable, _ranking_key


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


def test_assigned_included_academy_class_cannot_be_rebooked():
    row = {
        "id": str(uuid.uuid4()),
        "session_type": "cohort_class",
        "cohort_id": str(uuid.uuid4()),
        "cohort_fee_mode": "included",
        "access": {"bookable": True, "access_source": "cohort_enrollment"},
    }
    assert _is_rebookable(row, set()) is False
    row["cohort_fee_mode"] = "paid_extra"
    assert _is_rebookable(row, set()) is True


def test_already_confirmed_or_blocked_session_cannot_be_recommended():
    session_id = uuid.uuid4()
    row = {"id": str(session_id), "session_type": "club", "access": {"bookable": True}}
    assert _is_rebookable(row, {session_id}) is False
    row["access"]["bookable"] = False
    assert _is_rebookable(row, set()) is False
