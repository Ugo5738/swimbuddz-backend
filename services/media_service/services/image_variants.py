"""Deterministic presentation-image recipes and variant generation."""

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from fastapi import HTTPException, status
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field


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


class RecipeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NormalizedCrop(RecipeModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class ImageAdjustments(RecipeModel):
    brightness: int = Field(default=0, ge=-100, le=100)
    contrast: int = Field(default=0, ge=-100, le=100)
    saturation: int = Field(default=0, ge=-100, le=100)
    warmth: int = Field(default=0, ge=-100, le=100)
    highlights: int = Field(default=0, ge=-100, le=100)
    shadows: int = Field(default=0, ge=-100, le=100)


class ImageFilter(RecipeModel):
    name: Literal["original", "clean", "pool", "warm", "monochrome"] = "original"
    strength: int = Field(default=100, ge=0, le=100)


class ImageTransformRecipe(RecipeModel):
    """Versioned recipe stored with every generated presentation image."""

    version: Literal[1] = 1
    crop: NormalizedCrop
    rotation: float = Field(default=0, ge=-375, le=375)
    flip_horizontal: bool = False
    flip_vertical: bool = False
    adjustments: ImageAdjustments = Field(default_factory=ImageAdjustments)
    filter: ImageFilter = Field(default_factory=ImageFilter)


FILTER_ADJUSTMENTS: dict[str, ImageAdjustments] = {
    "original": ImageAdjustments(),
    "clean": ImageAdjustments(brightness=4, contrast=8, saturation=4),
    "pool": ImageAdjustments(brightness=2, contrast=10, saturation=12, warmth=-6),
    "warm": ImageAdjustments(brightness=3, contrast=4, saturation=6, warmth=12),
    "monochrome": ImageAdjustments(saturation=-100, contrast=8),
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
    recipe: ImageTransformRecipe | None = None,
    crop_x: float | None = None,
    crop_y: float | None = None,
    crop_width: float | None = None,
    crop_height: float | None = None,
) -> GeneratedImageVariant:
    """Apply a recipe and resize to the purpose preset.

    The crop-only arguments remain supported during rolling deployments where an
    older frontend may briefly communicate with the new media service.
    """
    preset = PRESENTATION_IMAGE_PRESETS.get(purpose)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This media purpose does not support image adjustment",
        )

    resolved_recipe = recipe or _legacy_crop_recipe(
        crop_x, crop_y, crop_width, crop_height
    )
    crop = resolved_recipe.crop
    _validate_normalized_crop(crop.x, crop.y, crop.width, crop.height)

    try:
        with Image.open(BytesIO(image_data)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            source_width, source_height = image.size

            if resolved_recipe.flip_horizontal:
                image = ImageOps.mirror(image)
            if resolved_recipe.flip_vertical:
                image = ImageOps.flip(image)
            if resolved_recipe.rotation:
                image = image.rotate(
                    -resolved_recipe.rotation,
                    expand=True,
                    resample=Image.Resampling.BICUBIC,
                )

            transformed_width, transformed_height = image.size
            left = round(crop.x * transformed_width)
            top = round(crop.y * transformed_height)
            right = round((crop.x + crop.width) * transformed_width)
            bottom = round((crop.y + crop.height) * transformed_height)

            left = max(0, min(left, transformed_width - 1))
            top = max(0, min(top, transformed_height - 1))
            right = max(left + 1, min(right, transformed_width))
            bottom = max(top + 1, min(bottom, transformed_height))

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
            variant = _apply_adjustments(variant, resolved_recipe)
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


def effective_adjustments(recipe: ImageTransformRecipe) -> ImageAdjustments:
    """Combine the selected preset and manual controls into one bounded result."""
    preset = FILTER_ADJUSTMENTS[recipe.filter.name]
    strength = recipe.filter.strength / 100
    values = {
        name: max(
            -100,
            min(
                100,
                round(
                    getattr(recipe.adjustments, name) + getattr(preset, name) * strength
                ),
            ),
        )
        for name in ImageAdjustments.model_fields
    }
    return ImageAdjustments(**values)


def _legacy_crop_recipe(
    crop_x: float | None,
    crop_y: float | None,
    crop_width: float | None,
    crop_height: float | None,
) -> ImageTransformRecipe:
    if None in (crop_x, crop_y, crop_width, crop_height):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An image transformation recipe is required",
        )
    return ImageTransformRecipe(
        crop=NormalizedCrop(
            x=crop_x,
            y=crop_y,
            width=crop_width,
            height=crop_height,
        )
    )


def _apply_adjustments(image: Image.Image, recipe: ImageTransformRecipe) -> Image.Image:
    adjustments = effective_adjustments(recipe)
    if all(value == 0 for value in adjustments.model_dump().values()):
        return image

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    adjusted = image.convert("RGB")
    brightness_factor = max(0, 1 + adjustments.brightness / 200)
    adjusted = adjusted.point(lambda value: _clamp_byte(value * brightness_factor))
    contrast_factor = max(0, 1 + adjustments.contrast / 100)
    adjusted = adjusted.point(
        lambda value: _clamp_byte((value - 128) * contrast_factor + 128)
    )
    adjusted = ImageEnhance.Color(adjusted).enhance(
        max(0, 1 + adjustments.saturation / 100)
    )

    if adjustments.warmth:
        warmth = adjustments.warmth / 100
        red, green, blue = adjusted.split()
        red = red.point(lambda value: _clamp_byte(value + 24 * warmth))
        blue = blue.point(lambda value: _clamp_byte(value - 24 * warmth))
        adjusted = Image.merge("RGB", (red, green, blue))

    luminance = ImageOps.grayscale(adjusted)
    if adjustments.shadows:
        amount = abs(adjustments.shadows) / 100
        mask = luminance.point(
            lambda value: round(((1 - value / 255) ** 2) * amount * 96)
        )
        target = Image.new(
            "RGB", adjusted.size, "white" if adjustments.shadows > 0 else "black"
        )
        adjusted = Image.composite(target, adjusted, mask)
    if adjustments.highlights:
        amount = abs(adjustments.highlights) / 100
        mask = luminance.point(lambda value: round(((value / 255) ** 2) * amount * 96))
        target = Image.new(
            "RGB",
            adjusted.size,
            "white" if adjustments.highlights > 0 else "black",
        )
        adjusted = Image.composite(target, adjusted, mask)

    if alpha is not None:
        adjusted.putalpha(alpha)
    return adjusted


def _clamp_byte(value: float) -> int:
    return max(0, min(255, round(value)))


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
