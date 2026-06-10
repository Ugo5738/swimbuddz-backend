"""LLM-generated qualitative summary for a Stroke Lab analysis.

Per the design doc: 2-3 sentences of plain-language feedback derived from
the numerical metrics. Strictly observational — never prescriptive — so
the product can be marketed as a "technique check" not a "coach
replacement".

Uses the existing `services.ai_service.providers.call_llm` abstraction so
the model is configurable via env (AI_DEFAULT_MODEL). Default target is
Claude Haiku for cost.
"""

from __future__ import annotations

from typing import Optional

from libs.common.logging import get_logger

from services.ai_service.providers.base import call_llm

logger = get_logger(__name__)


SYSTEM_PROMPT = """You write short, observational feedback for a swimmer who has just \
uploaded a video to Stroke Lab. Stroke Lab is a measurement tool — not a coach.

Rules you must follow:
  1. Two to three sentences total. No bullet points.
  2. Describe ONLY what the numbers say. Never prescribe drills, never say \
"you should", never compare to elites.
  3. If a metric is missing (None), do not invent a value.
  4. Always end with the literal sentence: "Not a coach replacement — \
share the clip with a coach for personal guidance."
  5. Plain English. No jargon ("catch", "high elbow", etc.).

Output the feedback as raw text. No preamble, no markdown.
"""


async def generate_summary(metrics: dict, stroke_type: str) -> Optional[str]:
    """Produce a 2-3 sentence summary from the metrics dict.

    Returns None (not an empty string) on failure so the caller can store
    `summary_text=None` and the API response can flag it as unavailable
    without lying about the model output.
    """
    user_lines = [f"Stroke type: {stroke_type}"]
    for key, label in (
        ("pose_detection_rate", "Pose detection rate"),
        ("stroke_rate_spm", "Stroke rate (strokes per minute)"),
        ("body_roll_proxy_degrees", "Body roll proxy (degrees)"),
        ("breath_count_left", "Left-side breaths"),
        ("breath_count_right", "Right-side breaths"),
        ("breath_balance_left_ratio", "Left/total breath ratio"),
    ):
        v = metrics.get(key)
        if v is not None:
            user_lines.append(f"{label}: {v}")
    user_prompt = "\n".join(user_lines)

    try:
        response = await call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=200,
            trace_name="strokelab_summary",
        )
        text = (response.content or "").strip()
        if not text:
            return None
        return text
    except Exception as exc:
        logger.warning("Stroke Lab summary LLM call failed: %s — returning None", exc)
        return None
