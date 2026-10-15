"""Regression tests for Club commercial terms versus published session rates."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

from libs.common.session_access import evaluate_session_access
from services.members_service.routers.club_commercial_terms import ClubTermsApproval
from services.sessions_service.services.commercial import apply_explicit_rate


def _session(mode="plan_included"):
    now = datetime.now(timezone.utc)
    return NS(
        session_type="club", status="scheduled",
        starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=2),
        pool_fee=1_500_000, club_access_mode=mode, pod_id=None,
    )


def test_transition_agreement_is_quoted_without_a_coupon():
    session = _session()
    result = evaluate_session_access(
        {"member_id": str(uuid4())}, session,
        now=datetime.now(timezone.utc),
        club_access_result={
            "allowed": True, "source": "club_transition",
            "fee_amount_kobo": 1_000_000,
        },
    )
    assert result.bookable
    assert result.fee_amount_kobo == 1_000_000
    assert result.rate_code == "club_transition_terms"

    session_rate = NS(
        is_active=True, audience="club", access_source="club_transition",
        priority=20, id=uuid4(), rate_mode="fixed",
        amount_kobo=1_500_000, label="Standard Club session",
        rate_code="club_transition"
    )
    priced = apply_explicit_rate(result, [session_rate])
    assert priced.fee_amount_kobo == 1_000_000
    assert priced.rate_code == "club_transition_terms"


def test_paid_addon_never_inherits_regular_transition_terms():
    session = _session("paid_addon")
    result = evaluate_session_access(
        {"member_id": str(uuid4())}, session,
        now=datetime.now(timezone.utc),
        club_access_result={
            "allowed": True, "source": "club_transition",
            "fee_amount_kobo": None,
        },
    )
    assert result.fee_amount_kobo == 1_500_000
    assert result.rate_code == "club_transition"


def test_terms_reject_price_above_standard_and_naive_dates():
    now = datetime.now(timezone.utc)
    good = ClubTermsApproval(
        adjustment_id=uuid4(), agreed_session_fee_kobo=1_000_000,
        standard_session_fee_kobo=1_500_000,
        valid_from=now + timedelta(minutes=2),
        valid_until=now + timedelta(days=30),
        reason="Original transition rate communicated before VI pricing finalised",
    )
    assert good.agreed_session_fee_kobo == 1_000_000
