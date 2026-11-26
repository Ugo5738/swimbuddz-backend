"""No-API regression tests for Stroke Lab v2 reliability & model modernization (ADR-013).

Covers:
  - Gemini 3+ explicit video_metadata.fps + media_resolution + thinking headroom
  - Resilient JSON extraction when thinking models leak preamble text
  - Conservative fallback cost estimation for late-2026 models (cost circuit-breaker safety)
  - Graded 3-tier gate (profile_quality + catastrophic_flags + legacy cache compat)
  - Runtime evidence verifier (closed enums, ADR-013 bans, overhead body-line suppression)
  - Cross-stroke consensus, hybrid chunk video + key stills, and selective adjudication
  - Deterministic water-safe TI drill grounding (rejecting hypoxic / pull-harder advice)
  - Apache-2.0 RTMPose engine resolution & peak counting
  - Deterministic $0 longitudinal swim comparison
  - Coach-labeled multi-repeat bake-off scoring engine
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from services.ai_service.analysis.drills import (
    is_safe_drill_text,
    resolve_ti_drill,
)
from services.ai_service.coach.coach import GateVerdict
from services.ai_service.coach.frames import Frame
from services.ai_service.coach.pose import _resolve_pose_engine, count_recoveries
from services.ai_service.pipeline.compare import compare_runs
from services.ai_service.pipeline.components.aggregator import AggregatorComponent
from services.ai_service.pipeline.components.chunk_coach import ChunkCoachComponent
from services.ai_service.pipeline.components.gate import GateComponent, _tier
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    Finding,
    FrameRef,
    GateTier,
    InputProfile,
    Instance,
    Phase,
    PipelineConfig,
    RunContext,
)
from services.ai_service.pipeline.verify import (
    adjudicate_uncertain_finding,
    check_honesty_violations,
    compute_cross_stroke_consensus,
    verify_chunk_finding,
)
from services.ai_service.providers import base
from services.ai_service.validation.bakeoff_eval import (
    ClipGroundTruth,
    CoachAnnotation,
    RunObservation,
    compute_inter_rater_agreement,
    score_candidate,
)


def _strip(n: int = 8, dt: float = 0.25) -> list[Frame]:
    return [
        Frame(index=i, timestamp_s=round(i * dt, 3), jpeg=f"jpg-{i}".encode())
        for i in range(n)
    ]


# ── 1. Provider layer: FPS, thinking headroom, JSON recovery, fallback pricing ──


def test_provider_from_model_recognizes_o4_and_2026_models():
    assert base._provider_from_model("o4-mini") == "openai"
    assert base._provider_from_model("gpt-5.4-mini") == "openai"
    assert base._provider_from_model("gpt-5.6") == "openai"
    assert base._provider_from_model("gemini/gemini-3.8-flash") == "google"
    assert base._provider_from_model("gemini/gemini-3.5-flash-lite") == "google"


def test_parse_json_recovers_from_thinking_preamble_and_fences():
    r1 = base.AIProviderResponse(
        content='Here is my analysis:\n{"aspects": [{"aspect": "body_line"}]}\nDone.',
        model="gemini/gemini-3.8-flash",
        provider="google",
    )
    assert r1.parse_json() == {"aspects": [{"aspect": "body_line"}]}

    r2 = base.AIProviderResponse(
        content='```json\n{"ok": true}\n```',
        model="gpt-4o",
        provider="openai",
    )
    assert r2.parse_json() == {"ok": True}


def test_fallback_cost_estimation_covers_late_2026_models():
    # Post-promo Jan 2027 standard rates for Gemini 3.8 Flash: $1.50 in / $7.50 out
    cost = base.estimate_model_cost_usd("gemini/gemini-3.8-flash", 10_000, 1_000)
    assert cost == pytest.approx(0.015 + 0.0075, rel=1e-4)

    # Lean Gemini 3.5 Flash-Lite: $0.30 in / $2.50 out
    lite = base.estimate_model_cost_usd("gemini/gemini-3.5-flash-lite", 10_000, 1_000)
    assert lite == pytest.approx(0.003 + 0.0025, rel=1e-4)

    # Unknown new model with non-zero tokens still produces a conservative non-zero cost
    unk = base.estimate_model_cost_usd("future/model-x", 10_000, 1_000)
    assert unk > 0.0


def test_call_vlm_passes_gemini3_video_fps_thinking_headroom_and_estimates_cost(
    monkeypatch,
):
    captured: dict = {}

    class FakeResp:
        choices = [SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        usage = SimpleNamespace(prompt_tokens=16_000, completion_tokens=500)

        def model_dump(self):
            return {"ok": True}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeResp()

    fake_litellm = SimpleNamespace(
        drop_params=False,
        acompletion=fake_acompletion,
        completion_cost=lambda **kw: 0.0,  # simulate missing LiteLLM price entry
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(
        base,
        "get_settings",
        lambda: SimpleNamespace(
            GEMINI_API_KEY="gem-key",
            OPENAI_API_KEY="oa-key",
            AI_VISION_MODEL="",
            AI_DEFAULT_MODEL="gpt-4o",
            STROKELAB_MAX_MODEL_CALLS_PER_ATTEMPT=10,
            STROKELAB_MAX_MODEL_COST_PER_ATTEMPT_USD=0.50,
        ),
    )

    token = base.start_vlm_usage_capture()
    try:
        resp = asyncio.run(
            base.call_vlm(
                system_prompt="sys",
                user_prompt="usr",
                images=[b"img1"],
                video=b"mp4bytes",
                video_fps=4.0,
                video_detail="medium",
                reasoning_effort="low",
                model="gemini/gemini-3.8-flash",
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
        )
    finally:
        events = base.stop_vlm_usage_capture(token)

    # Thinking-model headroom bumped 1200 -> 4096 and reasoning_effort forwarded
    assert captured["max_tokens"] == 4096
    assert captured["reasoning_effort"] == "low"
    user_parts = captured["messages"][1]["content"]
    file_part = next(p for p in user_parts if p["type"] == "file")
    assert file_part["file"]["format"] == "video/mp4"
    assert file_part["file"]["video_metadata"] == {"fps": 4.0}
    assert file_part["file"]["detail"] == "medium"
    # Fallback cost estimator prevented $0.00 blind spot
    assert resp.cost_usd > 0.02
    success_ev = next(e for e in events if e["kind"] == "success")
    assert success_ev["cost_estimated"] is True
    assert success_ev["cost_usd"] == resp.cost_usd


def test_litellm_gemini3_wire_transform_preserves_video_fps_and_resolution():
    """Verify against the installed LiteLLM library that our file block actually
    serializes to Gemini's wire video_metadata.fps + media_resolution fields."""
    from litellm.llms.gemini.chat.transformation import GoogleAIStudioGeminiConfig

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this stroke."},
                {
                    "type": "file",
                    "file": {
                        "file_data": "data:video/mp4;base64,AAAA",
                        "format": "video/mp4",
                        "video_metadata": {"fps": 4.0},
                        "detail": "medium",
                    },
                },
            ],
        }
    ]
    transformed = GoogleAIStudioGeminiConfig()._transform_messages(
        messages=messages, model="gemini-3.8-flash"
    )
    parts = transformed[0]["parts"]
    video_part = next(p for p in parts if "inline_data" in p)
    assert video_part["inline_data"]["mime_type"] == "video/mp4"
    assert video_part["video_metadata"] == {"fps": 4.0}
    assert video_part["media_resolution"] == {"level": "MEDIA_RESOLUTION_MEDIUM"}


# ── 2. Graded 3-tier gate ─────────────────────────────────────────────────────


def test_graded_gate_tier_handles_profile_quality_and_catastrophic_flags():
    # Partial side-on profile is downgraded to BORDERLINE (not CLEAN)
    v_partial = GateVerdict(
        usable=True,
        view="side-on",
        stroke="freestyle",
        swimmer_count=1,
        agreement=1.0,
        n_votes=3,
        n_valid=3,
        profile_quality="partial",
    )
    assert _tier(v_partial) == GateTier.BORDERLINE

    # Clean side-on profile stays CLEAN
    v_clean = GateVerdict(
        usable=True,
        view="side-on",
        stroke="freestyle",
        swimmer_count=1,
        agreement=1.0,
        n_votes=3,
        n_valid=3,
        profile_quality="clean",
    )
    assert _tier(v_clean) == GateTier.CLEAN

    # Catastrophic flag forces REFUSE
    v_cat = GateVerdict(
        usable=False,
        view="side-on",
        stroke="freestyle",
        swimmer_count=0,
        agreement=1.0,
        n_votes=3,
        n_valid=3,
        profile_quality="poor",
        catastrophic_flags=["no_swimmer"],
    )
    assert _tier(v_cat) == GateTier.REFUSE


def test_gate_component_replays_legacy_binary_cache_cleanly():
    legacy_cache = {
        "gate": {
            "usable": True,
            "view": "side-on",
            "stroke": "freestyle",
            "swimmer_count": 1,
            "agreement": 1.0,
            "n_votes": 3,
            "n_valid": 3,
            "votes": [],
            "cost_usd": 0.005,
            "latency_ms": 200,
            "model": "o4-mini",
        }
    }
    ctx = RunContext(frames=_strip(3), cache=legacy_cache)
    res = asyncio.run(GateComponent().run(ctx))
    assert res.meta["tier"] == GateTier.CLEAN
    assert res.cost_usd == 0.0


# ── 3. Runtime verifier, hybrid chunk coach & selective adjudicator ───────────


def test_runtime_verifier_enforces_adr_013_and_overhead_visibility():
    # Overhead cannot judge hip/leg sink
    f_overhead_sink = Finding(
        component="chunk_coach",
        observation="Your hips sink low in the water.",
        severity=SEVERITY_FIX,
        area="body_line",
        confidence=0.85,
        extra={"verdict": "hips_low"},
    )
    v, reason = verify_chunk_finding(f_overhead_sink, view="overhead")
    assert v is None and reason == "overhead_body_line_sink_not_visible"

    # Side-on crossover claim is rejected
    f_crossover = Finding(
        component="chunk_coach",
        observation="Your right hand crosses the midline on entry.",
        severity=SEVERITY_FIX,
        area="entry_reach",
        confidence=0.8,
        extra={"verdict": "overreach"},
    )
    v2, reason2 = verify_chunk_finding(f_crossover, view="side-on")
    assert v2 is None and "side_view_crossover" in (reason2 or "")

    # Banned numbers & underwater catch faults are caught
    assert "banned_numbers" in check_honesty_violations("Your stroke rate is 62 SPM.")
    assert "invisible_underwater_aspect" in check_honesty_violations(
        "Your early vertical forearm drops during the catch phase.",
        profile=InputProfile.SIDE_ON_ABOVE,
    )

    # Invalid closed-enum verdict is rejected
    f_bad_enum = Finding(
        component="chunk_coach",
        observation="Weird elbow",
        severity=SEVERITY_FIX,
        area="recovery_elbow",
        confidence=0.8,
        extra={"verdict": "broken"},
    )
    v3, reason3 = verify_chunk_finding(f_bad_enum, view="side-on")
    assert v3 is None and (reason3 or "").startswith("invalid_verdict:")


def test_chunk_coach_sends_hybrid_stills_and_video_fps(monkeypatch):
    seen_calls: list[dict] = []

    class Resp:
        model = "gemini/gemini-3.8-flash"
        provider = "google"
        output_tokens = 200
        cost_usd = 0.015
        content = (
            '{"aspects":[{"aspect":"head_breath","visible":true,"verdict":"lifted",'
            '"note":"Your head lifts forward before you breathe.","confidence":0.85},'
            '{"aspect":"entry_reach","visible":true,"verdict":"overreach",'
            '"note":"Your hand crosses the midline.","confidence":0.8}]}'
        )

        def parse_json(self):
            return base.AIProviderResponse.parse_json(self)

    async def fake_call_vlm(**kw):
        seen_calls.append(kw)
        return Resp()

    monkeypatch.setattr("services.ai_service.providers.base.call_vlm", fake_call_vlm)
    monkeypatch.setattr(
        ChunkCoachComponent, "_read_chunk", lambda self, ctx, inst: b"fake-mp4"
    )

    ctx = RunContext(
        frames=_strip(6),
        strip=_strip(8),
        cache={},
        video_path="/tmp/fake.mp4",
        config=PipelineConfig(
            coach_model="gemini/gemini-3.8-flash",
            coach_video=True,
            coach_video_fps=4.0,
            coach_video_detail="medium",
            coach_chunk_hybrid_stills=True,
            coach_reasoning_effort="low",
        ),
    )
    ctx.gate = SimpleNamespace(view="side-on")
    ctx.instances = [Instance(Phase.RECOVERY, 0, 0.25, 1.0, 0.5, arm="near")]

    res = asyncio.run(ChunkCoachComponent().run(ctx))
    assert len(seen_calls) == 1
    call = seen_calls[0]
    assert call["video"] == b"fake-mp4"
    assert call["video_fps"] == 4.0
    assert call["video_detail"] == "medium"
    assert call["reasoning_effort"] == "low"
    assert 1 <= len(call["images"]) <= 2  # hybrid high-res key stills attached!
    assert call["image_detail"] == "high"
    # Crossover finding was deterministically dropped by verifier; head_breath kept
    assert len(res.findings) == 1
    assert res.findings[0].area == "head_breath"
    assert res.meta["verified_dropped"] == 1
    assert res.meta["chunk_diagnostics"][0]["used_hybrid_stills"] is True


def test_cross_stroke_consensus_safe_drills_and_selective_adjudication(monkeypatch):
    strip = _strip(8)
    ctx = RunContext(
        frames=strip,
        strip=strip,
        config=PipelineConfig(
            coach_model="gemini/gemini-3.8-flash",
            adjudicator_model="gpt-5.4-mini",
        ),
    )
    ctx.gate = SimpleNamespace(view="side-on")
    # head_breath seen on 2/3 strokes (consistent); recovery_elbow seen on only 1/3 (uncertain!)
    ctx.run_findings = [
        Finding(
            component="chunk_coach",
            observation="Your head lifts before breathing.",
            severity=SEVERITY_FIX,
            area="head_breath",
            confidence=0.85,
            instance_id=1,
            evidence_frames=[FrameRef(1, 0.25)],
            extra={"verdict": "lifted", "t": 0.25},
        ),
        Finding(
            component="chunk_coach",
            observation="Your head lifts again on the second stroke.",
            severity=SEVERITY_FIX,
            area="head_breath",
            confidence=0.82,
            instance_id=2,
            evidence_frames=[FrameRef(4, 1.0)],
            extra={"verdict": "lifted", "t": 1.0},
        ),
        Finding(
            component="chunk_coach",
            observation="Your elbow looks dropped on one recovery.",
            severity=SEVERITY_FIX,
            area="recovery_elbow",
            confidence=0.78,
            instance_id=3,
            evidence_frames=[FrameRef(6, 1.5)],
            extra={"verdict": "dropped", "t": 1.5},
        ),
    ]

    consensus = compute_cross_stroke_consensus(ctx.run_findings)
    assert consensus["head_breath"].consistent is True
    assert consensus["head_breath"].needs_adjudication is False
    assert consensus["recovery_elbow"].single_stroke_only is True
    assert consensus["recovery_elbow"].needs_adjudication is True

    class AggResp:
        model = "gemini/gemini-3.8-flash"
        provider = "google"
        output_tokens = 300
        cost_usd = 0.01
        content = "{}"

        def parse_json(self):
            return {
                "summary": "Focus on head carriage first.",
                "priority_fixes": [
                    {
                        "area": "head_breath",
                        "fault": "Your head lifts before you breathe.",
                        "why_it_matters": "Lifting your chin drops your hips.",
                        "drill": "Hold your breath for 25m and pull harder.",  # UNSAFE!
                    },
                    {
                        "area": "recovery_elbow",
                        "fault": "Your elbow drops slightly on recovery.",
                        "why_it_matters": "Creates tension in the shoulder.",
                        "drill": "Shark Fin recovery drill.",
                    },
                    {
                        "area": "body_rotation",  # HALLUCINATED (not in chunk findings!)
                        "fault": "You do not rotate enough.",
                        "why_it_matters": "Creates drag.",
                        "drill": "Zen Skate.",
                    },
                ],
                "strengths": [],
            }

    async def fake_call_vlm(**kw):
        return AggResp()

    adjudicator_calls: list[str] = []

    async def fake_adjudicate(jpegs, *, aspect, verdict, observation, model):
        adjudicator_calls.append(aspect)
        return {
            "decision": "contradicted",
            "confidence": 0.8,
            "reason": "Elbow sits above hand in the high-res frame.",
        }, 0.005

    monkeypatch.setattr("services.ai_service.providers.base.call_vlm", fake_call_vlm)
    res = asyncio.run(AggregatorComponent(adjudicate_fn=fake_adjudicate).run(ctx))

    # Hallucinated body_rotation fix was dropped; only recovery_elbow triggered adjudication!
    assert adjudicator_calls == ["recovery_elbow"]
    assert res.meta["verification"]["filtered_fixes"] == 1
    assert res.cost_usd == pytest.approx(0.015)

    head_fix = next(f for f in res.findings if f.area == "head_breath")
    assert head_fix.severity == SEVERITY_FIX
    # Unsafe hypoxic/pull-harder drill replaced with deterministic safe TI drill!
    assert head_fix.extra["drill_key"] == "ti_one_goggle_breath"
    assert is_safe_drill_text(head_fix.extra["drill"])
    assert "hold your breath" not in head_fix.extra["drill"].lower()

    elbow_finding = next(f for f in res.findings if f.area == "recovery_elbow")
    # Contradicted single-stroke fix downgraded to INFO!
    assert elbow_finding.severity == SEVERITY_INFO
    assert elbow_finding.extra["adjudication"]["status"] == "contradicted"


def test_ti_drill_bank_safety_and_mapping():
    assert is_safe_drill_text("Hold your breath for a whole lap") is False
    assert is_safe_drill_text("Pull harder with both arms") is False
    assert is_safe_drill_text("Superman Glide with eyes straight down") is True

    d = resolve_ti_drill("body_line", "hips_low")
    assert d is not None and d["key"] == "ti_superman_glide"
    assert d["academy_ref"] is not None


def test_adjudicate_confirmed_keeps_fix():
    finding = Finding(
        component="aggregator",
        observation="Your head lifts.",
        severity=SEVERITY_FIX,
        area="head_breath",
        confidence=0.60,
        extra={"verdict": "lifted"},
    )

    async def confirm(*args, **kwargs):
        return {
            "decision": "confirmed",
            "confidence": 0.88,
            "reason": "Chin clearly up.",
        }, 0.004

    rec, cost = asyncio.run(
        adjudicate_uncertain_finding(
            finding, [b"jpg"], model="gpt-5.4-mini", adjudicate_fn=confirm
        )
    )
    assert rec["status"] == "confirmed"
    assert cost == 0.004
    assert finding.severity == SEVERITY_FIX
    assert finding.confidence >= 0.85


# ── 4. Apache-2.0 pose engine resolution & counting ───────────────────────────


def test_pose_engine_resolution_prefers_apache_rtmpose_when_installed(monkeypatch):
    assert _resolve_pose_engine("rtmpose") == "rtmpose"
    assert _resolve_pose_engine("yolov8") == "yolov8"

    import importlib.util

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if name == "rtmlib" else None
    )
    assert _resolve_pose_engine("auto") == "rtmpose"

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert _resolve_pose_engine("auto") == "yolov8"


def test_pose_keypoints_dispatch_selects_rtmpose_without_ultralytics(monkeypatch):
    from services.ai_service.coach.pose import RecoveryResult, _pose_keypoints
    from services.ai_service.pipeline.components.pose_recovery import _payload

    called: list[str] = []
    monkeypatch.setattr(
        "services.ai_service.coach.pose._pose_keypoints_rtmpose",
        lambda frames: (called.append("rtmpose") or [None] * len(frames)),
    )
    kps, engine = _pose_keypoints(["f1", "f2"], engine="rtmpose")
    assert engine == "rtmpose"
    assert called == ["rtmpose"]
    assert len(kps) == 2

    payload = _payload(
        RecoveryResult(
            count=3,
            confidence="ok",
            detection_rate=0.9,
            near_wrist_conf=0.8,
            peaks_s=(0.8, 2.0, 3.2),
            engine="rtmpose",
        )
    )
    assert payload["engine"] == "rtmpose"
    assert payload["count"] == 3


def test_count_recoveries_supports_rtmpose_engine(monkeypatch):
    np = pytest.importorskip("numpy")

    # Build 40 synthetic frames at 10fps (4.0s) with 2 clear near-wrist peaks.
    times = [i * 0.1 for i in range(40)]
    kps = []
    for t in times:
        arr = np.zeros((17, 3), dtype=float)
        # Shoulder y=200, near wrist rises above shoulder (smaller y) at t~1.0s and t~2.8s
        arr[6] = [100.0, 200.0, 0.9]  # right shoulder
        wave = 60.0 * (
            np.exp(-((t - 1.0) ** 2) / 0.04) + np.exp(-((t - 2.8) ** 2) / 0.04)
        )
        arr[10] = [100.0, 220.0 - wave, 0.9]  # right wrist (near arm)
        arr[5] = [100.0, 200.0, 0.2]
        arr[9] = [100.0, 210.0, 0.2]
        kps.append(arr)

    monkeypatch.setattr(
        "services.ai_service.coach.pose._pose_keypoints_rtmpose",
        lambda frames: kps,
    )
    res = count_recoveries([None] * len(times), times, engine="rtmpose")
    assert res.engine == "rtmpose"
    assert res.refused is False
    assert res.count == 2
    assert len(res.peaks_s) == 2


# ── 5. Deterministic longitudinal swim comparison ─────────────────────────────


def test_compare_runs_tracks_improvement_and_ongoing_focus():
    previous = {
        "result": {
            "gate_tier": "clean",
            "refused": False,
            "results": [
                {
                    "component": "aggregator",
                    "findings": [
                        {
                            "area": "head_breath",
                            "severity": "fix",
                            "confidence": 0.85,
                            "observation": "Your head lifts before breathing.",
                            "extra": {"verdict": "lifted"},
                        },
                        {
                            "area": "recovery_elbow",
                            "severity": "fix",
                            "confidence": 0.8,
                            "observation": "Your elbow drops.",
                            "extra": {"verdict": "dropped"},
                        },
                        {
                            "area": "body_line",
                            "severity": "strength",
                            "confidence": 0.8,
                            "observation": "Your body line stays level.",
                            "extra": {"verdict": "flat"},
                        },
                    ],
                }
            ],
        }
    }
    current = {
        "result": {
            "gate_tier": "clean",
            "refused": False,
            "results": [
                {
                    "component": "chunk_coach",
                    "findings": [
                        {
                            "area": "head_breath",
                            "severity": "strength",
                            "confidence": 0.86,
                            "observation": "Your head stays quiet and eyes down.",
                            "extra": {"verdict": "neutral"},
                        },
                        {
                            "area": "recovery_elbow",
                            "severity": "fix",
                            "confidence": 0.82,
                            "observation": "Your elbow still drops on recovery.",
                            "extra": {"verdict": "dropped"},
                        },
                        {
                            "area": "body_line",
                            "severity": "strength",
                            "confidence": 0.85,
                            "observation": "Long, level body line.",
                            "extra": {"verdict": "flat"},
                        },
                    ],
                }
            ],
        }
    }

    diff = compare_runs(
        current,
        previous,
        previous_job_id="job-prev-1",
        previous_completed_at="2026-11-18T10:00:00Z",
    )
    assert diff is not None
    assert diff["improved_count"] == 1
    assert diff["still_working_count"] == 1
    assert diff["maintained_count"] == 1
    assert diff["caveat"] is None
    by_aspect = {a["aspect"]: a["status"] for a in diff["aspects"]}
    assert by_aspect["head_breath"] == "improved"
    assert by_aspect["recovery_elbow"] == "still_working"
    assert by_aspect["body_line"] == "maintained"
    assert "Head & breathing" in diff["headline"]


# ── 6. Multi-coach bake-off evaluation harness scoring ────────────────────────


def test_bakeoff_eval_scores_inter_rater_repeatability_and_acceptance_gates():
    gt = [
        ClipGroundTruth(
            clip_id="clip_good",
            video_file="clip_good.mp4",
            annotations=(
                CoachAnnotation("c1", usable=True, leading_fix_area="head_breath"),
                CoachAnnotation("c2", usable=True, leading_fix_area="head_breath"),
            ),
        ),
        ClipGroundTruth(
            clip_id="clip_bad",
            video_file="clip_bad.mp4",
            annotations=(
                CoachAnnotation("c1", usable=False, catastrophic_unusable=True),
                CoachAnnotation("c2", usable=False, catastrophic_unusable=True),
            ),
        ),
    ]
    ira = compute_inter_rater_agreement(gt)
    assert ira["usability_agreement_rate"] == 1.0
    assert ira["leading_fix_agreement_rate"] == 1.0

    obs = [
        # 3 repeats on clip_good — all consistent & match coach truth
        *[
            RunObservation(
                clip_id="clip_good",
                repeat_idx=i,
                config_name="C_gemini38_chunks_4fps_hybrid",
                refused=False,
                gate_tier="clean",
                leading_fix_area="head_breath",
                fix_areas=["head_breath"],
                high_conf_fix_areas=["head_breath"],
                cost_usd=0.045,
                latency_ms=4200 + i * 100,
                delivered_report=True,
            )
            for i in range(3)
        ],
        # 3 repeats on clip_bad — all properly refused
        *[
            RunObservation(
                clip_id="clip_bad",
                repeat_idx=i,
                config_name="C_gemini38_chunks_4fps_hybrid",
                refused=True,
                gate_tier="refuse",
                leading_fix_area=None,
                cost_usd=0.005,
                latency_ms=1100,
                delivered_report=False,
            )
            for i in range(3)
        ],
    ]

    summary = score_candidate("C_gemini38_chunks_4fps_hybrid", gt, obs)
    assert summary["leading_fix_agreement"]["rate"] == 1.0
    assert summary["catastrophic_refusal"]["rate"] == 1.0
    assert summary["unsupported_high_conf"]["rate"] == 0.0
    assert summary["honesty_violations"]["rate"] == 0.0
    assert summary["repeatability"]["rate"] == 1.0
    assert summary["all_gates_passed"] is True
    assert summary["economics"]["cost_per_delivered_report_usd"] == pytest.approx(0.05)
