"""Anonymous, aggregate-only editorial engagement counts."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import ContentPost
from services.communications_service.models.engagement import ContentEngagement

router = APIRouter(prefix="/content", tags=["content-engagement"])


class EventInput(BaseModel):
    event_type: Literal["page_view", "watch_click", "assessment_click", "academy_click", "club_click", "event_click", "register_click"]
    source: Literal["website", "beyond_the_pool", "articles", "stories"] = "website"


@router.post("/{post_id}/engagement", status_code=204)
async def record_engagement(
    post_id: uuid.UUID,
    payload: EventInput,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    post = await db.scalar(
        select(ContentPost).where(ContentPost.id == post_id, ContentPost.is_published.is_(True))
    )
    if not post:
        raise HTTPException(status_code=404, detail="Published content not found")
    # Anonymous counters: no visitor token, fingerprint, IP, or PII stored.
    stmt = insert(ContentEngagement).values(
        id=uuid.uuid4(), post_id=post_id, event_type=payload.event_type,
        source=payload.source, total=1,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_content_engagement_bucket",
        set_={"total": ContentEngagement.total + 1},
    )
    await db.execute(stmt)
    await db.commit()


@router.get("/admin/engagement")
async def engagement_report(
    _current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rows = await db.execute(
        select(ContentPost.id, ContentPost.title, ContentEngagement.event_type,
               func.sum(ContentEngagement.total))
        .join(ContentEngagement, ContentPost.id == ContentEngagement.post_id)
        .group_by(ContentPost.id, ContentPost.title, ContentEngagement.event_type)
        .order_by(ContentPost.title)
    )
    return [
        {"post_id": str(post_id), "title": title, "event": event, "count": int(count)}
        for post_id, title, event, count in rows.all()
    ]
