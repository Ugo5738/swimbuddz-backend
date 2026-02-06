"""AI-assisted cohort complexity scoring.

Generates complexity dimension scores for a cohort based on its
characteristics (age group, skill level, special needs, etc.).
"""

import json

from libs.common.logging import get_logger
from services.ai_service.providers.base import AIProviderResponse, call_llm
from services.ai_service.schemas import (
    CohortComplexityScoringRequest,
    CohortComplexityScoringResponse,
    DimensionScore,
)

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert swimming education analyst for SwimBuddz, a swimming academy in Lagos, Nigeria.

Your task is to evaluate the complexity of a swimming cohort across 7 standardized dimensions.
Each dimension is scored 1-5 where:
- 1 = Very Low complexity (minimal coach expertise needed)
- 2 = Low complexity
- 3 = Medium complexity
- 4 = High complexity
- 5 = Very High complexity (requires expert-level coach)

The 7 dimensions are:
1. skill_technical: Technical skill difficulty of the curriculum
2. safety_risk: Water safety risk level (age, ability, venue)
3. class_management: Difficulty of managing the class dynamics
4. curriculum_depth: Depth and breadth of curriculum content
5. student_diversity: Variation in student abilities/needs
6. environmental_complexity: Venue and environmental factors
7. assessment_complexity: How complex the evaluation/progress tracking is

Based on the total score, recommend a coach grade:
- grade_1 (total 7-14): Entry-level coach can handle
- grade_2 (total 15-25): Experienced coach needed
- grade_3 (total 26-35): Expert coach required

Return your response as valid JSON matching the specified schema."""

USER_PROMPT_TEMPLATE = """Evaluate the complexity of this swimming cohort:

- Program Category: {program_category}
- Age Group: {age_group}
- Skill Level: {skill_level}
- Special Needs: {special_needs}
- Location Type: {location_type}
- Duration: {duration_weeks} weeks
- Class Size: {class_size} students

Return JSON with this exact structure:
{{
    "dimensions": [
        {{"dimension": "skill_technical", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "safety_risk", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "class_management", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "curriculum_depth", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "student_diversity", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "environmental_complexity", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "assessment_complexity", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}}
    ],
    "total_score": <sum of all scores>,
    "required_coach_grade": "<grade_1|grade_2|grade_3>",
    "overall_rationale": "<summary explanation>",
    "confidence": <overall confidence 0-1>
}}"""


async def score_cohort_complexity(
    request: CohortComplexityScoringRequest,
    model: str = None,
) -> tuple[CohortComplexityScoringResponse, AIProviderResponse]:
    """Score cohort complexity using an LLM.

    Returns both the parsed response and the raw AI provider response
    for logging purposes.
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        program_category=request.program_category,
        age_group=request.age_group,
        skill_level=request.skill_level,
        special_needs=request.special_needs or "None",
        location_type=request.location_type,
        duration_weeks=request.duration_weeks,
        class_size=request.class_size,
    )

    ai_response = await call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        temperature=0.1,
        trace_name="cohort_complexity_scoring",
    )

    # Parse structured output
    parsed = ai_response.parse_json()

    dimensions = [
        DimensionScore(
            dimension=d["dimension"],
            score=d["score"],
            rationale=d["rationale"],
            confidence=d.get("confidence", 0.8),
        )
        for d in parsed["dimensions"]
    ]

    result = CohortComplexityScoringResponse(
        dimensions=dimensions,
        total_score=parsed["total_score"],
        required_coach_grade=parsed["required_coach_grade"],
        overall_rationale=parsed["overall_rationale"],
        confidence=parsed.get("confidence", 0.8),
        ai_request_id="",  # Set by caller after logging
        model_used=ai_response.model,
    )

    return result, ai_response
