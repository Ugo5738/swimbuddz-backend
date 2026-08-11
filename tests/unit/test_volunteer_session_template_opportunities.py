from datetime import time

from services.volunteer_service.routers.internal import _materialised_source_slot_ids
from services.volunteer_service.schemas import SessionTemplateVolunteerSlotCreate


def test_session_template_slot_accepts_shift_time_overrides() -> None:
    slot = SessionTemplateVolunteerSlotCreate(
        session_template_id="4ccb17e1-5a3a-47c2-87f5-68f3c3e19399",
        role_id="7df87c50-9d73-4421-b3bb-5964c94002b5",
        start_time_override="08:30",
        end_time_override="10:00",
    )

    assert slot.start_time_override == time(8, 30)
    assert slot.end_time_override == time(10, 0)


def test_template_materialisation_is_keyed_by_slot_not_role() -> None:
    rows = [
        ({"source_template_slot_id": "slot-morning", "role_id": "same-role"},),
        ({"source_template_slot_id": "slot-afternoon", "role_id": "same-role"},),
        ({"unrelated": "metadata"},),
        (None,),
    ]

    assert _materialised_source_slot_ids(rows) == {
        "slot-morning",
        "slot-afternoon",
    }
