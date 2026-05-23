"""Pydantic schemas for the outreach admin endpoints."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class OutreachStateResponse(BaseModel):
    """Current outreach status for one contact — used by the admin UI."""

    contact_id: uuid.UUID
    outreach_paused: bool
    outreach_started_at: Optional[datetime] = None
    last_outbound_email_at: Optional[datetime] = None
    last_outbound_email_type: Optional[str] = None
    next_email_number: Optional[int] = None  # 1, 2, 3 or None when done
    has_inbound_reply: bool


class OutreachStartRequest(BaseModel):
    """Optional metadata at sequence kickoff."""

    note: Optional[str] = Field(None, max_length=500)


class OutreachPreviewResponse(BaseModel):
    number: int
    subject: str
    plain: str
    html: str


class OutreachSendResult(BaseModel):
    """Result of a 'send next outreach email now' action."""

    sent: bool
    email_number: Optional[int] = None
    touchpoint_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None  # populated when sent=False


class OutreachCycleResult(BaseModel):
    """Result of the scheduler run-now action."""

    considered: int
    sent: int
    skipped: int
