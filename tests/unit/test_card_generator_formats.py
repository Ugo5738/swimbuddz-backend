from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from services.reporting_service.services import card_generator


def _report():
    return SimpleNamespace(
        member_auth_id="member-auth-id",
        member_name="Jesudara Hinmikaiye",
        member_tier=None,
        quarter=2,
        year=2026,
        pool_hours=16,
        total_sessions_attended=8,
        attendance_rate=1.0,
        sessions_by_type={"cohort_class": 8},
        streak_longest=4,
        milestones_achieved=5,
        programs_enrolled=1,
        volunteer_hours=0,
        bubbles_earned=0,
        events_attended=0,
        rides_taken=0,
        certificates_earned=0,
        orders_placed=0,
        is_first_quarter=False,
        attendance_percentile=0.0,
    )


async def _no_photo_url(member_auth_id: str) -> None:
    return None


async def _referral_link(member_auth_id: str) -> str:
    return "https://swimbuddz.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("format,expected_size", card_generator.FORMATS.items())
async def test_generate_card_image_returns_expected_format_dimensions(
    monkeypatch, format, expected_size
):
    monkeypatch.setattr(card_generator, "_fetch_member_photo_url", _no_photo_url)
    monkeypatch.setattr(card_generator, "_fetch_referral_link", _referral_link)

    image_data = await card_generator.generate_card_image(_report(), format=format)

    with Image.open(BytesIO(image_data)) as img:
        assert img.size == expected_size


def test_card_stats_label_academy_attendance_for_cohort_member():
    report = _report()

    stats = card_generator._card_stats(report)

    assert stats[0] == ("100%", "Academy attendance")


def test_card_stats_label_club_attendance_without_academy_activity():
    report = _report()
    report.attendance_rate = 0.8
    report.sessions_by_type = {"club": 10}
    report.milestones_achieved = 0
    report.programs_enrolled = 0

    stats = card_generator._card_stats(report)

    assert stats[0] == ("80%", "Club attendance")


def test_card_stats_uses_community_metric_instead_of_attendance_for_community_member():
    report = _report()
    report.sessions_by_type = {"community": 4}
    report.attendance_rate = 1.0
    report.milestones_achieved = 0
    report.programs_enrolled = 0
    report.volunteer_hours = 2.5
    report.bubbles_earned = 30

    stats = card_generator._card_stats(report)

    assert stats[0] == ("2.5h", "Volunteered")
    assert ("30", "Bubbles") in stats
