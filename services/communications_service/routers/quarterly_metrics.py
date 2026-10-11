"""Published content and podcast dates for quarterly reporting."""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, reporting_window
from libs.db.session import get_async_db
from services.communications_service.models import ContentPost

router = APIRouter(prefix="/internal/communications", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    podcast = or_(
        ContentPost.category == "beyond_the_pool",
        ContentPost.episode_number.is_not(None),
    )
    posts = (
        (
            await db.execute(
                select(ContentPost).where(
                    ContentPost.is_published.is_(True),
                    or_(
                        (~podcast)
                        & (ContentPost.published_at >= start)
                        & (ContentPost.published_at < end),
                        podcast
                        & (ContentPost.episode_air_date >= date_from.date())
                        & (ContentPost.episode_air_date <= date_to.date()),
                        podcast
                        & ContentPost.episode_air_date.is_(None)
                        & (ContentPost.published_at >= start)
                        & (ContentPost.published_at < end),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    podcasts = [
        p
        for p in posts
        if p.category == "beyond_the_pool" or p.episode_number is not None
    ]
    articles = [p for p in posts if p not in podcasts]
    return ReportingSection(
        metrics={
            "content_posts_published": len(articles),
            "podcast_episodes_published": len(podcasts),
        },
        details={
            "categories": dict(Counter(p.category for p in articles)),
            "episodes": [
                {
                    "title": p.title,
                    "episode_number": p.episode_number,
                    "published_at": p.published_at.isoformat()
                    if p.published_at
                    else None,
                    "original_release_at": p.episode_air_date.isoformat()
                    if p.episode_air_date
                    else None,
                    "date_basis": "broadcast"
                    if p.episode_air_date
                    else "content_publication",
                    "url": p.video_url,
                }
                for p in podcasts
            ],
        },
        notes=[
            "Articles use content publication dates. Podcasts use original broadcast date when recorded, otherwise content publication date. External broadcasts are not counted until registered; an upload date must not override a known Q3 broadcast.",
        ],
    )
