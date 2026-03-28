"""Reporting service enums."""

from enum import Enum


def enum_values(enum_class):
    """Return list of enum values for SQLAlchemy Enum column."""
    return [e.value for e in enum_class]


class ReportStatus(str, Enum):
    """Status of a quarterly snapshot job."""

    PENDING = "pending"
    COMPUTING = "computing"
    COMPLETED = "completed"
    FAILED = "failed"


class LeaderboardCategory(str, Enum):
    """Categories available for leaderboard ranking."""

    ATTENDANCE = "attendance"
    STREAKS = "streaks"
    MILESTONES = "milestones"
    VOLUNTEER_HOURS = "volunteer_hours"
    BUBBLES_EARNED = "bubbles_earned"


class CardFormat(str, Enum):
    """Shareable card image formats."""

    SQUARE = "square"  # 1080x1080
    STORY = "story"  # 1080x1920


class ForecastStatus(str, Enum):
    """Status of a seasonality forecast run."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class DemandLevel(str, Enum):
    """Categorised expected demand level for a month."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    PEAK = "peak"


class MonthStatus(str, Enum):
    """Performance classification for a month vs forecast."""

    EXPECTED_SEASONAL_DIP = "expected_seasonal_dip"
    ON_TRACK = "on_track"
    OUTPERFORMING = "outperforming"
    UNDERPERFORMING = "underperforming"


class DataSource(str, Enum):
    """Origin of a data record."""

    SYSTEM = "system"
    MANUAL = "manual"
    IMPORT = "import"
    PRIOR = "prior"
