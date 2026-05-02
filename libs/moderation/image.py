"""Image / video moderation via AWS Rekognition.

Provider docs: https://docs.aws.amazon.com/rekognition/latest/dg/moderation.html
Cost: roughly $1 per 1,000 images (image) / different tier for video.

SWIM-CONTEXT CAVEAT
  SwimBuddz legitimately publishes photos of children in swimwear at pools.
  Generic moderators (including Rekognition) will false-positive on this content
  aggressively under labels like "Suggestive" → "Revealing Clothes". DO NOT
  auto-delete on any Rekognition flag. Always quarantine to a manual review
  queue and let a ``safeguarding_admin`` approve or remove.

Design notes:
  * Results return raw labels with confidence scores; policy decisions live
    in the caller.
  * "Suggestive" is intentionally mapped to ``ModerationCategory.SUGGESTIVE``
    (separate from ``SEXUAL``) so policy can treat pool photos distinctly.
  * Video moderation is NOT implemented in Phase 0 — chat allows only ≤30s
    clips per design §8.2; add video when Phase 2 safeguarding ships.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from libs.moderation.types import (
    ModerationCategory,
    ModerationLabel,
    ModerationResult,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

# Rekognition returns a hierarchical taxonomy (ParentName → Name). We map the
# top-level parents to our coarse categories. Children inherit their parent's
# mapping unless explicitly overridden.
_REKOGNITION_PARENT_MAP: dict[str, ModerationCategory] = {
    "Explicit Nudity": ModerationCategory.SEXUAL,
    "Explicit": ModerationCategory.SEXUAL,
    "Non-Explicit Nudity of Intimate parts and Kissing": ModerationCategory.SEXUAL,
    "Suggestive": ModerationCategory.SUGGESTIVE,
    "Violence": ModerationCategory.VIOLENCE,
    "Visually Disturbing": ModerationCategory.VIOLENCE,
    "Rude Gestures": ModerationCategory.HARASSMENT,
    "Drugs & Tobacco": ModerationCategory.OTHER,
    "Alcohol": ModerationCategory.OTHER,
    "Gambling": ModerationCategory.OTHER,
    "Hate Symbols": ModerationCategory.HATE,
}

# Category-scoped default thresholds. Tune per surface — safeguarding is more
# sensitive than suggestive content.
_DEFAULT_FLAG_THRESHOLDS: dict[ModerationCategory, float] = {
    ModerationCategory.SAFEGUARDING: 50.0,  # Rekognition returns 0-100
    ModerationCategory.SEXUAL: 75.0,
    ModerationCategory.VIOLENCE: 70.0,
    ModerationCategory.HATE: 60.0,
    ModerationCategory.SUGGESTIVE: 90.0,  # Pool photos — tolerate unless very high
    ModerationCategory.HARASSMENT: 75.0,
    ModerationCategory.OTHER: 85.0,
}


async def moderate_image(
    *,
    image_bytes: Optional[bytes] = None,
    s3_bucket: Optional[str] = None,
    s3_key: Optional[str] = None,
    min_confidence: float = 50.0,
    region: Optional[str] = None,
    flag_thresholds: Optional[dict[ModerationCategory, float]] = None,
) -> ModerationResult:
    """Moderate an image.

    Accepts either raw image bytes OR an S3 location. For chat attachments we
    upload to a quarantine bucket first and call with ``s3_bucket``/``s3_key``
    — that path scales better than in-line bytes.

    Args:
        image_bytes: Raw image bytes (png/jpeg/webp). Mutually exclusive with
                     S3 inputs.
        s3_bucket: S3 bucket holding the image (must be in same region).
        s3_key: Object key in the S3 bucket.
        min_confidence: Rekognition ``MinConfidence`` — labels below this are
                        not returned at all. Default 50 matches Rekognition's
                        recommendation.
        region: AWS region. Defaults to ``AWS_REGION`` env var.
        flag_thresholds: Per-category thresholds (Rekognition confidence 0-100)
                        used to compute the ``flagged`` boolean. Merged over
                        defaults, so you can override selectively.

    Returns:
        ModerationResult with labels and a ``flagged`` boolean reflecting
        whether any label exceeded its category threshold.

    Raises:
        ProviderUnavailableError: credentials missing or call fails.
        ValueError: both or neither of (bytes, s3_key) provided.
    """
    has_bytes = image_bytes is not None
    has_s3 = s3_bucket is not None and s3_key is not None
    if has_bytes == has_s3:
        raise ValueError("Provide exactly one of image_bytes OR (s3_bucket + s3_key).")

    region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        raise ProviderUnavailableError(
            "AWS region not configured (set AWS_REGION or pass region=...)"
        )

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise ProviderUnavailableError(
            "boto3 not installed. Add it to pyproject.toml."
        ) from exc

    client = boto3.client("rekognition", region_name=region)
    call_args: dict = {"MinConfidence": min_confidence}
    if has_bytes:
        call_args["Image"] = {"Bytes": image_bytes}
    else:
        call_args["Image"] = {"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}

    try:
        response = client.detect_moderation_labels(**call_args)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Rekognition moderation call failed: %s", exc)
        raise ProviderUnavailableError(str(exc)) from exc

    thresholds = {**_DEFAULT_FLAG_THRESHOLDS, **(flag_thresholds or {})}

    labels: list[ModerationLabel] = []
    flagged = False
    for entry in response.get("ModerationLabels", []) or []:
        provider_label: str = entry.get("Name", "")
        parent: str = entry.get("ParentName", "") or provider_label
        confidence: float = float(entry.get("Confidence", 0.0))
        category = _REKOGNITION_PARENT_MAP.get(parent, ModerationCategory.OTHER)
        labels.append(
            ModerationLabel(
                category=category,
                confidence=confidence,
                provider_label=provider_label,
            )
        )
        if confidence >= thresholds.get(category, 90.0):
            flagged = True

    return ModerationResult(
        flagged=flagged,
        labels=labels,
        provider="aws_rekognition",
        raw=response,
    )
