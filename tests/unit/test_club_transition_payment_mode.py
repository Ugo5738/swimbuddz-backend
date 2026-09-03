from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.members_service.routers.clubs import (
    _application_payment_mode,
    _assert_transition_can_start,
    _new_club_enrollment,
)
from services.members_service.schemas.club import ClubObservedAssessmentUpdate


def _application(**overrides):
    values = {
        "approved_payment_modes": ["quarterly_prepaid"],
        "transition_session_rate_kobo": None,
        "transition_expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_admin_can_approve_only_transition_with_snapshotted_terms():
    assessment = ClubObservedAssessmentUpdate(
        outcome="club_ready_modified",
        approved_payment_modes=["transition_per_session"],
        transition_session_rate_kobo=500_000,
        transition_expires_at=date(2026, 12, 31),
    )
    application = _application(
        approved_payment_modes=assessment.approved_payment_modes,
        transition_session_rate_kobo=assessment.transition_session_rate_kobo,
        transition_expires_at=assessment.transition_expires_at,
    )

    assert _application_payment_mode(application, None) == "transition_per_session"
    with pytest.raises(HTTPException) as exc_info:
        _application_payment_mode(application, "quarterly_prepaid")
    assert exc_info.value.status_code == 403


def test_admin_can_approve_both_modes_but_member_cannot_spoof_another_mode():
    application = _application(
        approved_payment_modes=["quarterly_prepaid", "transition_per_session"],
        transition_session_rate_kobo=500_000,
        transition_expires_at=date(2026, 12, 31),
    )

    assert (
        _application_payment_mode(application, "quarterly_prepaid")
        == "quarterly_prepaid"
    )
    assert (
        _application_payment_mode(application, "transition_per_session")
        == "transition_per_session"
    )
    with pytest.raises(HTTPException, match="not approved"):
        _application_payment_mode(application, "invented_mode")


def test_transition_cannot_be_selected_without_rate_and_expiry_snapshot():
    application = _application(approved_payment_modes=["transition_per_session"])

    with pytest.raises(HTTPException, match="missing its rate or expiry"):
        _application_payment_mode(application, "transition_per_session")


def test_expired_transition_cannot_start_a_new_checkout():
    application = _application(
        approved_payment_modes=["transition_per_session"],
        transition_session_rate_kobo=500_000,
        transition_expires_at=date.today() - timedelta(days=1),
    )

    with pytest.raises(HTTPException, match="transition has expired"):
        _assert_transition_can_start(application, on_date=date.today())


def test_transition_activation_builds_dated_location_and_rate_snapshot():
    application = SimpleNamespace(
        id="application-ay",
        member_id="member-ay",
        club_id="club-yaba",
        preferred_pod_id="pod-ay",
        transition_session_rate_kobo=520_000,
        transition_expires_at=date(2026, 12, 31),
    )
    plan = SimpleNamespace(
        id="plan-yaba-q4",
        pool_id="pool-yaba",
        operating_area_id="area-yaba",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
    )

    enrollment = _new_club_enrollment(
        application=application,
        plan=plan,
        payment_reference="PAY-AY-TRANSITION",
        activation_date=date(2026, 9, 15),
        payment_mode="transition_per_session",
    )

    assert enrollment.starts_at.date() == date(2026, 9, 15)
    assert enrollment.ends_at.date() == date(2027, 1, 1)
    assert enrollment.pool_id == "pool-yaba"
    assert enrollment.operating_area_id == "area-yaba"
    assert enrollment.payment_mode == "transition_per_session"
    assert enrollment.transition_session_rate_kobo == 520_000
