"""Shared helpers for the coach agreement & handbook routers."""

import hashlib
import re
from datetime import date as date_type

from fastapi import HTTPException
from services.members_service.models import AgreementVersion, CoachProfile, Member
from sqlalchemy import select


def _strip_internal_handbook_sections(content: str) -> str:
    """
    Coaches should not see internal-only appendices (e.g. Appendix B: system integration spec).
    Filter at the API boundary (defense in depth, even if the frontend also hides it).
    """
    m = re.search(r"^##\s+Appendix\s+B\b.*$", content, flags=re.MULTILINE)
    if not m:
        return content
    return content[: m.start()].rstrip() + "\n"


def _compute_agreement_hash(content: str) -> str:
    """Compute SHA-256 hash of agreement content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_agreement_for_coach(
    template_content: str,
    member: "Member",
    coach_profile: "CoachProfile",
) -> str:
    """Render agreement template by replacing placeholders with coach data.

    Placeholders replaced:
      [DATE]             -> today's date
      [COACH FULL NAME]  -> member first + last name
      [Coach Address]    -> from member profile (address, city, state)
      [Phone Number]     -> from member profile
      [Email Address]    -> from member record
      [PERCENTAGE]       -> "See Coach Handbook" (varies by assignment)
      [X]                -> highest grade number
      [Category]         -> highest grade category
      [GRADE LEVEL]      -> current grade description
    """
    profile = member.profile

    # Build address string
    address_parts = []
    if profile and profile.address:
        address_parts.append(profile.address)
    if profile and profile.city:
        address_parts.append(profile.city)
    if profile and profile.state:
        address_parts.append(profile.state)
    address_str = ", ".join(address_parts) if address_parts else "Not provided"

    phone_str = profile.phone if profile and profile.phone else "Not provided"
    full_name = f"{member.first_name} {member.last_name}"

    # Determine highest grade from category grades
    grade_fields = {
        "Learn to Swim": coach_profile.learn_to_swim_grade,
        "Special Populations": coach_profile.special_populations_grade,
        "Institutional": coach_profile.institutional_grade,
        "Competitive/Elite": coach_profile.competitive_elite_grade,
        "Certifications": coach_profile.certifications_grade,
        "Specialized Disciplines": coach_profile.specialized_disciplines_grade,
        "Adjacent Services": coach_profile.adjacent_services_grade,
    }
    grade_order = {"grade_1": 1, "grade_2": 2, "grade_3": 3}
    highest_grade = None
    highest_category = None
    highest_num = 0
    for category, grade_val in grade_fields.items():
        if grade_val and grade_order.get(grade_val, 0) > highest_num:
            highest_num = grade_order[grade_val]
            highest_grade = grade_val
            highest_category = category

    grade_labels = {
        "grade_1": "Grade 1 – Foundational",
        "grade_2": "Grade 2 – Technical",
        "grade_3": "Grade 3 – Advanced/Specialist",
    }
    has_grades = highest_grade is not None

    # Perform replacements
    rendered = template_content
    rendered = rendered.replace("[DATE]", date_type.today().strftime("%B %d, %Y"))
    rendered = rendered.replace("[COACH FULL NAME]", full_name)
    rendered = rendered.replace("[Coach Address]", address_str)
    rendered = rendered.replace("[Phone Number]", phone_str)
    rendered = rendered.replace("[Email Address]", member.email)

    # Grade level
    if has_grades:
        rendered = rendered.replace("[GRADE LEVEL]", grade_labels[highest_grade])
    else:
        rendered = rendered.replace(
            "Current Grade: **[GRADE LEVEL]**",
            "Current Grade: **To be determined upon assignment**",
        )
        # Fallback if pattern doesn't match exactly
        rendered = rendered.replace("[GRADE LEVEL]", "To be determined upon assignment")

    # Revenue share line: **[PERCENTAGE]%** (Grade [X], [Category])
    # The template has: **[PERCENTAGE]%** (Grade [X], [Category])
    if has_grades:
        grade_num_str = str(highest_num)
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            f"**See Coach Handbook** (Grade {grade_num_str}, {highest_category})",
        )
    else:
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            "**To be determined upon grade assignment** (see Coach Handbook for pay bands)",
        )

    # Fallback for any remaining individual placeholders
    rendered = rendered.replace("[PERCENTAGE]", "TBD")
    rendered = rendered.replace("[X]", str(highest_num) if has_grades else "TBD")
    rendered = rendered.replace("[Category]", highest_category or "TBD")

    return rendered


async def _get_current_agreement_version(session) -> AgreementVersion:
    """Get the current agreement version from the database.

    Raises HTTPException(404) if no current version exists.
    """
    result = await session.execute(
        select(AgreementVersion).where(AgreementVersion.is_current.is_(True))
    )
    current = result.scalar_one_or_none()
    if not current:
        raise HTTPException(
            status_code=404,
            detail="No current agreement version found. Contact an administrator.",
        )
    return current
