"""Member-owned exceptions to included Academy attendance."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AcademyAttendanceExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["present", "absent", "excused", "late"]
    notes: str | None = Field(default=None, max_length=500)
