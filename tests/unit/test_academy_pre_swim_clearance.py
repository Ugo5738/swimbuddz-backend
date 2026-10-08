"""Safety readiness is required for Academy attendance, not for checkout."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from services.members_service.routers.internal.swim_clearance import (
    missing_requirements,
)
from services.attendance_service.routers.member import _shared


def test_safety_clearance_identifies_missing_fields():
    member = SimpleNamespace(is_active=True, profile=None, emergency_contact=None)
    missing = missing_requirements(member)
    assert "contact_phone" in missing
    assert "swimming_background" in missing
    assert "emergency_contact" in missing


def test_member_ready_with_contact_background_and_emergency():
    member = SimpleNamespace(
        is_active=True,
        profile=SimpleNamespace(
            phone="+2348000000000",
            swim_level="beginner",
            deep_water_comfort="uncomfortable",
        ),
        emergency_contact=SimpleNamespace(
            name="Trusted Person",
            phone="+2348010000000",
            contact_relationship="friend",
        ),
    )
    assert missing_requirements(member) == []


@pytest.mark.asyncio
async def test_non_academy_swims_skip_clearance(monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("Unexpected Members API call")

    monkeypatch.setattr(_shared, "internal_post", unexpected)
    await _shared.require_academy_safety_clearance(
        {"session_type": "club"}, [__import__("uuid").uuid4()]
    )


@pytest.mark.asyncio
async def test_academy_sign_in_denied_without_clearance(monkeypatch):
    member_id = __import__("uuid").uuid4()

    async def not_ready(*args, **kwargs):
        return httpx.Response(
            200,
            json={str(member_id): {"ready": False, "missing": ["emergency_contact"]}},
        )

    monkeypatch.setattr(_shared, "internal_post", not_ready)
    with pytest.raises(HTTPException) as error:
        await _shared.require_academy_safety_clearance(
            {"session_type": "cohort_class"}, [member_id]
        )
    assert error.value.status_code == 409
