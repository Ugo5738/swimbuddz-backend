"""Authorization regression tests for `download_certificate`.

This endpoint had a broken auth gate prior to today: it compared
`enrollment.member_id` (a DB-internal UUID) to `current_user.id`
(non-existent attribute — AuthUser has `user_id`) and looked at
`current_user.is_admin` (also non-existent). Both would have raised
AttributeError on EVERY call, surfacing as 500s.

These tests pin the fixed behaviour:
  - Owner (matching `member_auth_id`) can pass the auth check
  - Non-owner (mismatched auth_id) gets 403
  - Admin (roles include "admin") can pass the auth check on
    someone else's enrollment

We don't assert the PDF content — the 403 vs 404 ('not yet available')
status is enough to prove the gate is correct, and the rest of the
endpoint is unchanged.
"""

import uuid

import pytest


def _override_current_user(academy_app, auth_id, *, roles=("member",)):
    """Pin the AuthUser for one test."""
    from libs.auth.dependencies import (
        get_current_user,
        get_optional_user,
        require_admin,
    )
    from libs.auth.models import AuthUser

    user = AuthUser(
        user_id=auth_id,
        email=f"{auth_id}@test.com",
        role="authenticated",
        app_metadata={"roles": list(roles)},
        user_metadata={},
    )

    async def _get():
        return user

    academy_app.dependency_overrides[get_current_user] = _get
    academy_app.dependency_overrides[get_optional_user] = _get
    if "admin" in roles:
        academy_app.dependency_overrides[require_admin] = _get


@pytest.mark.asyncio
@pytest.mark.integration
async def test_download_certificate_blocks_non_owner_non_admin(
    academy_client, db_session
):
    """A member whose auth_id doesn't match `enrollment.member_auth_id` and
    who isn't admin must get a 403 — not a 500 from a broken attribute access.
    """
    from services.academy_service.app.main import app as academy_app
    from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory

    owner_auth_id = str(uuid.uuid4())
    intruder_auth_id = str(uuid.uuid4())

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add_all([program, cohort])
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id, member_auth_id=owner_auth_id
    )
    db_session.add(enrollment)
    await db_session.commit()

    _override_current_user(academy_app, intruder_auth_id, roles=("member",))

    response = await academy_client.get(
        f"/academy/enrollments/{enrollment.id}/certificate.pdf"
    )
    # Pre-fix: 500 (AttributeError). Post-fix: 403.
    assert response.status_code == 403, response.text
    assert "Not authorized" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_download_certificate_admin_can_view_any_enrollment(
    academy_client, db_session
):
    """An admin should pass the ownership check even on someone else's
    enrollment. We assert the auth check passes (status != 403) — the
    endpoint will then 404 because no cert was issued, which is fine.
    """
    from services.academy_service.app.main import app as academy_app
    from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory

    owner_auth_id = str(uuid.uuid4())
    admin_auth_id = str(uuid.uuid4())

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add_all([program, cohort])
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id, member_auth_id=owner_auth_id
    )
    db_session.add(enrollment)
    await db_session.commit()

    _override_current_user(academy_app, admin_auth_id, roles=("admin", "member"))

    response = await academy_client.get(
        f"/academy/enrollments/{enrollment.id}/certificate.pdf"
    )
    # Admin gets past the ownership check; will then 404 for missing cert.
    # Crucial assertion: NOT a 500, NOT a 403.
    assert response.status_code != 500, response.text
    assert response.status_code != 403, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_download_certificate_owner_passes_auth_check(academy_client, db_session):
    """Owner (matching member_auth_id) must NOT be blocked by the
    ownership check.
    """
    from services.academy_service.app.main import app as academy_app
    from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory

    owner_auth_id = str(uuid.uuid4())

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add_all([program, cohort])
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id, member_auth_id=owner_auth_id
    )
    db_session.add(enrollment)
    await db_session.commit()

    _override_current_user(academy_app, owner_auth_id, roles=("member",))

    response = await academy_client.get(
        f"/academy/enrollments/{enrollment.id}/certificate.pdf"
    )
    # Owner passes the auth check; 404 is fine (no cert issued in fixture).
    assert response.status_code != 500, response.text
    assert response.status_code != 403, response.text
