from fastapi import APIRouter
from services.academy_service.routers._shared import *  # noqa: F401, F403

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- Admin Tasks ---


@router.post("/admin/tasks/transition-cohort-statuses")
async def trigger_cohort_status_transitions(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Manually trigger cohort status transitions (OPEN→ACTIVE, ACTIVE→COMPLETED).
    Useful for testing or manual corrections.
    """

    await transition_cohort_statuses()
    return {"message": "Cohort status transitions triggered successfully"}


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_academy_records(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete academy enrollments and progress records for a member (Admin only).
    """
    enrollment_ids = (
        (
            await db.execute(
                select(Enrollment.id).where(Enrollment.member_id == member_id)
            )
        )
        .scalars()
        .all()
    )

    deleted_progress = 0
    if enrollment_ids:
        progress_result = await db.execute(
            delete(StudentProgress).where(
                StudentProgress.enrollment_id.in_(enrollment_ids)
            )
        )
        deleted_progress = progress_result.rowcount or 0

    enrollment_result = await db.execute(
        delete(Enrollment).where(Enrollment.member_id == member_id)
    )

    await db.commit()
    return {
        "deleted_enrollments": enrollment_result.rowcount or 0,
        "deleted_progress": deleted_progress,
    }
