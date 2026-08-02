"""Image crop presets and deterministic presentation-variant generation."""

from dataclasses import dataclass
from io import BytesIO

from fastapi import HTTPException, status
from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class ImageVariantPreset:
    width: int
    height: int


PRESENTATION_IMAGE_PRESETS: dict[str, ImageVariantPreset] = {
    "profile_photo": ImageVariantPreset(800, 800),
    "cover_image": ImageVariantPreset(1600, 900),
    "content_image": ImageVariantPreset(1200, 675),
    "category_image": ImageVariantPreset(1200, 900),
    "collection_image": ImageVariantPreset(1200, 900),
    "product_image": ImageVariantPreset(1200, 1200),
    "badge_image": ImageVariantPreset(512, 512),
    "challenge_example": ImageVariantPreset(1200, 675),
    "homepage_banner": ImageVariantPreset(1920, 1080),
    "homepage_community_photo": ImageVariantPreset(1000, 1000),
}


@dataclass(frozen=True)
class GeneratedImageVariant:
    data: bytes
    content_type: str
    extension: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int


def generate_image_variant(
    image_data: bytes,
    *,
    purpose: str,
    crop_x: float,
    crop_y: float,
    crop_width: float,
    crop_height: float,
) -> GeneratedImageVariant:
    """Crop normalized source coordinates and resize to the purpose preset."""
    preset = PRESENTATION_IMAGE_PRESETS.get(purpose)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This media purpose does not support image adjustment",
        )

    _validate_normalized_crop(crop_x, crop_y, crop_width, crop_height)

    try:
        with Image.open(BytesIO(image_data)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            source_width, source_height = image.size

            left = round(crop_x * source_width)
            top = round(crop_y * source_height)
            right = round((crop_x + crop_width) * source_width)
            bottom = round((crop_y + crop_height) * source_height)

            left = max(0, min(left, source_width - 1))
            top = max(0, min(top, source_height - 1))
            right = max(left + 1, min(right, source_width))
            bottom = max(top + 1, min(bottom, source_height))

            crop_pixel_width = right - left
            crop_pixel_height = bottom - top
            requested_aspect = preset.width / preset.height
            crop_aspect = crop_pixel_width / crop_pixel_height
            if abs(crop_aspect - requested_aspect) / requested_aspect > 0.02:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Crop area does not match the required aspect ratio",
                )

            variant = image.crop((left, top, right, bottom)).resize(
                (preset.width, preset.height),
                Image.Resampling.LANCZOS,
            )
            encoded, content_type, extension = _encode_variant(variant)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected file could not be processed as an image",
        ) from exc

    return GeneratedImageVariant(
        data=encoded,
        content_type=content_type,
        extension=extension,
        source_width=source_width,
        source_height=source_height,
        output_width=preset.width,
        output_height=preset.height,
    )


def _validate_normalized_crop(
    crop_x: float,
    crop_y: float,
    crop_width: float,
    crop_height: float,
) -> None:
    values = (crop_x, crop_y, crop_width, crop_height)
    if any(value < 0 or value > 1 for value in values):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Crop coordinates must be between 0 and 1",
        )
    if crop_width <= 0 or crop_height <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Crop dimensions must be greater than zero",
        )
    tolerance = 0.0001
    if crop_x + crop_width > 1 + tolerance or crop_y + crop_height > 1 + tolerance:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Crop area must stay within the source image",
        )


def _encode_variant(image: Image.Image) -> tuple[bytes, str, str]:
    buffer = BytesIO()
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "image/png", "png"

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue(), "image/jpeg", "jpg"
