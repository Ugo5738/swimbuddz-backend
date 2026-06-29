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

from libs.common.logging import get_logger
from services.ai_service.pipeline.component import Component
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

logger = get_logger(__name__)

# Severity priority for picking the representative (worst) chunk of an aspect.
_SEV_ORDER = {SEVERITY_FIX: 0, SEVERITY_STRENGTH: 1}

AGG_PROMPT = """You are the head freestyle coach writing a swimmer's overall \
feedback from per-stroke notes taken across a few of their strokes. Each note has \
an aspect, what was seen, and how serious it is (fix = work on this, strength = \
doing well, info = neutral).

Synthesise them into ONE honest, encouraging, plain-English coaching read. Rules:
- Use ONLY what's in the notes — never invent a fault or a strength.
- If the SAME fault shows across strokes, state it ONCE as a recurring priority.
- Order the fixes by impact (use the notes' severity).
- Be concrete and kind; no jargon.

Return ONLY this JSON:
{"summary": "<2-3 sentence overall read>",
 "priority_fixes": [{"area": "<the aspect id from the notes>", "fault": "<one sentence>", "why_it_matters": "<one sentence>", "drill": "<one short drill to try>"}],
 "strengths": ["<one sentence>", ...]}"""


class AggregatorComponent(Component):
    name = "aggregator"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        chunk = [f for f in ctx.run_findings if f.component == "chunk_coach"]
        if not chunk:
            return ComponentResult(self.name, [])  # nothing to collate
        rep = self._rep_by_area(chunk)  # most-severe chunk per aspect → grounding
        parsed, cost, model = await self._summarize(chunk, ctx)

        if not parsed:  # LLM failed → keep the read via a deterministic synthesis
            return ComponentResult(
                self.name,
                self._fallback(rep),
                cost_usd=cost,
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"summary": None, "model": model, "fallback": True},
            )

        findings: list[Finding] = []
        for fx in parsed.get("priority_fixes") or []:
            if not isinstance(fx, dict):
                continue
            fault = str(fx.get("fault") or "").strip()
            if not fault:
                continue
            area = str(fx.get("area") or "").strip() or None
            r = rep.get(area or "")
            findings.append(
                Finding(
                    component=self.name,
                    observation=fault,
                    severity=SEVERITY_FIX,
                    area=area,
                    confidence=r.confidence if r else 0.6,
                    evidence_frames=list(r.evidence_frames) if r else [],
                    instance_id=r.instance_id if r else None,
                    extra={
                        "why_it_matters": str(fx.get("why_it_matters") or ""),
                        "drill": str(fx.get("drill") or ""),
                    },
                )
            )
        for w in parsed.get("strengths") or []:
            if isinstance(w, str) and w.strip():
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
            meta={"summary": parsed.get("summary"), "model": model},
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

    async def _summarize(self, chunk: list[Finding], ctx: RunContext):
        """Text-only LLM over the structured notes → (parsed dict | None, cost, model).
        Uses call_vlm with no images so it inherits the patient rate-limit retry."""
        notes = [
            {
                "aspect": f.area,
                "severity": f.severity,
                "seen": f.observation,
                "stroke_s": (f.extra or {}).get("t"),
            }
            for f in chunk
            if f.observation
        ]
        from services.ai_service.providers.base import call_vlm  # lazy: needs litellm

        try:
            resp = await call_vlm(
                system_prompt=AGG_PROMPT,
                user_prompt=(
                    "Per-stroke notes:\n"
                    + json.dumps(notes, ensure_ascii=False)
                    + "\n\nReturn only the JSON."
                ),
                images=[],
                model=ctx.config.coach_model,
                max_tokens=900,
                response_format={"type": "json_object"},
                trace_name="strokelab_aggregate",
            )
            return resp.parse_json(), resp.cost_usd, resp.model
        except Exception as exc:  # rate-limit slipped past retries / bad JSON
            logger.warning(
                "aggregator: summary failed (%s) — deterministic fallback", exc
            )
            return None, 0.0, "fallback"

    def _fallback(self, rep: dict[str, Finding]) -> list[Finding]:
        """No summary text, but re-surface the deduped fixes/strengths so the
        top-line read survives an LLM failure (each still pinned to its chunk)."""
        out: list[Finding] = []
        for area, f in rep.items():
            if f.severity not in (SEVERITY_FIX, SEVERITY_STRENGTH):
                continue
            out.append(
                Finding(
                    component=self.name,
                    observation=f.observation,
                    severity=f.severity,
                    area=area,
                    confidence=f.confidence,
                    evidence_frames=list(f.evidence_frames),
                    instance_id=f.instance_id,
                    extra=dict(f.extra or {}),
                )
            )
        return out
