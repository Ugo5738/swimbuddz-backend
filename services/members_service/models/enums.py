"""Enum definitions for members service models."""

import enum


class CoachGrade(str, enum.Enum):
    """Coach grade levels based on credentials and experience."""

    GRADE_1 = "grade_1"
    GRADE_2 = "grade_2"
    GRADE_3 = "grade_3"
