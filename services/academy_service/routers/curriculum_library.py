"""Admin-only authoring for independent, versioned Academy curriculum content.

The first API slice does not touch existing programme-bound milestones,
learner outcomes, tuition or certificates. Publication freezes a version.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
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
    AcademyDrillVersion,
    AcademyKnowledgeQuiz,
    AcademyKnowledgeQuizVersion,
    AcademyLessonBlueprint,
    AcademyLessonVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    AcademyModule,
    AcademyModuleVersion,
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


class CatalogDefinitionCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    title: str = Field(min_length=3, max_length=200)
    description: str | None = None


class CatalogVersionCreate(BaseModel):
    content: dict[str, Any]


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
        if "assessment_context_json" in result and not isinstance(
            result["assessment_context_json"], dict
        ):
            raise HTTPException(
                status_code=422, detail="Assessment context must be an object"
            )
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

    version.state = "published"
    version.published_at = utc_now()
    await db.commit()
    return _version_data(kind, version)
