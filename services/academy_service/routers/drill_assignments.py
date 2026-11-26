"""Coach/admin TI drill prescriptions and learner practice tracking.

Prescribing or mastering drills supports coaching progression without ever
automatically awarding practical competency credentials (ADR-010, ADR-013).
"""

import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    is_admin_or_service,
    require_coach_for_cohort,
)
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id, get_member_by_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyDrill,
    AcademyDrillVersion,
    AcademyMilestoneDefinition,
    AcademyStudentDrillAssignment,
    Enrollment,
    EnrollmentStatus,
)

router = APIRouter(prefix="/competencies", tags=["academy-drill-prescriptions"])

DrillAssignmentStatus = Literal["assigned", "practiced", "mastered", "archived"]

# Deterministic, curated mapping from Stroke Lab TI academy_ref keys (ADR-013)
# to canonical Academy V2 drill codes (ADR-010). Free-text AI drill creation is
# intentionally forbidden; every prescribed drill resolves to a published catalog version.
STROKELAB_ACADEMY_REF_TO_DRILL_CODE: dict[str, str] = {
    "academy:ti:superman-glide": "DRL-F4-SUPERMAN-GLIDE",
    "academy:ti:one-goggle-breathing": "DRL-S1-ONE-GOGGLE-BREATH",
    "academy:ti:zen-skate": "DRL-S1-TI-SKATE",
    "academy:ti:shark-fin-recovery": "DRL-S1-SHARK-FIN-RECOVERY",
    "academy:ti:patient-lead-hand": "DRL-S1-6-KICK-SWITCH",
    "academy:ti:mail-slot-entry": "DRL-S1-MAIL-SLOT-ENTRY",
}


class StrokeLabDrillInput(BaseModel):
    academy_ref: str = Field(min_length=5, max_length=120)
    strokelab_job_id: str | None = Field(default=None, max_length=120)
    focal_cue_note: str | None = Field(default=None, max_length=1000)
    sets: int = Field(default=3, ge=1, le=20)
    distance_m: Decimal | None = Field(default=Decimal("25"), gt=0, le=500)


class AssignDrillInput(BaseModel):
    drill_version_id: uuid.UUID
    enrollment_id: uuid.UUID | None = None
    member_id: uuid.UUID | None = None
    member_auth_id: str | None = Field(default=None, max_length=160)
    milestone_definition_id: uuid.UUID | None = None
    focal_cue_note: str | None = Field(default=None, max_length=1000)
    sets: int = Field(default=3, ge=1, le=50)
    distance_m: Decimal | None = Field(default=None, gt=0, le=5000)


class UpdateDrillStatusInput(BaseModel):
    status: DrillAssignmentStatus
    focal_cue_note: str | None = Field(default=None, max_length=1000)


def _is_human_admin(user: AuthUser) -> bool:
    return user.role != "service_role" and is_admin_or_service(user)


def _require_coach_or_admin(user: AuthUser) -> str:
    if user.role == "service_role":
        raise HTTPException(
            status_code=403, detail="Human coach or administrator required"
        )
    if _is_human_admin(user):
        return "admin"
    if user.has_role("coach"):
        return "coach"
    raise HTTPException(
        status_code=403, detail="Coach or administrator privileges required"
    )


async def serialize_drill_assignments(
    db: AsyncSession, rows: list[AcademyStudentDrillAssignment]
) -> list[dict]:
    """Hydrate drill assignments with versioned drill cues & milestone labels (zero N+1)."""
    if not rows:
        return []
    dv_ids = {r.drill_version_id for r in rows}
    drill_versions = (
        (
            await db.execute(
                select(AcademyDrillVersion).where(AcademyDrillVersion.id.in_(dv_ids))
            )
        )
        .scalars()
        .all()
    )
    dv_by_id = {v.id: v for v in drill_versions}
    drills = (
        (
            await db.execute(
                select(AcademyDrill).where(
                    AcademyDrill.id.in_({v.drill_id for v in drill_versions})
                )
            )
        )
        .scalars()
        .all()
        if drill_versions
        else []
    )
    drill_by_id = {d.id: d for d in drills}

    ms_ids = {r.milestone_definition_id for r in rows if r.milestone_definition_id}
    milestones = (
        (
            await db.execute(
                select(AcademyMilestoneDefinition).where(
                    AcademyMilestoneDefinition.id.in_(ms_ids)
                )
            )
        )
        .scalars()
        .all()
        if ms_ids
        else []
    )
    ms_by_id = {m.id: m for m in milestones}

    result = []
    for row in rows:
        dv = dv_by_id.get(row.drill_version_id)
        drill = drill_by_id.get(dv.drill_id) if dv else None
        ms = (
            ms_by_id.get(row.milestone_definition_id)
            if row.milestone_definition_id
            else None
        )
        result.append(
            {
                "id": str(row.id),
                "member_id": str(row.member_id),
                "member_auth_id": row.member_auth_id,
                "enrollment_id": str(row.enrollment_id) if row.enrollment_id else None,
                "drill_version_id": str(row.drill_version_id),
                "drill_id": str(dv.drill_id) if dv else None,
                "drill_code": drill.code if drill else None,
                "drill_title": drill.title if drill else "Practice drill",
                "drill_version": dv.version if dv else None,
                "primary_focal_cue": dv.primary_focal_cue if dv else None,
                "steps_json": dv.steps_json if dv else [],
                "requires_supervision": bool(dv.requires_supervision) if dv else True,
                "milestone_definition_id": (
                    str(row.milestone_definition_id)
                    if row.milestone_definition_id
                    else None
                ),
                "milestone_code": ms.code if ms else None,
                "milestone_title": ms.title if ms else None,
                "assigned_by_auth_id": row.assigned_by_auth_id,
                "assigned_by_role": row.assigned_by_role,
                "focal_cue_note": row.focal_cue_note,
                "sets": row.sets,
                "distance_m": (
                    float(row.distance_m) if row.distance_m is not None else None
                ),
                "status": row.status,
                "practiced_at": row.practiced_at,
                "completed_at": row.completed_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    return result


async def prescribe_strokelab_drill_for_member(
    db: AsyncSession,
    *,
    member_auth_id: str,
    member_id: uuid.UUID | None,
    academy_ref: str,
    strokelab_job_id: str | None = None,
    focal_cue_note: str | None = None,
    sets: int = 3,
    distance_m: Decimal | None = Decimal("25"),
) -> dict:
    """Resolve a vetted Stroke Lab academy_ref to a published V2 drill assignment.

    Strictly idempotent for active assignments and NEVER creates a competency
    verification row (ADR-010 / ADR-013).
    """
    drill_code = STROKELAB_ACADEMY_REF_TO_DRILL_CODE.get(academy_ref.strip())
    if drill_code is None:
        raise HTTPException(
            status_code=422,
            detail="Unsupported Stroke Lab drill reference",
        )

    drill = (
        await db.execute(
            select(AcademyDrill).where(
                AcademyDrill.code == drill_code,
                AcademyDrill.is_retired.is_(False),
            )
        )
    ).scalar_one_or_none()
    if drill is None:
        raise HTTPException(
            status_code=404,
            detail=f"Academy V2 drill {drill_code} is not yet published in the catalog",
        )

    drill_version = (
        await db.execute(
            select(AcademyDrillVersion)
            .where(
                AcademyDrillVersion.drill_id == drill.id,
                AcademyDrillVersion.state == "published",
                AcademyDrillVersion.published_at.is_not(None),
            )
            .order_by(AcademyDrillVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if drill_version is None:
        raise HTTPException(
            status_code=404,
            detail=f"Academy V2 drill {drill_code} has no published version",
        )

    # Link to the swimmer's active enrolled Academy cohort when present so
    # their assigned coach also sees the recommended TI drill in their deck.
    active_enrollment = (
        await db.execute(
            select(Enrollment)
            .where(
                Enrollment.member_auth_id == member_auth_id,
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            .order_by(Enrollment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    resolved_member_id = member_id or (
        active_enrollment.member_id if active_enrollment else None
    )
    if resolved_member_id is None:
        member = await get_member_by_auth_id(member_auth_id, calling_service="academy")
        if not member or not member.get("id"):
            raise HTTPException(
                status_code=404, detail="Member profile not found for drill assignment"
            )
        resolved_member_id = uuid.UUID(str(member["id"]))

    existing = (
        await db.execute(
            select(AcademyStudentDrillAssignment)
            .where(
                AcademyStudentDrillAssignment.member_auth_id == member_auth_id,
                AcademyStudentDrillAssignment.drill_version_id == drill_version.id,
                AcademyStudentDrillAssignment.assigned_by_role == "strokelab_ai",
                AcademyStudentDrillAssignment.status.in_(["assigned", "practiced"]),
            )
            .order_by(AcademyStudentDrillAssignment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        serialized = (await serialize_drill_assignments(db, [existing]))[0]
        serialized["already_assigned"] = True
        return serialized

    source_tag = (
        f"strokelab:{strokelab_job_id.strip()}"
        if strokelab_job_id and strokelab_job_id.strip()
        else "strokelab:advisory"
    )[:160]
    row = AcademyStudentDrillAssignment(
        member_id=resolved_member_id,
        member_auth_id=member_auth_id,
        enrollment_id=active_enrollment.id if active_enrollment else None,
        drill_version_id=drill_version.id,
        milestone_definition_id=None,
        assigned_by_auth_id=source_tag,
        assigned_by_role="strokelab_ai",
        focal_cue_note=focal_cue_note.strip() if focal_cue_note else None,
        sets=sets,
        distance_m=distance_m,
        status="assigned",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    serialized = (await serialize_drill_assignments(db, [row]))[0]
    serialized["already_assigned"] = False
    return serialized


@router.post("/me/drills/from-strokelab", status_code=201)
async def save_strokelab_drill_to_my_plan(
    payload: StrokeLabDrillInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member saves a vetted Stroke Lab TI drill recommendation into their Academy drill list."""
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Member identity required")
    return await prescribe_strokelab_drill_for_member(
        db,
        member_auth_id=str(current_user.user_id),
        member_id=None,
        academy_ref=payload.academy_ref,
        strokelab_job_id=payload.strokelab_job_id,
        focal_cue_note=payload.focal_cue_note,
        sets=payload.sets,
        distance_m=payload.distance_m,
    )


@router.get("/drills/published")
async def list_published_drills_for_coaching(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List latest published version of each active TI drill for coaches & admins."""
    _require_coach_or_admin(current_user)
    latest_versions = (
        (
            await db.execute(
                select(AcademyDrillVersion)
                .where(AcademyDrillVersion.state == "published")
                .distinct(AcademyDrillVersion.drill_id)
                .order_by(
                    AcademyDrillVersion.drill_id,
                    AcademyDrillVersion.version.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not latest_versions:
        return []
    drills = (
        (
            await db.execute(
                select(AcademyDrill)
                .where(
                    AcademyDrill.id.in_({v.drill_id for v in latest_versions}),
                    AcademyDrill.is_retired.is_(False),
                )
                .order_by(AcademyDrill.code)
            )
        )
        .scalars()
        .all()
    )
    ver_by_drill = {v.drill_id: v for v in latest_versions}
    return [
        {
            "drill_id": str(drill.id),
            "drill_version_id": str(ver_by_drill[drill.id].id),
            "code": drill.code,
            "title": drill.title,
            "description": drill.description,
            "version": ver_by_drill[drill.id].version,
            "primary_focal_cue": ver_by_drill[drill.id].primary_focal_cue,
            "steps_json": ver_by_drill[drill.id].steps_json,
            "requires_supervision": bool(ver_by_drill[drill.id].requires_supervision),
        }
        for drill in drills
        if drill.id in ver_by_drill
    ]


@router.get("/me/drills")
async def list_my_assigned_drills(
    include_archived: bool = Query(default=False),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List TI drills prescribed to the authenticated swimmer."""
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Member identity required")
    stmt = (
        select(AcademyStudentDrillAssignment)
        .where(
            AcademyStudentDrillAssignment.member_auth_id == str(current_user.user_id)
        )
        .order_by(AcademyStudentDrillAssignment.created_at.desc())
        .limit(100)
    )
    if not include_archived:
        stmt = stmt.where(AcademyStudentDrillAssignment.status != "archived")
    rows = (await db.execute(stmt)).scalars().all()
    return await serialize_drill_assignments(db, rows)


@router.post("/me/drills/{assignment_id}/practiced")
async def mark_my_drill_practiced(
    assignment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Learner logs that they practiced an assigned drill (cannot self-mark mastered)."""
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Member identity required")
    row = (
        await db.execute(
            select(AcademyStudentDrillAssignment)
            .where(AcademyStudentDrillAssignment.id == assignment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.member_auth_id != str(current_user.user_id):
        raise HTTPException(status_code=404, detail="Drill assignment not found")
    if row.status in ("mastered", "archived"):
        raise HTTPException(
            status_code=409, detail="Completed or archived drills cannot be changed"
        )
    if row.status == "assigned":
        row.status = "practiced"
        row.practiced_at = utc_now()
        await db.commit()
        await db.refresh(row)
    items = await serialize_drill_assignments(db, [row])
    return items[0]


@router.get("/enrollments/{enrollment_id}/drills")
async def list_enrollment_drills(
    enrollment_id: uuid.UUID,
    include_archived: bool = Query(default=False),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List prescribed TI drills for a cohort enrollment (owner, assigned coach, or admin)."""
    enrollment = await db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    is_owner = current_user.role != "service_role" and enrollment.member_auth_id == str(
        current_user.user_id
    )
    if not is_owner:
        if enrollment.cohort_id:
            await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
        elif not _is_human_admin(current_user):
            raise HTTPException(status_code=403, detail="Not authorized")

    filters = [AcademyStudentDrillAssignment.enrollment_id == enrollment_id]
    if enrollment.member_auth_id:
        filters.append(
            (AcademyStudentDrillAssignment.member_auth_id == enrollment.member_auth_id)
            & (AcademyStudentDrillAssignment.enrollment_id.is_(None))
        )
    stmt = (
        select(AcademyStudentDrillAssignment)
        .where(or_(*filters))
        .order_by(AcademyStudentDrillAssignment.created_at.desc())
        .limit(100)
    )
    if not include_archived:
        stmt = stmt.where(AcademyStudentDrillAssignment.status != "archived")
    rows = (await db.execute(stmt)).scalars().all()
    return await serialize_drill_assignments(db, rows)


@router.post("/coach/drills/assign", status_code=201)
async def assign_student_drill(
    payload: AssignDrillInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Coach or admin prescribes a published TI drill version to a swimmer."""
    role = _require_coach_or_admin(current_user)

    drill_version = await db.get(AcademyDrillVersion, payload.drill_version_id)
    if (
        drill_version is None
        or drill_version.state != "published"
        or drill_version.published_at is None
    ):
        raise HTTPException(
            status_code=409, detail="Published drill version is required"
        )

    if payload.milestone_definition_id is not None:
        ms = await db.get(AcademyMilestoneDefinition, payload.milestone_definition_id)
        if ms is None:
            raise HTTPException(
                status_code=404, detail="Milestone definition not found"
            )

    enrollment: Enrollment | None = None
    if payload.enrollment_id is not None:
        enrollment = await db.get(Enrollment, payload.enrollment_id)
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Enrollment not found")
        if enrollment.cohort_id is None or not enrollment.member_auth_id:
            raise HTTPException(
                status_code=409, detail="Verifiable enrollment with cohort required"
            )
        await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
        member_id = enrollment.member_id
        member_auth_id = str(enrollment.member_auth_id)
    else:
        if payload.member_id is None:
            raise HTTPException(
                status_code=422,
                detail="Either enrollment_id or member_id must be provided",
            )
        member_id = payload.member_id
        member_auth_id = (payload.member_auth_id or "").strip()
        if not member_auth_id:
            member = await get_member_by_id(str(member_id), calling_service="academy")
            if not member or not member.get("auth_id"):
                raise HTTPException(
                    status_code=404, detail="Member profile or auth identity not found"
                )
            member_auth_id = str(member["auth_id"])

    row = AcademyStudentDrillAssignment(
        member_id=member_id,
        member_auth_id=member_auth_id,
        enrollment_id=enrollment.id if enrollment else None,
        drill_version_id=drill_version.id,
        milestone_definition_id=payload.milestone_definition_id,
        assigned_by_auth_id=str(current_user.user_id),
        assigned_by_role=role,
        focal_cue_note=(
            payload.focal_cue_note.strip() if payload.focal_cue_note else None
        ),
        sets=payload.sets,
        distance_m=payload.distance_m,
        status="assigned",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    items = await serialize_drill_assignments(db, [row])
    return items[0]


@router.patch("/coach/drills/{assignment_id}/status")
async def update_student_drill_status(
    assignment_id: uuid.UUID,
    payload: UpdateDrillStatusInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Coach or admin updates a prescribed drill's status (e.g., mastered or archived)."""
    _require_coach_or_admin(current_user)
    row = (
        await db.execute(
            select(AcademyStudentDrillAssignment)
            .where(AcademyStudentDrillAssignment.id == assignment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Drill assignment not found")

    if row.enrollment_id is not None:
        enrollment = await db.get(Enrollment, row.enrollment_id)
        if enrollment and enrollment.cohort_id:
            await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
    elif not _is_human_admin(current_user) and row.assigned_by_auth_id != str(
        current_user.user_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the assigning coach or an administrator may update this drill",
        )

    now = utc_now()
    row.status = payload.status
    if payload.focal_cue_note is not None:
        row.focal_cue_note = payload.focal_cue_note.strip() or None
    if payload.status == "practiced" and row.practiced_at is None:
        row.practiced_at = now
    elif payload.status == "mastered":
        row.practiced_at = row.practiced_at or now
        row.completed_at = now
    elif payload.status == "archived":
        row.completed_at = row.completed_at or now
    await db.commit()
    await db.refresh(row)
    items = await serialize_drill_assignments(db, [row])
    return items[0]
