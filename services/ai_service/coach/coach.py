"""Orchestration for the Stroke Lab VLM coach: frames -> vision LLM -> report.

Provider-agnostic: the model is just a LiteLLM model string, so the same code
runs against a hosted Tier-A model (gpt-4o-mini, claude, gemini) today and a
self-hosted open-weights endpoint (ollama/, openrouter/, together_ai/) later —
only the string changes.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from services.ai_service.coach.frames import Frame
from services.ai_service.coach.prompt import (
    RESPONSE_FORMAT,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_VIDEO,
    build_user_prompt,
    build_user_prompt_video,
)


@dataclass
class CoachReport:
    """The parsed coaching JSON plus the call's cost/usage telemetry."""

    raw: dict
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    n_frames: int


def _gate_note(gate_context: Optional[dict]) -> str:
    """A line telling the coach the view/stroke gate already passed upstream, so
    it doesn't re-refuse a clip on angle grounds the dedicated gate already cleared.
    It may still hedge/give fewer fixes if the frames are limited."""
    if not gate_context:
        return ""
    view = gate_context.get("view", "side-on")
    n = gate_context.get("swimmer_count", 1)
    return (
        "\n\nNOTE: An independent view/stroke check has ALREADY PASSED for this "
        f"clip (view={view}, stroke=freestyle, swimmers={n}). Treat the side-on + "
        "freestyle gate as satisfied and coach the clearest swimmer. You may still "
        "give fewer fixes or lower confidence if the frames are limited, but do NOT "
        "set usable_for_coaching=false on view/angle grounds alone."
    )


async def run_coach(
    frames: list[Frame],
    *,
    stroke_hint: str = "freestyle",
    model: Optional[str] = None,
    image_detail: str = "auto",
    max_tokens: int = 1500,
    temperature: float = 0.0,
    gate_context: Optional[dict] = None,
    goal_block: str = "",
    video: Optional[bytes] = None,
    video_fps: Optional[float] = None,
    video_detail: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> CoachReport:
    """Run the vision coach over pre-selected frames.

    ``temperature`` defaults to 0.0: the view/usability gate flip-flops on
    borderline elevated angles at higher temperatures (measured ~25% on a
    borderline clip), and a measurement tool should be as repeatable as we can
    make it. (Even at 0.0 the provider is not perfectly deterministic.)
    """
    # Imported lazily so frames.py stays usable without the LLM stack.
    from services.ai_service.providers.base import call_vlm

    # Video mode (Gemini): send the clip itself + the motion-aware prompt; cite by
    # timestamp. Stills mode (OpenAI): the validated 8-frame path. One swap point.
    use_video = video is not None
    base_user = (
        build_user_prompt_video(stroke_hint)
        if use_video
        else build_user_prompt(frames, stroke_hint)
    )
    # The video coach timestamps every aspect → its JSON is much longer than the
    # stills coach's; the 1500 default truncates it mid-string. Give it headroom.
    vlm_max_tokens = max(max_tokens, 4096) if use_video else max_tokens
    resp = await call_vlm(
        system_prompt=SYSTEM_PROMPT_VIDEO if use_video else SYSTEM_PROMPT,
        user_prompt=base_user
        + _gate_note(gate_context)
        + (f"\n\n{goal_block}" if goal_block else ""),
        images=[] if use_video else [f.jpeg for f in frames],
        video=video,
        video_fps=video_fps if use_video else None,
        video_detail=video_detail if use_video else None,
        reasoning_effort=reasoning_effort,
        model=model,
        image_detail=image_detail,
        temperature=temperature,
        max_tokens=vlm_max_tokens,
        response_format=RESPONSE_FORMAT,
        trace_name="strokelab_vlm_coach",
    )
    try:
        data = resp.parse_json()
    except Exception:
        data = {"_parse_error": True, "_raw_text": resp.content}

    return CoachReport(
        raw=data,
        model=resp.model,
        provider=resp.provider,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cost_usd=resp.cost_usd,
        latency_ms=resp.latency_ms,
        n_frames=len(frames),
    )


# ── Gate-voting ───────────────────────────────────────────────────────
#
# The view/usability verdict flip-flops on borderline elevated angles (measured
# ~25% on one clip). So before the expensive coaching call we run a SHORT, cheap,
# low-detail gate prompt several times and take the majority. This both stabilises
# the accept/refuse decision and saves money — a refused clip never pays for the
# full coaching call.

GATE_SYSTEM_PROMPT = """\
You are a practical gatekeeper for an automated freestyle swim-technique tool. You \
are shown a few still frames (in time order) from one short clip. Decide ONLY \
whether the clip has enough visible freestyle swimming for the coach to give some \
above-water feedback, and grade the camera profile quality. DO NOT coach.

Return ONLY this JSON (no prose): {"view": "...", "stroke": "...", \
"profile_quality": "clean|partial|poor", "catastrophic_flags": [], \
"usable_for_coaching": true|false, "swimmer_count": 0, "confidence": 0.0, \
"reason": "short"}

Definitions:
- view ∈ side-on | head-on | overhead | underwater | mixed | unclear. "side-on" \
means a side or side-oblique profile where the swimmer generally travels ACROSS \
the frame (left↔right) and at least the above-water recovery, head, or body \
rotation can be seen. A deck/elevated angle can still be side-on/usable when the \
swimmer's path is across the frame and one side of the body is visible. \
"overhead" means mostly from above. If the freestyle swimmer, arm recovery, head, \
or body rotation are still clear, use view="overhead", profile_quality="partial", \
and usable_for_coaching=true. If it is too far, too cropped, or too straight-down \
to see any useful above-water technique, set profile_quality="poor" and usable=false. \
Mark head-on when the swimmer moves TOWARD or AWAY from the camera; underwater when \
most useful information is below the surface; mixed when the angle changes across frames.
- profile_quality ∈ clean | partial | poor:
  * "clean": clear side-on or near-side-on freestyle profile with one main trackable \
swimmer where above-water recovery, head, and body alignment are clearly readable.
  * "partial": elevated, angled, overhead, slightly distant, or multi-swimmer footage \
where at least one above-water aspect (recovery, head, or rotation) is still coachable \
with caveats.
  * "poor": unusable for coaching (non-freestyle, head-on, underwater, no visible \
swimmer, too dark/blurry/cropped, or no clear stroke frames).
- catastrophic_flags: zero or more of ["non_freestyle", "underwater", "head_on", \
"no_swimmer", "unreadable"]. Include a flag ONLY when clearly true.
- stroke ∈ freestyle | other | unclear.
- usable_for_coaching = true if stroke is freestyle, at least one swimmer is clear \
enough to track, catastrophic_flags is empty, and at least ONE above-water aspect \
can be judged: arm recovery, head position, body rotation, or body line.
- swimmer_count = distinct people visibly swimming.
- confidence ∈ 0.0-1.0.
JSON only."""

GATE_RESPONSE_FORMAT = {"type": "json_object"}


@dataclass
class GateVerdict:
    """Majority-voted accept/refuse decision plus the per-vote breakdown."""

    usable: bool
    view: str
    stroke: str
    swimmer_count: int
    agreement: float  # fraction of valid votes agreeing with the usable verdict
    n_votes: int
    n_valid: int
    votes: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""
    profile_quality: str = (
        ""  # graded: "clean" | "partial" | "poor" ("" on legacy cache)
    )
    catastrophic_flags: list[str] = field(default_factory=list)


def _mode(values: list, default):
    vals = [v for v in values if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else default


def _majority_flags(votes: list[dict], n_valid: int) -> list[str]:
    if n_valid <= 0:
        return []
    counts: Counter[str] = Counter()
    for v in votes:
        raw = v.get("catastrophic_flags")
        if isinstance(raw, list):
            for flag in {str(f).strip().lower() for f in raw if f}:
                counts[flag] += 1
    threshold = max(1, (n_valid + 1) // 2)  # strict/half majority
    return sorted(flag for flag, count in counts.items() if count >= threshold)


async def run_gate(
    frames: list[Frame],
    *,
    model: Optional[str] = None,
    n_votes: int = 3,
    image_detail: str = "low",
    temperature: float = 0.0,
    max_tokens: int = 1500,
    stroke_hint: str = "freestyle",
    reasoning_effort: Optional[str] = "low",
) -> GateVerdict:
    """Run the cheap gate prompt ``n_votes`` times (concurrently) and vote.

    ``max_tokens`` defaults high (1500) because o-series reasoning models — which
    judge the borderline side-on/overhead call most accurately — spend tokens on
    reasoning first; a small cap leaves no room for the JSON answer. Non-reasoning
    models bill only the ~50 tokens they actually emit, so the high cap is free.
    """
    from services.ai_service.providers.base import call_vlm

    images = [f.jpeg for f in frames]
    user = build_user_prompt(frames, stroke_hint)

    async def one():
        return await call_vlm(
            system_prompt=GATE_SYSTEM_PROMPT,
            user_prompt=user,
            images=images,
            model=model,
            image_detail=image_detail,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=GATE_RESPONSE_FORMAT,
            reasoning_effort=reasoning_effort,
            trace_name="strokelab_gate",
        )

    resps = await asyncio.gather(
        *(one() for _ in range(n_votes)), return_exceptions=True
    )

    votes: list[dict] = []
    cost, latency, model_used = 0.0, 0, ""
    for r in resps:
        if isinstance(r, Exception):
            continue
        cost += r.cost_usd
        latency = max(latency, r.latency_ms)
        model_used = r.model
        try:
            votes.append(r.parse_json())
        except Exception:
            continue

    n_valid = len(votes)
    usable_votes = sum(1 for v in votes if v.get("usable_for_coaching") is True)
    usable = n_valid > 0 and usable_votes >= (n_valid / 2.0)  # majority (ties pass)
    agreement = (
        (usable_votes if usable else n_valid - usable_votes) / n_valid
        if n_valid
        else 0.0
    )
    pq_values = [
        str(v.get("profile_quality")).strip().lower()
        for v in votes
        if v.get("profile_quality") in {"clean", "partial", "poor"}
    ]
    return GateVerdict(
        usable=usable,
        view=_mode([v.get("view") for v in votes], "unclear"),
        stroke=_mode([v.get("stroke") for v in votes], "unclear"),
        swimmer_count=_mode([v.get("swimmer_count") for v in votes], 0),
        agreement=agreement,
        n_votes=n_votes,
        n_valid=n_valid,
        votes=votes,
        cost_usd=cost,
        latency_ms=latency,
        model=model_used,
        profile_quality=_mode(pq_values, ""),
        catastrophic_flags=_majority_flags(votes, n_valid),
    )


@dataclass
class Analysis:
    """A full clip result: the voted gate, then coaching only if it passed."""

    gate: GateVerdict
    coach: Optional[CoachReport]  # None when the gate refused (no expensive call)
    total_cost_usd: float


async def analyze(
    frames: list[Frame],
    *,
    model: Optional[str] = None,
    gate_model: Optional[str] = None,
    gate_votes: int = 3,
    gate_detail: str = "low",
    coach_detail: str = "auto",
    stroke_hint: str = "freestyle",
) -> Analysis:
    """Gate-vote first; only coach (the expensive call) if the gate accepts.

    ``gate_model`` may differ from the coaching ``model`` — empirically a reasoning
    model (o4-mini) judges the side-on/overhead gate best, while gpt-4o gives the
    best coaching prose. The agnostic layer makes per-call model choice free.
    """
    gate = await run_gate(
        frames,
        model=gate_model or model,
        n_votes=gate_votes,
        image_detail=gate_detail,
        stroke_hint=stroke_hint,
    )
    if not gate.usable:
        return Analysis(gate=gate, coach=None, total_cost_usd=gate.cost_usd)
    coach = await run_coach(
        frames,
        model=model,
        image_detail=coach_detail,
        stroke_hint=stroke_hint,
        gate_context={"view": gate.view, "swimmer_count": gate.swimmer_count},
    )
    return Analysis(
        gate=gate, coach=coach, total_cost_usd=gate.cost_usd + coach.cost_usd
    )
