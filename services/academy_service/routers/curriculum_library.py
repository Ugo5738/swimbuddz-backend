"""Admin-only authoring for independent, versioned Academy curriculum content.

Additive to legacy programme-bound milestones, lessons and quizzes. Publication
freezes a version and validates publish-time assessment contracts so broken
immutable releases cannot enter cohort snapshots.
"""

import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyDrill,
    AcademyDrillSkill,
    AcademyDrillVersion,
    AcademyKnowledgeQuiz,
    AcademyKnowledgeQuizVersion,
    AcademyLessonBlueprint,
    AcademyLessonDrill,
    AcademyLessonVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneSkill,
    AcademyMilestoneVersion,
    AcademyModule,
    AcademyModuleLesson,
    AcademyModuleMilestone,
    AcademyModuleQuiz,
    AcademyModuleVersion,
    Skill,
)

router = APIRouter(
    prefix="/admin/curriculum-library", tags=["academy-curriculum-library"]
)

CatalogKind = Literal["milestone", "drill", "lesson", "quiz", "module"]

CATALOG_MODELS = {
    "milestone": (AcademyMilestoneDefinition, AcademyMilestoneVersion, "definition_id"),
    "drill": (AcademyDrill, AcademyDrillVersion, "drill_id"),
    "lesson": (AcademyLessonBlueprint, AcademyLessonVersion, "lesson_id"),
    "quiz": (AcademyKnowledgeQuiz, AcademyKnowledgeQuizVersion, "quiz_id"),
    "module": (AcademyModule, AcademyModuleVersion, "module_id"),
}

VERSION_FIELDS = {
    "milestone": {
        "rubric_json",
        "evidence_policy",
        "assessment_context_json",
        "safety_gate",
        "demo_media_id",
    },
    "drill": {
        "steps_json",
        "safety_json",
        "primary_focal_cue",
        "requires_supervision",
        "demo_media_id",
    },
    "lesson": {"duration_minutes", "blocks_json", "safety_json"},
    "quiz": {"questions_json", "passing_percent"},
    "module": {"outcomes_json", "completion_policy_json"},
}

REQUIRED_VERSION_FIELDS = {
    "milestone": {"rubric_json"},
    "drill": {"steps_json", "primary_focal_cue"},
    "lesson": {"duration_minutes", "blocks_json"},
    "quiz": {"questions_json"},
    "module": {"outcomes_json"},
}

OPTIONAL_VERSION_DEFAULTS: dict[str, dict[str, Any]] = {
    "milestone": {
        "evidence_policy": "video",
        "assessment_context_json": {},
        "safety_gate": False,
        "demo_media_id": None,
    },
    "drill": {
        "safety_json": {},
        "requires_supervision": True,
        "demo_media_id": None,
    },
    "lesson": {
        "safety_json": {},
    },
    "quiz": {
        "passing_percent": 90,
    },
    "module": {
        "completion_policy_json": {},
    },
}


class CatalogDefinitionCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    title: str = Field(min_length=3, max_length=200)
    description: str | None = None


class CatalogVersionCreate(BaseModel):
    content: dict[str, Any]


class LessonDrillInput(BaseModel):
    drill_version_id: uuid.UUID
    order_index: int = Field(default=0, ge=0)
    sets: int = Field(default=1, ge=1)
    distance_m: Decimal | None = Field(default=None, gt=0)
    focal_cue_override: str | None = Field(default=None, max_length=500)


class ModuleLessonInput(BaseModel):
    lesson_version_id: uuid.UUID
    order_index: int = Field(default=0, ge=0)


class ModuleQuizInput(BaseModel):
    quiz_version_id: uuid.UUID
    required_for_completion: bool = True


class SkillLinkInput(BaseModel):
    skill_id: uuid.UUID


def _definition_data(item) -> dict:
    return {
        "id": str(item.id),
        "code": item.code,
        "title": item.title,
        "description": item.description,
        "is_retired": item.is_retired,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _version_data(kind: str, item) -> dict:
    return {
        "id": str(item.id),
        "version": item.version,
        "state": item.state,
        "published_at": item.published_at,
        "created_at": item.created_at,
        "content": {field: getattr(item, field) for field in VERSION_FIELDS[kind]},
    }


def _validate_validity_days(assessment_context: dict[str, Any]) -> int | None:
    valid_days = assessment_context.get("validity_days")
    if valid_days is None:
        return None
    if (
        isinstance(valid_days, bool)
        or not isinstance(valid_days, int)
        or valid_days <= 0
    ):
        raise HTTPException(
            status_code=422,
            detail="Assessment validity_days must be a positive integer",
        )
    return valid_days


def _validate_milestone_for_publish(version: AcademyMilestoneVersion) -> None:
    """Enforce the same rubric & validity contract required at verification time."""
    rubric = version.rubric_json if isinstance(version.rubric_json, dict) else {}
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise HTTPException(
            status_code=422,
            detail="Published milestones require non-empty rubric criteria",
        )
    codes = [
        item.strip()
        if isinstance(item, str)
        else (
            item.get("code").strip()
            if isinstance(item, dict) and isinstance(item.get("code"), str)
            else None
        )
        for item in criteria
    ]
    if any(not code for code in codes) or len(set(codes)) != len(codes):
        raise HTTPException(
            status_code=422,
            detail="Milestone rubric criteria must be unique non-empty codes",
        )
    context = (
        version.assessment_context_json
        if isinstance(version.assessment_context_json, dict)
        else {}
    )
    valid_days = _validate_validity_days(context)
    if version.safety_gate and valid_days is None:
        raise HTTPException(
            status_code=422,
            detail="Safety-gate milestones require a positive validity_days interval",
        )


def _content(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    allowed = VERSION_FIELDS[kind]
    unknown = set(data) - allowed
    missing = REQUIRED_VERSION_FIELDS[kind] - set(data)
    if unknown or missing:
        raise HTTPException(
            status_code=422,
            detail={
                "unknown_fields": sorted(unknown),
                "missing_fields": sorted(missing),
            },
        )

    result = dict(data)
    if kind == "milestone":
        if not isinstance(result["rubric_json"], dict) or not result["rubric_json"]:
            raise HTTPException(
                status_code=422, detail="A milestone needs a nonempty rubric"
            )
        if result.get("evidence_policy", "video") not in {
            "video",
            "time_trial",
            "observation",
        }:
            raise HTTPException(status_code=422, detail="Invalid evidence policy")
        if "assessment_context_json" in result:
            if not isinstance(result["assessment_context_json"], dict):
                raise HTTPException(
                    status_code=422, detail="Assessment context must be an object"
                )
            _validate_validity_days(result["assessment_context_json"])
    elif kind == "drill":
        if not isinstance(result["steps_json"], list) or not result["steps_json"]:
            raise HTTPException(
                status_code=422, detail="Drills need at least one instruction"
            )
        if (
            not isinstance(result["primary_focal_cue"], str)
            or not result["primary_focal_cue"].strip()
        ):
            raise HTTPException(status_code=422, detail="Drills need a focal cue")
    elif kind == "lesson":
        if (
            isinstance(result["duration_minutes"], bool)
            or not isinstance(result["duration_minutes"], int)
        ) or result["duration_minutes"] <= 0:
            raise HTTPException(
                status_code=422, detail="Lesson duration must be positive"
            )
        if not isinstance(result["blocks_json"], list) or not result["blocks_json"]:
            raise HTTPException(status_code=422, detail="Lessons need teaching blocks")
    elif kind == "quiz":
        if (
            not isinstance(result["questions_json"], list)
            or not result["questions_json"]
        ):
            raise HTTPException(status_code=422, detail="Quiz needs questions")
        percent = result.get("passing_percent", 90)
        if (
            isinstance(percent, bool) or not isinstance(percent, int)
        ) or not 1 <= percent <= 100:
            raise HTTPException(status_code=422, detail="Invalid passing percentage")
    elif kind == "module":
        if not isinstance(result["outcomes_json"], list) or not result["outcomes_json"]:
            raise HTTPException(
                status_code=422, detail="Module needs learning outcomes"
            )

    for field in ("safety_json", "completion_policy_json"):
        if field in result and not isinstance(result[field], dict):
            raise HTTPException(status_code=422, detail=f"{field} must be an object")
    for field in ("safety_gate", "requires_supervision"):
        if field in result and not isinstance(result[field], bool):
            raise HTTPException(
                status_code=422, detail=f"{field} must be true or false"
            )
    if result.get("demo_media_id"):
        try:
            result["demo_media_id"] = uuid.UUID(str(result["demo_media_id"]))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(
                status_code=422, detail="Invalid demo media ID"
            ) from exc
    return result


def _assert_published(entity, label: str) -> None:
    if entity is None or entity.state != "published" or entity.published_at is None:
        raise HTTPException(status_code=409, detail=f"{label} must be published")


# ---------------------------------------------------------------------------
# Composition & skill-link endpoints (registered ahead of /{kind})
# ---------------------------------------------------------------------------


@router.get("/skills")
async def list_catalog_skills(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        (
            await db.execute(
                select(Skill).order_by(Skill.category, Skill.name).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "category": row.category,
            "description": row.description,
        }
        for row in rows
    ]


@router.get("/lessons/{lesson_version_id}/drills")
async def list_lesson_drills(
    lesson_version_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    lesson = await db.get(AcademyLessonVersion, lesson_version_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson version not found")
    links = (
        (
            await db.execute(
                select(AcademyLessonDrill)
                .where(AcademyLessonDrill.lesson_version_id == lesson_version_id)
                .order_by(AcademyLessonDrill.order_index, AcademyLessonDrill.id)
            )
        )
        .scalars()
        .all()
    )
    if not links:
        return []
    drill_versions = (
        (
            await db.execute(
                select(AcademyDrillVersion).where(
                    AcademyDrillVersion.id.in_(
                        {link.drill_version_id for link in links}
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    dv_by_id = {dv.id: dv for dv in drill_versions}
    drills = (
        (
            await db.execute(
                select(AcademyDrill).where(
                    AcademyDrill.id.in_({dv.drill_id for dv in drill_versions})
                )
            )
        )
        .scalars()
        .all()
    )
    drill_by_id = {d.id: d for d in drills}
    results = []
    for link in links:
        dv = dv_by_id.get(link.drill_version_id)
        drill = drill_by_id.get(dv.drill_id) if dv else None
        results.append(
            {
                "id": str(link.id),
                "lesson_version_id": str(link.lesson_version_id),
                "drill_version_id": str(link.drill_version_id),
                "drill_id": str(dv.drill_id) if dv else None,
                "drill_code": drill.code if drill else None,
                "drill_title": drill.title if drill else None,
                "drill_version": dv.version if dv else None,
                "primary_focal_cue": dv.primary_focal_cue if dv else None,
                "order_index": link.order_index,
                "sets": link.sets,
                "distance_m": float(link.distance_m)
                if link.distance_m is not None
                else None,
                "focal_cue_override": link.focal_cue_override,
            }
        )
    return results


@router.post("/lessons/{lesson_version_id}/drills", status_code=201)
async def attach_lesson_drill(
    lesson_version_id: uuid.UUID,
    payload: LessonDrillInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    lesson = (
        await db.execute(
            select(AcademyLessonVersion)
            .where(AcademyLessonVersion.id == lesson_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson version not found")
    if lesson.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published lesson drills cannot be changed"
        )
    drill_version = await db.get(AcademyDrillVersion, payload.drill_version_id)
    _assert_published(drill_version, "Drill version")

    row = AcademyLessonDrill(
        lesson_version_id=lesson_version_id,
        drill_version_id=payload.drill_version_id,
        order_index=payload.order_index,
        sets=payload.sets,
        distance_m=payload.distance_m,
        focal_cue_override=(
            payload.focal_cue_override.strip() if payload.focal_cue_override else None
        ),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Drill or order index is already assigned to this lesson version",
        ) from exc
    return {"id": str(row.id)}


@router.delete("/lessons/{lesson_version_id}/drills/{link_id}", status_code=204)
async def remove_lesson_drill(
    lesson_version_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    lesson = (
        await db.execute(
            select(AcademyLessonVersion)
            .where(AcademyLessonVersion.id == lesson_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson version not found")
    if lesson.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published lesson drills cannot be changed"
        )
    link = (
        await db.execute(
            select(AcademyLessonDrill).where(
                AcademyLessonDrill.id == link_id,
                AcademyLessonDrill.lesson_version_id == lesson_version_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Lesson drill link not found")
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)


@router.get("/modules/{module_version_id}/composition")
async def get_module_composition(
    module_version_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    module = await db.get(AcademyModuleVersion, module_version_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")

    lesson_links = (
        (
            await db.execute(
                select(AcademyModuleLesson)
                .where(AcademyModuleLesson.module_version_id == module_version_id)
                .order_by(AcademyModuleLesson.order_index, AcademyModuleLesson.id)
            )
        )
        .scalars()
        .all()
    )
    milestone_links = (
        (
            await db.execute(
                select(AcademyModuleMilestone)
                .where(AcademyModuleMilestone.module_version_id == module_version_id)
                .order_by(AcademyModuleMilestone.order_index, AcademyModuleMilestone.id)
            )
        )
        .scalars()
        .all()
    )
    quiz_links = (
        (
            await db.execute(
                select(AcademyModuleQuiz)
                .where(AcademyModuleQuiz.module_version_id == module_version_id)
                .order_by(AcademyModuleQuiz.id)
            )
        )
        .scalars()
        .all()
    )

    lesson_versions = (
        (
            await db.execute(
                select(AcademyLessonVersion).where(
                    AcademyLessonVersion.id.in_(
                        {link.lesson_version_id for link in lesson_links}
                    )
                )
            )
        )
        .scalars()
        .all()
        if lesson_links
        else []
    )
    lv_by_id = {item.id: item for item in lesson_versions}
    lessons_map = (
        {
            item.id: item
            for item in (
                await db.execute(
                    select(AcademyLessonBlueprint).where(
                        AcademyLessonBlueprint.id.in_(
                            {lv.lesson_id for lv in lesson_versions}
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        if lesson_versions
        else {}
    )

    milestone_versions = (
        (
            await db.execute(
                select(AcademyMilestoneVersion).where(
                    AcademyMilestoneVersion.id.in_(
                        {link.milestone_version_id for link in milestone_links}
                    )
                )
            )
        )
        .scalars()
        .all()
        if milestone_links
        else []
    )
    mv_by_id = {item.id: item for item in milestone_versions}
    milestones_map = (
        {
            item.id: item
            for item in (
                await db.execute(
                    select(AcademyMilestoneDefinition).where(
                        AcademyMilestoneDefinition.id.in_(
                            {mv.definition_id for mv in milestone_versions}
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        if milestone_versions
        else {}
    )

    quiz_versions = (
        (
            await db.execute(
                select(AcademyKnowledgeQuizVersion).where(
                    AcademyKnowledgeQuizVersion.id.in_(
                        {link.quiz_version_id for link in quiz_links}
                    )
                )
            )
        )
        .scalars()
        .all()
        if quiz_links
        else []
    )
    qv_by_id = {item.id: item for item in quiz_versions}
    quizzes_map = (
        {
            item.id: item
            for item in (
                await db.execute(
                    select(AcademyKnowledgeQuiz).where(
                        AcademyKnowledgeQuiz.id.in_(
                            {qv.quiz_id for qv in quiz_versions}
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        if quiz_versions
        else {}
    )

    return {
        "module_version_id": str(module.id),
        "state": module.state,
        "lessons": [
            {
                "id": str(link.id),
                "lesson_version_id": str(link.lesson_version_id),
                "code": (
                    lessons_map[lv_by_id[link.lesson_version_id].lesson_id].code
                    if link.lesson_version_id in lv_by_id
                    and lv_by_id[link.lesson_version_id].lesson_id in lessons_map
                    else None
                ),
                "title": (
                    lessons_map[lv_by_id[link.lesson_version_id].lesson_id].title
                    if link.lesson_version_id in lv_by_id
                    and lv_by_id[link.lesson_version_id].lesson_id in lessons_map
                    else None
                ),
                "version": (
                    lv_by_id[link.lesson_version_id].version
                    if link.lesson_version_id in lv_by_id
                    else None
                ),
                "order_index": link.order_index,
            }
            for link in lesson_links
        ],
        "milestones": [
            {
                "id": str(link.id),
                "milestone_version_id": str(link.milestone_version_id),
                "code": (
                    milestones_map[
                        mv_by_id[link.milestone_version_id].definition_id
                    ].code
                    if link.milestone_version_id in mv_by_id
                    and mv_by_id[link.milestone_version_id].definition_id
                    in milestones_map
                    else None
                ),
                "title": (
                    milestones_map[
                        mv_by_id[link.milestone_version_id].definition_id
                    ].title
                    if link.milestone_version_id in mv_by_id
                    and mv_by_id[link.milestone_version_id].definition_id
                    in milestones_map
                    else None
                ),
                "version": (
                    mv_by_id[link.milestone_version_id].version
                    if link.milestone_version_id in mv_by_id
                    else None
                ),
                "safety_gate": (
                    bool(mv_by_id[link.milestone_version_id].safety_gate)
                    if link.milestone_version_id in mv_by_id
                    else False
                ),
                "required_for_completion": link.required_for_completion,
                "order_index": link.order_index,
            }
            for link in milestone_links
        ],
        "quizzes": [
            {
                "id": str(link.id),
                "quiz_version_id": str(link.quiz_version_id),
                "code": (
                    quizzes_map[qv_by_id[link.quiz_version_id].quiz_id].code
                    if link.quiz_version_id in qv_by_id
                    and qv_by_id[link.quiz_version_id].quiz_id in quizzes_map
                    else None
                ),
                "title": (
                    quizzes_map[qv_by_id[link.quiz_version_id].quiz_id].title
                    if link.quiz_version_id in qv_by_id
                    and qv_by_id[link.quiz_version_id].quiz_id in quizzes_map
                    else None
                ),
                "version": (
                    qv_by_id[link.quiz_version_id].version
                    if link.quiz_version_id in qv_by_id
                    else None
                ),
                "required_for_completion": link.required_for_completion,
            }
            for link in quiz_links
        ],
    }


@router.post("/modules/{module_version_id}/lessons", status_code=201)
async def attach_module_lesson(
    module_version_id: uuid.UUID,
    payload: ModuleLessonInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    module = (
        await db.execute(
            select(AcademyModuleVersion)
            .where(AcademyModuleVersion.id == module_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")
    if module.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published module lessons cannot be changed"
        )
    lesson_version = await db.get(AcademyLessonVersion, payload.lesson_version_id)
    _assert_published(lesson_version, "Lesson version")

    row = AcademyModuleLesson(
        module_version_id=module_version_id,
        lesson_version_id=payload.lesson_version_id,
        order_index=payload.order_index,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Lesson or order index is already assigned to this module version",
        ) from exc
    return {"id": str(row.id)}


@router.delete("/modules/{module_version_id}/lessons/{link_id}", status_code=204)
async def remove_module_lesson(
    module_version_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    module = (
        await db.execute(
            select(AcademyModuleVersion)
            .where(AcademyModuleVersion.id == module_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")
    if module.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published module lessons cannot be changed"
        )
    link = (
        await db.execute(
            select(AcademyModuleLesson).where(
                AcademyModuleLesson.id == link_id,
                AcademyModuleLesson.module_version_id == module_version_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Module lesson link not found")
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)


@router.post("/modules/{module_version_id}/quizzes", status_code=201)
async def attach_module_quiz(
    module_version_id: uuid.UUID,
    payload: ModuleQuizInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    module = (
        await db.execute(
            select(AcademyModuleVersion)
            .where(AcademyModuleVersion.id == module_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")
    if module.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published module quizzes cannot be changed"
        )
    quiz_version = await db.get(AcademyKnowledgeQuizVersion, payload.quiz_version_id)
    _assert_published(quiz_version, "Quiz version")

    row = AcademyModuleQuiz(
        module_version_id=module_version_id,
        quiz_version_id=payload.quiz_version_id,
        required_for_completion=payload.required_for_completion,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Quiz is already assigned to this module version",
        ) from exc
    return {"id": str(row.id)}


@router.delete("/modules/{module_version_id}/quizzes/{link_id}", status_code=204)
async def remove_module_quiz(
    module_version_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    module = (
        await db.execute(
            select(AcademyModuleVersion)
            .where(AcademyModuleVersion.id == module_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")
    if module.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published module quizzes cannot be changed"
        )
    link = (
        await db.execute(
            select(AcademyModuleQuiz).where(
                AcademyModuleQuiz.id == link_id,
                AcademyModuleQuiz.module_version_id == module_version_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Module quiz link not found")
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)


async def _list_entity_skills(
    db: AsyncSession,
    parent_model,
    link_model,
    fk_name: str,
    parent_id: uuid.UUID,
) -> list[dict]:
    if await db.get(parent_model, parent_id) is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    links = (
        (
            await db.execute(
                select(link_model).where(getattr(link_model, fk_name) == parent_id)
            )
        )
        .scalars()
        .all()
    )
    if not links:
        return []
    skills = (
        (
            await db.execute(
                select(Skill).where(Skill.id.in_({link.skill_id for link in links}))
            )
        )
        .scalars()
        .all()
    )
    skill_by_id = {skill.id: skill for skill in skills}
    return [
        {
            "id": str(link.id),
            "skill_id": str(link.skill_id),
            "name": skill_by_id[link.skill_id].name
            if link.skill_id in skill_by_id
            else None,
            "category": skill_by_id[link.skill_id].category
            if link.skill_id in skill_by_id
            else None,
        }
        for link in links
    ]


async def _attach_entity_skill(
    db: AsyncSession,
    parent_model,
    link_model,
    fk_name: str,
    parent_id: uuid.UUID,
    skill_id: uuid.UUID,
) -> dict:
    if await db.get(parent_model, parent_id) is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    if await db.get(Skill, skill_id) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    row = link_model(**{fk_name: parent_id, "skill_id": skill_id})
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Skill already linked") from exc
    return {"id": str(row.id)}


async def _remove_entity_skill(
    db: AsyncSession,
    link_model,
    fk_name: str,
    parent_id: uuid.UUID,
    link_id: uuid.UUID,
) -> Response:
    link = (
        await db.execute(
            select(link_model).where(
                link_model.id == link_id,
                getattr(link_model, fk_name) == parent_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Skill link not found")
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)


@router.get("/milestones/{definition_id}/skills")
async def list_milestone_skills(
    definition_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _list_entity_skills(
        db,
        AcademyMilestoneDefinition,
        AcademyMilestoneSkill,
        "milestone_id",
        definition_id,
    )


@router.post("/milestones/{definition_id}/skills", status_code=201)
async def attach_milestone_skill(
    definition_id: uuid.UUID,
    payload: SkillLinkInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _attach_entity_skill(
        db,
        AcademyMilestoneDefinition,
        AcademyMilestoneSkill,
        "milestone_id",
        definition_id,
        payload.skill_id,
    )


@router.delete("/milestones/{definition_id}/skills/{link_id}", status_code=204)
async def remove_milestone_skill(
    definition_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _remove_entity_skill(
        db, AcademyMilestoneSkill, "milestone_id", definition_id, link_id
    )


@router.get("/drills/{definition_id}/skills")
async def list_drill_skills(
    definition_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _list_entity_skills(
        db, AcademyDrill, AcademyDrillSkill, "drill_id", definition_id
    )


@router.post("/drills/{definition_id}/skills", status_code=201)
async def attach_drill_skill(
    definition_id: uuid.UUID,
    payload: SkillLinkInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _attach_entity_skill(
        db, AcademyDrill, AcademyDrillSkill, "drill_id", definition_id, payload.skill_id
    )


@router.delete("/drills/{definition_id}/skills/{link_id}", status_code=204)
async def remove_drill_skill(
    definition_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _remove_entity_skill(
        db, AcademyDrillSkill, "drill_id", definition_id, link_id
    )


# ---------------------------------------------------------------------------
# Generic definition & version authoring endpoints
# ---------------------------------------------------------------------------


@router.get("/{kind}")
async def list_definitions(
    kind: CatalogKind,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    model, _, _ = CATALOG_MODELS[kind]
    rows = (
        (await db.execute(select(model).order_by(model.code).limit(500)))
        .scalars()
        .all()
    )
    return [_definition_data(row) for row in rows]


@router.post("/{kind}", status_code=201)
async def create_definition(
    kind: CatalogKind,
    payload: CatalogDefinitionCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    model, _, _ = CATALOG_MODELS[kind]
    row = model(**payload.model_dump())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Catalog code already exists"
        ) from exc
    await db.refresh(row)
    return _definition_data(row)


@router.post("/{kind}/{definition_id}/retire")
async def retire_definition(
    kind: CatalogKind,
    definition_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    model, _, _ = CATALOG_MODELS[kind]
    definition = (
        await db.execute(
            select(model).where(model.id == definition_id).with_for_update()
        )
    ).scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    if not definition.is_retired:
        definition.is_retired = True
        await db.commit()
        await db.refresh(definition)
    return _definition_data(definition)


@router.get("/{kind}/{definition_id}/versions")
async def list_versions(
    kind: CatalogKind,
    definition_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    model, version_model, fk = CATALOG_MODELS[kind]
    if await db.get(model, definition_id) is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    result = await db.execute(
        select(version_model)
        .where(getattr(version_model, fk) == definition_id)
        .order_by(version_model.version.desc())
    )
    return [_version_data(kind, row) for row in result.scalars().all()]


@router.post("/{kind}/{definition_id}/versions", status_code=201)
async def create_version(
    kind: CatalogKind,
    definition_id: uuid.UUID,
    payload: CatalogVersionCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    definition_model, version_model, fk = CATALOG_MODELS[kind]
    validated = _content(kind, payload.content)

    # Lock the identity for serialized allocation of sequential version numbers.
    result = await db.execute(
        select(definition_model)
        .where(definition_model.id == definition_id)
        .with_for_update()
    )
    definition = result.scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    if definition.is_retired:
        raise HTTPException(
            status_code=409, detail="Retired definitions cannot gain versions"
        )

    last = (
        await db.execute(
            select(version_model.version)
            .where(getattr(version_model, fk) == definition_id)
            .order_by(version_model.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    row = version_model(
        **{fk: definition_id},
        version=(last or 0) + 1,
        created_by_auth_id=str(current_user.user_id),
        **validated,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _version_data(kind, row)


@router.patch("/{kind}/{definition_id}/versions/{version_id}")
async def update_draft_version(
    kind: CatalogKind,
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: CatalogVersionCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    definition_model, version_model, fk = CATALOG_MODELS[kind]
    validated = _content(kind, payload.content)

    definition = await db.get(definition_model, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    if definition.is_retired:
        raise HTTPException(
            status_code=409, detail="Retired definitions cannot be edited"
        )
    version = (
        await db.execute(
            select(version_model)
            .where(
                version_model.id == version_id,
                getattr(version_model, fk) == definition_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="Published versions are immutable")

    merged = {**OPTIONAL_VERSION_DEFAULTS[kind], **validated}
    for field, value in merged.items():
        setattr(version, field, value)
    await db.commit()
    await db.refresh(version)
    return _version_data(kind, version)


@router.post("/{kind}/{definition_id}/versions/{version_id}/publish")
async def publish_version(
    kind: CatalogKind,
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    definition_model, version_model, fk = CATALOG_MODELS[kind]
    definition = await db.get(definition_model, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Definition not found")
    if definition.is_retired:
        raise HTTPException(
            status_code=409, detail="Retired definitions cannot publish"
        )
    version = (
        await db.execute(
            select(version_model)
            .where(
                version_model.id == version_id,
                getattr(version_model, fk) == definition_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if version.state == "published":
        return _version_data(kind, version)
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="Invalid version state")
    if kind == "milestone":
        _validate_milestone_for_publish(version)

    version.state = "published"
    version.published_at = utc_now()
    await db.commit()
    await db.refresh(version)
    return _version_data(kind, version)
