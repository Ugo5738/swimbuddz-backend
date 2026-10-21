"""Curated, publicly discoverable pools; authenticated moderated reviews."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from libs.auth.dependencies import get_current_user, require_admin
from libs.db.session import get_async_db
from services.pools_service.models import (
    Pool,
    PoolAsset,
    PartnershipStatus,
    PoolAccessOffer,
    PoolSubmission,
    PoolSubmissionStatus,
)
from services.pools_service.models.directory_review import PoolDirectoryReview
from services.pools_service.models.enums import PoolAssetType
from services.pools_service.schemas.directory import (
    DirectoryPool,
    DirectoryPhoto,
    DirectoryList,
    DirectoryReview,
    DirectoryReviews,
    DirectoryReviewCreate,
    DirectorySuggestion,
    DirectoryCorrection,
)
from libs.common.datetime_utils import utc_now
from services.pools_service.services.directory_geo import (
    distance_km_sql,
    likely_same_pool,
)

router = APIRouter(prefix="/pools/directory", tags=["pool-directory"])
admin = APIRouter(prefix="/admin/pools/directory", tags=["admin-pool-directory"])


async def _public_pool(pool: Pool, db: AsyncSession) -> DirectoryPool:
    rating = await db.execute(
        select(
            func.avg(PoolDirectoryReview.rating), func.count(PoolDirectoryReview.id)
        ).where(
            PoolDirectoryReview.pool_id == pool.id,
            PoolDirectoryReview.moderation_status == "approved",
        )
    )
    average, count = rating.one()
    bookable = bool(
        (
            await db.execute(
                select(PoolAccessOffer.id)
                .where(
                    PoolAccessOffer.pool_id == pool.id,
                    PoolAccessOffer.status == "published",
                    PoolAccessOffer.public_booking_enabled.is_(True),
                    PoolAccessOffer.self_directed_permitted.is_(True),
                    PoolAccessOffer.starts_at > utc_now(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    )
    photo_rows = (
        await db.execute(
            select(PoolAsset)
            .where(
                PoolAsset.pool_id == pool.id,
                PoolAsset.directory_public.is_(True),
                PoolAsset.asset_type == PoolAssetType.PHOTO,
            )
            .order_by(PoolAsset.is_primary.desc(), PoolAsset.display_order, PoolAsset.created_at)
            .limit(8)
        )
    ).scalars().all()
    photos = [
        DirectoryPhoto(id=asset.id, url=asset.url, caption=asset.caption)
        for asset in photo_rows
        if asset.url and asset.url.startswith("https://")
    ]
    assessment_ready = bool(
        pool.directory_assessment_rating
        and pool.directory_assessment_summary
        and pool.directory_assessment_updated_at
    )
    return DirectoryPool(
        id=pool.id,
        slug=pool.slug,
        name=pool.name,
        location_area=pool.location_area,
        address=pool.address,
        latitude=pool.latitude,
        longitude=pool.longitude,
        pool_length_m=pool.pool_length_m,
        depth_min_m=pool.depth_min_m,
        depth_max_m=pool.depth_max_m,
        indoor_outdoor=pool.indoor_outdoor.value if pool.indoor_outdoor else None,
        has_changing_rooms=pool.has_changing_rooms,
        has_showers=pool.has_showers,
        has_lockers=pool.has_lockers,
        has_parking=pool.has_parking,
        has_lifeguard=pool.has_lifeguard,
        last_verified_at=pool.last_verified_at,
        is_partner=pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
        bookable=bookable
        and pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
        rating_average=round(float(average), 1) if average is not None else None,
        rating_count=count,
        photos=photos,
        swimbuddz_assessment_rating=pool.directory_assessment_rating if assessment_ready else None,
        swimbuddz_assessment_summary=pool.directory_assessment_summary if assessment_ready else None,
        swimbuddz_assessment_updated_at=pool.directory_assessment_updated_at if assessment_ready else None,
    )


@router.get("", response_model=DirectoryList)
async def list_directory(
    search: str | None = Query(None, max_length=100),
    area: str | None = Query(None, max_length=100),
    latitude: float | None = Query(None, ge=-90, le=90),
    longitude: float | None = Query(None, ge=-180, le=180),
    radius_km: float | None = Query(None, gt=0, le=250),
    sort: str = Query("name", pattern="^(name|nearest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Pool).where(
        Pool.directory_published.is_(True), Pool.is_active.is_(True)
    )
    if search:
        query = query.where(
            or_(Pool.name.ilike(f"%{search}%"), Pool.location_area.ilike(f"%{search}%"))
        )
    if area:
        query = query.where(Pool.location_area.ilike(f"%{area}%"))
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Latitude and longitude must be supplied together")
    if (radius_km is not None or sort == "nearest") and latitude is None:
        raise HTTPException(422, "Nearby searches require coordinates")
    distance = None
    if latitude is not None and longitude is not None:
        distance = distance_km_sql(Pool, latitude, longitude)
        if radius_km is not None or sort == "nearest":
            query = query.where(Pool.latitude.is_not(None), Pool.longitude.is_not(None))
        if radius_km is not None:
            query = query.where(
                Pool.latitude.is_not(None),
                Pool.longitude.is_not(None),
                distance <= radius_km,
            )
    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                (
                    query.order_by(distance.asc(), Pool.id)
                    if sort == "nearest" and distance is not None
                    else query.order_by(Pool.name, Pool.id)
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return DirectoryList(
        items=[await _public_pool(pool, db) for pool in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{pool_id}", response_model=DirectoryPool)
async def pool_detail(pool_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    pool = (
        await db.execute(
            select(Pool).where(
                Pool.id == pool_id,
                Pool.directory_published.is_(True),
                Pool.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not pool:
        raise HTTPException(404, "Pool not found")
    return await _public_pool(pool, db)


@router.get("/{pool_id}/reviews", response_model=DirectoryReviews)
async def reviews(pool_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    if not (
        await db.execute(
            select(Pool.id).where(
                Pool.id == pool_id, Pool.directory_published.is_(True)
            )
        )
    ).scalar_one_or_none():
        raise HTTPException(404, "Pool not found")
    reviews = (
        (
            await db.execute(
                select(PoolDirectoryReview)
                .where(
                    PoolDirectoryReview.pool_id == pool_id,
                    PoolDirectoryReview.moderation_status == "approved",
                )
                .order_by(PoolDirectoryReview.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return DirectoryReviews(
        items=[
            DirectoryReview(
                id=r.id, rating=r.rating, review=r.review, created_at=r.created_at
            )
            for r in reviews
        ],
        total=len(reviews),
    )


@router.post("/{pool_id}/corrections", status_code=201)
async def suggest_correction(
    pool_id: uuid.UUID,
    body: DirectoryCorrection,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reuse the existing moderation queue without creating a second pool."""
    pool = (
        await db.execute(
            select(Pool).where(
                Pool.id == pool_id,
                Pool.directory_published.is_(True),
                Pool.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if pool is None:
        raise HTTPException(404, "Pool not found")
    existing = (
        await db.execute(
            select(PoolSubmission.id)
            .where(
                PoolSubmission.promoted_pool_id == pool_id,
                PoolSubmission.submitter_auth_id == user.user_id,
                PoolSubmission.status == PoolSubmissionStatus.PENDING,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Your previous correction is still awaiting review")
    submission = PoolSubmission(
        submitter_auth_id=user.user_id,
        submitter_email=getattr(user, "email", None),
        pool_name=pool.name,
        location_area=pool.location_area,
        address=pool.address,
        member_notes=body.details.strip(),
        promoted_pool_id=pool.id,
        status=PoolSubmissionStatus.PENDING,
    )
    db.add(submission)
    await db.commit()
    return {
        "status": "pending",
        "message": "Thank you. We will review this correction before changing the public listing.",
    }


@router.post("/{pool_id}/reviews", status_code=201)
async def submit_review(
    pool_id: uuid.UUID,
    body: DirectoryReviewCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    if not (
        await db.execute(
            select(Pool.id).where(
                Pool.id == pool_id,
                Pool.directory_published.is_(True),
                Pool.is_active.is_(True),
            )
        )
    ).scalar_one_or_none():
        raise HTTPException(404, "Pool not found")
    prior = (
        await db.execute(
            select(PoolDirectoryReview).where(
                PoolDirectoryReview.pool_id == pool_id,
                PoolDirectoryReview.author_auth_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    if prior:
        prior.rating, prior.review, prior.moderation_status = (
            body.rating,
            body.review,
            "pending",
        )
    else:
        db.add(
            PoolDirectoryReview(
                pool_id=pool_id,
                author_auth_id=user.user_id,
                rating=body.rating,
                review=body.review,
                moderation_status="pending",
            )
        )
    await db.commit()
    return {"status": "pending", "message": "Your review will appear after moderation."}


@admin.patch("/{pool_id}/publish")
async def set_published(
    pool_id: uuid.UUID,
    published: bool,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pool = await db.get(Pool, pool_id)
    if not pool:
        raise HTTPException(404, "Pool not found")
    if published and pool.last_verified_at is None:
        raise HTTPException(
            422, "Verify pool details and set last_verified_at before publishing"
        )
    pool.directory_published = published
    await db.commit()
    return {"pool_id": pool_id, "directory_published": published}


@admin.get("/reviews/pending")
async def pending_reviews(
    _admin=Depends(require_admin), db: AsyncSession = Depends(get_async_db)
):
    rows = (
        (
            await db.execute(
                select(PoolDirectoryReview)
                .where(PoolDirectoryReview.moderation_status == "pending")
                .order_by(PoolDirectoryReview.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"id": r.id, "pool_id": r.pool_id, "rating": r.rating, "review": r.review}
        for r in rows
    ]


@admin.patch("/reviews/{review_id}")
async def moderate_review(
    review_id: uuid.UUID,
    approved: bool,
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    row = await db.get(PoolDirectoryReview, review_id)
    if not row:
        raise HTTPException(404, "Review not found")
    row.moderation_status = "approved" if approved else "rejected"
    await db.commit()
    return {"status": row.moderation_status}


@router.post("/suggestions", status_code=201)
async def suggest_pool(
    body: DirectorySuggestion,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Anyone with a free visitor login may contribute; no paid membership required."""
    if body.website:
        # Honeypot. Do not publish or reward form spam.
        raise HTTPException(400, "Invalid suggestion")
    normalized = body.pool_name.strip()
    area = body.location_area.strip()
    candidates = (
        (
            await db.execute(
                select(Pool).where(Pool.location_area.ilike(area)).limit(100)
            )
        )
        .scalars()
        .all()
    )
    if any(
        likely_same_pool(
            normalized, area, pool.name, pool.location_area, body.address, pool.address
        )
        for pool in candidates
    ):
        raise HTTPException(
            409,
            "A similar pool is already recorded in this area. Please submit a correction.",
        )
    duplicate = (
        await db.execute(
            select(Pool.id, Pool.name)
            .where(
                func.lower(Pool.name) == normalized.casefold(),
                func.lower(Pool.location_area) == area.casefold(),
            )
            .limit(1)
        )
    ).first()
    if duplicate:
        raise HTTPException(
            409,
            "This pool is already recorded. Please open its directory listing or contact SwimBuddz with corrections.",
        )
    existing = (
        await db.execute(
            select(PoolSubmission.id)
            .where(
                func.lower(PoolSubmission.pool_name) == normalized.casefold(),
                func.lower(PoolSubmission.location_area) == area.casefold(),
                PoolSubmission.status == PoolSubmissionStatus.PENDING,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            409, "This pool has already been suggested and is awaiting review."
        )
    submission = PoolSubmission(
        submitter_auth_id=user.user_id,
        submitter_email=getattr(user, "email", None),
        pool_name=normalized,
        location_area=area,
        address=body.address,
        contact_email=body.contact_email,
        member_notes=body.details,
        status=PoolSubmissionStatus.PENDING,
    )
    db.add(submission)
    await db.commit()
    return {
        "status": "pending",
        "message": "Thanks. SwimBuddz will review your pool suggestion.",
    }
