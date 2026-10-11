"""Small contracts shared by domain-owned, read-only reporting endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ReportingSection(BaseModel):
    available: bool = True
    metrics: dict[str, int | float | None] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def reporting_window(
    start: datetime, inclusive_end: datetime
) -> tuple[datetime, datetime]:
    """Turn the existing API's inclusive second into an exclusive upper bound.

    Quarterly callers send 23:59:59 in Africa/Lagos. A half-open SQL window
    includes fractional timestamps in that final second and excludes October 1.
    """
    start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = (
        inclusive_end
        if inclusive_end.tzinfo
        else inclusive_end.replace(tzinfo=timezone.utc)
    )
    if start > end:
        raise HTTPException(status_code=422, detail="Reporting start must precede end")
    return start, end + timedelta(seconds=1)


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
