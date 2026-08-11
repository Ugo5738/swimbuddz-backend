import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from services.communications_service.models import ContentCommentLike
from services.communications_service.schemas import ContentCommentResponse


def test_comment_like_model_prevents_duplicate_member_reactions():
    table = ContentCommentLike.__table__
    unique_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]

    assert "uq_content_comment_likes_comment_member" in unique_constraints
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "CASCADE"


def test_comment_response_has_public_reaction_defaults():
    response = ContentCommentResponse(
        id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        content="Useful tip",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    assert response.like_count == 0
    assert response.liked_by_me is False
