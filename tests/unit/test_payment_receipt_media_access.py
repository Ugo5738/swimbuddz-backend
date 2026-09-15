from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.media_service.routers.media import _require_original_access
from tests.conftest import make_admin_user, make_member_user


@pytest.mark.parametrize(
    "metadata,url",
    [
        ({"purpose": "payment_proof"}, "https://storage/receipt"),
        ({}, "https://storage/payment-proofs/PAY-123/receipt.png"),
    ],
)
def test_only_receipt_uploader_and_admin_can_resolve_or_play_receipt(metadata, url):
    receipt = SimpleNamespace(metadata_info=metadata, file_url=url, uploaded_by="owner")
    for user in (None, make_member_user(user_id="someone-else")):
        with pytest.raises(HTTPException) as exc:
            _require_original_access(receipt, user)
        assert exc.value.status_code == 404
    _require_original_access(receipt, make_member_user(user_id="owner"))
    _require_original_access(receipt, make_admin_user())


def test_public_images_remain_public():
    _require_original_access(
        SimpleNamespace(metadata_info={}, file_url="https://storage/public/cap.png"),
        None,
    )


async def test_anonymous_batch_and_list_queries_exclude_receipt_metadata_and_legacy_paths():
    import uuid
    from unittest.mock import AsyncMock
    from sqlalchemy.dialects import postgresql
    from services.media_service.routers.assets import resolve_media_urls
    from services.media_service.routers.media import list_media

    result = SimpleNamespace(
        fetchall=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert await resolve_media_urls([str(uuid.uuid4())], db) == {}
    assert await list_media(db=db) == []
    for call in db.execute.await_args_list:
        sql = str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "payment_proof" in sql
        assert "payment-proofs/" in sql
        assert "NOT LIKE" in sql
