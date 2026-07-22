import uuid

from services.academy_service.routers.enrollments.admin_payments import (
    _academy_access_application_key,
)


def test_academy_access_key_is_stable_for_retries_of_one_payment():
    enrollment_id = uuid.uuid4()

    first = _academy_access_application_key(enrollment_id, "PAY-123")
    retry = _academy_access_application_key(enrollment_id, "PAY-123")

    assert first == retry == "academy-payment:PAY-123:paid-access"


def test_distinct_installments_receive_distinct_application_keys():
    enrollment_id = uuid.uuid4()

    first = _academy_access_application_key(enrollment_id, "PAY-001")
    second = _academy_access_application_key(enrollment_id, "PAY-002")

    assert first != second


def test_reference_free_admin_call_keeps_enrollment_scoped_fallback():
    enrollment_id = uuid.uuid4()

    assert _academy_access_application_key(enrollment_id, None) == (
        f"academy:{enrollment_id}:paid-access"
    )
