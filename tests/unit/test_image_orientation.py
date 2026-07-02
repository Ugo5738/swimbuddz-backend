from io import BytesIO

from PIL import Image

from services.media_service.services.storage import StorageService
from services.reporting_service.services.card_generator import (
    _open_photo_respecting_orientation,
)


def _jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    img = Image.new("RGB", (width, height), "red")
    exif = img.getexif()
    exif[274] = orientation

    buffer = BytesIO()
    img.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _image_size(image_data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_data)) as img:
        return img.size


def _image_format(image_data: bytes) -> str | None:
    with Image.open(BytesIO(image_data)) as img:
        return img.format


def _exif_orientation(image_data: bytes) -> int | None:
    with Image.open(BytesIO(image_data)) as img:
        return img.getexif().get(274)


def test_media_storage_normalizes_exif_orientation_before_upload():
    service = StorageService.__new__(StorageService)
    original = _jpeg_with_orientation(width=40, height=80, orientation=6)

    normalized = service._normalize_image_orientation(original, "image/jpeg")

    assert _image_size(normalized) == (80, 40)
    assert _exif_orientation(normalized) is None


def test_media_thumbnail_applies_exif_orientation():
    service = StorageService.__new__(StorageService)
    original = _jpeg_with_orientation(width=40, height=80, orientation=6)

    thumbnail = service._create_thumbnail(original, size=(200, 200))

    assert _image_size(thumbnail) == (80, 40)


def test_media_thumbnail_preserves_non_jpeg_format():
    service = StorageService.__new__(StorageService)
    img = Image.new("RGBA", (40, 80), "red")
    buffer = BytesIO()
    img.save(buffer, format="PNG")

    thumbnail = service._create_thumbnail(buffer.getvalue(), size=(20, 20))

    assert _image_format(thumbnail) == "PNG"


def test_report_card_photo_loader_applies_exif_orientation():
    original = _jpeg_with_orientation(width=40, height=80, orientation=6)

    photo = _open_photo_respecting_orientation(original)

    assert photo.size == (80, 40)
