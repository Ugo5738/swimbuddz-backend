"""
Cohort Complexity Scoring Logic

Based on the SwimBuddz Coach Operations Framework.
See docs/academy/COHORT_SCORING_TOOL.md for full documentation.
"""

from typing import List, Tuple

from services.academy_service.models import CoachGrade, ProgramCategory

# ============================================================================
# PAY BAND LOOKUP TABLE
# ============================================================================

# Pay bands by category and grade
# Values are (min_percentage, max_percentage) as integers
PAY_BANDS: dict[ProgramCategory, dict[CoachGrade, Tuple[int, int]]] = {
    ProgramCategory.LEARN_TO_SWIM: {
        CoachGrade.GRADE_1: (35, 42),
        CoachGrade.GRADE_2: (43, 52),
        CoachGrade.GRADE_3: (53, 65),
    },
    ProgramCategory.SPECIAL_POPULATIONS: {
        CoachGrade.GRADE_1: (38, 45),
        CoachGrade.GRADE_2: (46, 55),
        CoachGrade.GRADE_3: (56, 68),
    },
    ProgramCategory.INSTITUTIONAL: {
        CoachGrade.GRADE_1: (33, 40),
        CoachGrade.GRADE_2: (41, 50),
        CoachGrade.GRADE_3: (51, 62),
    },
    ProgramCategory.COMPETITIVE_ELITE: {
        CoachGrade.GRADE_1: (37, 44),
        CoachGrade.GRADE_2: (45, 55),
        CoachGrade.GRADE_3: (55, 68),
    },
    ProgramCategory.CERTIFICATIONS: {
        # Grade 1 cannot deliver certification programs
        CoachGrade.GRADE_1: (0, 0),  # Not applicable
        CoachGrade.GRADE_2: (46, 55),
        CoachGrade.GRADE_3: (56, 68),
    },
    ProgramCategory.SPECIALIZED_DISCIPLINES: {
        CoachGrade.GRADE_1: (37, 44),
        CoachGrade.GRADE_2: (45, 55),
        CoachGrade.GRADE_3: (55, 68),
    },
    ProgramCategory.ADJACENT_SERVICES: {
        CoachGrade.GRADE_1: (30, 40),
        CoachGrade.GRADE_2: (40, 55),
        CoachGrade.GRADE_3: (55, 65),
    },
}


# ============================================================================
# DIMENSION LABELS BY CATEGORY
# ============================================================================

DIMENSION_LABELS: dict[ProgramCategory, List[str]] = {
    ProgramCategory.LEARN_TO_SWIM: [
        "Age Group Complexity",
        "Skill Phase",
        "Learner-to-Coach Ratio",
        "Emotional Labour",
        "Environment",
        "Session Prep & Adaptation",
        "Parent/Guardian Management",
    ],
    ProgramCategory.SPECIAL_POPULATIONS: [
        "Population Type",
        "Medical/Safety Coordination",
        "Adaptation Intensity",
        "Psychological Sensitivity",
        "Caregiver/Support Coordination",
        "Liability & Documentation",
        "Coach Certification Required",
    ],
    ProgramCategory.INSTITUTIONAL: [
        "Institution Type",
        "Group Size",
        "Logistics Complexity",
        "Reporting & Accountability",
        "Customization Required",
        "Stakeholder Management",
        "Contract/Commercial Pressure",
    ],
    ProgramCategory.COMPETITIVE_ELITE: [
        "Performance Level",
        "Training Volume",
        "Periodization Complexity",
        "Technical Precision",
        "Mental Performance Coaching",
        "Athlete Management",
        "Competition & Travel",
    ],
    ProgramCategory.CERTIFICATIONS: [
        "Certification Type",
        "Assessment Rigor",
        "Curriculum Standardization",
        "Instructor Qualification Required",
        "Liability & Compliance",
        "Pass Rate Pressure",
        "Materials & Equipment",
    ],
    ProgramCategory.SPECIALIZED_DISCIPLINES: [
        "Discipline Type",
        "Technical Specialization",
        "Safety & Risk Profile",
        "Equipment & Facility Requirements",
        "Physical Conditioning Demands",
        "Coach Background Required",
        "Competition/Performance Pathway",
    ],
    ProgramCategory.ADJACENT_SERVICES: [
        "Service Type",
        "Participant Management",
        "External Partnerships",
        "Operational Complexity",
        "Staff Requirements",
        "Revenue & Commercial Model",
        "Risk & Insurance",
    ],
}


# ============================================================================
# SCORING FUNCTIONS
# ============================================================================


def calculate_total_score(dimension_scores: List[int]) -> int:
    """
    Calculate total complexity score from dimension scores.

    Args:
        dimension_scores: List of 7 scores (1-5 each)

    Returns:
        Total score (7-35)

    Raises:
        ValueError: If scores are invalid
    """
    if len(dimension_scores) != 7:
        raise ValueError("Exactly 7 dimension scores required")

    for i, score in enumerate(dimension_scores):
        if score < 1 or score > 5:
            raise ValueError(
                f"Dimension {i + 1} score must be between 1 and 5, got {score}"
            )

    return sum(dimension_scores)


def determine_coach_grade(total_score: int) -> CoachGrade:
    """
    Determine required coach grade from total complexity score.

    Args:
        total_score: Sum of all dimension scores (7-35)

    Returns:
        Required CoachGrade
    """
    if total_score <= 14:
        return CoachGrade.GRADE_1
    elif total_score <= 24:
        return CoachGrade.GRADE_2
    else:
        return CoachGrade.GRADE_3


def get_pay_band(category: ProgramCategory, grade: CoachGrade) -> Tuple[int, int]:
    """
    Get pay band (min, max percentages) for category and grade.

    Args:
        category: Program category
        grade: Coach grade

    Returns:
        Tuple of (min_percentage, max_percentage)

    Raises:
        ValueError: If no pay band defined for combination
    """
    category_bands = PAY_BANDS.get(category)
    if category_bands is None:
        raise ValueError(f"No pay bands defined for category: {category}")

    band = category_bands.get(grade)
    if band is None:
        raise ValueError(f"No pay band defined for {category} at {grade}")

    # Special case: Grade 1 cannot deliver certification programs
    if category == ProgramCategory.CERTIFICATIONS and grade == CoachGrade.GRADE_1:
        raise ValueError("Grade 1 coaches cannot deliver certification programs")

    return band


def calculate_complexity_score(
    category: ProgramCategory, dimension_scores: List[int]
) -> dict:
    """
    Calculate complete complexity score result.

    Args:
        category: The program category
        dimension_scores: List of 7 scores (1-5 each)

    Returns:
        dict with total_score, required_coach_grade, pay_band_min, pay_band_max
    """
    total_score = calculate_total_score(dimension_scores)
    required_grade = determine_coach_grade(total_score)
    pay_band_min, pay_band_max = get_pay_band(category, required_grade)

    return {
        "total_score": total_score,
        "required_coach_grade": required_grade,
        "pay_band_min": pay_band_min,
        "pay_band_max": pay_band_max,
    }


def is_coach_eligible_for_grade(
    coach_grade: CoachGrade, required_grade: CoachGrade
) -> bool:
    """
    Check if a coach's grade meets or exceeds the required grade.

    Grade hierarchy: GRADE_1 < GRADE_2 < GRADE_3
    A higher grade can always fulfill a lower grade requirement.

    Args:
        coach_grade: The coach's grade
        required_grade: The required grade for the cohort

    Returns:
        True if coach is eligible, False otherwise
    """
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    coach_level = grade_order.index(coach_grade)
    required_level = grade_order.index(required_grade)

    return coach_level >= required_level


def get_dimension_labels(category: ProgramCategory) -> List[str]:
    """
    Get the dimension labels for a specific category.

    Args:
        category: Program category

    Returns:
        List of 7 dimension label strings
    """
    labels = DIMENSION_LABELS.get(category)
    if labels is None:
        raise ValueError(f"No dimension labels defined for category: {category}")
    return labels
