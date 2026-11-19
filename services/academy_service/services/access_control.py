"""Row-level Academy read permissions shared by enrollment and progress routes.

Ownership is based on the Supabase auth id, NOT the local member primary key.
A coach must be assigned to the enrollment's cohort. Privileged service/admin
access continues to follow the existing central authorization helper.
"""

from fastapi import HTTPException, status

from libs.auth.dependencies import is_admin_or_service, require_coach_for_cohort
from libs.auth.models import AuthUser
from services.academy_service.models import Enrollment


async def ensure_enrollment_read_access(
    enrollment: Enrollment, current_user: AuthUser, db
) -> None:
    """Raise 403 unless caller owns this enrollment or manages its cohort."""
    if is_admin_or_service(current_user):
        return

    if enrollment.member_auth_id is not None and str(enrollment.member_auth_id) == str(
        current_user.user_id
    ):
        return

    if enrollment.cohort_id is not None and current_user.has_role("coach"):
        await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to view this enrollment",
    )
