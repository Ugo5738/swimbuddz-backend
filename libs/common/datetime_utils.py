"""Datetime utilities for timezone-aware UTC timestamps.

Usage:
    from libs.common.datetime_utils import utc_now

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime.

    This replaces the deprecated datetime.utcnow() which returns naive datetimes.
    Always use this for timestamps in the database.
    """
    return datetime.now(timezone.utc)
