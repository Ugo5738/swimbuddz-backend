"""Deterministic runtime evidence verification & selective adjudication (ADR-013).

Enforces SwimBuddz's "honesty over helpfulness" invariants at runtime — not only
in offline evaluation scripts:
  1. Closed-enum (aspect, verdict) validation.
  2. Camera-angle visibility rules (suppress underwater catch/pull/kick on
     above-water footage, ban side-on crossover claims, suppress overhead
     body-line sink claims, and reject banned numeric claims like SPM/degrees).
  3. Cross-stroke consensus across sampled chunks so recurring issues outrank
     single-stroke noise.
  4. Optional selective second-model adjudication (e.g. GPT-5.4 mini / GPT-4o
     on high-resolution evidence stills) invoked ONLY for uncertain top fixes
     on clean clips, keeping unit cost strictly bounded.

Pure Python at import time (no cv2/litellm at module load).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    Finding,
    GateTier,
    InputProfile,
)

# Authoritative closed-enum verdicts matching pipeline/grade.py.
ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "body_line": frozenset(
        {"flat", "hips_low", "legs_low", "piked", "arched", "unclear"}
    ),
    "recovery_elbow": frozenset({"high", "wide", "dropped", "unclear"}),
    "body_rotation": frozenset({"good", "limited", "unclear"}),
    "head_breath": frozenset(
        {"neutral", "lifted", "left", "right", "both", "none_seen", "unclear"}
    ),
    "entry_reach": frozenset({"clean_extended", "short", "overreach", "unclear"}),
}

_BODY_LINE_SINK_VERDICTS = frozenset({"hips_low", "legs_low", "piked", "arched"})

# Mechanical honesty patterns matching validation/coach_eval.py + ADR-013.
_BANNED_NUMBERS_RE = re.compile(
    r"(strokes?\s*per\s*minute|\bspm\b|\bcadence\b|\d+\s*°|\b\d+\s*degrees?\b)",
    re.I,
)
_UNDERWATER_ONLY_RE = re.compile(
    r"\b(early\s+vertical\s+forearm|\bevf\b|underwater\s+pull|underwater\s+catch|pull[- ]?through|s[- ]?pull|dropped\s+catch|catch\s+phase)\b",
    re.I,
)
_CROSSOVER_RE = re.compile(
    r"(cross[- ]?over|cross(?:es|ing)?\s+(?:the\s+)?midline|cross(?:es|ing)?\s+in\s+front\s+of\s+(?:your|the)\s+head)",
    re.I,
)

_BORDERLINE_MAX_CONF = 0.65
_SINGLE_STROKE_MAX_CONF = 0.68

# Optional injectable adjudicator function for no-API tests:
# adjudicate_fn(frames, *, aspect, verdict, observation, model) -> (dict, cost_usd)
AdjudicateFn = Callable[..., Awaitable[tuple[dict, float]]]

ADJUDICATOR_SYSTEM_PROMPT = """\
You are a strict second-opinion freestyle swim technique verifier. You are shown \
1-3 high-resolution still frames from ONE stroke moment, plus a candidate fault \
claimed by a primary video model.

Your ONLY job is to verify whether the frames clearly support that specific claim. \
Follow HONESTY OVER HELPFULNESS:
- Say "confirmed" ONLY if the frames clearly show the claimed fault.
- Say "contradicted" if the frames clearly show the opposite (e.g., neutral head, \
level body line, or high elbow).
- Say "unclear" if splash, angle, distance, or occlusion prevents verifying it.
- NEVER invent new faults, NEVER report crossover from side-on views, NEVER judge \
underwater catch/pull/kick from above-water views, and NEVER output SPM or degrees.

Return ONLY this JSON:
{"decision": "confirmed" | "contradicted" | "unclear", "confidence": 0.0-1.0, "reason": "<one short sentence>"}"""


def validate_aspect_verdict(aspect: str, verdict: str) -> bool:
    """Return True if (aspect, verdict) is in the closed Stroke Lab rubric."""
    allowed = ALLOWED_VERDICTS.get(aspect)
    return bool(allowed and verdict in allowed)


def check_honesty_violations(
    text: str,
    *,
    profile: InputProfile = InputProfile.SIDE_ON_ABOVE,
    view: Optional[str] = None,
    severity: str = SEVERITY_FIX,
) -> list[str]:
    """Return a list of ADR-013 honesty violation codes found in ``text``."""
    if not text:
        return []
    violations: list[str] = []
    if _BANNED_NUMBERS_RE.search(text):
        violations.append("banned_numbers")
    # Underwater faults are banned whenever footage is not verified UNDERWATER.
    if profile != InputProfile.UNDERWATER and severity == SEVERITY_FIX:
        if _UNDERWATER_ONLY_RE.search(text):
            violations.append("invisible_underwater_aspect")
    # Crossover is structurally unjudgeable from side-on / unknown / mixed views.
    normalized_view = (view or "").strip().lower()
    if normalized_view != "overhead" and _CROSSOVER_RE.search(text):
        violations.append("side_view_crossover")
    return violations


def verify_chunk_finding(
    finding: Finding,
    *,
    profile: InputProfile = InputProfile.SIDE_ON_ABOVE,
    view: Optional[str] = None,
    gate_tier: GateTier = GateTier.CLEAN,
    max_frame_index: Optional[int] = None,
) -> tuple[Optional[Finding], Optional[str]]:
    """Verify one per-chunk finding in deterministic code.

    Returns ``(verified_finding, rejection_reason)``. Rejected findings return
    ``(None, reason)`` so callers can log diagnostics instead of publishing
    unsupported or ADR-013-violating claims.
    """
    area = (finding.area or "").strip()
    verdict = str((finding.extra or {}).get("verdict") or "").strip()
    if not area or not verdict or verdict == "unclear":
        return None, "missing_or_unclear_verdict"
    if not validate_aspect_verdict(area, verdict):
        return None, f"invalid_verdict:{area}:{verdict}"

    normalized_view = (view or "").strip().lower()
    # Top-down / overhead cameras cannot see whether hips or legs sink below the waterline.
    if (
        normalized_view == "overhead"
        and area == "body_line"
        and verdict in _BODY_LINE_SINK_VERDICTS
    ):
        return None, "overhead_body_line_sink_not_visible"

    violations = check_honesty_violations(
        finding.observation,
        profile=profile,
        view=normalized_view,
        severity=finding.severity,
    )
    if violations:
        return None, f"honesty_violation:{','.join(violations)}"

    # Keep only valid in-bounds evidence frame references when strip size is known.
    if max_frame_index is not None and finding.evidence_frames:
        finding.evidence_frames = [
            ref for ref in finding.evidence_frames if 0 <= ref.index < max_frame_index
        ]

    # Clamp confidence to [0.0, 0.99] and cap borderline clips.
    finding.confidence = max(0.0, min(0.99, float(finding.confidence or 0.0)))
    if gate_tier == GateTier.BORDERLINE:
        finding.confidence = min(finding.confidence, _BORDERLINE_MAX_CONF)

    finding.extra = dict(finding.extra or {})
    finding.extra["verified"] = True
    return finding, None


@dataclass(frozen=True)
class AspectConsensus:
    """Cross-stroke verification summary for one aspect's dominant fix."""

    area: str
    verdict: str
    support_count: int
    total_coached_chunks: int
    consistency_ratio: float
    mean_confidence: float
    consistent: bool
    single_stroke_only: bool
    needs_adjudication: bool

    def to_dict(self) -> dict:
        return {
            "area": self.area,
            "verdict": self.verdict,
            "support_count": self.support_count,
            "total_coached_chunks": self.total_coached_chunks,
            "consistency_ratio": round(self.consistency_ratio, 3),
            "mean_confidence": round(self.mean_confidence, 3),
            "consistent": self.consistent,
            "single_stroke_only": self.single_stroke_only,
            "needs_adjudication": self.needs_adjudication,
        }


def compute_cross_stroke_consensus(
    chunk_findings: list[Finding],
    *,
    total_coached_chunks: Optional[int] = None,
) -> dict[str, AspectConsensus]:
    """Compute cross-stroke support for every aspect that emitted a FIX finding.

    If a swim has 3 coached strokes and a fault appears in 2+ strokes, it is
    ``consistent=True``. If it appears in only 1 of >=2 coached strokes, it is
    flagged ``single_stroke_only=True`` and eligible for selective adjudication
    or confidence capping so one noisy frame never masquerades as a confirmed
    systemic habit.
    """
    chunk_ids = {f.instance_id for f in chunk_findings if f.instance_id is not None}
    n_chunks = max(1, total_coached_chunks or len(chunk_ids) or 1)

    fixes_by_area: dict[str, list[Finding]] = {}
    for f in chunk_findings:
        if f.severity != SEVERITY_FIX or not f.area:
            continue
        fixes_by_area.setdefault(f.area, []).append(f)

    out: dict[str, AspectConsensus] = {}
    for area, fs in fixes_by_area.items():
        # Distinct stroke instances showing a fix in this area.
        by_inst: dict[int, Finding] = {}
        for idx, f in enumerate(fs):
            key = f.instance_id if f.instance_id is not None else -(idx + 1)
            prev = by_inst.get(key)
            if prev is None or f.confidence > prev.confidence:
                by_inst[key] = f

        verdicts = [
            str((f.extra or {}).get("verdict") or "")
            for f in by_inst.values()
            if (f.extra or {}).get("verdict")
        ]
        dominant_verdict = Counter(verdicts).most_common(1)[0][0] if verdicts else ""
        support = len(by_inst)
        ratio = min(1.0, support / n_chunks)
        mean_conf = sum(f.confidence for f in by_inst.values()) / support
        single_stroke_only = n_chunks >= 2 and support == 1
        consistent = (n_chunks == 1 and mean_conf >= 0.65) or support >= 2
        needs_adjudication = single_stroke_only or (0.45 <= mean_conf < 0.72)

        out[area] = AspectConsensus(
            area=area,
            verdict=dominant_verdict,
            support_count=support,
            total_coached_chunks=n_chunks,
            consistency_ratio=ratio,
            mean_confidence=mean_conf,
            consistent=consistent,
            single_stroke_only=single_stroke_only,
            needs_adjudication=needs_adjudication,
        )
    return out


def cap_single_stroke_confidence(
    finding: Finding, consensus: Optional[AspectConsensus]
) -> None:
    """Cap confidence when a fault appeared in only 1 of >=2 coached strokes."""
    if consensus is not None and consensus.single_stroke_only:
        finding.confidence = min(finding.confidence, _SINGLE_STROKE_MAX_CONF)


async def adjudicate_uncertain_finding(
    finding: Finding,
    evidence_jpegs: list[bytes],
    *,
    model: str,
    reasoning_effort: Optional[str] = "low",
    adjudicate_fn: Optional[AdjudicateFn] = None,
) -> tuple[dict, float]:
    """Run a selective second-model check on high-res evidence stills.

    Called ONLY for uncertain top fixes when ``adjudicator_model`` is configured.
    If the adjudicator returns ``contradicted`` or ``unclear``, the finding is
    downgraded in-place from ``SEVERITY_FIX`` to ``SEVERITY_INFO`` so Stroke Lab
    never publishes an unconfirmed high-impact fault.
    """
    verdict = str((finding.extra or {}).get("verdict") or "")
    area = finding.area or "technique"

    if adjudicate_fn is not None:
        parsed, cost = await adjudicate_fn(
            evidence_jpegs,
            aspect=area,
            verdict=verdict,
            observation=finding.observation,
            model=model,
        )
    else:
        if not evidence_jpegs:
            return {"status": "skipped_no_frames", "model": model}, 0.0
        from services.ai_service.providers.base import call_vlm

        user_prompt = (
            f"Candidate finding to verify:\n"
            f"- Aspect: {area}\n"
            f"- Verdict: {verdict or 'unspecified'}\n"
            f"- Claim: {finding.observation}\n\n"
            "Do these high-resolution frames clearly confirm this claim? "
            "Return only the JSON."
        )
        resp = await call_vlm(
            system_prompt=ADJUDICATOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            images=evidence_jpegs[:3],
            model=model,
            temperature=0.0,
            max_tokens=500,
            image_detail="high",
            response_format={"type": "json_object"},
            reasoning_effort=reasoning_effort,
            trace_name="strokelab_adjudicate",
        )
        cost = resp.cost_usd
        try:
            parsed = resp.parse_json()
        except Exception:
            parsed = {"decision": "unclear", "confidence": 0.0, "reason": "parse_error"}

    decision = str(parsed.get("decision") or "unclear").strip().lower()
    adj_conf = max(0.0, min(0.99, float(parsed.get("confidence", 0.0) or 0.0)))
    reason = str(parsed.get("reason") or "").strip()

    record = {
        "status": decision
        if decision in {"confirmed", "contradicted", "unclear"}
        else "unclear",
        "model": model,
        "confidence": round(adj_conf, 3),
        "reason": reason[:200],
    }
    finding.extra = dict(finding.extra or {})
    finding.extra["adjudication"] = record

    if record["status"] == "confirmed":
        finding.confidence = max(
            finding.confidence, min(0.9, adj_conf or finding.confidence)
        )
    else:
        finding.severity = SEVERITY_INFO
        finding.confidence = min(finding.confidence, 0.45)

    return record, cost
