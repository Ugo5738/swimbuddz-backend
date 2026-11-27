"""Member Swim Scorecard domain logic, aggregation & privacy projection (ADR-026)."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.service_client.scorecard import (
    fetch_academy_scorecard_summary,
    fetch_reporting_scorecard_summary,
    fetch_strokelab_scorecard_summary,
    fetch_volunteer_scorecard_summary,
)
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubReadinessAssessment,
    Member,
    MemberScorecardSettings,
    MemberSkillProgress,
    MemberSwimBenchmark,
    Pod,
    PodAssignment,
    PodStatus,
)
from services.members_service.schemas.scorecard import (
    AcademyMilestoneSummaryItem,
    AcademyProgressSection,
    BatchBenchmarkDayRequest,
    BatchBenchmarkDayResponse,
    BenchmarkItemResponse,
    BenchmarkTrendSeries,
    ClubReadinessSnapshot,
    ConsistencyCommunitySection,
    CreateBenchmarkRequest,
    _BenchmarkFieldsBase,
    MemberScorecardResponse,
    PeerScorecardHighlightResponse,
    PendingBenchmarkReviewItem,
    PendingSkillReviewItem,
    PersonalBestRecord,
    PodScorecardOverviewResponse,
    PublicShareScorecardResponse,
    ScorecardDomainSection,
    ScorecardHeadlineStats,
    ScorecardReviewQueueResponse,
    ScorecardRosterMemberSummary,
    ScorecardSettingsResponse,
    ScorecardSkillItem,
    StrokeLabRunSummary,
    StrokeLabSection,
    TrendPoint,
    UpdateScorecardSettingsRequest,
)
from services.members_service.services.club_access import (
    current_club_enrollment_until,
    has_current_club_access,
)
from services.members_service.services.membership_status import (
    build_membership_status_summary,
)
from services.members_service.services.scorecard_catalog import (
    CLUB_ONLY_BENCHMARK_TYPES,
    PROFICIENCY_ORDER,
    SKILL_BY_CODE,
    SKILL_DOMAIN_SPECS,
    SKILLS_BY_DOMAIN,
    STROKE_LABELS,
    get_skill_spec,
)


CLUB_READY_OUTCOMES = {"club_ready", "club_ready_modified"}
TRUST_RANK = {"self_logged": 1, "strokelab_ai": 2, "coach_verified": 3}


# ── Pure Calculation Helpers ──────────────────────────────────────────────


def compute_pace_per_100m(
    distance_m: Optional[int], duration_seconds: Optional[float]
) -> Optional[float]:
    """Compute pace in seconds per 100m when both distance and duration exist."""
    if (
        not distance_m
        or not duration_seconds
        or distance_m <= 0
        or duration_seconds <= 0
    ):
        return None
    return round((float(duration_seconds) / float(distance_m)) * 100.0, 2)


def compute_swolf(
    *,
    benchmark_type: str,
    duration_seconds: Optional[float],
    stroke_count: Optional[int],
    explicit_swolf: Optional[float],
    distance_m: Optional[int],
    pool_length_m: Optional[float],
) -> Optional[float]:
    """Compute single-length SWOLF (seconds + strokes) when inputs represent one length."""
    if explicit_swolf is not None:
        return round(float(explicit_swolf), 1)
    if duration_seconds is None or stroke_count is None:
        return None
    is_single_length = benchmark_type in {"spl", "swolf"} and (
        distance_m is None
        or (
            pool_length_m is not None
            and abs(float(distance_m) - float(pool_length_m)) <= 0.5
        )
    )
    if not is_single_length:
        return None
    return round(float(duration_seconds) + float(stroke_count), 1)


def format_duration(seconds: float) -> str:
    """Format seconds as M:SS.s (or SS.ss when under 60s)."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins}:{rem:04.1f}"


def format_pace_per_100m(seconds: float) -> str:
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins}:{rem:04.1f}/100m"


def _full_name(member: Member) -> str:
    return f"{member.first_name} {member.last_name}".strip()


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def generate_share_token() -> str:
    return secrets.token_urlsafe(24)


# ── Skill & Trust Transition Rules (Pure Logic) ───────────────────────────


def apply_self_skill_update(
    row: MemberSkillProgress,
    *,
    proficiency: str,
    member_notes: Optional[str],
    request_verification: bool,
    now: Optional[datetime] = None,
) -> MemberSkillProgress:
    """Apply a member self-update without ever erasing coach-verified progress."""
    now = now or utc_now()
    row.member_notes = member_notes
    row.last_self_updated_at = now

    verified_prof = row.verified_proficiency
    if verified_prof is not None:
        verified_rank = PROFICIENCY_ORDER.get(verified_prof, 0)
        new_rank = PROFICIENCY_ORDER.get(proficiency, 0)
        if new_rank <= verified_rank:
            # Never silently downgrade or strip coach verification.
            row.proficiency = verified_prof
            row.verification_source = "coach_verified"
            row.verification_status = "verified"
            return row

        # Member claims a higher proficiency than currently verified; preserve
        # verified_proficiency baseline and flag the higher claim.
        row.proficiency = proficiency
        row.verification_source = "self_logged"
        row.verification_status = (
            "pending_review" if request_verification else "unverified"
        )
        return row

    row.proficiency = proficiency
    row.verification_source = "self_logged"
    if proficiency == "not_started":
        row.verification_status = "unverified"
    else:
        row.verification_status = (
            "pending_review" if request_verification else "unverified"
        )
    return row


def apply_verifier_skill_decision(
    row: MemberSkillProgress,
    *,
    action: str,
    proficiency: Optional[str],
    verifier_member_id: Optional[uuid.UUID],
    verifier_role: str,
    verifier_notes: Optional[str],
    now: Optional[datetime] = None,
) -> MemberSkillProgress:
    """Apply a Coach, Pod Lead, or Admin verification/rejection on a skill."""
    now = now or utc_now()
    if action == "reject":
        if not verifier_notes or not verifier_notes.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feedback notes (verifier_notes) are required when rejecting a skill claim.",
            )
        row.verification_status = "rejected"
        row.verifier_notes = verifier_notes.strip()
        row.verified_by_member_id = verifier_member_id
        row.verified_by_role = verifier_role
        row.verified_at = now
        if row.verified_proficiency is not None:
            row.proficiency = row.verified_proficiency
            row.verification_source = "coach_verified"
        else:
            row.proficiency = (
                "learning" if row.proficiency != "not_started" else "not_started"
            )
            row.verification_source = "self_logged"
        return row

    target_proficiency = proficiency or row.proficiency
    if target_proficiency == "not_started":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot verify a skill at 'not_started' proficiency.",
        )
    row.proficiency = target_proficiency
    row.verified_proficiency = target_proficiency
    row.verification_source = "coach_verified"
    row.verification_status = "verified"
    row.verified_by_member_id = verifier_member_id
    row.verified_by_role = verifier_role
    row.verified_at = now
    if verifier_notes is not None:
        row.verifier_notes = verifier_notes.strip() or None
    return row


# ── Personal Bests & Trends Aggregation ───────────────────────────────────


def compute_personal_bests(
    benchmarks: Sequence[MemberSwimBenchmark],
    *,
    club_readiness: Optional[ClubReadinessSnapshot] = None,
    strokelab: Optional[StrokeLabSection] = None,
    verified_only: bool = False,
) -> List[PersonalBestRecord]:
    """Compute pool-length-aware personal bests across all metrics."""
    valid = [
        b
        for b in benchmarks
        if b.verification_status != "rejected"
        and (
            not verified_only
            or (
                b.verification_source == "coach_verified"
                and b.verification_status == "verified"
            )
        )
    ]

    records: List[PersonalBestRecord] = []

    # 1. Max continuous distance per stroke (from max_distance, time_trial, open_water)
    best_dist_by_stroke: Dict[str, MemberSwimBenchmark] = {}
    for b in valid:
        if b.distance_m is None:
            continue
        stroke_key = "open_water" if b.benchmark_type == "open_water" else b.stroke
        prev = best_dist_by_stroke.get(stroke_key)
        if prev is None or (
            b.distance_m,
            TRUST_RANK.get(b.verification_source, 0),
            b.recorded_at,
        ) > (
            prev.distance_m or 0,
            TRUST_RANK.get(prev.verification_source, 0),
            prev.recorded_at,
        ):
            best_dist_by_stroke[stroke_key] = b

    for stroke_key, b in sorted(
        best_dist_by_stroke.items(), key=lambda kv: -(kv[1].distance_m or 0)
    ):
        stroke_label = STROKE_LABELS.get(stroke_key, stroke_key.title())
        records.append(
            PersonalBestRecord(
                key=f"max_distance:{stroke_key}",
                label=f"Longest {stroke_label}",
                stroke=stroke_key,
                benchmark_type="open_water"
                if stroke_key == "open_water"
                else "max_distance",
                value=float(b.distance_m or 0),
                display_value=f"{b.distance_m}m",
                unit="m",
                pool_length_m=b.pool_length_m,
                pool_name=b.pool_name,
                recorded_at=b.recorded_at,
                verification_source=b.verification_source,  # type: ignore[arg-type]
                is_verified=(
                    b.verification_source == "coach_verified"
                    and b.verification_status == "verified"
                ),
                benchmark_id=b.id,
            )
        )

    # Include Club Readiness baseline distance if no verified distance matches/exceeds it
    if (
        club_readiness
        and club_readiness.is_club_ready
        and club_readiness.nonstop_distance_m
        and club_readiness.completed_at
    ):
        existing_max = max(
            (b.distance_m or 0 for b in valid if b.distance_m), default=0
        )
        if club_readiness.nonstop_distance_m > existing_max:
            records.append(
                PersonalBestRecord(
                    key="max_distance:club_readiness",
                    label="Club Readiness Nonstop Swim",
                    stroke="any",
                    benchmark_type="max_distance",
                    value=float(club_readiness.nonstop_distance_m),
                    display_value=f"{club_readiness.nonstop_distance_m}m",
                    unit="m",
                    recorded_at=club_readiness.completed_at,
                    verification_source="coach_verified",
                    is_verified=True,
                )
            )

    # 2. Fastest time trials by (stroke, distance_m)
    best_tt: Dict[Tuple[str, int], MemberSwimBenchmark] = {}
    for b in valid:
        if (
            b.benchmark_type != "time_trial"
            or not b.distance_m
            or not b.duration_seconds
        ):
            continue
        key = (b.stroke, b.distance_m)
        prev = best_tt.get(key)
        if prev is None or b.duration_seconds < (prev.duration_seconds or float("inf")):
            best_tt[key] = b

    for (stroke_key, dist), b in sorted(
        best_tt.items(), key=lambda kv: (kv[0][0], kv[0][1])
    ):
        stroke_label = STROKE_LABELS.get(stroke_key, stroke_key.title())
        pace_txt = (
            f" ({format_pace_per_100m(b.pace_per_100m_seconds)})"
            if b.pace_per_100m_seconds
            else ""
        )
        records.append(
            PersonalBestRecord(
                key=f"time_trial:{stroke_key}:{dist}",
                label=f"Fastest {dist}m {stroke_label}",
                stroke=stroke_key,
                benchmark_type="time_trial",
                value=float(b.duration_seconds or 0.0),
                display_value=f"{format_duration(b.duration_seconds or 0.0)}{pace_txt}",
                unit="s",
                pool_length_m=b.pool_length_m,
                pool_name=b.pool_name,
                recorded_at=b.recorded_at,
                verification_source=b.verification_source,  # type: ignore[arg-type]
                is_verified=(
                    b.verification_source == "coach_verified"
                    and b.verification_status == "verified"
                ),
                benchmark_id=b.id,
            )
        )

    # 3. Lowest SPL by (stroke, pool_length_m)
    best_spl: Dict[Tuple[str, float], MemberSwimBenchmark] = {}
    for b in valid:
        if b.stroke_count is None or b.pool_length_m is None:
            continue
        if b.benchmark_type not in {"spl", "swolf"}:
            continue
        key = (b.stroke, round(float(b.pool_length_m), 1))
        prev = best_spl.get(key)
        if prev is None or b.stroke_count < (prev.stroke_count or 10**9):
            best_spl[key] = b

    for (stroke_key, pool_len), b in sorted(best_spl.items()):
        stroke_label = STROKE_LABELS.get(stroke_key, stroke_key.title())
        pool_len_txt = f"{pool_len:g}m"
        records.append(
            PersonalBestRecord(
                key=f"spl:{stroke_key}:{pool_len_txt}",
                label=f"Best {stroke_label} SPL ({pool_len_txt} pool)",
                stroke=stroke_key,
                benchmark_type="spl",
                value=float(b.stroke_count or 0),
                display_value=f"{b.stroke_count} SPL",
                unit="strokes/length",
                pool_length_m=pool_len,
                pool_name=b.pool_name,
                recorded_at=b.recorded_at,
                verification_source=b.verification_source,  # type: ignore[arg-type]
                is_verified=(
                    b.verification_source == "coach_verified"
                    and b.verification_status == "verified"
                ),
                benchmark_id=b.id,
            )
        )

    # 4. Lowest SWOLF by (stroke, pool_length_m)
    best_swolf: Dict[Tuple[str, float], MemberSwimBenchmark] = {}
    for b in valid:
        if b.swolf_score is None or b.pool_length_m is None:
            continue
        key = (b.stroke, round(float(b.pool_length_m), 1))
        prev = best_swolf.get(key)
        if prev is None or b.swolf_score < (prev.swolf_score or float("inf")):
            best_swolf[key] = b

    for (stroke_key, pool_len), b in sorted(best_swolf.items()):
        stroke_label = STROKE_LABELS.get(stroke_key, stroke_key.title())
        pool_len_txt = f"{pool_len:g}m"
        records.append(
            PersonalBestRecord(
                key=f"swolf:{stroke_key}:{pool_len_txt}",
                label=f"Best {stroke_label} SWOLF ({pool_len_txt} pool)",
                stroke=stroke_key,
                benchmark_type="swolf",
                value=float(b.swolf_score or 0.0),
                display_value=f"{b.swolf_score:g} SWOLF",
                unit="score",
                pool_length_m=pool_len,
                pool_name=b.pool_name,
                recorded_at=b.recorded_at,
                verification_source=b.verification_source,  # type: ignore[arg-type]
                is_verified=(
                    b.verification_source == "coach_verified"
                    and b.verification_status == "verified"
                ),
                benchmark_id=b.id,
            )
        )

    # 5. Latest Stroke Rate (SPM) from benchmarks or Stroke Lab AI
    if not verified_only:
        spm_candidates: List[Tuple[datetime, float, str, str, Optional[uuid.UUID]]] = []
        for b in valid:
            if b.stroke_rate_spm is not None:
                spm_candidates.append(
                    (
                        b.recorded_at,
                        float(b.stroke_rate_spm),
                        b.stroke,
                        b.verification_source,
                        b.id,
                    )
                )
        if strokelab and strokelab.recent_runs:
            for run in strokelab.recent_runs:
                if run.stroke_rate_spm is not None:
                    spm_candidates.append(
                        (
                            run.completed_at,
                            float(run.stroke_rate_spm),
                            run.stroke_type or "freestyle",
                            "strokelab_ai",
                            None,
                        )
                    )
        if spm_candidates:
            spm_candidates.sort(key=lambda item: item[0], reverse=True)
            rec_at, spm_val, stroke_key, src, b_id = spm_candidates[0]
            stroke_label = STROKE_LABELS.get(stroke_key, stroke_key.title())
            records.append(
                PersonalBestRecord(
                    key=f"stroke_rate:{stroke_key}",
                    label=f"Latest {stroke_label} Stroke Rate",
                    stroke=stroke_key,
                    benchmark_type="stroke_rate",
                    value=round(spm_val, 1),
                    display_value=f"{spm_val:.1f} SPM",
                    unit="spm",
                    recorded_at=rec_at,
                    verification_source=src,  # type: ignore[arg-type]
                    is_verified=(src == "coach_verified"),
                    benchmark_id=b_id,
                )
            )

    return records


def compute_trend_series(
    benchmarks: Sequence[MemberSwimBenchmark],
    strokelab: Optional[StrokeLabSection] = None,
) -> List[BenchmarkTrendSeries]:
    """Build chronological trend series for frontend progression charts."""
    valid = sorted(
        [b for b in benchmarks if b.verification_status != "rejected"],
        key=lambda b: b.recorded_at,
    )

    distance_points: List[TrendPoint] = []
    spl_points: List[TrendPoint] = []
    swolf_points: List[TrendPoint] = []
    spm_points: List[TrendPoint] = []
    pace_points: List[TrendPoint] = []

    for b in valid:
        if b.distance_m is not None and b.benchmark_type in {
            "max_distance",
            "open_water",
        }:
            distance_points.append(
                TrendPoint(
                    recorded_at=b.recorded_at,
                    value=float(b.distance_m),
                    display_value=f"{b.distance_m}m",
                    stroke=b.stroke,
                    pool_length_m=b.pool_length_m,
                    pool_name=b.pool_name,
                    verification_source=b.verification_source,  # type: ignore[arg-type]
                    label=b.benchmark_day_label,
                )
            )
        if (
            b.stroke_count is not None
            and b.pool_length_m is not None
            and b.benchmark_type in {"spl", "swolf"}
        ):
            spl_points.append(
                TrendPoint(
                    recorded_at=b.recorded_at,
                    value=float(b.stroke_count),
                    display_value=f"{b.stroke_count} SPL ({b.pool_length_m:g}m)",
                    stroke=b.stroke,
                    pool_length_m=b.pool_length_m,
                    pool_name=b.pool_name,
                    verification_source=b.verification_source,  # type: ignore[arg-type]
                    label=b.benchmark_day_label,
                )
            )
        if b.swolf_score is not None and b.pool_length_m is not None:
            swolf_points.append(
                TrendPoint(
                    recorded_at=b.recorded_at,
                    value=float(b.swolf_score),
                    display_value=f"{b.swolf_score:g} SWOLF ({b.pool_length_m:g}m)",
                    stroke=b.stroke,
                    pool_length_m=b.pool_length_m,
                    pool_name=b.pool_name,
                    verification_source=b.verification_source,  # type: ignore[arg-type]
                    label=b.benchmark_day_label,
                )
            )
        if b.stroke_rate_spm is not None:
            spm_points.append(
                TrendPoint(
                    recorded_at=b.recorded_at,
                    value=round(float(b.stroke_rate_spm), 1),
                    display_value=f"{b.stroke_rate_spm:.1f} SPM",
                    stroke=b.stroke,
                    pool_length_m=b.pool_length_m,
                    pool_name=b.pool_name,
                    verification_source=b.verification_source,  # type: ignore[arg-type]
                    label=b.benchmark_day_label,
                )
            )
        if b.pace_per_100m_seconds is not None and b.benchmark_type in {
            "time_trial",
            "open_water",
        }:
            pace_points.append(
                TrendPoint(
                    recorded_at=b.recorded_at,
                    value=round(float(b.pace_per_100m_seconds), 2),
                    display_value=format_pace_per_100m(b.pace_per_100m_seconds),
                    stroke=b.stroke,
                    pool_length_m=b.pool_length_m,
                    pool_name=b.pool_name,
                    verification_source=b.verification_source,  # type: ignore[arg-type]
                    label=b.benchmark_day_label
                    or (f"{b.distance_m}m" if b.distance_m else None),
                )
            )

    if strokelab and strokelab.recent_runs:
        for run in strokelab.recent_runs:
            if run.stroke_rate_spm is not None:
                spm_points.append(
                    TrendPoint(
                        recorded_at=run.completed_at,
                        value=round(float(run.stroke_rate_spm), 1),
                        display_value=f"{run.stroke_rate_spm:.1f} SPM",
                        stroke=run.stroke_type or "freestyle",
                        verification_source="strokelab_ai",
                        label="Stroke Lab AI",
                    )
                )
        spm_points.sort(key=lambda p: p.recorded_at)

    return [
        BenchmarkTrendSeries(
            metric_key="max_distance",
            title="Continuous Distance Progression",
            unit="m",
            higher_is_better=True,
            points=distance_points,
        ),
        BenchmarkTrendSeries(
            metric_key="spl",
            title="Strokes Per Length (SPL)",
            unit="strokes",
            higher_is_better=False,
            points=spl_points,
        ),
        BenchmarkTrendSeries(
            metric_key="swolf",
            title="SWOLF Efficiency",
            unit="score",
            higher_is_better=False,
            points=swolf_points,
        ),
        BenchmarkTrendSeries(
            metric_key="stroke_rate",
            title="Stroke Rate (SPM)",
            unit="spm",
            higher_is_better=None,
            points=spm_points,
        ),
        BenchmarkTrendSeries(
            metric_key="pace_per_100m",
            title="Pace per 100m",
            unit="s/100m",
            higher_is_better=False,
            points=pace_points,
        ),
    ]


# ── DB Lookup & Authorization Helpers ─────────────────────────────────────


async def get_member_by_auth_or_404(db: AsyncSession, auth_id: str) -> Member:
    member = (
        await db.execute(
            select(Member)
            .where(Member.auth_id == auth_id)
            .options(
                selectinload(Member.membership),
                selectinload(Member.profile),
                selectinload(Member.coach_profile),
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )
    return member


async def get_member_by_id_or_404(db: AsyncSession, member_id: uuid.UUID) -> Member:
    member = (
        await db.execute(
            select(Member)
            .where(Member.id == member_id)
            .options(
                selectinload(Member.membership),
                selectinload(Member.profile),
                selectinload(Member.coach_profile),
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    return member


async def get_or_create_settings(
    db: AsyncSession, member_id: uuid.UUID
) -> MemberScorecardSettings:
    settings_row = (
        await db.execute(
            select(MemberScorecardSettings).where(
                MemberScorecardSettings.member_id == member_id
            )
        )
    ).scalar_one_or_none()
    if settings_row is None:
        settings_row = MemberScorecardSettings(member_id=member_id)
        db.add(settings_row)
        await db.flush()
    return settings_row


async def _get_led_pod_ids(db: AsyncSession, member_id: uuid.UUID) -> List[uuid.UUID]:
    if not await has_current_club_access(db, member_id=member_id, at=utc_now()):
        return []
    rows = await db.execute(
        select(Pod.id).where(
            or_(Pod.pod_lead_id == member_id, Pod.assistant_pod_lead_id == member_id),
            Pod.status == PodStatus.ACTIVE,
        )
    )
    return [row[0] for row in rows.all()]


async def _get_active_pod_member_ids(
    db: AsyncSession, pod_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    if not pod_ids:
        return set()
    rows = await db.execute(
        select(PodAssignment.member_id).where(
            PodAssignment.pod_id.in_(pod_ids),
            PodAssignment.left_at.is_(None),
        )
    )
    return {row[0] for row in rows.all()}


async def _get_member_active_pod(
    db: AsyncSession, member_id: uuid.UUID
) -> Optional[Tuple[Pod, Optional[Club]]]:
    row = (
        await db.execute(
            select(Pod, Club)
            .join(PodAssignment, PodAssignment.pod_id == Pod.id)
            .outerjoin(Club, Club.id == Pod.club_id)
            .where(
                PodAssignment.member_id == member_id,
                PodAssignment.left_at.is_(None),
                Pod.status == PodStatus.ACTIVE,
            )
            .order_by(PodAssignment.assigned_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


def _is_coach_member(current_user: AuthUser, actor_member: Optional[Member]) -> bool:
    if current_user.has_role("coach"):
        return True
    if actor_member is not None:
        if "coach" in (actor_member.roles or []):
            return True
        if (
            actor_member.coach_profile is not None
            and actor_member.coach_profile.status
            in {
                "active",
                "approved",
            }
        ):
            return True
    return False


async def resolve_verifier_scope(
    db: AsyncSession, current_user: AuthUser
) -> Tuple[Optional[Member], str, Optional[List[uuid.UUID]]]:
    """Return ``(actor_member, role, scoped_pod_ids_or_none)`` or raise 403.

    - Admins & Coaches receive ``scoped_pod_ids = None`` (unrestricted roster).
    - Active Pod Leads / Assistant Pod Leads receive their active led pod IDs.
    """
    actor_member = (
        await db.execute(
            select(Member)
            .where(Member.auth_id == current_user.user_id)
            .options(selectinload(Member.coach_profile))
        )
    ).scalar_one_or_none()

    if is_admin_or_service(current_user):
        return actor_member, "admin", None

    if _is_coach_member(current_user, actor_member):
        return actor_member, "coach", None

    if actor_member is not None:
        led_pod_ids = await _get_led_pod_ids(db, actor_member.id)
        if led_pod_ids:
            return actor_member, "pod_lead", led_pod_ids

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Coach, Admin, or active Pod Lead privileges are required.",
    )


async def assert_can_verify_target(
    db: AsyncSession,
    current_user: AuthUser,
    target_member_id: uuid.UUID,
) -> Tuple[Optional[Member], str]:
    actor_member, role, scoped_pod_ids = await resolve_verifier_scope(db, current_user)
    if scoped_pod_ids is not None:
        allowed_members = await _get_active_pod_member_ids(db, scoped_pod_ids)
        if target_member_id not in allowed_members:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pod Leads can only verify skills and log benchmarks for active members of Pods they lead.",
            )
    return actor_member, role


async def resolve_viewer_audience(
    db: AsyncSession,
    viewer_user: AuthUser,
    target_member: Member,
) -> str:
    """Resolve exact viewer audience for `target_member` or raise 403."""
    if viewer_user.user_id == target_member.auth_id:
        return "self"

    viewer_member = (
        await db.execute(
            select(Member)
            .where(Member.auth_id == viewer_user.user_id)
            .options(selectinload(Member.coach_profile))
        )
    ).scalar_one_or_none()

    if is_admin_or_service(viewer_user) or _is_coach_member(viewer_user, viewer_member):
        return "coach_or_admin"

    if viewer_member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this swimmer's scorecard.",
        )

    led_pod_ids = await _get_led_pod_ids(db, viewer_member.id)
    if led_pod_ids:
        led_members = await _get_active_pod_member_ids(db, led_pod_ids)
        if target_member.id in led_members:
            return "pod_lead"

    # Check if viewer and target share an active Pod.
    viewer_pods = {
        row[0]
        for row in (
            await db.execute(
                select(PodAssignment.pod_id)
                .join(Pod, Pod.id == PodAssignment.pod_id)
                .where(
                    PodAssignment.member_id == viewer_member.id,
                    PodAssignment.left_at.is_(None),
                    Pod.status == PodStatus.ACTIVE,
                )
            )
        ).all()
    }
    if viewer_pods:
        shared = (
            await db.execute(
                select(PodAssignment.id).where(
                    PodAssignment.member_id == target_member.id,
                    PodAssignment.pod_id.in_(viewer_pods),
                    PodAssignment.left_at.is_(None),
                )
            )
        ).first()
        if shared is not None:
            return "pod_mate"

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This swimmer's scorecard is private.",
    )


async def member_has_full_performance_access(
    db: AsyncSession, member: Member, current_user: Optional[AuthUser] = None
) -> Tuple[bool, bool]:
    """Return ``(has_club_access, has_full_performance_access)`` per ADR-001 & ADR-026."""
    has_club = await has_current_club_access(db, member_id=member.id, at=utc_now())
    if has_club:
        return True, True
    if current_user is not None and (
        is_admin_or_service(current_user) or _is_coach_member(current_user, member)
    ):
        return False, True
    if "coach" in (member.roles or []) or "admin" in (member.roles or []):
        return False, True
    if member.coach_profile is not None and member.coach_profile.status in {
        "active",
        "approved",
    }:
        return False, True
    return False, False


# ── Club Readiness & Skill Domain Assembly ────────────────────────────────


async def _load_club_readiness_snapshot(
    db: AsyncSession, member_id: uuid.UUID
) -> ClubReadinessSnapshot:
    row = (
        await db.execute(
            select(ClubReadinessAssessment)
            .join(
                ClubApplication,
                ClubApplication.id == ClubReadinessAssessment.application_id,
            )
            .where(ClubApplication.member_id == member_id)
            .order_by(
                ClubReadinessAssessment.completed_at.desc().nullslast(),
                ClubReadinessAssessment.updated_at.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return ClubReadinessSnapshot(is_club_ready=False)

    is_ready = row.outcome in CLUB_READY_OUTCOMES
    return ClubReadinessSnapshot(
        is_club_ready=is_ready,
        outcome=row.outcome,
        nonstop_distance_m=row.nonstop_distance_m,
        deep_water_comfort=row.deep_water_comfort,
        primary_technique_focus=row.primary_technique_focus,
        first_club_milestone=row.first_club_milestone,
        completed_at=row.completed_at,
    )


def _assemble_skill_domains(
    skill_rows: Sequence[MemberSkillProgress],
    *,
    club_readiness: ClubReadinessSnapshot,
    verifier_names: Dict[uuid.UUID, str],
    include_private_notes: bool,
) -> Tuple[List[ScorecardDomainSection], ScorecardHeadlineStats]:
    row_by_code = {r.skill_code: r for r in skill_rows}

    domains_out: List[ScorecardDomainSection] = []
    total_skills = 0
    verified_skills_total = 0
    confident_total = 0
    pending_total = 0
    safety_gates_total = 0
    safety_gates_cleared = 0
    verified_strokes_unlocked: List[str] = []

    for domain_spec in SKILL_DOMAIN_SPECS:
        domain_skills_out: List[ScorecardSkillItem] = []
        started_count = 0
        confident_or_mastered_count = 0
        verified_count = 0
        pending_review_count = 0

        for spec in SKILLS_BY_DOMAIN.get(domain_spec.code, []):
            total_skills += 1
            if spec.is_safety_gate:
                safety_gates_total += 1

            row = row_by_code.get(spec.code)
            auto_via: Optional[str] = None
            proficiency = row.proficiency if row else "not_started"
            verification_source = row.verification_source if row else "self_logged"
            verification_status = row.verification_status if row else "unverified"
            verified_prof = row.verified_proficiency if row else None
            verified_by_id = row.verified_by_member_id if row else None
            verified_by_role = row.verified_by_role if row else None
            verified_at = row.verified_at if row else None

            # Auto-verify foundational competencies if member passed Club Readiness
            if (
                club_readiness.is_club_ready
                and spec.club_readiness_auto_verify
                and PROFICIENCY_ORDER.get(verified_prof or "not_started", 0)
                < PROFICIENCY_ORDER["confident"]
            ):
                verified_prof = "confident"
                auto_via = "Club Readiness Assessment"
                if (
                    PROFICIENCY_ORDER.get(proficiency, 0)
                    < PROFICIENCY_ORDER["confident"]
                ):
                    proficiency = "confident"
                    verification_source = "coach_verified"
                    verification_status = "verified"
                if verified_at is None:
                    verified_at = club_readiness.completed_at

            is_verified = (
                verified_prof is not None
                and PROFICIENCY_ORDER.get(verified_prof, 0)
                >= PROFICIENCY_ORDER["learning"]
            )
            is_safety_gate_cleared = spec.is_safety_gate and verified_prof in {
                "confident",
                "mastered",
            }

            if proficiency != "not_started":
                started_count += 1
            if proficiency in {"confident", "mastered"}:
                confident_or_mastered_count += 1
                confident_total += 1
            if is_verified:
                verified_count += 1
                verified_skills_total += 1
            if verification_status == "pending_review":
                pending_review_count += 1
                pending_total += 1
            if is_safety_gate_cleared:
                safety_gates_cleared += 1

            domain_skills_out.append(
                ScorecardSkillItem(
                    skill_code=spec.code,
                    domain=spec.domain,  # type: ignore[arg-type]
                    title=spec.title,
                    description=spec.description,
                    ti_focal_point=spec.ti_focal_point,
                    order_index=spec.order_index,
                    is_safety_gate=spec.is_safety_gate,
                    proficiency=proficiency,  # type: ignore[arg-type]
                    verification_source=verification_source,  # type: ignore[arg-type]
                    verification_status=verification_status,  # type: ignore[arg-type]
                    verified_proficiency=verified_prof,  # type: ignore[arg-type]
                    is_verified=is_verified,
                    is_safety_gate_cleared=is_safety_gate_cleared,
                    auto_verified_via=auto_via,
                    verified_by_member_id=verified_by_id,
                    verified_by_name=(
                        verifier_names.get(verified_by_id) if verified_by_id else None
                    ),
                    verified_by_role=verified_by_role,  # type: ignore[arg-type]
                    verified_at=verified_at,
                    member_notes=(
                        row.member_notes if (row and include_private_notes) else None
                    ),
                    verifier_notes=(
                        row.verifier_notes if (row and include_private_notes) else None
                    ),
                    updated_at=row.updated_at if row else None,
                )
            )

        # A stroke domain counts as "unlocked" when >= 50% of its skills are
        # verified at confident/mastered (or at least 2 verified confident/mastered).
        verified_confident_in_domain = sum(
            1
            for item in domain_skills_out
            if item.verified_proficiency in {"confident", "mastered"}
        )
        stroke_unlocked = len(
            domain_skills_out
        ) > 0 and verified_confident_in_domain >= max(
            2, (len(domain_skills_out) + 1) // 2
        )
        if stroke_unlocked and domain_spec.layer == "strokes":
            verified_strokes_unlocked.append(domain_spec.short_title)

        domains_out.append(
            ScorecardDomainSection(
                code=domain_spec.code,  # type: ignore[arg-type]
                title=domain_spec.title,
                short_title=domain_spec.short_title,
                description=domain_spec.description,
                order_index=domain_spec.order_index,
                layer=domain_spec.layer,
                default_expanded_for_beginner=domain_spec.default_expanded_for_beginner,
                total_skills=len(domain_skills_out),
                started_count=started_count,
                confident_or_mastered_count=confident_or_mastered_count,
                verified_count=verified_count,
                pending_review_count=pending_review_count,
                stroke_unlocked=stroke_unlocked,
                skills=domain_skills_out,
            )
        )

    headline = ScorecardHeadlineStats(
        total_skills=total_skills,
        verified_skills_count=verified_skills_total,
        confident_or_mastered_count=confident_total,
        pending_review_count=pending_total,
        safety_gates_cleared=safety_gates_cleared,
        safety_gates_total=safety_gates_total,
        verified_strokes_unlocked=verified_strokes_unlocked,
    )
    return domains_out, headline


async def _enrich_from_sibling_services(
    member: Member,
    *,
    include_strokelab: bool = True,
) -> Tuple[
    StrokeLabSection,
    AcademyProgressSection,
    ConsistencyCommunitySection,
    Dict[str, bool],
]:
    """Fetch cross-service read summaries in parallel with graceful degradation."""
    tasks = [
        fetch_academy_scorecard_summary(member.auth_id),
        fetch_reporting_scorecard_summary(member.auth_id),
        fetch_volunteer_scorecard_summary(str(member.id)),
    ]
    if include_strokelab:
        tasks.append(fetch_strokelab_scorecard_summary(member.auth_id))

    results = await asyncio.gather(*tasks, return_exceptions=False)
    acad_data, acad_ok = results[0]
    rep_data, rep_ok = results[1]
    vol_data, vol_ok = results[2]
    if include_strokelab:
        sl_data, sl_ok = results[3]
    else:
        sl_data, sl_ok = None, False

    # Stroke Lab
    sl_runs: List[StrokeLabRunSummary] = []
    if sl_ok and sl_data:
        for item in sl_data.get("recent_runs") or []:
            dt = _parse_iso_dt(item.get("completed_at"))
            if dt is None:
                continue
            sl_runs.append(
                StrokeLabRunSummary(
                    job_id=str(item.get("job_id", "")),
                    stroke_type=str(item.get("stroke_type", "freestyle")),
                    completed_at=dt,
                    stroke_rate_spm=item.get("stroke_rate_spm"),
                    body_roll_proxy_degrees=item.get("body_roll_proxy_degrees"),
                    breath_balance_left_ratio=item.get("breath_balance_left_ratio"),
                    focus_area=item.get("focus_area"),
                    summary_text=item.get("summary_text"),
                )
            )
    latest_run = sl_runs[0] if sl_runs else None
    strokelab_section = StrokeLabSection(
        available=sl_ok,
        total_analyses=int((sl_data or {}).get("total_analyses", len(sl_runs)))
        if sl_ok
        else 0,
        latest_stroke_rate_spm=latest_run.stroke_rate_spm if latest_run else None,
        latest_body_roll_degrees=latest_run.body_roll_proxy_degrees
        if latest_run
        else None,
        latest_breath_balance_left_ratio=(
            latest_run.breath_balance_left_ratio if latest_run else None
        ),
        latest_focus_area=latest_run.focus_area if latest_run else None,
        latest_summary_text=latest_run.summary_text if latest_run else None,
        latest_completed_at=latest_run.completed_at if latest_run else None,
        recent_runs=sl_runs,
    )

    # Academy
    recent_ms: List[AcademyMilestoneSummaryItem] = []
    if acad_ok and acad_data:
        for m in acad_data.get("recent_milestones") or []:
            recent_ms.append(
                AcademyMilestoneSummaryItem(
                    milestone_name=str(m.get("milestone_name", "")),
                    achieved_at=_parse_iso_dt(m.get("achieved_at")),
                    program_name=m.get("program_name"),
                    cohort_name=m.get("cohort_name"),
                )
            )
    academy_section = AcademyProgressSection(
        available=acad_ok,
        milestones_achieved=int((acad_data or {}).get("milestones_achieved", 0))
        if acad_ok
        else 0,
        milestones_in_progress=int((acad_data or {}).get("milestones_in_progress", 0))
        if acad_ok
        else 0,
        certificates_earned=int((acad_data or {}).get("certificates_earned", 0))
        if acad_ok
        else 0,
        cohorts_completed=int((acad_data or {}).get("cohorts_completed", 0))
        if acad_ok
        else 0,
        active_enrollments=int((acad_data or {}).get("active_enrollments", 0))
        if acad_ok
        else 0,
        recent_milestones=recent_ms,
    )

    # Consistency & Community
    vol_hours = 0.0
    if vol_ok and vol_data and vol_data.get("total_hours") is not None:
        vol_hours = float(vol_data["total_hours"])
    elif rep_ok and rep_data and rep_data.get("volunteer_hours") is not None:
        vol_hours = float(rep_data["volunteer_hours"])

    consistency_section = ConsistencyCommunitySection(
        available=rep_ok,
        volunteer_available=vol_ok,
        total_sessions_attended=int((rep_data or {}).get("total_sessions_attended", 0))
        if rep_ok
        else 0,
        total_pool_hours=float((rep_data or {}).get("total_pool_hours", 0.0))
        if rep_ok
        else 0.0,
        longest_streak_weeks=int((rep_data or {}).get("longest_streak_weeks", 0))
        if rep_ok
        else 0,
        current_streak_weeks=int((rep_data or {}).get("current_streak_weeks", 0))
        if rep_ok
        else 0,
        punctuality_rate=(rep_data or {}).get("punctuality_rate") if rep_ok else None,
        volunteer_hours=round(vol_hours, 1),
        volunteer_tier=(vol_data or {}).get("tier") if vol_ok else None,
        volunteer_recognition_tier=(vol_data or {}).get("recognition_tier")
        if vol_ok
        else None,
        rides_shared=int((rep_data or {}).get("rides_shared", 0)) if rep_ok else 0,
        events_attended=int((rep_data or {}).get("events_attended", 0))
        if rep_ok
        else 0,
        latest_quarter_label=(rep_data or {}).get("latest_quarter_label")
        if rep_ok
        else None,
    )

    availability = {
        "academy": acad_ok,
        "reporting": rep_ok,
        "volunteer": vol_ok,
        "strokelab": sl_ok if include_strokelab else False,
    }
    return strokelab_section, academy_section, consistency_section, availability


async def _resolve_active_programmes(db: AsyncSession, member: Member) -> List[str]:
    membership = member.membership
    if membership is None:
        return []
    club_until = await current_club_enrollment_until(
        db, member_id=member.id, at=utc_now()
    )
    summary = build_membership_status_summary(
        primary_tier=membership.primary_tier,
        active_tiers=membership.active_tiers,
        requested_tiers=membership.requested_tiers,
        community_paid_until=membership.community_paid_until,
        club_paid_until=membership.club_paid_until,
        academy_paid_until=membership.academy_paid_until,
        post_academy_club_until=getattr(membership, "post_academy_club_until", None),
        club_enrollment_until=club_until,
    )
    tier_statuses = summary.get("tier_statuses") or {}
    labels = []
    for key, label in (
        ("community", "Community"),
        ("academy", "Academy"),
        ("club", "Club"),
    ):
        if (tier_statuses.get(key) or {}).get("status") == "active":
            labels.append(label)
    return labels


async def _resolve_verifier_names(
    db: AsyncSession, ids: Iterable[Optional[uuid.UUID]]
) -> Dict[uuid.UUID, str]:
    clean_ids = {i for i in ids if i is not None}
    if not clean_ids:
        return {}
    rows = (
        await db.execute(
            select(Member.id, Member.first_name, Member.last_name).where(
                Member.id.in_(clean_ids)
            )
        )
    ).all()
    return {r[0]: f"{r[1]} {r[2]}".strip() for r in rows}


def _populate_headline_from_pbs(
    headline: ScorecardHeadlineStats,
    personal_bests: Sequence[PersonalBestRecord],
) -> None:
    dist_pbs = [
        pb
        for pb in personal_bests
        if pb.benchmark_type in {"max_distance", "open_water"}
    ]
    if dist_pbs:
        top = max(dist_pbs, key=lambda p: (p.value, 1 if p.is_verified else 0))
        headline.top_distance_m = int(top.value)
        headline.top_distance_stroke = STROKE_LABELS.get(top.stroke, top.stroke.title())
        headline.top_distance_verified = top.is_verified

    spl_pbs = [pb for pb in personal_bests if pb.benchmark_type == "spl"]
    if spl_pbs:
        best_spl = min(spl_pbs, key=lambda p: p.value)
        headline.best_spl = int(best_spl.value)
        headline.best_spl_pool_length_m = best_spl.pool_length_m

    swolf_pbs = [pb for pb in personal_bests if pb.benchmark_type == "swolf"]
    if swolf_pbs:
        best_swolf = min(swolf_pbs, key=lambda p: p.value)
        headline.best_swolf = best_swolf.value
        headline.best_swolf_pool_length_m = best_swolf.pool_length_m

    spm_pbs = [pb for pb in personal_bests if pb.benchmark_type == "stroke_rate"]
    if spm_pbs:
        latest_spm = max(spm_pbs, key=lambda p: p.recorded_at)
        headline.latest_stroke_rate_spm = latest_spm.value
        headline.latest_stroke_rate_source = latest_spm.verification_source


# ── Main Scorecard Builders ───────────────────────────────────────────────


async def build_member_scorecard(
    db: AsyncSession,
    member: Member,
    *,
    viewer_audience: str,
    viewer_user: Optional[AuthUser] = None,
) -> MemberScorecardResponse:
    """Build the full Member Swim Scorecard projected for `viewer_audience`."""
    include_strokelab = viewer_audience in {"self", "coach_or_admin"}
    include_private_notes = viewer_audience in {"self", "coach_or_admin", "pod_lead"}

    skill_rows = (
        (
            await db.execute(
                select(MemberSkillProgress).where(
                    MemberSkillProgress.member_id == member.id
                )
            )
        )
        .scalars()
        .all()
    )
    benchmark_rows = (
        (
            await db.execute(
                select(MemberSwimBenchmark)
                .where(MemberSwimBenchmark.member_id == member.id)
                .order_by(
                    MemberSwimBenchmark.recorded_at.desc(),
                    MemberSwimBenchmark.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    settings_row = await get_or_create_settings(db, member.id)
    club_readiness = await _load_club_readiness_snapshot(db, member.id)
    pod_club = await _get_member_active_pod(db, member.id)
    has_club, has_full_perf = await member_has_full_performance_access(
        db, member, viewer_user if viewer_audience == "self" else None
    )
    active_programmes = await _resolve_active_programmes(db, member)
    profile_photo_url = await resolve_media_url(member.profile_photo_media_id)

    verifier_names = await _resolve_verifier_names(
        db,
        [r.verified_by_member_id for r in skill_rows]
        + [b.verified_by_member_id for b in benchmark_rows],
    )

    (
        strokelab_section,
        academy_section,
        consistency_section,
        availability,
    ) = await _enrich_from_sibling_services(member, include_strokelab=include_strokelab)

    domains, headline = _assemble_skill_domains(
        skill_rows,
        club_readiness=club_readiness,
        verifier_names=verifier_names,
        include_private_notes=include_private_notes,
    )

    personal_bests = compute_personal_bests(
        benchmark_rows,
        club_readiness=club_readiness,
        strokelab=strokelab_section if include_strokelab else None,
        verified_only=False,
    )
    _populate_headline_from_pbs(headline, personal_bests)

    trends = compute_trend_series(
        benchmark_rows,
        strokelab=strokelab_section if include_strokelab else None,
    )

    benchmarks_out = [
        BenchmarkItemResponse(
            id=b.id,
            member_id=b.member_id,
            stroke=b.stroke,  # type: ignore[arg-type]
            benchmark_type=b.benchmark_type,  # type: ignore[arg-type]
            recorded_at=b.recorded_at,
            distance_m=b.distance_m,
            duration_seconds=b.duration_seconds,
            pace_per_100m_seconds=b.pace_per_100m_seconds,
            stroke_count=b.stroke_count,
            stroke_rate_spm=b.stroke_rate_spm,
            swolf_score=b.swolf_score,
            pool_length_m=b.pool_length_m,
            pool_id=b.pool_id,
            pool_name=b.pool_name,
            session_id=b.session_id,
            benchmark_day_label=b.benchmark_day_label,
            verification_source=b.verification_source,  # type: ignore[arg-type]
            verification_status=b.verification_status,  # type: ignore[arg-type]
            verified_by_member_id=b.verified_by_member_id,
            verified_by_name=(
                verifier_names.get(b.verified_by_member_id)
                if b.verified_by_member_id
                else None
            ),
            verified_by_role=b.verified_by_role,  # type: ignore[arg-type]
            verified_at=b.verified_at,
            notes=b.notes if include_private_notes else None,
            verifier_notes=b.verifier_notes if include_private_notes else None,
            created_at=b.created_at,
        )
        for b in benchmark_rows
    ]

    # Hide punctuality rate from Pod Lead (keep operational attendance metrics private)
    if viewer_audience == "pod_lead":
        consistency_section.punctuality_rate = None

    return MemberScorecardResponse(
        member_id=member.id,
        member_name=_full_name(member),
        first_name=member.first_name,
        profile_photo_url=profile_photo_url,
        member_since=member.created_at,
        active_programmes=active_programmes,
        has_club_access=has_club,
        has_full_performance_access=has_full_perf
        or viewer_audience in {"coach_or_admin", "pod_lead"},
        viewer_audience=viewer_audience,  # type: ignore[arg-type]
        can_edit_self=(viewer_audience == "self"),
        can_verify=(viewer_audience in {"coach_or_admin", "pod_lead"}),
        pod_name=pod_club[0].name if pod_club else None,
        club_name=(pod_club[1].name if (pod_club and pod_club[1]) else None),
        club_readiness=club_readiness,
        headline=headline,
        domains=domains,
        personal_bests=personal_bests,
        benchmarks=benchmarks_out,
        trends=trends,
        strokelab=strokelab_section,
        academy=academy_section,
        consistency=consistency_section,
        settings=(
            ScorecardSettingsResponse.model_validate(settings_row)
            if viewer_audience == "self"
            else None
        ),
        source_availability=availability,
    )


async def build_peer_highlight(
    db: AsyncSession,
    member: Member,
    *,
    profile_photo_url: Optional[str] = None,
) -> PeerScorecardHighlightResponse:
    """Build sanitized positive-only peer highlight for Pod-mates (ADR-026)."""
    settings_row = (
        await db.execute(
            select(MemberScorecardSettings).where(
                MemberScorecardSettings.member_id == member.id
            )
        )
    ).scalar_one_or_none()
    pod_visible = settings_row.pod_visibility_enabled if settings_row else True
    show_benchmarks = settings_row.show_benchmarks_to_pod if settings_row else False

    if profile_photo_url is None and member.profile_photo_media_id:
        profile_photo_url = await resolve_media_url(member.profile_photo_media_id)

    if not pod_visible:
        return PeerScorecardHighlightResponse(
            member_id=member.id,
            member_name=_full_name(member),
            first_name=member.first_name,
            profile_photo_url=profile_photo_url,
            pod_visibility_enabled=False,
        )

    skill_rows = (
        (
            await db.execute(
                select(MemberSkillProgress).where(
                    MemberSkillProgress.member_id == member.id
                )
            )
        )
        .scalars()
        .all()
    )
    club_readiness = await _load_club_readiness_snapshot(db, member.id)
    _, headline = _assemble_skill_domains(
        skill_rows,
        club_readiness=club_readiness,
        verifier_names={},
        include_private_notes=False,
    )

    verified_benchmarks: List[PersonalBestRecord] = []
    if show_benchmarks:
        b_rows = (
            (
                await db.execute(
                    select(MemberSwimBenchmark).where(
                        MemberSwimBenchmark.member_id == member.id,
                        MemberSwimBenchmark.verification_source == "coach_verified",
                        MemberSwimBenchmark.verification_status == "verified",
                    )
                )
            )
            .scalars()
            .all()
        )
        verified_benchmarks = compute_personal_bests(
            b_rows, club_readiness=club_readiness, verified_only=True
        )

    vol_data, vol_ok = await fetch_volunteer_scorecard_summary(str(member.id))
    rec_tier = (vol_data or {}).get("recognition_tier") if vol_ok else None

    return PeerScorecardHighlightResponse(
        member_id=member.id,
        member_name=_full_name(member),
        first_name=member.first_name,
        profile_photo_url=profile_photo_url,
        pod_visibility_enabled=True,
        is_club_ready=club_readiness.is_club_ready,
        verified_strokes_unlocked=headline.verified_strokes_unlocked,
        verified_skills_count=headline.verified_skills_count,
        total_skills=headline.total_skills,
        volunteer_recognition_tier=rec_tier,
        verified_benchmarks=verified_benchmarks,
    )


async def build_public_share_card(
    db: AsyncSession, share_token: str
) -> PublicShareScorecardResponse:
    """Return strictly opt-in positive milestone card for a valid share_token."""
    settings_row = (
        await db.execute(
            select(MemberScorecardSettings).where(
                MemberScorecardSettings.share_token == share_token,
                MemberScorecardSettings.public_share_card_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if settings_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared Swim Scorecard not found or sharing is disabled.",
        )

    member = await get_member_by_id_or_404(db, settings_row.member_id)
    if not member.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared Swim Scorecard not found.",
        )

    skill_rows = (
        (
            await db.execute(
                select(MemberSkillProgress).where(
                    MemberSkillProgress.member_id == member.id
                )
            )
        )
        .scalars()
        .all()
    )
    verified_benchmarks = (
        (
            await db.execute(
                select(MemberSwimBenchmark).where(
                    MemberSwimBenchmark.member_id == member.id,
                    MemberSwimBenchmark.verification_source == "coach_verified",
                    MemberSwimBenchmark.verification_status == "verified",
                )
            )
        )
        .scalars()
        .all()
    )

    club_readiness = await _load_club_readiness_snapshot(db, member.id)
    domains, headline = _assemble_skill_domains(
        skill_rows,
        club_readiness=club_readiness,
        verifier_names={},
        include_private_notes=False,
    )
    pbs = compute_personal_bests(
        verified_benchmarks, club_readiness=club_readiness, verified_only=True
    )
    _populate_headline_from_pbs(headline, pbs)

    _, _, consistency, _ = await _enrich_from_sibling_services(
        member, include_strokelab=False
    )
    active_programmes = await _resolve_active_programmes(db, member)
    profile_photo_url = await resolve_media_url(member.profile_photo_media_id)

    # Strictly positive domain highlights (only domains with >= 1 verified skill)
    domain_highlights = [
        {
            "code": d.code,
            "title": d.short_title,
            "verified_count": d.verified_count,
            "total_skills": d.total_skills,
            "unlocked": d.stroke_unlocked,
        }
        for d in domains
        if d.verified_count > 0
    ]

    return PublicShareScorecardResponse(
        display_name=_full_name(member),
        first_name=member.first_name,
        profile_photo_url=profile_photo_url,
        member_since=member.created_at,
        active_programmes=active_programmes,
        is_club_ready=club_readiness.is_club_ready,
        verified_strokes_unlocked=headline.verified_strokes_unlocked,
        verified_skills_count=headline.verified_skills_count,
        total_skills=headline.total_skills,
        safety_gates_cleared=headline.safety_gates_cleared,
        safety_gates_total=headline.safety_gates_total,
        top_verified_distance_m=headline.top_distance_m,
        top_verified_distance_stroke=headline.top_distance_stroke,
        best_verified_spl=headline.best_spl,
        best_verified_spl_pool_length_m=headline.best_spl_pool_length_m,
        best_verified_swolf=headline.best_swolf,
        best_verified_swolf_pool_length_m=headline.best_swolf_pool_length_m,
        total_sessions_attended=(
            consistency.total_sessions_attended if consistency.available else None
        ),
        total_pool_hours=consistency.total_pool_hours
        if consistency.available
        else None,
        volunteer_recognition_tier=consistency.volunteer_recognition_tier,
        domain_highlights=domain_highlights,
    )


# ── Mutations (Self-Logging, Verification, Settings & Benchmark Day) ──────


async def upsert_self_skill(
    db: AsyncSession,
    member: Member,
    skill_code: str,
    *,
    proficiency: str,
    member_notes: Optional[str],
    request_verification: bool,
) -> ScorecardSkillItem:
    spec = get_skill_spec(skill_code)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown skill code: {skill_code}",
        )

    row = (
        await db.execute(
            select(MemberSkillProgress).where(
                MemberSkillProgress.member_id == member.id,
                MemberSkillProgress.skill_code == skill_code,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = MemberSkillProgress(
            member_id=member.id,
            skill_code=spec.code,
            domain=spec.domain,
        )
        db.add(row)

    apply_self_skill_update(
        row,
        proficiency=proficiency,
        member_notes=member_notes,
        request_verification=request_verification,
    )
    await db.commit()
    await db.refresh(row)

    verifier_names = await _resolve_verifier_names(db, [row.verified_by_member_id])
    club_readiness = await _load_club_readiness_snapshot(db, member.id)
    domains, _ = _assemble_skill_domains(
        [row],
        club_readiness=club_readiness,
        verifier_names=verifier_names,
        include_private_notes=True,
    )
    for d in domains:
        for item in d.skills:
            if item.skill_code == skill_code:
                return item
    raise HTTPException(status_code=500, detail="Failed to project updated skill.")


async def verify_member_skill(
    db: AsyncSession,
    target_member: Member,
    skill_code: str,
    *,
    action: str,
    proficiency: Optional[str],
    verifier_member: Optional[Member],
    verifier_role: str,
    verifier_notes: Optional[str],
) -> ScorecardSkillItem:
    spec = get_skill_spec(skill_code)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown skill code: {skill_code}",
        )

    row = (
        await db.execute(
            select(MemberSkillProgress).where(
                MemberSkillProgress.member_id == target_member.id,
                MemberSkillProgress.skill_code == skill_code,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        if action == "reject":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pending skill claim found to reject.",
            )
        row = MemberSkillProgress(
            member_id=target_member.id,
            skill_code=spec.code,
            domain=spec.domain,
            proficiency=proficiency or "confident",
        )
        db.add(row)

    apply_verifier_skill_decision(
        row,
        action=action,
        proficiency=proficiency,
        verifier_member_id=verifier_member.id if verifier_member else None,
        verifier_role=verifier_role,
        verifier_notes=verifier_notes,
    )
    await db.commit()
    await db.refresh(row)

    verifier_names = await _resolve_verifier_names(db, [row.verified_by_member_id])
    club_readiness = await _load_club_readiness_snapshot(db, target_member.id)
    domains, _ = _assemble_skill_domains(
        [row],
        club_readiness=club_readiness,
        verifier_names=verifier_names,
        include_private_notes=True,
    )
    for d in domains:
        for item in d.skills:
            if item.skill_code == skill_code:
                return item
    raise HTTPException(status_code=500, detail="Failed to project verified skill.")


def _build_benchmark_row(
    *,
    member_id: uuid.UUID,
    payload: _BenchmarkFieldsBase,
    verification_source: str,
    verification_status: str,
    verified_by_member_id: Optional[uuid.UUID] = None,
    verified_by_role: Optional[str] = None,
    verified_at: Optional[datetime] = None,
    verifier_notes: Optional[str] = None,
) -> MemberSwimBenchmark:
    pace = compute_pace_per_100m(payload.distance_m, payload.duration_seconds)
    swolf = compute_swolf(
        benchmark_type=payload.benchmark_type,
        duration_seconds=payload.duration_seconds,
        stroke_count=payload.stroke_count,
        explicit_swolf=payload.swolf_score,
        distance_m=payload.distance_m,
        pool_length_m=payload.pool_length_m,
    )
    return MemberSwimBenchmark(
        member_id=member_id,
        stroke=payload.stroke,
        benchmark_type=payload.benchmark_type,
        recorded_at=payload.recorded_at or utc_now(),
        distance_m=payload.distance_m,
        duration_seconds=payload.duration_seconds,
        pace_per_100m_seconds=pace,
        stroke_count=payload.stroke_count,
        stroke_rate_spm=payload.stroke_rate_spm,
        swolf_score=swolf,
        pool_length_m=payload.pool_length_m,
        pool_id=payload.pool_id,
        pool_name=payload.pool_name.strip() if payload.pool_name else None,
        session_id=payload.session_id,
        benchmark_day_label=(
            payload.benchmark_day_label.strip() if payload.benchmark_day_label else None
        ),
        verification_source=verification_source,
        verification_status=verification_status,
        verified_by_member_id=verified_by_member_id,
        verified_by_role=verified_by_role,
        verified_at=verified_at,
        notes=payload.notes.strip() if payload.notes else None,
        verifier_notes=verifier_notes.strip() if verifier_notes else None,
    )


async def create_self_benchmark(
    db: AsyncSession,
    member: Member,
    current_user: AuthUser,
    payload: CreateBenchmarkRequest,
) -> BenchmarkItemResponse:
    _, has_full_perf = await member_has_full_performance_access(
        db, member, current_user
    )
    if payload.benchmark_type in CLUB_ONLY_BENCHMARK_TYPES and not has_full_perf:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Time trials, SPL, Stroke Rate, SWOLF, and Open Water benchmarks are "
                "included with active Club access. You can log Max Continuous Distance "
                "now, or join Club to unlock full performance tracking."
            ),
        )

    row = _build_benchmark_row(
        member_id=member.id,
        payload=payload,
        verification_source="self_logged",
        verification_status="pending_review"
        if payload.request_verification
        else "unverified",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return BenchmarkItemResponse.model_validate(row)


async def delete_self_benchmark(
    db: AsyncSession, member: Member, benchmark_id: uuid.UUID
) -> None:
    row = (
        await db.execute(
            select(MemberSwimBenchmark).where(
                MemberSwimBenchmark.id == benchmark_id,
                MemberSwimBenchmark.member_id == member.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Benchmark record not found.",
        )
    if (
        row.verification_source == "coach_verified"
        and row.verification_status == "verified"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coach- or Pod-verified benchmarks cannot be deleted by members.",
        )
    await db.delete(row)
    await db.commit()


async def create_verified_benchmark(
    db: AsyncSession,
    target_member: Member,
    payload: CreateBenchmarkRequest,
    *,
    verifier_member: Optional[Member],
    verifier_role: str,
    verifier_notes: Optional[str] = None,
) -> BenchmarkItemResponse:
    now = utc_now()
    row = _build_benchmark_row(
        member_id=target_member.id,
        payload=payload,
        verification_source="coach_verified",
        verification_status="verified",
        verified_by_member_id=verifier_member.id if verifier_member else None,
        verified_by_role=verifier_role,
        verified_at=now,
        verifier_notes=verifier_notes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    verifier_names = await _resolve_verifier_names(db, [row.verified_by_member_id])
    resp = BenchmarkItemResponse.model_validate(row)
    resp.verified_by_name = (
        verifier_names.get(row.verified_by_member_id)
        if row.verified_by_member_id
        else None
    )
    return resp


async def review_benchmark(
    db: AsyncSession,
    benchmark_id: uuid.UUID,
    *,
    current_user: AuthUser,
    action: str,
    verifier_notes: Optional[str],
) -> BenchmarkItemResponse:
    row = (
        await db.execute(
            select(MemberSwimBenchmark).where(MemberSwimBenchmark.id == benchmark_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Benchmark record not found.",
        )

    verifier_member, verifier_role = await assert_can_verify_target(
        db, current_user, row.member_id
    )
    now = utc_now()
    if action == "reject":
        if not verifier_notes or not verifier_notes.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feedback notes (verifier_notes) are required when rejecting a benchmark.",
            )
        row.verification_status = "rejected"
        row.verifier_notes = verifier_notes.strip()
        row.verified_by_member_id = verifier_member.id if verifier_member else None
        row.verified_by_role = verifier_role
        row.verified_at = now
    else:
        row.verification_source = "coach_verified"
        row.verification_status = "verified"
        row.verified_by_member_id = verifier_member.id if verifier_member else None
        row.verified_by_role = verifier_role
        row.verified_at = now
        if verifier_notes is not None:
            row.verifier_notes = verifier_notes.strip() or None

    await db.commit()
    await db.refresh(row)
    verifier_names = await _resolve_verifier_names(db, [row.verified_by_member_id])
    resp = BenchmarkItemResponse.model_validate(row)
    resp.verified_by_name = (
        verifier_names.get(row.verified_by_member_id)
        if row.verified_by_member_id
        else None
    )
    return resp


async def record_batch_benchmark_day(
    db: AsyncSession,
    current_user: AuthUser,
    payload: BatchBenchmarkDayRequest,
) -> BatchBenchmarkDayResponse:
    verifier_member, verifier_role, scoped_pod_ids = await resolve_verifier_scope(
        db, current_user
    )
    allowed_members = (
        await _get_active_pod_member_ids(db, scoped_pod_ids)
        if scoped_pod_ids is not None
        else None
    )

    member_ids = {entry.member_id for entry in payload.entries}
    if allowed_members is not None:
        unauthorized = member_ids - allowed_members
        if unauthorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="One or more swimmers are not active members of Pods you lead.",
            )

    existing_ids = {
        row[0]
        for row in (
            await db.execute(select(Member.id).where(Member.id.in_(member_ids)))
        ).all()
    }
    missing = member_ids - existing_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Swimmer(s) not found: {', '.join(str(m) for m in missing)}",
        )

    now = utc_now()
    created_rows: List[MemberSwimBenchmark] = []
    for entry in payload.entries:
        merged = entry.model_copy(
            update={
                "recorded_at": entry.recorded_at or payload.recorded_at or now,
                "session_id": entry.session_id or payload.session_id,
                "pool_id": entry.pool_id or payload.pool_id,
                "pool_name": entry.pool_name or payload.pool_name,
                "pool_length_m": entry.pool_length_m or payload.pool_length_m,
                "benchmark_day_label": (
                    entry.benchmark_day_label or payload.benchmark_day_label
                ),
            }
        )
        # Re-validate merged constraints (e.g. inherited pool_length_m)
        row = _build_benchmark_row(
            member_id=entry.member_id,
            payload=merged,
            verification_source="coach_verified",
            verification_status="verified",
            verified_by_member_id=verifier_member.id if verifier_member else None,
            verified_by_role=verifier_role,
            verified_at=now,
            verifier_notes=entry.verifier_notes,
        )
        db.add(row)
        created_rows.append(row)

    await db.commit()
    for r in created_rows:
        await db.refresh(r)

    verifier_name = _full_name(verifier_member) if verifier_member else None
    out: List[BenchmarkItemResponse] = []
    for r in created_rows:
        item = BenchmarkItemResponse.model_validate(r)
        item.verified_by_name = verifier_name
        out.append(item)

    return BatchBenchmarkDayResponse(
        benchmark_day_label=payload.benchmark_day_label,
        recorded_count=len(out),
        benchmarks=out,
    )


async def update_member_settings(
    db: AsyncSession,
    member: Member,
    payload: UpdateScorecardSettingsRequest,
) -> ScorecardSettingsResponse:
    row = await get_or_create_settings(db, member.id)
    if payload.pod_visibility_enabled is not None:
        row.pod_visibility_enabled = payload.pod_visibility_enabled
    if payload.show_benchmarks_to_pod is not None:
        row.show_benchmarks_to_pod = payload.show_benchmarks_to_pod
    if payload.directory_highlights_enabled is not None:
        row.directory_highlights_enabled = payload.directory_highlights_enabled
    if payload.preferred_pool_length_m is not None:
        row.preferred_pool_length_m = payload.preferred_pool_length_m

    if payload.public_share_card_enabled is not None:
        row.public_share_card_enabled = payload.public_share_card_enabled
        if row.public_share_card_enabled and not row.share_token:
            row.share_token = generate_share_token()

    if payload.rotate_share_token:
        row.share_token = generate_share_token()

    await db.commit()
    await db.refresh(row)
    return ScorecardSettingsResponse.model_validate(row)


# ── Review Queue, Roster & Pod Overview Queries ───────────────────────────


async def get_review_queue(
    db: AsyncSession,
    current_user: AuthUser,
    *,
    pod_id: Optional[uuid.UUID] = None,
) -> ScorecardReviewQueueResponse:
    _, role, scoped_pod_ids = await resolve_verifier_scope(db, current_user)

    filter_member_ids: Optional[set[uuid.UUID]] = None
    if scoped_pod_ids is not None:
        if pod_id is not None and pod_id not in scoped_pod_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not lead the requested pod.",
            )
        target_pods = [pod_id] if pod_id is not None else scoped_pod_ids
        filter_member_ids = await _get_active_pod_member_ids(db, target_pods)
    elif pod_id is not None:
        filter_member_ids = await _get_active_pod_member_ids(db, [pod_id])

    skill_query = (
        select(MemberSkillProgress, Member)
        .join(Member, Member.id == MemberSkillProgress.member_id)
        .where(
            MemberSkillProgress.verification_status == "pending_review",
            Member.is_active.is_(True),
        )
        .order_by(MemberSkillProgress.updated_at.asc())
    )
    bench_query = (
        select(MemberSwimBenchmark, Member)
        .join(Member, Member.id == MemberSwimBenchmark.member_id)
        .where(
            MemberSwimBenchmark.verification_status == "pending_review",
            Member.is_active.is_(True),
        )
        .order_by(MemberSwimBenchmark.created_at.asc())
    )
    if filter_member_ids is not None:
        if not filter_member_ids:
            return ScorecardReviewQueueResponse(
                actor_role=role,  # type: ignore[arg-type]
                scoped_pod_ids=scoped_pod_ids or [],
            )
        skill_query = skill_query.where(
            MemberSkillProgress.member_id.in_(filter_member_ids)
        )
        bench_query = bench_query.where(
            MemberSwimBenchmark.member_id.in_(filter_member_ids)
        )

    skill_pairs = (await db.execute(skill_query)).all()
    bench_pairs = (await db.execute(bench_query)).all()

    all_mids = {m.id for _, m in skill_pairs} | {m.id for _, m in bench_pairs}
    pod_name_by_member: Dict[uuid.UUID, str] = {}
    if all_mids:
        pod_rows = (
            await db.execute(
                select(PodAssignment.member_id, Pod.name)
                .join(Pod, Pod.id == PodAssignment.pod_id)
                .where(
                    PodAssignment.member_id.in_(all_mids),
                    PodAssignment.left_at.is_(None),
                    Pod.status == PodStatus.ACTIVE,
                )
            )
        ).all()
        for mid, pname in pod_rows:
            pod_name_by_member[mid] = pname

    pending_skills = []
    for sp, member in skill_pairs:
        spec = SKILL_BY_CODE.get(sp.skill_code)
        if spec is None:
            continue
        pending_skills.append(
            PendingSkillReviewItem(
                progress_id=sp.id,
                member_id=member.id,
                member_name=_full_name(member),
                pod_name=pod_name_by_member.get(member.id),
                skill_code=sp.skill_code,
                skill_title=spec.title,
                domain=sp.domain,  # type: ignore[arg-type]
                is_safety_gate=spec.is_safety_gate,
                claimed_proficiency=sp.proficiency,  # type: ignore[arg-type]
                verified_proficiency=sp.verified_proficiency,  # type: ignore[arg-type]
                member_notes=sp.member_notes,
                updated_at=sp.updated_at,
            )
        )

    pending_benchmarks = [
        PendingBenchmarkReviewItem(
            benchmark_id=b.id,
            member_id=member.id,
            member_name=_full_name(member),
            pod_name=pod_name_by_member.get(member.id),
            stroke=b.stroke,  # type: ignore[arg-type]
            benchmark_type=b.benchmark_type,  # type: ignore[arg-type]
            recorded_at=b.recorded_at,
            distance_m=b.distance_m,
            duration_seconds=b.duration_seconds,
            pace_per_100m_seconds=b.pace_per_100m_seconds,
            stroke_count=b.stroke_count,
            stroke_rate_spm=b.stroke_rate_spm,
            swolf_score=b.swolf_score,
            pool_length_m=b.pool_length_m,
            pool_name=b.pool_name,
            notes=b.notes,
            created_at=b.created_at,
        )
        for b, member in bench_pairs
    ]

    return ScorecardReviewQueueResponse(
        actor_role=role,  # type: ignore[arg-type]
        scoped_pod_ids=scoped_pod_ids or [],
        pending_skills=pending_skills,
        pending_benchmarks=pending_benchmarks,
    )


async def list_scorecard_roster(
    db: AsyncSession,
    current_user: AuthUser,
    *,
    search: Optional[str] = None,
    pod_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[ScorecardRosterMemberSummary]:
    _, _, scoped_pod_ids = await resolve_verifier_scope(db, current_user)

    filter_member_ids: Optional[set[uuid.UUID]] = None
    if scoped_pod_ids is not None:
        if pod_id is not None and pod_id not in scoped_pod_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not lead the requested pod.",
            )
        target_pods = [pod_id] if pod_id is not None else scoped_pod_ids
        filter_member_ids = await _get_active_pod_member_ids(db, target_pods)
    elif pod_id is not None:
        filter_member_ids = await _get_active_pod_member_ids(db, [pod_id])

    query = select(Member).where(Member.is_active.is_(True))
    if filter_member_ids is not None:
        if not filter_member_ids:
            return []
        query = query.where(Member.id.in_(filter_member_ids))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Member.first_name.ilike(pattern),
                Member.last_name.ilike(pattern),
                Member.email.ilike(pattern),
            )
        )
    query = query.order_by(Member.first_name, Member.last_name).limit(limit)
    members = list((await db.execute(query)).scalars().all())
    if not members:
        return []

    mids = [m.id for m in members]
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    # Batch-fetch skills, benchmarks, pods, and club readiness
    skill_rows = (
        (
            await db.execute(
                select(MemberSkillProgress).where(
                    MemberSkillProgress.member_id.in_(mids)
                )
            )
        )
        .scalars()
        .all()
    )
    skills_by_member: Dict[uuid.UUID, List[MemberSkillProgress]] = {}
    for sp in skill_rows:
        skills_by_member.setdefault(sp.member_id, []).append(sp)

    bench_rows = (
        (
            await db.execute(
                select(MemberSwimBenchmark).where(
                    MemberSwimBenchmark.member_id.in_(mids),
                    MemberSwimBenchmark.verification_status != "rejected",
                )
            )
        )
        .scalars()
        .all()
    )
    benches_by_member: Dict[uuid.UUID, List[MemberSwimBenchmark]] = {}
    for b in bench_rows:
        benches_by_member.setdefault(b.member_id, []).append(b)

    pod_rows = (
        await db.execute(
            select(PodAssignment.member_id, Pod.id, Pod.name)
            .join(Pod, Pod.id == PodAssignment.pod_id)
            .where(
                PodAssignment.member_id.in_(mids),
                PodAssignment.left_at.is_(None),
                Pod.status == PodStatus.ACTIVE,
            )
        )
    ).all()
    pod_by_member = {r[0]: (r[1], r[2]) for r in pod_rows}

    readiness_rows = (
        await db.execute(
            select(ClubApplication.member_id, ClubReadinessAssessment)
            .join(
                ClubReadinessAssessment,
                ClubReadinessAssessment.application_id == ClubApplication.id,
            )
            .where(ClubApplication.member_id.in_(mids))
            .order_by(
                ClubReadinessAssessment.completed_at.desc().nullslast(),
                ClubReadinessAssessment.updated_at.desc(),
            )
        )
    ).all()
    readiness_by_member: Dict[uuid.UUID, ClubReadinessSnapshot] = {}
    for mid, ra in readiness_rows:
        if mid not in readiness_by_member:
            readiness_by_member[mid] = ClubReadinessSnapshot(
                is_club_ready=ra.outcome in CLUB_READY_OUTCOMES,
                outcome=ra.outcome,
                nonstop_distance_m=ra.nonstop_distance_m,
                completed_at=ra.completed_at,
            )

    summaries: List[ScorecardRosterMemberSummary] = []
    now = utc_now()
    for m in members:
        ra = readiness_by_member.get(m.id, ClubReadinessSnapshot(is_club_ready=False))
        m_skills = skills_by_member.get(m.id, [])
        m_benches = benches_by_member.get(m.id, [])
        _, headline = _assemble_skill_domains(
            m_skills,
            club_readiness=ra,
            verifier_names={},
            include_private_notes=False,
        )
        pending_benches = sum(
            1 for b in m_benches if b.verification_status == "pending_review"
        )
        top_dist = max(
            [b.distance_m for b in m_benches if b.distance_m is not None]
            + (
                [ra.nonstop_distance_m]
                if (ra.is_club_ready and ra.nonstop_distance_m)
                else []
            ),
            default=None,
        )
        last_bench = max((b.recorded_at for b in m_benches), default=None)
        pod_info = pod_by_member.get(m.id)
        has_club = await has_current_club_access(db, member_id=m.id, at=now)

        summaries.append(
            ScorecardRosterMemberSummary(
                member_id=m.id,
                member_name=_full_name(m),
                email=m.email,
                profile_photo_url=(
                    url_map.get(str(m.profile_photo_media_id))
                    if m.profile_photo_media_id
                    else None
                ),
                has_club_access=has_club,
                is_club_ready=ra.is_club_ready,
                pod_id=pod_info[0] if pod_info else None,
                pod_name=pod_info[1] if pod_info else None,
                verified_skills_count=headline.verified_skills_count,
                total_skills=headline.total_skills,
                pending_reviews_count=headline.pending_review_count + pending_benches,
                verified_strokes_unlocked=headline.verified_strokes_unlocked,
                top_distance_m=top_dist,
                last_benchmark_at=last_bench,
            )
        )

    return summaries


async def get_pod_scorecard_overview(
    db: AsyncSession,
    current_user: AuthUser,
    pod_id: uuid.UUID,
) -> PodScorecardOverviewResponse:
    pod = (await db.execute(select(Pod).where(Pod.id == pod_id))).scalar_one_or_none()
    if pod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found."
        )

    viewer_member = (
        await db.execute(
            select(Member)
            .where(Member.auth_id == current_user.user_id)
            .options(selectinload(Member.coach_profile))
        )
    ).scalar_one_or_none()

    is_privileged = is_admin_or_service(current_user) or _is_coach_member(
        current_user, viewer_member
    )
    is_pod_lead = (
        viewer_member is not None
        and pod.status == PodStatus.ACTIVE
        and viewer_member.id in {pod.pod_lead_id, pod.assistant_pod_lead_id}
        and await has_current_club_access(db, member_id=viewer_member.id, at=utc_now())
    )
    is_pod_member = False
    if viewer_member is not None:
        membership_row = (
            await db.execute(
                select(PodAssignment.id).where(
                    PodAssignment.pod_id == pod_id,
                    PodAssignment.member_id == viewer_member.id,
                    PodAssignment.left_at.is_(None),
                )
            )
        ).first()
        is_pod_member = membership_row is not None

    if not (is_privileged or is_pod_lead or is_pod_member):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only members of this Pod, Pod Leads, Coaches, or Admins can view Pod scorecards.",
        )

    pod_members = (
        (
            await db.execute(
                select(Member)
                .join(PodAssignment, PodAssignment.member_id == Member.id)
                .where(
                    PodAssignment.pod_id == pod_id,
                    PodAssignment.left_at.is_(None),
                    Member.is_active.is_(True),
                )
                .order_by(Member.first_name, Member.last_name)
            )
        )
        .scalars()
        .all()
    )

    media_ids = [
        m.profile_photo_media_id for m in pod_members if m.profile_photo_media_id
    ]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    highlights = []
    for m in pod_members:
        photo = (
            url_map.get(str(m.profile_photo_media_id))
            if m.profile_photo_media_id
            else None
        )
        highlights.append(await build_peer_highlight(db, m, profile_photo_url=photo))

    return PodScorecardOverviewResponse(
        pod_id=pod.id,
        pod_name=pod.name,
        viewer_can_verify=(is_privileged or is_pod_lead),
        members=highlights,
    )
