"""Public pool publication is an explicit admin action."""

import pytest
from fastapi import HTTPException

from services.pools_service.models.enums import PoolAssetType
from services.pools_service.routers.admin_related import _validate_public_asset
from services.pools_service.schemas.directory import DirectoryPool, DirectoryPhoto
from services.pools_service.schemas.public_partner import PublicPartnerPool


def test_public_photo_approval_requires_https_photograph():
    _validate_public_asset(
        PoolAssetType.PHOTO, "https://cdn.example.org/pool.jpg", True
    )
    _validate_public_asset(
        PoolAssetType.DOCUMENT, "http://private.local/document.pdf", False
    )
    with pytest.raises(HTTPException):
        _validate_public_asset(
            PoolAssetType.DOCUMENT, "https://cdn.example.org/document.pdf", True
        )
    with pytest.raises(HTTPException):
        _validate_public_asset(
            PoolAssetType.PHOTO, "http://cdn.example.org/pool.jpg", True
        )
    with pytest.raises(HTTPException):
        _validate_public_asset(PoolAssetType.PHOTO, None, True)


def test_public_directory_has_only_curated_assessment_fields():
    assert "swimbuddz_assessment_summary" in DirectoryPool.model_fields
    assert "photos" in DirectoryPool.model_fields
    assert "directory_assessment_summary" not in PublicPartnerPool.model_fields
    assert "directory_public" not in DirectoryPhoto.model_fields
