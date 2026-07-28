from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest


class _ScalarResult:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    def scalar_one_or_none(self):
        return self.one

    def scalars(self):
        return self

    def all(self):
        return self.many


class _RecognitionDb:
    def __init__(self, profile, active_ids):
        self.results = [
            _ScalarResult(one=profile),
            _ScalarResult(many=active_ids),
        ]

    async def execute(self, _query):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_recognition_emails_winner_with_photo_and_notifies_crew(monkeypatch):
    from services.volunteer_service.services import recognition

    winner_id = UUID("da82f2df-dcf9-40a8-9229-ff58d56b1d29")
    teammate_id = UUID("a30a1b89-e536-4aef-8ef8-e2f55ea6eb1b")
    db = _RecognitionDb(
        profile=SimpleNamespace(spotlight_quote="Community first"),
        active_ids=[winner_id, teammate_id],
    )
    monkeypatch.setattr(
        recognition,
        "resolve_members_with_photos",
        AsyncMock(
            return_value={
                str(winner_id): SimpleNamespace(
                    first_name="Ada",
                    full_name="Ada Volunteer",
                    email="ada@example.com",
                    profile_photo_url="https://cdn.example.test/ada.jpg",
                )
            }
        ),
    )
    dispatch = AsyncMock(return_value={"dispatched": 1})
    monkeypatch.setattr(recognition, "dispatch_notification", dispatch)

    await recognition.announce_volunteer_of_the_month(
        db,
        member_id=winner_id,
        period_start=date(2026, 6, 1),
        monthly_hours=7.5,
    )

    winner_call = dispatch.await_args_list[0].kwargs
    assert winner_call["member_ids"] == [str(winner_id)]
    assert winner_call["channels"] == ["in_app", "email"]
    assert winner_call["email_data"]["to_email"] == "ada@example.com"
    assert "ada.jpg" in winner_call["email_data"]["html_content"]
    assert "7.5 volunteer hours" in winner_call["email_data"]["html_content"]

    crew_call = dispatch.await_args_list[1].kwargs
    assert crew_call["member_ids"] == [str(teammate_id)]
    assert crew_call["channels"] == ["in_app"]
