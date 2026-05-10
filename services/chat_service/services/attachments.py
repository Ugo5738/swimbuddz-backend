"""Chat attachment uploads with moderation pre-scan.

Phase 1: images only (JPEG / PNG / WebP, ≤10 MB) per design §8.2. The flow:

  1. Validate type + size at the API boundary
  2. Upload bytes to Supabase Storage bucket `chat-attachments`
  3. Run AWS Rekognition on the bytes (provider-unavailable = open in dev)
  4. If a CHILD-SAFETY (`SAFEGUARDING`) label fired, **delete** the upload
     and return rejected — that content never lives in our bucket.
  5. Otherwise return the descriptor with the moderation result attached.

The descriptor is written into the message's JSONB `attachments` array on
send. `send_message` enforces the second-tier policy (per-channel rules
about non-safeguarding flags) — the rule lives there because that's where
the channel context is available.
"""

import io
import uuid
from typing import Optional

from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError

from libs.common.logging import get_logger
from libs.common.supabase import get_supabase_admin_client
from libs.moderation import (
    ModerationCategory,
    ProviderUnavailableError,
    moderate_image,
)

from services.chat_service.schemas import (
    AttachmentDescriptor,
    AttachmentModeration,
    AttachmentUploadResponse,
)

logger = get_logger(__name__)

# Per design §8.2.
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset(
    ["image/jpeg", "image/png", "image/webp"]
)
_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

CHAT_ATTACHMENTS_BUCKET = "chat-attachments"

# Categories that we refuse to deliver under any circumstance — even adult
# channels. Currently just child-safety. Anything else (suggestive, sexual,
# violence) gets queued for review per channel context.
HARD_REJECT_CATEGORIES: frozenset[ModerationCategory] = frozenset(
    [ModerationCategory.SAFEGUARDING]
)


def _build_storage_key(member_id: uuid.UUID, mime: str) -> str:
    """Flat path: bucket/<uuid>.<ext>. Member id is intentionally NOT in the
    key — leaking it via filename would be a privacy regression."""
    ext = _MIME_TO_EXT.get(mime, "bin")
    return f"{uuid.uuid4()}.{ext}"


def _probe_image(data: bytes) -> tuple[int, int]:
    """Return (width, height) by reading the image header.

    Raises 400 on anything Pillow can't decode — which catches both
    malformed uploads and non-image content masquerading as an image MIME."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a recognised image",
        ) from exc


async def _run_moderation(data: bytes) -> AttachmentModeration:
    """Run Rekognition on raw bytes. Provider-unavailable returns
    `skipped=True` so dev environments without AWS creds keep working —
    callers (`send_message`) interpret `skipped` per channel policy."""
    try:
        result = await moderate_image(image_bytes=data)
    except ProviderUnavailableError as exc:
        logger.warning("Image moderation skipped (provider unavailable): %s", exc)
        return AttachmentModeration(
            provider="aws_rekognition", flagged=False, skipped=True
        )
    except ValueError as exc:
        # Bad arg combination — programmer error. Fail loud.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    top = result.top_label()
    return AttachmentModeration(
        provider=result.provider,
        flagged=result.flagged,
        skipped=False,
        top_category=top.category.value if top else None,
        top_confidence=top.confidence if top else None,
    )


def _hard_reject_label(moderation: AttachmentModeration) -> bool:
    """True if the moderation result includes a category we never deliver."""
    if not moderation.flagged or moderation.top_category is None:
        return False
    try:
        cat = ModerationCategory(moderation.top_category)
    except ValueError:
        return False
    return cat in HARD_REJECT_CATEGORIES


async def upload_image_attachment(
    *,
    member_id: uuid.UUID,
    data: bytes,
    mime: str,
    declared_filename: Optional[str] = None,
) -> AttachmentUploadResponse:
    """Validate + store + moderate an image. Returns a descriptor on success
    or a structured rejection on hard-reject."""
    if mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported image type {mime!r}. "
                f"Allowed: {sorted(ALLOWED_IMAGE_MIMES)}."
            ),
        )
    size = len(data)
    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file"
        )
    if size > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image too large: {size} bytes (max {MAX_IMAGE_BYTES}).",
        )

    width, height = _probe_image(data)
    storage_key = _build_storage_key(member_id, mime)

    # Moderate BEFORE uploading. Hard-rejected bytes never touch our storage —
    # cleaner than upload-then-delete, and avoids a window where rejected
    # content sits at rest if the delete fails.
    moderation = await _run_moderation(data)
    if _hard_reject_label(moderation):
        return AttachmentUploadResponse(
            rejected=True,
            rejection_reason=(
                "Image rejected by safeguarding policy. This category is not"
                " permitted in any channel."
            ),
        )

    client = get_supabase_admin_client()
    try:
        client.storage.from_(CHAT_ATTACHMENTS_BUCKET).upload(
            path=storage_key,
            file=data,
            file_options={"content-type": mime},
        )
    except Exception as exc:
        logger.error("Supabase upload failed for chat attachment: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage upload failed",
        ) from exc

    public_url: Optional[str]
    try:
        public_url = client.storage.from_(CHAT_ATTACHMENTS_BUCKET).get_public_url(
            storage_key
        )
    except Exception:
        public_url = None

    descriptor = AttachmentDescriptor(
        type="image",
        storage_key=storage_key,
        mime=mime,
        size=size,
        width=width,
        height=height,
        public_url=public_url,
        moderation=moderation,
    )
    logger.info(
        "Chat attachment uploaded key=%s member=%s size=%d flagged=%s",
        storage_key,
        member_id,
        size,
        moderation.flagged,
    )
    return AttachmentUploadResponse(descriptor=descriptor)
