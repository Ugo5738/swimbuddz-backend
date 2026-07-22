from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from libs.auth.models import AuthUser
from services.reporting_service import dependencies
from services.reporting_service.services.activity_policy import (
    leaderboard_eligible,
    report_activity_state,
    share_card_eligible,
)


@pytest.mark.asyncio
async def test_active_inherited_community_can_view_reports(monkeypatch):
    async def member_lookup(*args, **kwargs):
        return {"id": "member-1"}

    async def membership_lookup(*args, **kwargs):
        return {"effective_paid_tiers": ["academy", "club", "community"]}

    monkeypatch.setattr(dependencies, "get_member_by_auth_id", member_lookup)
    monkeypatch.setattr(dependencies, "get_member_membership", membership_lookup)

    member = await dependencies.require_active_community_membership(
        AuthUser(sub="auth-1")
    )
    assert member["id"] == "member-1"


@pytest.mark.asyncio
async def test_prospect_cannot_view_reports(monkeypatch):
    async def member_lookup(*args, **kwargs):
        return {"id": "member-1"}

    async def membership_lookup(*args, **kwargs):
        return {"effective_paid_tiers": []}

    monkeypatch.setattr(dependencies, "get_member_by_auth_id", member_lookup)
    monkeypatch.setattr(dependencies, "get_member_membership", membership_lookup)

    with pytest.raises(HTTPException) as exc:
        await dependencies.require_active_community_membership(AuthUser(sub="auth-1"))
    assert exc.value.status_code == 403


def test_report_activity_and_eligibility_policy():
    empty = SimpleNamespace(
        total_sessions_attended=0,
        volunteer_hours=0,
        events_attended=0,
        milestones_achieved=0,
        certificates_earned=0,
    )
    volunteer = SimpleNamespace(**{**empty.__dict__, "volunteer_hours": 1.5})

    low_attendance = SimpleNamespace(**{**empty.__dict__, "total_sessions_attended": 2})
    active_attendance = SimpleNamespace(
        **{**empty.__dict__, "total_sessions_attended": 3}
    )

    assert report_activity_state(empty) == "no_activity"
    assert share_card_eligible(empty) is False
    assert report_activity_state(volunteer) == "low_activity"
    assert share_card_eligible(volunteer) is True
    assert report_activity_state(low_attendance) == "low_activity"
    assert leaderboard_eligible(low_attendance) is False
    assert report_activity_state(active_attendance) == "active"
    assert leaderboard_eligible(active_attendance) is True
