"""Reporting Service models package.

Re-exports all models and enums so that:
  - ``from services.reporting_service.models import MemberQuarterlyReport`` works
  - Alembic env.py imports continue to work without modification
  - SQLAlchemy's mapper registry sees every model class on import
"""

from services.reporting_service.models.core import (  # noqa: F401
    CommunityQuarterlyStats,
    MemberQuarterlyReport,
    QuarterlySnapshot,
)
from services.reporting_service.models.enums import (  # noqa: F401
    CardFormat,
    DataSource,
    DemandLevel,
    ForecastStatus,
    LeaderboardCategory,
    MonthStatus,
    ReportStatus,
)
from services.reporting_service.models.seasonality import (  # noqa: F401
    ExternalFactor,
    MonthlyActual,
    SeasonalityForecast,
)

__all__ = [
    # Models
    "QuarterlySnapshot",
    "MemberQuarterlyReport",
    "CommunityQuarterlyStats",
    "MonthlyActual",
    "SeasonalityForecast",
    "ExternalFactor",
    # Enums
    "ReportStatus",
    "LeaderboardCategory",
    "CardFormat",
    "ForecastStatus",
    "DemandLevel",
    "MonthStatus",
    "DataSource",
]
