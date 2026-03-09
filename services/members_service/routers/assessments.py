"""Swim readiness assessment endpoints.

Public endpoints for submitting and viewing assessments, plus an
authenticated endpoint for viewing personal assessment history.
"""

import hashlib
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import Member
from services.members_service.models.assessment import SwimAssessment
from services.members_service.schemas.assessment import (
    AssessmentResponse,
    AssessmentStatsResponse,
    AssessmentSubmit,
)

router = APIRouter(prefix="/assessments", tags=["assessments"])

# ---------------------------------------------------------------------------
# Server-side scoring logic (canonical – frontend has a preview copy)
# ---------------------------------------------------------------------------

# Question definitions: (id, dimension, max_score_per_option)
# This must stay in sync with the frontend lib/assessment.ts
QUESTIONS = [
    {"id": "water_comfort", "dimension": "water_comfort", "max_option": 3},
    {"id": "face_in_water", "dimension": "breathing", "max_option": 3},
    {"id": "floating", "dimension": "floating", "max_option": 3},
    {"id": "kicking", "dimension": "kicking", "max_option": 3},
    {"id": "arm_stroke", "dimension": "arm_stroke", "max_option": 3},
    {"id": "distance", "dimension": "distance", "max_option": 4},
    {"id": "deep_water", "dimension": "deep_water", "max_option": 3},
    {"id": "treading", "dimension": "treading", "max_option": 3},
    {"id": "stroke_knowledge", "dimension": "stroke_knowledge", "max_option": 4},
    {"id": "breathing_while_swimming", "dimension": "breathing", "max_option": 3},
    {"id": "frequency", "dimension": "consistency", "max_option": 4},
    {"id": "confidence", "dimension": "water_comfort", "max_option": 3},
]

VALID_IDS = {q["id"] for q in QUESTIONS}

DIMENSION_META: dict[str, dict] = {
    "water_comfort": {"label": "Water Comfort", "icon": "💧", "max": 6},
    "floating": {"label": "Floating Ability", "icon": "🫧", "max": 3},
    "breathing": {"label": "Breathing Control", "icon": "💨", "max": 6},
    "kicking": {"label": "Kicking Ability", "icon": "🦵", "max": 3},
    "arm_stroke": {"label": "Arm Stroke", "icon": "💪", "max": 3},
    "distance": {"label": "Distance", "icon": "📏", "max": 4},
    "deep_water": {"label": "Deep Water", "icon": "🌊", "max": 3},
    "treading": {"label": "Treading Water", "icon": "⏱️", "max": 3},
    "stroke_knowledge": {"label": "Stroke Knowledge", "icon": "🏊", "max": 4},
    "consistency": {"label": "Consistency", "icon": "🔥", "max": 4},
}

MAX_RAW_SCORE = sum(d["max"] for d in DIMENSION_META.values())  # 39


def _score_to_level(total: int) -> str:
    if total <= 15:
        return "non_swimmer"
    if total <= 35:
        return "beginner"
    if total <= 55:
        return "developing"
    if total <= 80:
        return "intermediate"
    return "advanced"


def _calculate(answers: dict[str, int]) -> tuple[int, int, str, list[dict]]:
    """Return (total_score, raw_score, level, dimension_scores)."""
    dim_scores: dict[str, int] = {}
    for q in QUESTIONS:
        val = answers.get(q["id"], 0)
        # Clamp to valid range
        val = max(0, min(val, q["max_option"]))
        dim_scores[q["dimension"]] = dim_scores.get(q["dimension"], 0) + val

    dimension_list = []
    for dim_id, meta in DIMENSION_META.items():
        score = dim_scores.get(dim_id, 0)
        pct = round((score / meta["max"]) * 100) if meta["max"] > 0 else 0
        rating = "strong" if pct >= 67 else ("moderate" if pct >= 34 else "needs_work")
        dimension_list.append(
            {
                "dimension": dim_id,
                "label": meta["label"],
                "icon": meta["icon"],
                "score": score,
                "maxScore": meta["max"],
                "percentage": pct,
                "rating": rating,
            }
        )

    raw = sum(d["score"] for d in dimension_list)
    total = round((raw / MAX_RAW_SCORE) * 100) if MAX_RAW_SCORE > 0 else 0
    level = _score_to_level(total)
    return total, raw, level, dimension_list


def _hash_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Optional auth dependency (returns None when no token present)
# ---------------------------------------------------------------------------

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_optional_bearer = HTTPBearer(auto_error=False)


async def _optional_member_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_async_db),
) -> Optional[uuid.UUID]:
    """Extract member_id from JWT if present, otherwise return None."""
    if not credentials:
        return None
    try:
        user: AuthUser = await get_current_user(credentials)
        result = await db.execute(
            select(Member.id).where(Member.auth_id == user.user_id)
        )
        member = result.scalar_one_or_none()
        return member
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
@router.post(
    "/", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED
)
async def submit_assessment(
    body: AssessmentSubmit,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    member_id: Optional[uuid.UUID] = Depends(_optional_member_id),
):
    """Submit a completed swim readiness assessment.

    Public endpoint — no authentication required. If the caller supplies a
    valid JWT the result is linked to their member record.
    """
    # Validate answer keys
    unknown = set(body.answers.keys()) - VALID_IDS
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown question IDs: {sorted(unknown)}",
        )

    total_score, raw_score, level, dimension_scores = _calculate(body.answers)

    assessment = SwimAssessment(
        member_id=member_id,
        answers=body.answers,
        total_score=total_score,
        raw_score=raw_score,
        level=level,
        dimension_scores=dimension_scores,
        ip_hash=_hash_ip(request.client.host if request.client else None),
        user_agent=(request.headers.get("user-agent") or "")[:512],
    )

    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


@router.get("/stats", response_model=AssessmentStatsResponse)
async def get_assessment_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """Get aggregate assessment statistics. Public endpoint."""
    # Total count
    count_result = await db.execute(select(func.count(SwimAssessment.id)))
    total_count = count_result.scalar() or 0

    # Average score
    avg_result = await db.execute(select(func.avg(SwimAssessment.total_score)))
    average_score = round(float(avg_result.scalar() or 0), 1)

    # Level distribution
    dist_result = await db.execute(
        select(SwimAssessment.level, func.count(SwimAssessment.id)).group_by(
            SwimAssessment.level
        )
    )
    level_distribution = {row[0]: row[1] for row in dist_result.all()}

    return AssessmentStatsResponse(
        total_count=total_count,
        level_distribution=level_distribution,
        average_score=average_score,
    )


@router.get("/me", response_model=list[AssessmentResponse])
async def get_my_assessments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get assessment history for the authenticated user."""
    member_result = await db.execute(
        select(Member.id).where(Member.auth_id == current_user.user_id)
    )
    member_id = member_result.scalar_one_or_none()
    if not member_id:
        raise HTTPException(status_code=404, detail="Member not found")

    result = await db.execute(
        select(SwimAssessment)
        .where(SwimAssessment.member_id == member_id)
        .order_by(SwimAssessment.created_at.desc())
        .limit(20)
    )
    return result.scalars().all()


@router.get("/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single assessment result by ID. Public endpoint."""
    result = await db.execute(
        select(SwimAssessment).where(SwimAssessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment
