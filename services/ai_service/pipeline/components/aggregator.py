"""Aggregator — the collated/summary coach (chunk-centric Stage-3).

Reads EVERY per-chunk per-aspect finding the chunk coach produced (via
``ctx.run_findings``) and writes ONE collated coaching read: a short overall
summary + the prioritised top fixes (a fault recurring across strokes collapses to
ONE) + genuine strengths. The per-chunk findings stay the grounded stroke-by-stroke
detail; THIS is the top-line read the swimmer sees first.

Text-only LLM (no video/frames — it reasons over the structured findings the chunk
coach already produced), so it's cheap + low-token. Each emitted top fix is pinned
to the most-severe chunk of that aspect (evidence + instance_id) so it still gets a
clip + thumbnail. Falls back to a deterministic dedupe-by-aspect synthesis if the
LLM errors — the read is never lost.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from libs.common.logging import get_logger
from services.ai_service.analysis.drills import (
    format_ti_drill_line,
    is_safe_drill_text,
    resolve_ti_drill,
)
from services.ai_service.coach.rubric import build_ti_block
from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.components.aspect import COACH_VOICE
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_STRENGTH,
    ComponentResult,
    Finding,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)
from services.ai_service.pipeline.verify import (
    AdjudicateFn,
    adjudicate_uncertain_finding,
    cap_single_stroke_confidence,
    check_honesty_violations,
    compute_cross_stroke_consensus,
)

logger = get_logger(__name__)

# Severity priority for picking the representative (worst) chunk of an aspect.
_SEV_ORDER = {SEVERITY_FIX: 0, SEVERITY_STRENGTH: 1}
_AGG_PARSE_SNIPPET_CHARS = 400

AGG_PROMPT = """You are the HEAD freestyle coach using a Total Immersion-informed \
SwimBuddz rubric, writing \
a swimmer's overall feedback from per-stroke notes taken across a few of their \
strokes. Each note carries an aspect id, what was seen, and a severity (fix = work \
on this, strength = doing well, info = neutral).

Synthesise the notes into ONE honest, encouraging, plain-English read spoken \
DIRECTLY to the swimmer ("you", "your").

HONESTY
- Use ONLY what the notes say. Never invent, upgrade, or soften a fault, and never \
invent a strength. If the notes are thin, say less.
- If the SAME fault shows across several strokes, state it ONCE as a recurring \
priority (e.g. "across most of your strokes…"), not once per stroke.

PRIORITISE (fix the foundation first — it unlocks everything else):
1. body_line and head_breath FIRST — a level body and a heavy, eyes-down head let \
you swim downhill; a lifted head or sinking hips make every other fault worse and \
tire you fastest.
2. body_rotation NEXT — rolling onto each side feeds a longer, stronger stroke.
3. entry_reach and recovery_elbow NEXT — streamline and relaxation before force.
4. catch/pull/kick propulsion details LAST, and only if the notes actually support \
them.
Within that order, lead with the notes marked most severe. Return AT MOST 3 \
priority_fixes — fewer is better, never pad. Each fix's "area" MUST be an aspect id \
from the notes.

TI POSITION MODEL
- Treat patient front-quadrant length as an entry/reach issue: one arm stays \
forward while the other recovers, without freezing there forever.
- Treat side-lying skate and hip/shoulder rotation as the clean body position for \
freestyle, after body line and head position are handled.
- Treat relaxed high-elbow recovery as elbow-led with a soft hand; \
fingertip-drag/zipper is a drill cue, not a literal requirement in every stroke.
- Treat kick rhythm as support for rotation. A compact 2-beat kick is often right \
for efficiency/distance, but do not make it universal or mention it unless the \
notes actually show a kick/rhythm issue.

VOICE & SUBSTANCE
- Warm, calm, direct — a real coach at poolside, not a textbook. No hype, no \
jargon, no numbers.
- Per fix: "fault" = one plain sentence on what's off; "why_it_matters" = one \
sentence on how it slows or tires you; "drill" = ONE short, water-safe thing to try \
next session.
- Drill map: body_line/head_breath -> Superman Glide or eyes-down balance focal \
point; recovery_elbow -> Shark Fin or Zipper/fingertip-drag; entry_reach -> \
Patient Lead Hand, SpearSkate, or Switch drill; body_rotation -> Zen Skate or \
Switch drill; kick -> quiet legs in skate or a gentle 2-beat rhythm only when \
visible and goal-appropriate; catch/pull -> request underwater footage or an \
in-person coach unless the notes truly saw it.
- Add a "strength" ONLY if a note genuinely shows one. An empty strengths list is \
honest and fine.

Return ONLY this JSON, nothing else:
{"summary": "<2-3 sentence overall read>",
 "priority_fixes": [{"area": "<aspect id from the notes>", "fault": "<sentence>", "why_it_matters": "<sentence>", "drill": "<one short drill>"}],
 "strengths": ["<sentence>", ...]}"""


class AggregatorComponent(Component):
    name = "aggregator"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)
    # Thinking models share maxOutputTokens with internal reasoning tokens.
    max_tokens = 4096

    def __init__(self, adjudicate_fn: Optional[AdjudicateFn] = None):
        self._adjudicate_fn = adjudicate_fn

    def _ground_drill(
        self,
        *,
        area: Optional[str],
        verdict: Optional[str],
        llm_drill: str,
        discipline: str,
    ) -> dict:
        """Deterministically ground every fix to a curated water-safe TI drill."""
        ti_drill = resolve_ti_drill(area, verdict, discipline=discipline)
        if ti_drill is None:
            safe_copy = llm_drill if is_safe_drill_text(llm_drill) else ""
            return {"drill": safe_copy}
        # Prefer curated TI drill prescription when the LLM left it blank or
        # suggested unsafe / non-TI advice; always attach structured TI drill metadata.
        drill_copy = (
            llm_drill
            if is_safe_drill_text(llm_drill)
            else format_ti_drill_line(ti_drill)
        )
        return {
            "drill": drill_copy,
            "drill_key": ti_drill["key"],
            "drill_title": ti_drill["title"],
            "primary_focal_point": ti_drill["primary_focal_point"],
            "safety_note": ti_drill["safety_note"],
            "academy_ref": ti_drill["academy_ref"],
        }

    def _evidence_jpegs(self, finding: Finding, ctx: RunContext) -> list[bytes]:
        strip = ctx.strip or ctx.frames
        jpegs: list[bytes] = []
        for ref in finding.evidence_frames[:3]:
            if 0 <= ref.index < len(strip):
                jpegs.append(strip[ref.index].jpeg)
        return jpegs

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        chunk = [f for f in ctx.run_findings if f.component == "chunk_coach"]
        if not chunk:
            return ComponentResult(self.name, [])  # nothing to collate
        rep = self._rep_by_area(chunk)  # most-severe chunk per aspect → grounding
        consensus = compute_cross_stroke_consensus(chunk)
        parsed, cost, model, diagnostics = await self._summarize(
            chunk, ctx, consensus=consensus
        )

        if not parsed:  # LLM failed → keep the read via a deterministic synthesis
            fallback_findings = self._fallback(rep, consensus=consensus, ctx=ctx)
            return ComponentResult(
                self.name,
                fallback_findings,
                cost_usd=cost,
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={
                    "summary": None,
                    "model": model,
                    "fallback": True,
                    "diagnostics": diagnostics,
                    "verification": {
                        "consensus": {k: v.to_dict() for k, v in consensus.items()},
                        "filtered_fixes": 0,
                        "adjudications": [],
                    },
                },
            )

        gate_view = getattr(ctx.gate, "view", None) if ctx.gate is not None else None
        is_borderline = gate_view in {"overhead", "mixed"}
        adjudicator_model = (ctx.config.adjudicator_model or "").strip()
        findings: list[Finding] = []
        filtered_fixes = 0
        adjudications: list[dict] = []

        for fx in parsed.get("priority_fixes") or []:
            if not isinstance(fx, dict):
                continue
            fault = str(fx.get("fault") or "").strip()
            if not fault:
                continue
            area = str(fx.get("area") or "").strip() or None
            # Reject hallucinated areas not supported by any verified chunk finding.
            r = rep.get(area or "")
            if r is None:
                filtered_fixes += 1
                continue
            why = str(fx.get("why_it_matters") or "")
            if check_honesty_violations(
                f"{fault} {why}",
                profile=ctx.profile,
                view=gate_view,
                severity=SEVERITY_FIX,
            ):
                filtered_fixes += 1
                continue

            verdict = str((r.extra or {}).get("verdict") or "") or None
            cons = consensus.get(area or "")
            drill_meta = self._ground_drill(
                area=area,
                verdict=verdict,
                llm_drill=str(fx.get("drill") or ""),
                discipline=ctx.coaching.discipline,
            )
            extra = {
                "why_it_matters": why,
                "verdict": verdict,
                "verified": True,
                **drill_meta,
            }
            if cons is not None:
                extra["stroke_support"] = {
                    "count": cons.support_count,
                    "total_chunks": cons.total_coached_chunks,
                    "consistent": cons.consistent,
                    "single_stroke_only": cons.single_stroke_only,
                }

            finding = Finding(
                component=self.name,
                observation=fault,
                severity=SEVERITY_FIX,
                area=area,
                confidence=r.confidence,
                evidence_frames=list(r.evidence_frames),
                instance_id=r.instance_id,
                extra=extra,
            )
            cap_single_stroke_confidence(finding, cons)

            # Optional selective second-model adjudication: invoked ONLY for
            # uncertain top fixes on clean clips when adjudicator_model is set.
            if (
                (adjudicator_model or self._adjudicate_fn is not None)
                and not is_borderline
                and cons is not None
                and cons.needs_adjudication
            ):
                adj_record, adj_cost = await adjudicate_uncertain_finding(
                    finding,
                    self._evidence_jpegs(finding, ctx),
                    model=adjudicator_model or "adjudicator",
                    reasoning_effort=ctx.config.coach_reasoning_effort,
                    adjudicate_fn=self._adjudicate_fn,
                )
                cost += adj_cost
                adjudications.append({"area": area, **adj_record})

            findings.append(finding)

        for w in parsed.get("strengths") or []:
            if isinstance(w, str) and w.strip():
                if check_honesty_violations(
                    w,
                    profile=ctx.profile,
                    view=gate_view,
                    severity=SEVERITY_STRENGTH,
                ):
                    continue
                findings.append(
                    Finding(
                        component=self.name,
                        observation=w.strip(),
                        severity=SEVERITY_STRENGTH,
                        confidence=0.6,
                    )
                )

        return ComponentResult(
            self.name,
            findings,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={
                "summary": parsed.get("summary"),
                "model": model,
                "diagnostics": diagnostics,
                "verification": {
                    "consensus": {k: v.to_dict() for k, v in consensus.items()},
                    "filtered_fixes": filtered_fixes,
                    "adjudications": adjudications,
                },
            },
        )

    def _rep_by_area(self, chunk: list[Finding]) -> dict[str, Finding]:
        """The most-severe chunk finding per aspect (with its evidence) — the chunk a
        top fix points at so it gets a clip + thumbnail."""
        rep: dict[str, Finding] = {}
        for f in chunk:
            a = f.area or ""
            if not a:
                continue
            cur = rep.get(a)
            if cur is None or _SEV_ORDER.get(f.severity, 9) < _SEV_ORDER.get(
                cur.severity, 9
            ):
                rep[a] = f
        return rep

    async def _summarize(
        self, chunk: list[Finding], ctx: RunContext, *, consensus: dict
    ):
        """Text-only LLM over the structured notes → (parsed dict | None, cost, model).
        Uses call_vlm with no images so it inherits the patient rate-limit retry."""
        notes = []
        for f in chunk:
            if not f.observation:
                continue
            cons = consensus.get(f.area or "")
            note_item = {
                "aspect": f.area,
                "severity": f.severity,
                "verdict": (f.extra or {}).get("verdict"),
                "seen": f.observation,
                "stroke_s": (f.extra or {}).get("t"),
            }
            if cons is not None and f.severity == SEVERITY_FIX:
                note_item["seen_in_strokes"] = (
                    f"{cons.support_count}/{cons.total_coached_chunks}"
                )
                note_item["recurring"] = cons.consistent
            notes.append(note_item)
        from services.ai_service.providers.base import call_vlm  # lazy: needs litellm

        diagnostics = {"notes_count": len(notes), "max_tokens": self.max_tokens}
        try:
            resp = await call_vlm(
                system_prompt=f"{AGG_PROMPT}\n\n{build_ti_block()}\n\n{COACH_VOICE}",
                user_prompt=(
                    "Per-stroke notes (prefer recurring issues seen across multiple "
                    "strokes over single-stroke outliers):\n"
                    + json.dumps(notes, ensure_ascii=False)
                    + "\n\nReturn only the JSON."
                ),
                images=[],
                model=ctx.config.coach_model,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                reasoning_effort=ctx.config.coach_reasoning_effort,
                trace_name="strokelab_aggregate",
            )
        except Exception as exc:  # rate-limit slipped past retries / provider failure
            diagnostics.update(
                {
                    "parse_ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                }
            )
            logger.warning(
                "aggregator: summary call failed; model=%s max_tokens=%s "
                "notes=%s error=%s",
                ctx.config.coach_model,
                self.max_tokens,
                len(notes),
                f"{type(exc).__name__}: {exc}",
            )
            return None, 0.0, "fallback", diagnostics

        diagnostics.update(
            {
                "model": resp.model,
                "provider": resp.provider,
                "output_tokens": resp.output_tokens,
                "content_chars": len(resp.content or ""),
            }
        )
        try:
            parsed = resp.parse_json()
            diagnostics.update(
                {
                    "parse_ok": True,
                    "priority_fix_count": len(parsed.get("priority_fixes") or []),
                    "strength_count": len(parsed.get("strengths") or []),
                    "has_summary": bool(parsed.get("summary")),
                }
            )
            return parsed, resp.cost_usd, resp.model, diagnostics
        except Exception as exc:  # rate-limit slipped past retries / bad JSON
            content = resp.content or ""
            diagnostics.update(
                {
                    "parse_ok": False,
                    "parse_error_type": type(exc).__name__,
                    "content_start": " ".join(content.split())[
                        :_AGG_PARSE_SNIPPET_CHARS
                    ],
                }
            )
            logger.warning(
                "aggregator: JSON parse failed; model=%s provider=%s "
                "max_tokens=%s output_tokens=%s content_chars=%s error=%s "
                "content_start=%r",
                resp.model,
                resp.provider,
                self.max_tokens,
                resp.output_tokens,
                len(content),
                f"{type(exc).__name__}: {exc}",
                diagnostics["content_start"],
            )
            return None, resp.cost_usd, resp.model, diagnostics

    def _fallback(
        self,
        rep: dict[str, Finding],
        *,
        consensus: dict,
        ctx: RunContext,
    ) -> list[Finding]:
        """No summary text, but re-surface the deduped fixes/strengths so the
        top-line read survives an LLM failure (each still pinned to its chunk)."""
        out: list[Finding] = []
        for area, f in rep.items():
            if f.severity not in (SEVERITY_FIX, SEVERITY_STRENGTH):
                continue
            extra = dict(f.extra or {})
            if f.severity == SEVERITY_FIX:
                verdict = str(extra.get("verdict") or "") or None
                extra.update(
                    self._ground_drill(
                        area=area,
                        verdict=verdict,
                        llm_drill=str(extra.get("drill") or ""),
                        discipline=ctx.coaching.discipline,
                    )
                )
            finding = Finding(
                component=self.name,
                observation=f.observation,
                severity=f.severity,
                area=area,
                confidence=f.confidence,
                evidence_frames=list(f.evidence_frames),
                instance_id=f.instance_id,
                extra=extra,
            )
            if f.severity == SEVERITY_FIX:
                cap_single_stroke_confidence(finding, consensus.get(area))
            out.append(finding)
        return out
