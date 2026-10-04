from services.sessions_service.schemas.guest_pass import SessionRosterEntry
from services.sessions_service.schemas.participant import AdminGuestWalkInCreate


def test_guest_walk_in_defaults_to_missing_waiver_and_unknown_payment():
    payload = AdminGuestWalkInCreate(full_name="Walk In Guest")

    assert payload.waiver_status == "missing"
    assert payload.payment_status is None
    assert payload.fee_override_reason is None


def test_walk_in_roster_contract_keeps_commercial_and_safety_fields():
    row = SessionRosterEntry(
        id="11111111-1111-4111-8111-111111111111",
        participant_id="22222222-2222-4222-8222-222222222222",
        kind="walk_in_guest",
        full_name="Walk In Guest",
        booking_status="walk_in",
        attendance_status="present",
        fee_amount_kobo=1_500_000,
        payment_status="unpaid",
        waiver_status="missing",
    )

    dumped = row.model_dump(mode="json")
    assert dumped["participant_id"] == "22222222-2222-4222-8222-222222222222"
    assert dumped["fee_amount_kobo"] == 1_500_000
    assert dumped["payment_status"] == "unpaid"
    assert dumped["waiver_status"] == "missing"
