"""Receipt OCR is advisory and never used as a settlement decision."""

import io

from PIL import Image

from services.media_service.services.payment_receipt_extract import extract_receipt_hints


def test_image_ocr_extracts_only_bounded_suggestions(monkeypatch):
    import pytesseract

    monkeypatch.setattr(
        pytesseract,
        "image_to_string",
        lambda image, timeout: "Transaction ID: TXN-12345678\nAmount: NGN 55,000.00",
    )
    buffer = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buffer, format="PNG")
    hints = extract_receipt_hints(buffer.getvalue(), "image/png")
    assert hints["status"] == "suggested"
    assert hints["reference_suggestion"] == "TXN-12345678"
    assert hints["amount_suggestion"] == "55000.00"
    assert "verified" not in hints


def test_unsupported_document_does_not_block_submission():
    assert extract_receipt_hints(b"unrecognised", "application/octet-stream") == {
        "status": "unsupported"
    }
