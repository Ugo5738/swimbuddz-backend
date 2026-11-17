"""Projection writes retain locks/semantics but avoid repeated full profiles."""

from datetime import timedelta

import pytest
from sqlalchemy import event

from libs.common.datetime_utils import utc_now
from services.members_service.models import MemberMembership, MemberProfile
from services.members_service.routers.admin.academy import (
    admin_project_academy_membership_by_auth,
)
from services.members_service.routers.admin.patch import (
    MembershipPatchRequest,
    admin_patch_membership_by_auth,
)
from services.members_service.schemas import MemberResponse, ProjectAcademyRequest
from tests.factories import MemberFactory


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "operation",
    [
        "pending",
        "pending_set",
        "pending_clear",
        "academy",
        "academy_without_membership",
    ],
)
async def test_projection_write_returns_full_profile_with_three_reads(
    db_session, operation
):
    member = MemberFactory.create()
    expiry = utc_now() + timedelta(days=90)
    db_session.add(member)
    await db_session.flush()
    db_session.add(MemberProfile(member_id=member.id, phone="test-phone"))
    if operation != "academy_without_membership":
        db_session.add(
            MemberMembership(
                member_id=member.id,
                community_paid_until=expiry,
                club_paid_until=expiry,
                academy_paid_until=expiry,
                pending_tier_payments={"club": "newer-reference"},
            )
        )
    await db_session.commit()
    auth_id = member.auth_id
    db_session.expunge_all()
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        if operation.startswith("pending"):
            result = await admin_patch_membership_by_auth(
                auth_id,
                MembershipPatchRequest(
                    pending_payment_reference="latest-reference"
                    if operation == "pending_set"
                    else None,
                    pending_payment_tier="club",
                    expected_pending_payment_reference="newer-reference"
                    if operation == "pending_clear"
                    else "older-reference",
                ),
                None,
                db_session,
            )
        else:
            result = await admin_project_academy_membership_by_auth(
                auth_id,
                ProjectAcademyRequest(
                    paid_until=None, source_reference="test-projection"
                ),
                None,
                db_session,
            )
        response = MemberResponse.model_validate(result)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(queries) == 3
    assert "FOR UPDATE" in queries[0] and "LEFT OUTER JOIN" not in queries[0]
    assert "LEFT OUTER JOIN" in queries[-1] and "FOR UPDATE" not in queries[-1]
    assert response.profile.phone == "test-phone"
    if operation == "academy_without_membership":
        assert response.membership is None
    else:
        assert response.membership.community_paid_until == expiry
        assert response.membership.club_paid_until == expiry
        if operation.startswith("pending"):
            if operation == "pending_clear":
                assert "club" not in response.membership.pending_tier_payments
            else:
                assert response.membership.pending_tier_payments["club"] == (
                    "latest-reference"
                    if operation == "pending_set"
                    else "newer-reference"
                )
            assert response.membership.academy_paid_until == expiry
        else:
            assert response.membership.academy_paid_until is None
