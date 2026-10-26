"""Untrusted OCR hints for payment receipts; never proof of settled funds."""

import io
import re

from PIL import Image, ImageOps


_REFERENCE = re.compile(
    r"(?:transaction\s*(?:id|reference|ref)|session\s*id|payment\s*reference)"
    r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9_-]{5,60})",
    flags=re.IGNORECASE,
)
_AMOUNT = re.compile(r"(?:NGN|₦|N)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)


def extract_receipt_hints(data: bytes, content_type: str) -> dict:
    """Return bounded OCR suggestions, never an authorization or bank verification.

    Extracted fields can be wrong or spoofed. Failed OCR must not block upload.
    """
    text = ""
    try:
        if content_type == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
        elif content_type in {"image/png", "image/jpeg", "image/webp"}:
            import pytesseract

            image = Image.open(io.BytesIO(data))
            if image.width * image.height > 20_000_000:
                image.thumbnail((4000, 4000))
            image = ImageOps.exif_transpose(image).convert("RGB")
            text = pytesseract.image_to_string(image, timeout=8)
        else:
            return {"status": "unsupported"}
    except Exception:
        return {"status": "unavailable"}

    reference = _REFERENCE.search(text[:12000])
    amount = _AMOUNT.search(text[:12000])
    hints = {"status": "suggested" if text.strip() else "no_text"}
    if reference:
        hints["reference_suggestion"] = reference.group(1)[:64]
    if amount:
        hints["amount_suggestion"] = amount.group(1).replace(",", "")[:32]
    return hints
