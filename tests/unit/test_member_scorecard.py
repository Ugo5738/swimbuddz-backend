"""Unit tests for the Member Swim Scorecard domain, trust model & privacy (ADR-026)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from libs.auth.models import AuthUser
from services.members_service.models.scorecard import (
    MemberScorecardSettings,
    MemberSkillProgress,
    MemberSwimBenchmark,
)
from services.members_service.schemas.scorecard import (
    BatchBenchmarkDayRequest,
    ClubReadinessSnapshot,
    CreateBenchmarkRequest,
    StrokeLabRunSummary,
    StrokeLabSection,
)
from services.members_service.services import scorecard_service
from services.members_service.services.scorecard_catalog import (
    CLUB_ONLY_BENCHMARK_TYPES,
    SKILL_DOMAIN_SPECS,
    SKILL_SPECS,
    serialize_catalog,
)


def _now() -> datetime:
    return datetime(2026, 11, 27, 10, 0, 0, tzinfo=timezone.utc)


def _make_auth_user(
    *,
    user_id: str | None = None,
    email: str = "swimmer@example.com",
    roles: list[str] | None = None,
) -> AuthUser:
    return AuthUser(
        sub=user_id or str(uuid.uuid4()),
        email=email,
        role="authenticated",
        app_metadata={"roles": roles or ["member"]},
    )


# ── Catalog & Math Tests ──────────────────────────────────────────────────


def test_catalog_covers_all_six_domains_with_unique_codes():
    catalog = serialize_catalog()
    domain_codes = [d["code"] for d in catalog["domains"]]
    assert domain_codes == [
        "foundations",
        "freestyle",
        "backstroke",
        "breaststroke",
        "butterfly",
        "advanced_skills",
    ]
    assert len(SKILL_DOMAIN_SPECS) == 6
    all_codes = [s.code for s in SKILL_SPECS]
    assert len(all_codes) == len(set(all_codes))
    assert any(s.is_safety_gate for s in SKILL_SPECS)
    assert "max_distance" not in CLUB_ONLY_BENCHMARK_TYPES
    assert {
        "time_trial",
        "spl",
        "stroke_rate",
        "swolf",
        "open_water",
    } == CLUB_ONLY_BENCHMARK_TYPES


def test_pace_and_swolf_calculations():
    assert scorecard_service.compute_pace_per_100m(50, 55.0) == 110.0
    assert scorecard_service.compute_pace_per_100m(200, 240.0) == 120.0
    assert scorecard_service.compute_pace_per_100m(0, 60.0) is None

    # Single-length SPL with duration auto-computes SWOLF
    assert (
        scorecard_service.compute_swolf(
            benchmark_type="spl",
            duration_seconds=22.5,
            stroke_count=18,
            explicit_swolf=None,
            distance_m=25,
            pool_length_m=25.0,
        )
        == 40.5
    )
    # Multi-length distance does NOT fabricate SWOLF
    assert (
        scorecard_service.compute_swolf(
            benchmark_type="spl",
            duration_seconds=90.0,
            stroke_count=18,
            explicit_swolf=None,
            distance_m=100,
            pool_length_m=25.0,
        )
        is None
    )
    # Explicit SWOLF is respected
    assert (
        scorecard_service.compute_swolf(
            benchmark_type="swolf",
            duration_seconds=None,
            stroke_count=None,
            explicit_swolf=38.0,
            distance_m=None,
            pool_length_m=25.0,
        )
        == 38.0
    )


def test_benchmark_validation_requires_pool_length_for_spl_and_swolf():
    with pytest.raises(ValidationError, match="pool_length_m"):
        CreateBenchmarkRequest(
            stroke="freestyle",
            benchmark_type="spl",
            stroke_count=19,
        )

    with pytest.raises(ValidationError, match="pool_length_m"):
        CreateBenchmarkRequest(
            stroke="freestyle",
            benchmark_type="swolf",
            duration_seconds=23.0,
            stroke_count=19,
        )

    # Batch benchmark day inherits top-level pool_length_m cleanly
    batch = BatchBenchmarkDayRequest(
        benchmark_day_label="Q4 Yaba Benchmark Day",
        pool_length_m=25.0,
        entries=[
            {
                "member_id": uuid.uuid4(),
                "stroke": "freestyle",
                "benchmark_type": "spl",
                "stroke_count": 18,
                "duration_seconds": 22.0,
            }
        ],
    )
    assert len(batch.entries) == 1

    # Batch benchmark day still rejects SPL when neither batch nor entry has pool_length_m
    with pytest.raises(ValidationError, match="pool_length_m"):
        BatchBenchmarkDayRequest(
            benchmark_day_label="Missing Pool Length",
            entries=[
                {
                    "member_id": uuid.uuid4(),
                    "stroke": "freestyle",
                    "benchmark_type": "spl",
                    "stroke_count": 18,
                }
            ],
        )


# ── 3-Tier Trust Model & Safety Gate Tests ────────────────────────────────


def test_self_update_preserves_coach_verified_baseline():
    verifier_id = uuid.uuid4()
    row = MemberSkillProgress(
        member_id=uuid.uuid4(),
        skill_code="free_side_breathing",
        domain="freestyle",
        proficiency="confident",
        verified_proficiency="confident",
        verification_source="coach_verified",
        verification_status="verified",
        verified_by_member_id=verifier_id,
        verified_by_role="coach",
        verified_at=_now(),
    )

    # Attempting to self-set a lower level never erases coach verification
    scorecard_service.apply_self_skill_update(
        row,
        proficiency="learning",
        member_notes="Felt rusty today",
        request_verification=False,
        now=_now(),
    )
    assert row.proficiency == "confident"
    assert row.verified_proficiency == "confident"
    assert row.verification_source == "coach_verified"
    assert row.verification_status == "verified"
    assert row.member_notes == "Felt rusty today"

    # Claiming higher proficiency keeps verified_proficiency intact and enters review
    scorecard_service.apply_self_skill_update(
        row,
        proficiency="mastered",
        member_notes="Ready for coach check",
        request_verification=True,
        now=_now(),
    )
    assert row.proficiency == "mastered"
    assert row.verified_proficiency == "confident"
    assert row.verification_source == "self_logged"
    assert row.verification_status == "pending_review"
    assert row.verified_by_member_id == verifier_id


def test_verifier_reject_requires_notes_and_restores_verified_baseline():
    row = MemberSkillProgress(
        member_id=uuid.uuid4(),
        skill_code="free_side_breathing",
        domain="freestyle",
        proficiency="mastered",
        verified_proficiency="confident",
        verification_source="self_logged",
        verification_status="pending_review",
    )

    with pytest.raises(HTTPException) as exc:
        scorecard_service.apply_verifier_skill_decision(
            row,
            action="reject",
            proficiency=None,
            verifier_member_id=uuid.uuid4(),
            verifier_role="coach",
            verifier_notes="   ",
            now=_now(),
        )
    assert exc.value.status_code == 400

    scorecard_service.apply_verifier_skill_decision(
        row,
        action="reject",
        proficiency=None,
        verifier_member_id=uuid.uuid4(),
        verifier_role="coach",
        verifier_notes="Keep one goggle lens in water during side breath.",
        now=_now(),
    )
    assert row.verification_status == "rejected"
    assert row.proficiency == "confident"
    assert row.verified_proficiency == "confident"
    assert "goggle lens" in (row.verifier_notes or "")


def test_safety_gate_never_clears_from_self_logged_only_coach_or_club_readiness():
    member_id = uuid.uuid4()
    self_claim = MemberSkillProgress(
        member_id=member_id,
        skill_code="fnd_deep_water_tread",
        domain="foundations",
        proficiency="mastered",
        verified_proficiency=None,
        verification_source="self_logged",
        verification_status="unverified",
    )

    domains_unverified, headline_unverified = scorecard_service._assemble_skill_domains(
        [self_claim],
        club_readiness=ClubReadinessSnapshot(is_club_ready=False),
        verifier_names={},
        include_private_notes=True,
    )
    tread_item = next(
        s
        for d in domains_unverified
        if d.code == "foundations"
        for s in d.skills
        if s.skill_code == "fnd_deep_water_tread"
    )
    assert tread_item.is_safety_gate is True
    assert tread_item.is_safety_gate_cleared is False
    assert headline_unverified.safety_gates_cleared == 0

    # Once Club Readiness passes, foundational gates auto-verify
    domains_ready, headline_ready = scorecard_service._assemble_skill_domains(
        [],
        club_readiness=ClubReadinessSnapshot(
            is_club_ready=True,
            outcome="club_ready",
            nonstop_distance_m=100,
            completed_at=_now(),
        ),
        verifier_names={},
        include_private_notes=True,
    )
    tread_ready = next(
        s
        for d in domains_ready
        if d.code == "foundations"
        for s in d.skills
        if s.skill_code == "fnd_deep_water_tread"
    )
    assert tread_ready.is_safety_gate_cleared is True
    assert tread_ready.auto_verified_via == "Club Readiness Assessment"
    assert headline_ready.safety_gates_cleared == 2  # tread + 25m survival


# ── Personal Bests, Pool-Length Isolation & Trends ────────────────────────


def test_personal_bests_isolate_pool_lengths_and_merge_strokelab():
    mid = uuid.uuid4()
    t0 = _now() - timedelta(days=30)
    t1 = _now() - timedelta(days=10)
    t2 = _now()

    b_spl_15m = MemberSwimBenchmark(
        id=uuid.uuid4(),
        member_id=mid,
        stroke="freestyle",
        benchmark_type="spl",
        recorded_at=t0,
        stroke_count=12,
        duration_seconds=14.0,
        swolf_score=26.0,
        pool_length_m=15.0,
        pool_name="15m Hotel Pool",
        verification_source="self_logged",
        verification_status="unverified",
    )
    b_spl_25m = MemberSwimBenchmark(
        id=uuid.uuid4(),
        member_id=mid,
        stroke="freestyle",
        benchmark_type="spl",
        recorded_at=t1,
        stroke_count=18,
        duration_seconds=22.0,
        swolf_score=40.0,
        pool_length_m=25.0,
        pool_name="Rowe Park",
        verification_source="coach_verified",
        verification_status="verified",
    )
    b_rejected = MemberSwimBenchmark(
        id=uuid.uuid4(),
        member_id=mid,
        stroke="freestyle",
        benchmark_type="spl",
        recorded_at=t2,
        stroke_count=9,
        pool_length_m=25.0,
        verification_source="self_logged",
        verification_status="rejected",
    )

    sl_section = StrokeLabSection(
        available=True,
        total_analyses=1,
        latest_stroke_rate_spm=54.2,
        recent_runs=[
            StrokeLabRunSummary(
                job_id=str(uuid.uuid4()),
                stroke_type="freestyle",
                completed_at=t2,
                stroke_rate_spm=54.2,
            )
        ],
    )

    all_pbs = scorecard_service.compute_personal_bests(
        [b_spl_15m, b_spl_25m, b_rejected], strokelab=sl_section, verified_only=False
    )
    spl_pbs = [pb for pb in all_pbs if pb.benchmark_type == "spl"]
    # 15m and 25m SPL are preserved separately; rejected 9 SPL is ignored
    assert {(pb.pool_length_m, pb.value) for pb in spl_pbs} == {
        (15.0, 12.0),
        (25.0, 18.0),
    }

    spm_pbs = [pb for pb in all_pbs if pb.benchmark_type == "stroke_rate"]
    assert len(spm_pbs) == 1
    assert spm_pbs[0].verification_source == "strokelab_ai"
    assert spm_pbs[0].value == 54.2

    # Verified-only PBs (used for Peer/Public cards) exclude self-logged & AI
    verified_pbs = scorecard_service.compute_personal_bests(
        [b_spl_15m, b_spl_25m, b_rejected], strokelab=sl_section, verified_only=True
    )
    assert all(pb.is_verified for pb in verified_pbs)
    assert all(pb.pool_length_m == 25.0 for pb in verified_pbs)

    trends = scorecard_service.compute_trend_series(
        [b_spl_15m, b_spl_25m, b_rejected], strokelab=sl_section
    )
    spm_series = next(s for s in trends if s.metric_key == "stroke_rate")
    assert len(spm_series.points) == 1
    assert spm_series.points[0].verification_source == "strokelab_ai"


# ── Progressive Club Gating & Delete Protection Tests ─────────────────────


@pytest.mark.asyncio
async def test_progressive_club_gating_on_self_benchmarks(monkeypatch):
    member = SimpleNamespace(
        id=uuid.uuid4(),
        auth_id=str(uuid.uuid4()),
        roles=["member"],
        coach_profile=None,
    )
    user = _make_auth_user(user_id=member.auth_id)
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def _fake_refresh(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = _now()

    db.refresh = AsyncMock(side_effect=_fake_refresh)

    # Non-Club member cannot log SPL
    monkeypatch.setattr(
        scorecard_service, "has_current_club_access", AsyncMock(return_value=False)
    )
    with pytest.raises(HTTPException) as exc:
        await scorecard_service.create_self_benchmark(
            db,
            member,  # type: ignore[arg-type]
            user,
            CreateBenchmarkRequest(
                stroke="freestyle",
                benchmark_type="spl",
                stroke_count=20,
                pool_length_m=25.0,
            ),
        )
    assert exc.value.status_code == 403

    # Non-Club member CAN log max_distance
    created = await scorecard_service.create_self_benchmark(
        db,
        member,  # type: ignore[arg-type]
        user,
        CreateBenchmarkRequest(
            stroke="freestyle",
            benchmark_type="max_distance",
            distance_m=100,
        ),
    )
    assert created.distance_m == 100
    assert created.verification_source == "self_logged"

    # Active Club member CAN log SPL and auto-computes SWOLF
    monkeypatch.setattr(
        scorecard_service, "has_current_club_access", AsyncMock(return_value=True)
    )
    spl_created = await scorecard_service.create_self_benchmark(
        db,
        member,  # type: ignore[arg-type]
        user,
        CreateBenchmarkRequest(
            stroke="freestyle",
            benchmark_type="spl",
            stroke_count=18,
            duration_seconds=21.5,
            pool_length_m=25.0,
        ),
    )
    assert spl_created.swolf_score == 39.5


@pytest.mark.asyncio
async def test_member_cannot_delete_coach_verified_benchmark():
    member = SimpleNamespace(id=uuid.uuid4())
    verified_row = MemberSwimBenchmark(
        id=uuid.uuid4(),
        member_id=member.id,
        stroke="freestyle",
        benchmark_type="max_distance",
        recorded_at=_now(),
        distance_m=400,
        verification_source="coach_verified",
        verification_status="verified",
    )
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = verified_row
    db = MagicMock()
    db.execute = AsyncMock(return_value=exec_result)

    with pytest.raises(HTTPException) as exc:
        await scorecard_service.delete_self_benchmark(db, member, verified_row.id)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pod_lead_scoping_blocks_unrelated_swimmer(monkeypatch):
    lead_id = uuid.uuid4()
    own_pod_id = uuid.uuid4()
    pod_member_id = uuid.uuid4()
    outsider_id = uuid.uuid4()

    lead_member = SimpleNamespace(id=lead_id, roles=["member"], coach_profile=None)
    user = _make_auth_user(user_id=str(uuid.uuid4()), roles=["member"])

    monkeypatch.setattr(
        scorecard_service,
        "resolve_verifier_scope",
        AsyncMock(return_value=(lead_member, "pod_lead", [own_pod_id])),
    )
    monkeypatch.setattr(
        scorecard_service,
        "_get_active_pod_member_ids",
        AsyncMock(return_value={pod_member_id}),
    )

    actor, role = await scorecard_service.assert_can_verify_target(
        MagicMock(), user, pod_member_id
    )
    assert actor is lead_member
    assert role == "pod_lead"

    with pytest.raises(HTTPException) as exc:
        await scorecard_service.assert_can_verify_target(MagicMock(), user, outsider_id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_peer_highlight_hides_stats_when_pod_visibility_disabled():
    member = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Ada",
        last_name="Obi",
        profile_photo_media_id=None,
    )
    settings_row = MemberScorecardSettings(
        member_id=member.id,
        pod_visibility_enabled=False,
        show_benchmarks_to_pod=True,
    )
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = settings_row
    db = MagicMock()
    db.execute = AsyncMock(return_value=exec_result)

    peer = await scorecard_service.build_peer_highlight(db, member)  # type: ignore[arg-type]
    assert peer.pod_visibility_enabled is False
    assert peer.verified_strokes_unlocked == []
    assert peer.verified_benchmarks == []
    assert peer.verified_skills_count == 0


# ── Alembic Single-Head, Public Share & Service Wiring Tests ──────────────


def test_members_service_alembic_single_head():
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "services" / "members_service" / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(repo_root / "services" / "members_service" / "alembic")
    )
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == [
        "b20261127score"
    ], f"Expected single head b20261127score, got {heads}"


def test_scorecard_routes_mounted_across_members_and_gateway():
    from services.gateway_service.app.main import create_app as create_gateway_app
    from services.members_service.app.main import create_app as create_members_app

    members_app = create_members_app()
    member_paths = {getattr(r, "path", "") for r in members_app.routes}
    for expected in (
        "/scorecards/catalog",
        "/scorecards/me",
        "/scorecards/me/skills/{skill_code}",
        "/scorecards/me/benchmarks",
        "/scorecards/me/benchmarks/{benchmark_id}",
        "/scorecards/me/settings",
        "/scorecards/public/{share_token}",
        "/scorecards/members/{member_id}",
        "/scorecards/pod/{pod_id}",
        "/scorecards/review-queue",
        "/scorecards/roster",
        "/scorecards/benchmark-day/batch",
        "/scorecards/benchmarks/{benchmark_id}/review",
        "/scorecards/members/{member_id}/skills/{skill_code}/verify",
        "/scorecards/members/{member_id}/benchmarks",
    ):
        assert expected in member_paths, f"Missing route {expected} on members_service"

    gateway_app = create_gateway_app()
    gateway_paths = {getattr(r, "path", "") for r in gateway_app.routes}
    assert "/api/v1/scorecards/{path:path}" in gateway_paths


@pytest.mark.asyncio
async def test_public_share_card_404_when_disabled():
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=exec_result)

    with pytest.raises(HTTPException) as exc:
        await scorecard_service.build_public_share_card(db, "invalid-or-disabled-token")
    assert exc.value.status_code == 404


def test_catalog_endpoint_via_http():
    from fastapi.testclient import TestClient
    from services.members_service.app.main import create_app as create_members_app

    client = TestClient(create_members_app())
    resp = client.get("/scorecards/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["domains"]) == 6
    assert len(body["benchmark_types"]) == 6
    assert len(body["proficiencies"]) == 4
