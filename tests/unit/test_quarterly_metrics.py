from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from libs.common.reporting import reporting_window
from services.reporting_service.services import aggregator, ecosystem
from services.reporting_service.services.activity_policy import (
    meaningful_activity_count,
)
from services.reporting_service.services.quarter_utils import quarter_date_range
from services.wallet_service.routers.quarterly_metrics import summarize_transactions


def test_q3_window_includes_fractional_final_second_but_not_october():
    start, end = reporting_window(*quarter_date_range(2026, 3))
    assert start <= datetime.fromisoformat("2026-09-30T23:59:59.999999+01:00") < end
    assert not start <= datetime.fromisoformat("2026-10-01T00:00:00+01:00") < end


def test_completed_rewards_are_separate_from_refunds_topups_and_failed_flows():
    def transaction(kind, direction, amount, status="completed", wallet_id="a"):
        return SimpleNamespace(
            transaction_type=kind,
            direction=direction,
            amount=amount,
            status=status,
            wallet_id=wallet_id,
            reference_type="session_booking",
            service_source="sessions",
        )

    result = summarize_transactions(
        [
            transaction("topup", "credit", 210),
            transaction("refund", "credit", 87),
            transaction("admin_adjustment", "credit", 85),
            transaction("reward", "credit", 38),
            transaction("purchase", "debit", 211),
            transaction("admin_adjustment", "debit", 45),
            transaction("reward", "credit", 999, "failed"),
            transaction("purchase", "debit", 999, "pending"),
        ]
    )
    assert result.metrics["wallet_credits"] == 420
    assert result.metrics["wallet_debits"] == 256
    assert result.metrics["reward_bubbles"] == 38
    assert result.metrics["purchase_bubbles"] == 211
    assert result.metrics["reward_recipients"] == 1


@pytest.mark.asyncio
async def test_failed_source_is_unknown_but_successful_empty_source_is_zero(
    monkeypatch,
):
    monkeypatch.setattr(
        ecosystem, "internal_get", AsyncMock(side_effect=RuntimeError("offline"))
    )
    unavailable = await ecosystem.fetch_section("http://service", "/summary", {})
    assert unavailable["available"] is False
    assert "count" not in unavailable["metrics"]
    response = SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"metrics": {"count": 0}}
    )
    monkeypatch.setattr(ecosystem, "internal_get", AsyncMock(return_value=response))
    available = await ecosystem.fetch_section("http://service", "/summary", {})
    assert available["available"] is True
    assert available["metrics"]["count"] == 0


def test_location_aliases_deduplicate_people_without_exposing_identifiers():
    sections = {
        "sessions": {
            "details": {
                "attendance_available": True,
                "sessions": [
                    {
                        "location": "Yaba",
                        "hours": 3,
                        "member_attendance": 2,
                        "attendance": 2,
                        "member_ids": ["a", "b"],
                        "status": "scheduled",
                    },
                    {
                        "location": "Rowe Park Pool",
                        "hours": 1,
                        "member_attendance": 1,
                        "attendance": 1,
                        "member_ids": ["a"],
                        "status": "completed",
                    },
                ],
            }
        },
        "members": {
            "details": {
                "pods": [{"name": "Private Pod", "member_ids": ["a"]}],
                "clubs": [],
            }
        },
    }
    locations = ecosystem.location_metrics(sections)
    assert len(locations) == 1
    assert locations[0]["unique_members"] == 2
    assert locations[0]["member_swimmer_hours"] == 7
    assert locations[0]["completed_sessions"] == 1
    public_sections = ecosystem.strip_internal_details(sections)
    assert public_sections["sessions"]["details"] == {}
    assert public_sections["members"]["details"] == {}


@pytest.mark.asyncio
async def test_mixed_member_keeps_all_swims_separate_from_academy_rate(monkeypatch):
    academy = {
        "id": "academy",
        "session_type": "cohort_class",
        "starts_at": "2026-09-05T08:00:00+01:00",
        "ends_at": "2026-09-05T09:00:00+01:00",
        "location_name": "VI",
    }
    club = {
        **academy,
        "id": "club",
        "session_type": "club",
        "ends_at": "2026-09-05T11:00:00+01:00",
        "location_name": "Yaba",
    }
    unbooked = {**club, "id": "unbooked"}
    monkeypatch.setattr(
        aggregator, "_fetch_member_session_commitments", AsyncMock(return_value=[club])
    )
    monkeypatch.setattr(
        aggregator,
        "_fetch_member_cohort_enrollments",
        AsyncMock(return_value=[{"cohort_id": "cohort", "status": "enrolled"}]),
    )
    monkeypatch.setattr(
        aggregator, "_fetch_converted_guest_hours", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        aggregator, "_fetch_cohort_sessions", AsyncMock(return_value=[academy])
    )
    monkeypatch.setattr(
        aggregator, "_is_enrollment_active_for_session", lambda *_: True
    )
    monkeypatch.setattr(
        aggregator,
        "_fetch_member_attendance_records",
        AsyncMock(
            return_value=[
                {"session_id": "academy", "status": "present"},
                {"session_id": "club", "status": "present"},
            ]
        ),
    )
    result = await aggregator._compute_segment_attendance(
        member_auth_id="auth",
        member_id="member",
        start=quarter_date_range(2026, 3)[0],
        end=quarter_date_range(2026, 3)[1],
        fallback_attendance={
            "attendance_available": True,
            "attended_sessions": [academy, club, unbooked],
            "total_pool_hours": 7,
        },
    )
    assert result["primary_segment"] == "academy"
    assert result["total_attended"] == result["total_available"] == 1
    assert result["attendance_rate"] == 1
    assert result["all_sessions_attended"] == 3
    assert result["attended_by_type"] == {"cohort_class": 1, "club": 2}
    assert result["pool_hours"] == 7


def test_coach_attendance_does_not_count_as_a_swim():
    assert (
        aggregator._attendance_status_by_session(
            [{"session_id": "s", "status": "present", "role": "coach"}]
        )
        == {}
    )


def test_activity_policy_recognizes_secondary_programme_swims():
    report = SimpleNamespace(
        total_sessions_attended=0,
        events_attended=0,
        milestones_achieved=0,
        certificates_earned=0,
        volunteer_hours=0,
        activity_details={"attendance": {"all_sessions_attended": 3}},
    )
    assert meaningful_activity_count(report) == 3


def test_highlights_do_not_include_october_episode_in_q3():
    q3 = ecosystem.quarter_editorial(2026, 3)
    assert {e["episode_number"] for e in q3["external_podcast_episodes"]} == {1, 2}
    assert (
        ecosystem.quarter_editorial(2026, 4)["external_podcast_episodes"][0][
            "episode_number"
        ]
        == 3
    )
    assert any(h["status"] == "planning" for h in q3["highlights"])


@pytest.mark.asyncio
async def test_regeneration_preserves_member_leaderboard_privacy(monkeypatch):
    monkeypatch.setattr(
        aggregator,
        "_fetch_all_approved_members",
        AsyncMock(return_value=[{"auth_id": "a", "roles": ["member"]}]),
    )
    report = SimpleNamespace(
        member_auth_id="a",
        leaderboard_opt_out=True,
        member_tier="community",
        total_sessions_attended=0,
        total_sessions_available=0,
        programs_enrolled=0,
        events_attended=0,
        volunteer_hours=0,
        milestones_achieved=0,
        certificates_earned=0,
        activity_details={"attendance": {"all_sessions_attended": 3}},
    )
    monkeypatch.setattr(
        aggregator, "compute_member_report", AsyncMock(return_value=report)
    )
    percentile = AsyncMock()
    monkeypatch.setattr(aggregator, "_compute_percentile_ranks", percentile)
    db = AsyncMock()
    assert await aggregator.compute_all_member_reports(2026, 3, db) == 1
    assert report.leaderboard_opt_out is True
    db.delete.assert_not_called()
    percentile.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_regeneration_fails_without_deleting_existing_reports(
    monkeypatch,
):
    monkeypatch.setattr(
        aggregator,
        "_fetch_all_approved_members",
        AsyncMock(return_value=[{"auth_id": "a", "roles": ["member"]}]),
    )
    monkeypatch.setattr(
        aggregator,
        "compute_member_report",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    percentile = AsyncMock()
    monkeypatch.setattr(aggregator, "_compute_percentile_ranks", percentile)
    db = AsyncMock()
    with pytest.raises(RuntimeError, match="incomplete"):
        await aggregator.compute_all_member_reports(2026, 3, db)
    assert db.execute.await_count == 0  # No stale-row deletion on failure.
    db.rollback.assert_awaited_once()
    percentile.assert_not_called()


@pytest.mark.asyncio
async def test_attendance_uses_swim_date_even_when_recorded_in_october(monkeypatch):
    from libs.common import service_client
    from services.attendance_service.routers.internal import get_member_attendance_stats

    records = [
        SimpleNamespace(session_id="q3", status="present"),
        SimpleNamespace(session_id="q4", status="present"),
    ]
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: "member"),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: records)),
    ]
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: [
            {
                "id": "q3",
                "starts_at": "2026-09-30T08:00:00+01:00",
                "ends_at": "2026-09-30T09:00:00+01:00",
                "session_type": "cohort_class",
                "status": "scheduled",
                "location_name": "VI",
            },
            {
                "id": "q4",
                "starts_at": "2026-10-01T08:00:00+01:00",
                "ends_at": "2026-10-01T11:00:00+01:00",
                "session_type": "club",
                "status": "scheduled",
                "location_name": "Yaba",
            },
        ],
    )
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(service_client, "internal_post", post)
    result = await get_member_attendance_stats(
        "auth", *quarter_date_range(2026, 3), None, db
    )
    assert result.attendance_available is True
    assert result.total_present == 1
    assert result.total_pool_hours == 1
    assert result.by_location == {"VI": 1}
    assert post.call_args.kwargs["json"] == {"session_ids": ["q3", "q4"]}


@pytest.mark.parametrize(
    "domain",
    ["members", "wallet", "sessions", "media", "communications", "ai", "academy"],
)
def test_new_summary_routes_require_service_authentication(domain):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    module = importlib.import_module(
        f"services.{domain}_service.routers.quarterly_metrics"
    )
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        result = client.get(
            f"/internal/{domain}/reporting-summary",
            params={
                "from": "2026-07-01T00:00:00+01:00",
                "to": "2026-09-30T23:59:59+01:00",
            },
        )
    assert result.status_code in {401, 403}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("_fetch_member_attendance_records", ("member", ["session"])),
        ("_fetch_member_session_commitments", ("auth", "start", "end")),
        ("_fetch_converted_guest_hours", ("member", "start", "end")),
        ("_fetch_cohort_sessions", ("cohort", "start", "end")),
        ("_fetch_member_cohort_enrollments", ("auth", "start", "end")),
        ("_fetch_all_approved_members", ()),
    ],
)
async def test_unavailable_primary_sources_cannot_fabricate_a_programme_rate(
    monkeypatch, name, args
):
    monkeypatch.setattr(aggregator, "_safe_get", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="unavailable"):
        await getattr(aggregator, name)(*args)
