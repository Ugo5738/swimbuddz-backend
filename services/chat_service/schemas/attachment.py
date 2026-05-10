"""Attachment request/response schemas.

Phase 1 scope: images only (JPEG / PNG / WebP, ≤10 MB) per design §8.2.
Video and documents land in a follow-up slice.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AttachmentModeration(BaseModel):
    """Outcome of pre-deliver moderation on an uploaded image.

    Stored alongside the descriptor so admins/moderators can audit the
    decision later without re-scanning. `skipped=True` means the provider
    was unavailable (open-by-default in dev); `flagged=True` means at least
    one category exceeded its threshold (policy decision belongs to the
    caller, not to the moderator)."""

    provider: str
    flagged: bool
    skipped: bool = False
    top_category: Optional[str] = None
    top_confidence: Optional[float] = None


class AttachmentDescriptor(BaseModel):
    """One attachment as it appears inside a message's `attachments` list.

    The chat backend produces this when an image is uploaded; the client
    sends an array of these (JSON-serialised) when posting the message."""

    type: Literal["image"] = "image"
    storage_key: str = Field(..., description="Path within chat-attachments bucket")
    mime: str
    size: int = Field(..., ge=0)
    width: Optional[int] = None
    height: Optional[int] = None
    public_url: Optional[str] = None
    moderation: Optional[AttachmentModeration] = None

    model_config = ConfigDict(extra="ignore")


class AttachmentUploadResponse(BaseModel):
    """Response from `POST /chat/attachments`.

    The descriptor goes into the message's `attachments` array on send.
    `rejected=True` means the moderation hit a category that the chat policy
    refuses to deliver under any circumstances (currently: SAFEGUARDING) —
    the upload was deleted from the bucket and cannot be sent."""

    descriptor: Optional[AttachmentDescriptor] = None
    rejected: bool = False
    rejection_reason: Optional[str] = None
