"""AI-assisted cohort complexity scoring.

Generates complexity dimension scores for a cohort based on its
characteristics (age group, skill level, special needs, etc.).

The dimension names are category-specific — e.g. a "Learn to Swim"
cohort is scored on "Age Group Complexity", "Skill Phase", etc., while
an "Institutional" cohort uses "Institution Type", "Logistics
Complexity", etc.  The full mapping lives in
``services.academy_service.scoring.DIMENSION_LABELS``.
"""

from libs.common.logging import get_logger
from services.ai_service.providers.base import AIProviderResponse, call_llm
from services.ai_service.schemas import (
    CohortComplexityScoringRequest,
    CohortComplexityScoringResponse,
    DimensionScore,
)

logger = get_logger(__name__)

# ── Category → Dimension Labels ────────────────────────────────────
# Mirror of academy_service.scoring.DIMENSION_LABELS so that the AI
# prompt uses the correct per-category labels.

CATEGORY_DIMENSIONS: dict[str, list[str]] = {
    "learn_to_swim": [
        "Age Group Complexity",
        "Skill Phase",
        "Learner-to-Coach Ratio",
        "Emotional Labour",
        "Environment",
        "Session Prep & Adaptation",
        "Parent/Guardian Management",
    ],
    "special_populations": [
        "Population Type",
        "Medical/Safety Coordination",
        "Adaptation Intensity",
        "Psychological Sensitivity",
        "Caregiver/Support Coordination",
        "Liability & Documentation",
        "Coach Certification Required",
    ],
    "institutional": [
        "Institution Type",
        "Group Size",
        "Logistics Complexity",
        "Reporting & Accountability",
        "Customization Required",
        "Stakeholder Management",
        "Contract/Commercial Pressure",
    ],
    "competitive_elite": [
        "Performance Level",
        "Training Volume",
        "Periodization Complexity",
        "Technical Precision",
        "Mental Performance Coaching",
        "Athlete Management",
        "Competition & Travel",
    ],
    "certifications": [
        "Certification Type",
        "Assessment Rigor",
        "Curriculum Standardization",
        "Instructor Qualification Required",
        "Liability & Compliance",
        "Pass Rate Pressure",
        "Materials & Equipment",
    ],
    "specialized_disciplines": [
        "Discipline Type",
        "Technical Specialization",
        "Safety & Risk Profile",
        "Equipment & Facility Requirements",
        "Physical Conditioning Demands",
        "Coach Background Required",
        "Competition/Performance Pathway",
    ],
    "adjacent_services": [
        "Service Type",
        "Participant Management",
        "External Partnerships",
        "Operational Complexity",
        "Staff Requirements",
        "Revenue & Commercial Model",
        "Risk & Insurance",
    ],
}


SYSTEM_PROMPT = """You are an expert swimming education analyst for SwimBuddz, a swimming academy in Lagos, Nigeria.

Your task is to evaluate the complexity of a swimming cohort across 7 standardised dimensions.
Each dimension is scored 1-5 where:
- 1 = Very Low complexity (minimal coach expertise needed)
- 2 = Low complexity
- 3 = Medium complexity
- 4 = High complexity
- 5 = Very High complexity (requires expert-level coach)

The 7 dimensions are CATEGORY-SPECIFIC and will be listed in the user prompt.

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

Score the following 7 dimensions (specific to the "{program_category}" category):
{dimension_list}

Return JSON with this exact structure:
{{
    "dimensions": [
        {{"dimension": "dimension_1", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_2", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_3", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_4", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_5", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_6", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}},
        {{"dimension": "dimension_7", "score": <1-5>, "rationale": "<why>", "confidence": <0-1>}}
    ],
    "total_score": <sum of all scores>,
    "required_coach_grade": "<grade_1|grade_2|grade_3>",
    "overall_rationale": "<summary explanation>",
    "confidence": <overall confidence 0-1>
}}"""


def _build_dimension_list(category: str) -> str:
    """Build a numbered list of category-specific dimension labels for the prompt."""
    labels = CATEGORY_DIMENSIONS.get(category, CATEGORY_DIMENSIONS["learn_to_swim"])
    return "\n".join(
        f"{i + 1}. dimension_{i + 1}: {label}" for i, label in enumerate(labels)
    )


async def score_cohort_complexity(
    request: CohortComplexityScoringRequest,
    model: str = None,
) -> tuple[CohortComplexityScoringResponse, AIProviderResponse]:
    """Score cohort complexity using an LLM.

    Returns both the parsed response and the raw AI provider response
    for logging purposes.
    """
    category_key = request.program_category
    dimension_list = _build_dimension_list(category_key)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        program_category=request.program_category,
        age_group=request.age_group,
        skill_level=request.skill_level,
        special_needs=request.special_needs or "None",
        location_type=request.location_type,
        duration_weeks=request.duration_weeks,
        class_size=request.class_size,
        dimension_list=dimension_list,
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
