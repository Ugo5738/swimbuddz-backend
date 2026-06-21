"""Stage 1 — Segment recovery instances from the Stage-0 track.

A freestyle over-water arm recovery shows up as a peak in the upper-box motion
signal. Peak-detect (min-distance + a noise floor) → one recovery Instance per
peak, each spanning trough-to-trough (the arc the per-instance coach will read).

Pure numpy + the proven ``_local_peaks`` from pose_pipeline. No API, no model —
this is deterministic CV. Its accuracy is gated by ``validation/recovery_eval.py``
against the golden ``recovery_times`` / ``stroke_cycles`` labels BEFORE the
instance UX is trusted (see design doc §6.3).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from services.ai_service.pipeline.types import Instance, Phase, Track

_MIN_PERIOD_S = 0.6  # a single arm rarely recovers faster than this
_NOISE_FLOOR = 0.25  # ignore peaks below this fraction of the strongest peak


def segment_recoveries(
    track: Track,
    *,
    min_period_s: float = _MIN_PERIOD_S,
    smooth: int = 3,
) -> list[Instance]:
    import numpy as np

    from services.ai_service.analysis.pose_pipeline import _local_peaks

    pts = track.points
    if len(pts) < 3:
        return []
    sig = np.array([p.motion for p in pts], dtype="float32")
    if smooth > 1 and len(sig) >= smooth:  # light moving-average de-noise
        sig = np.convolve(sig, np.ones(smooth) / smooth, mode="same")
    times = [p.timestamp_s for p in pts]
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.1
    min_dist = max(2, round(min_period_s / dt))

    peaks = _local_peaks(sig, min_dist)
    if peaks:
        thr = _NOISE_FLOOR * float(np.max([sig[p] for p in peaks]))
        peaks = [p for p in peaks if sig[p] >= thr]

    smax = float(np.max(sig)) or 1.0
    instances: list[Instance] = []
    for i, pk in enumerate(peaks):
        prev_pk = peaks[i - 1] if i > 0 else None
        next_pk = peaks[i + 1] if i < len(peaks) - 1 else None
        start_idx = (
            (pk + prev_pk) // 2 if prev_pk is not None else max(0, pk - min_dist)
        )
        end_idx = (
            (pk + next_pk) // 2
            if next_pk is not None
            else min(len(pts) - 1, pk + min_dist)
        )
        instances.append(
            Instance(
                phase=Phase.RECOVERY,
                instance_id=i,
                start_s=times[start_idx],
                end_s=times[end_idx],
                peak_s=times[pk],
                peak_index=pk,
                confidence=min(1.0, float(sig[pk]) / smax),
            )
        )
    return instances


# ── VLM-classify → deterministic group (the validated counting path) ──────────
#
# The model classifies each frame's phase; THIS code counts. That split is the
# whole point: counting is len() of a deterministically-grouped list, never a
# number the model emits (the panel's fix for the over-counting failure).


@dataclass
class FrameLabel:
    """One frame's VLM phase classification — the input to grouping. Enriched in
    one VLM call: phase + which arm + the recovery sub-phase."""

    index: int
    phase: str  # recovery | entry | glide_extension | breath | indeterminate
    arm: str = "none"  # near | far | none
    subphase: str = ""  # recovery sub-phase: exit | mid | entry (else "")
    conf: float = 0.0


# Per-frame phase string → the Phase enum used on Instance.
_PHASE_ENUM = {
    "recovery": Phase.RECOVERY,
    "entry": Phase.ENTRY,
    "glide_extension": Phase.GLIDE,
    "breath": Phase.BREATH,
}
# Visible phases we segment into chunks. catch/pull/kick are underwater → never
# labelled from this footage → handled by dormant components, not here.
SEGMENTABLE_PHASES = ("recovery", "entry", "glide_extension", "breath")


def _mode3(seq: list[str], i: int) -> str:
    """Mode of the 3-frame window centred on i (categorical median-smooth)."""
    lo, hi = max(0, i - 1), min(len(seq), i + 2)
    return Counter(seq[lo:hi]).most_common(1)[0][0]


def _runs(seq: list[str]) -> list[tuple[str, int, int]]:
    """Run-length encode → list of (value, start_idx, end_idx)."""
    out: list[tuple[str, int, int]] = []
    start = 0
    n = len(seq)
    for i in range(1, n + 1):
        if i == n or seq[i] != seq[start]:
            out.append((seq[start], start, i - 1))
            start = i
    return out


def _dominant_arm(arms: list[str]) -> str:
    """Majority near/far across a recovery run. Defaults to 'near' (the camera-
    facing arm) when no arm was labelled — never drops the run."""
    cnt = Counter(a for a in arms if a in ("near", "far"))
    return cnt.most_common(1)[0][0] if cnt else "near"


def group_phase_instances(
    labels: list[FrameLabel],
    timestamps: list[float],
    *,
    min_period_s: float = 0.5,
    smooth: bool = True,
    segment_phases: tuple[str, ...] = SEGMENTABLE_PHASES,
) -> list[Instance]:
    """Group per-frame labels into phase INSTANCES (chunks) — deterministically.

    Every labelled, segmentable phase becomes chunks; NOTHING is discarded (the
    data-loss rule). Recovery is split per ARM (near + far) — far-arm recoveries
    get their own chunks instead of being thrown away — and same-arm splash
    doubles within ``min_period_s`` are merged. Other phases (entry/glide/breath)
    become their maximal runs. ``instance_id`` is per-phase, time-ordered.

    Counting/metrics live downstream (the collate component) — this only segments.
    """
    n = len(labels)
    if n == 0 or len(timestamps) != n:
        return []
    phases = [lab.phase for lab in labels]
    arms = [lab.arm for lab in labels]
    if smooth and n >= 3:
        phases = [_mode3(phases, i) for i in range(n)]

    raw: list[tuple[str, str, int, int]] = []  # (phase, arm, start, end)

    if "recovery" in segment_phases:
        # Find recovery runs by PHASE (smoothing already fused stray flips), assign
        # each its dominant arm, then merge only consecutive SAME-arm runs that are
        # too close (splash doubles). Alternating near/far stays separate; both arms
        # are kept — the far arm is never discarded.
        merged: list[list] = []  # [arm, start, end]
        for _, a, b in (r for r in _runs(phases) if r[0] == "recovery"):
            arm = _dominant_arm(arms[a : b + 1])
            if (
                merged
                and merged[-1][0] == arm
                and timestamps[a] - timestamps[merged[-1][2]] < min_period_s
            ):
                merged[-1][2] = b  # absorb a too-close same-arm double
            else:
                merged.append([arm, a, b])
        raw.extend(("recovery", arm, a, b) for arm, a, b in merged)

    for phase in segment_phases:
        if phase == "recovery":
            continue
        raw.extend((phase, "none", a, b) for ph, a, b in _runs(phases) if ph == phase)

    # per-phase, time-ordered instance_id
    raw.sort(key=lambda r: timestamps[r[2]])
    counters: dict[str, int] = {}
    instances: list[Instance] = []
    for phase, arm, a, b in raw:
        idx = counters.get(phase, 0)
        counters[phase] = idx + 1
        instances.append(
            Instance(
                phase=_PHASE_ENUM.get(phase, Phase.CLIP),
                instance_id=idx,
                arm=arm,
                start_s=timestamps[a],
                end_s=timestamps[b],
                peak_s=(timestamps[a] + timestamps[b]) / 2.0,
                peak_index=a,
                confidence=0.7,
            )
        )
    instances.sort(key=lambda inst: inst.start_s)
    return instances
