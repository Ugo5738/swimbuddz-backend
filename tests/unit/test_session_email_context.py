from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.communications_service.services import session_email_context


class _ConfigResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_shared_context_enriches_club_session_once(monkeypatch):
    config = SimpleNamespace(
        audience="club",
        featured_image_media_id="media-club",
        image_alt="Club swimmers",
        section_intro="Your Club training this week.",
        default_gear_notes="Bring fins and paddles.",
        is_enabled=True,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ConfigResult([config])))
    session = {
        "id": "session-1",
        "title": "Orca Technique",
        "session_type": "club",
        "starts_at": "2026-08-01T09:00:00+01:00",
        "ends_at": "2026-08-01T11:00:00+01:00",
        "timezone": "Africa/Lagos",
        "pool_id": "pool-1",
        "pod_id": "pod-1",
        "location_name": "Rowe Park Pool",
        "location_address": "Herbert Macaulay Way",
        "pool_fee": 500000,
        "capacity": 20,
        "occupied_slots": 7,
        "description": "Freestyle pacing and turns",
        "coach_member_ids": ["coach-1"],
    }
    pod = {
        "id": "pod-1",
        "handle": "orcas",
        "pod_lead_id": "lead-1",
        "assistant_pod_lead_id": "assistant-1",
        "active_member_ids": ["member-1"],
    }
    people = [
        {"id": "lead-1", "first_name": "Ada", "last_name": "Lead"},
        {"id": "assistant-1", "first_name": "Bola", "last_name": "Assist"},
        {"id": "coach-1", "first_name": "Chioma", "last_name": "Coach"},
    ]
    weather = {
        "condition_text": "Light rain",
        "temperature_text": "28°C",
        "rain_chance_text": "55% chance of rain",
        "rainfall_text": "~1mm during session",
        "explanation": "Light rain likely - warm and swimmable.",
    }
    monkeypatch.setattr(
        session_email_context,
        "resolve_media_urls",
        AsyncMock(return_value={"media-club": "https://cdn.test/club.jpg"}),
    )
    monkeypatch.setattr(
        session_email_context,
        "get_pod_by_id",
        AsyncMock(return_value=pod),
    )
    monkeypatch.setattr(
        session_email_context,
        "internal_post",
        AsyncMock(
            return_value=_Response(
                {
                    "configs": {
                        "session-1": [
                            {
                                "ride_area_name": "Ago Palace Way",
                                "cost": 5000,
                                "pickup_locations": [{"name": "Stop 1"}],
                            }
                        ]
                    }
                }
            )
        ),
    )
    monkeypatch.setattr(
        session_email_context,
        "get_session_weather_summary",
        AsyncMock(return_value=weather),
    )

    batch = await session_email_context.build_session_email_contexts(
        db,
        [session],
        known_people=people,
    )

    context = batch.sessions["session-1"]
    assert context["featured_image_url"] == "https://cdn.test/club.jpg"
    assert context["scope_label"] == "orcas Pod"
    assert context["leader_label"] == (
        "Pod Lead: Ada Lead | Assistant Pod Lead: Bola Assist | " "Coach: Chioma Coach"
    )
    assert context["weather_text"].startswith("Light rain | 28°C")
    assert context["transport_text"] == (
        "Transport available from Ago Palace Way via Stop 1 from NGN 5,000"
    )
    assert context["gear_notes"] == "Bring fins and paddles."
    assert context["fee_text"] == "NGN 5,000"
    assert context["availability_text"] == "13 spots left"
    assert batch.pod_member_ids_by_session["session-1"] == {"member-1"}
