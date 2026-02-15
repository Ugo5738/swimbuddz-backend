"""AI-assisted coach suggestion for cohorts.

Given a scored cohort and a list of eligible coaches, uses an LLM to
rank the coaches by suitability and provide rationale for each.
"""

from libs.common.logging import get_logger
from services.ai_service.providers.base import AIProviderResponse, call_llm

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert swimming education staffing advisor for SwimBuddz, a swimming academy in Lagos, Nigeria.

Your task is to rank a list of eligible coaches for a specific cohort assignment, based on:
1. The cohort's complexity profile (7 dimension scores)
2. Each coach's grade level, experience hours, and feedback rating

Ranking criteria (in order of importance):
- Grade match: A coach whose grade exactly matches the required grade is preferred over one
  who far exceeds it (avoid over-qualification waste), but never recommend an under-qualified coach.
- Experience alignment: More coaching hours = more reliable for complex cohorts.
- Feedback quality: Higher average rating indicates better student outcomes.
- Availability signal: Coaches with fewer hours may be newer and benefit from appropriate assignments.

For each coach, assign:
- match_score (0.0 to 1.0): overall suitability, where 1.0 = perfect fit
- rationale: 1-2 sentence explanation of why this coach is/isn't a good fit

Return valid JSON matching the specified schema."""

USER_PROMPT_TEMPLATE = """Rank these coaches for the following cohort:

COHORT DETAILS:
- Program: {program_name}
- Category: {program_category}
- Cohort: {cohort_name}
- Location: {location}
- Capacity: {capacity} students
- Total Complexity Score: {total_score}/35
- Required Coach Grade: {required_coach_grade}
- Dimension Breakdown: {dimension_summary}

ELIGIBLE COACHES:
{coaches_text}

Return JSON with this exact structure:
{{
    "rankings": [
        {{"member_id": "<uuid>", "name": "<name>", "match_score": <0-1>, "rationale": "<why>"}},
        ...
    ]
}}

Sort by match_score descending (best fit first). Include ALL coaches in the ranking."""


def _format_coaches(coaches: list[dict]) -> str:
    """Format coach list for the LLM prompt."""
    lines = []
    for i, c in enumerate(coaches, 1):
        rating = c.get("average_feedback_rating")
        rating_str = f"{rating:.1f}/5.0" if rating else "No ratings yet"
        lines.append(
            f"{i}. {c.get('name', 'Unknown')} "
            f"(ID: {c['member_id']}, "
            f"Grade: {c.get('grade', 'unknown')}, "
            f"Hours: {c.get('total_coaching_hours', 0)}, "
            f"Rating: {rating_str})"
        )
    return "\n".join(lines)


async def suggest_coaches(
    request: dict,
    model: str = None,
) -> tuple[dict, AIProviderResponse]:
    """Rank coaches for a cohort using an LLM.

    Args:
        request: Dict with program_category, cohort_name, program_name,
                 total_score, required_coach_grade, dimension_summary,
                 location, capacity, coaches (list of dicts).
        model: Optional LLM model override.

    Returns:
        (parsed_result_dict, ai_response)
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        program_name=request.get("program_name", ""),
        program_category=request.get("program_category", ""),
        cohort_name=request.get("cohort_name", ""),
        location=request.get("location", "Not specified"),
        capacity=request.get("capacity", "?"),
        total_score=request.get("total_score", "?"),
        required_coach_grade=request.get("required_coach_grade", "?"),
        dimension_summary=request.get("dimension_summary", ""),
        coaches_text=_format_coaches(request.get("coaches", [])),
    )

    ai_response = await call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        temperature=0.15,
        trace_name="coach_suggestion",
    )

    parsed = ai_response.parse_json()
    return parsed, ai_response
