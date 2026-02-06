"""AI-assisted coach grade scoring.

Evaluates a coach's readiness for grade promotion based on
their experience, certifications, feedback, and shadow evaluations.
"""

from libs.common.logging import get_logger
from services.ai_service.providers.base import AIProviderResponse, call_llm
from services.ai_service.schemas import (
    CoachGradeScoringRequest,
    CoachGradeScoringResponse,
)

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert swimming education HR advisor for SwimBuddz, a swimming academy in Lagos, Nigeria.

Your task is to evaluate a coach's readiness for grade progression.

Coach Grade Levels:
- grade_1: Entry-level. Can teach beginner classes with support. Requirements: completed shadow training, CPR certified, background check clear.
- grade_2: Experienced. Can teach intermediate classes independently. Requirements: 50+ hours, 3+ completed cohorts, 4.0+ rating.
- grade_3: Expert. Can teach any class, mentor other coaches. Requirements: 200+ hours, 10+ lead cohorts, 4.3+ rating.

Evaluate the coach holistically. Consider both quantitative metrics and qualitative factors.
Be encouraging but honest. Identify specific areas for growth.

Return your response as valid JSON matching the specified schema."""

USER_PROMPT_TEMPLATE = """Evaluate this coach for grade progression:

- Current Grade: {current_grade}
- Total Coaching Hours: {coaching_hours}
- Cohorts Completed: {cohorts_completed}
- Average Feedback Rating: {feedback_rating}/5.0
- Certifications: {certifications}
- Shadow Evaluations Passed: {shadow_evaluations_passed}

Return JSON with this exact structure:
{{
    "recommended_grade": "<grade_1|grade_2|grade_3>",
    "rationale": "<detailed explanation of recommendation>",
    "areas_for_improvement": ["<area 1>", "<area 2>", ...],
    "strengths": ["<strength 1>", "<strength 2>", ...],
    "confidence": <0-1>
}}"""


async def score_coach_grade(
    request: CoachGradeScoringRequest,
    model: str = None,
) -> tuple[CoachGradeScoringResponse, AIProviderResponse]:
    """Score a coach's grade readiness using an LLM.

    Returns both the parsed response and the raw AI provider response
    for logging purposes.
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        current_grade=request.current_grade or "none",
        coaching_hours=request.coaching_hours,
        cohorts_completed=request.cohorts_completed,
        feedback_rating=request.feedback_rating,
        certifications=(
            ", ".join(request.certifications) if request.certifications else "None"
        ),
        shadow_evaluations_passed=request.shadow_evaluations_passed,
    )

    ai_response = await call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        temperature=0.2,
        trace_name="coach_grade_scoring",
    )

    # Parse structured output
    parsed = ai_response.parse_json()

    result = CoachGradeScoringResponse(
        recommended_grade=parsed["recommended_grade"],
        rationale=parsed["rationale"],
        areas_for_improvement=parsed.get("areas_for_improvement", []),
        strengths=parsed.get("strengths", []),
        confidence=parsed.get("confidence", 0.8),
        ai_request_id="",  # Set by caller after logging
        model_used=ai_response.model,
    )

    return result, ai_response
