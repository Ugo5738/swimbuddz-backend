"""Self-service member endpoints (/me*)."""

from datetime import datetime, timedelta
from typing import List

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    ChallengeBadgeAward,
    Club,
    ClubEnrollment,
    Member,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    resolve_member_media_urls,
)
from services.members_service.schemas import (
    ChallengeBadgeAwardResponse,
    MembershipHistoryPeriodResponse,
    MembershipHistoryResponse,
    MemberResponse,
    MemberMembershipResponse,
    MemberUpdate,
)
from services.members_service.services.club_access import (
    current_club_enrollment_until,
)

logger = get_logger(__name__)
router = APIRouter()


def _history_status(
    *, starts_at: datetime | None, ends_at: datetime | None, now: datetime
) -> str:
    if starts_at is not None and starts_at > now:
        return "upcoming"
    if ends_at is not None and ends_at <= now:
        return "expired"
    return "active"


async def _member_response_with_club_enrollment(
    member: Member,
    db: AsyncSession,
) -> dict:
    """Serialize a member with the current dated Club product projection."""

    member_dict = MemberResponse.model_validate(member).model_dump()
    membership = member_dict.get("membership")
    if membership is not None:
        membership["club_enrollment_until"] = await current_club_enrollment_until(
            db,
            member_id=member.id,
            at=utc_now(),
        )
        member_dict["membership"] = MemberMembershipResponse.model_validate(
            membership
        ).model_dump()
    return await resolve_member_media_urls(member_dict)


@router.get("/me", response_model=MemberResponse)
async def get_current_member_profile(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the profile of the currently authenticated member."""
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    if normalize_member_tiers(member):
        db.add(member)
        await db.commit()
        await db.refresh(member)

    # Resolve media URLs
    return await _member_response_with_club_enrollment(member, db)


@router.get("/me/membership-history", response_model=MembershipHistoryResponse)
async def get_current_member_membership_history(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the authenticated member's dated Membership and Club history.

    New Club purchases use exact immutable enrollment dates. Older membership
    rows only retained an expiry, so their calculated starts are explicitly
    marked as estimates instead of being presented as audited payment dates.
    """

    member = (
        await db.execute(
            select(Member)
            .where(Member.auth_id == current_user.user_id)
            .options(*member_eager_load_options())
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    now = utc_now()
    periods: list[MembershipHistoryPeriodResponse] = []
    club_coverages: list[tuple[datetime, datetime, datetime]] = []
    membership = member.membership
    enrollment_rows = (
        await db.execute(
            select(ClubEnrollment, Club)
            .join(Club, Club.id == ClubEnrollment.club_id)
            .where(ClubEnrollment.member_id == member.id)
            .order_by(ClubEnrollment.starts_at.desc())
        )
    ).all()
    exact_club_coverages = [
        (enrollment.starts_at, enrollment.ends_at)
        for enrollment, _club in enrollment_rows
        if enrollment.status not in {"cancelled", "revoked"}
    ]

    if membership and membership.community_paid_until:
        periods.append(
            MembershipHistoryPeriodResponse(
                id="legacy-annual-membership",
                product="community",
                label="Annual Membership",
                starts_at=member.created_at,
                ends_at=membership.community_paid_until,
                status=_history_status(
                    starts_at=member.created_at,
                    ends_at=membership.community_paid_until,
                    now=now,
                ),
                source="legacy_membership",
                dates_are_estimated=True,
            )
        )

    legacy_club_already_represented = bool(
        membership
        and membership.club_paid_until
        and any(
            # A legacy period ending exactly when the new one starts is a
            # separate period, not a duplicate of the new enrollment.
            starts_at < membership.club_paid_until <= ends_at
            for starts_at, ends_at in exact_club_coverages
        )
    )
    if (
        membership
        and membership.club_paid_until
        and not legacy_club_already_represented
    ):
        legacy_months = membership.club_billing_cycle_months or 3
        estimated_start = membership.club_paid_until - relativedelta(
            months=legacy_months
        )
        periods.append(
            MembershipHistoryPeriodResponse(
                id="legacy-club-membership",
                product="club",
                label="Club",
                starts_at=estimated_start,
                ends_at=membership.club_paid_until,
                status=_history_status(
                    starts_at=estimated_start,
                    ends_at=membership.club_paid_until,
                    now=now,
                ),
                source="legacy_membership",
                dates_are_estimated=True,
            )
        )
        club_coverages.append(
            (estimated_start, membership.club_paid_until, membership.club_paid_until)
        )

    if membership and membership.post_academy_club_until:
        bridge_start = (
            membership.academy_paid_until
            if membership.academy_paid_until
            and membership.academy_paid_until < membership.post_academy_club_until
            else None
        )
        periods.append(
            MembershipHistoryPeriodResponse(
                id="post-academy-club",
                product="club",
                label="Post-Academy Club access",
                starts_at=bridge_start,
                ends_at=membership.post_academy_club_until,
                status=_history_status(
                    starts_at=bridge_start,
                    ends_at=membership.post_academy_club_until,
                    now=now,
                ),
                source="post_academy",
                dates_are_estimated=bridge_start is None,
            )
        )
        club_coverages.append(
            (
                bridge_start or member.created_at,
                membership.post_academy_club_until,
                membership.post_academy_club_until,
            )
        )

    for enrollment, club in enrollment_rows:
        # Enrollment ends are stored as exclusive midnight boundaries. Return
        # the inclusive covered instant so the UI says "Dec 31", not "Jan 1".
        display_end = enrollment.ends_at - timedelta(microseconds=1)
        period_status = _history_status(
            starts_at=enrollment.starts_at,
            ends_at=enrollment.ends_at,
            now=now,
        )
        if enrollment.status not in {"active", "expired"}:
            period_status = enrollment.status
        periods.append(
            MembershipHistoryPeriodResponse(
                id=str(enrollment.id),
                product="club",
                label=club.name,
                starts_at=enrollment.starts_at,
                ends_at=display_end,
                status=period_status,
                source="club_enrollment",
                club_name=club.name,
                payment_mode=enrollment.payment_mode,
            )
        )
        if period_status not in {"cancelled", "revoked"}:
            club_coverages.append(
                (enrollment.starts_at, enrollment.ends_at, display_end)
            )

    periods.sort(
        key=lambda period: period.starts_at or datetime.min.replace(tzinfo=now.tzinfo),
        reverse=True,
    )

    current_club = [
        coverage for coverage in club_coverages if coverage[0] <= now < coverage[1]
    ]
    upcoming_club = [coverage for coverage in club_coverages if coverage[0] > now]
    if current_club:
        club_renewal_status = "active"
    elif upcoming_club:
        club_renewal_status = "upcoming"
    elif club_coverages:
        club_renewal_status = "due"
    else:
        club_renewal_status = "never"

    renewal_due_at = None
    if club_coverages:
        # Independently prepaid future quarters must not hide a gap in access.
        # Follow the current (or next upcoming) continuous coverage only.
        coverage = (
            max(current_club, key=lambda item: item[1])
            if current_club
            else min(upcoming_club, key=lambda item: item[0])
            if upcoming_club
            else max(club_coverages, key=lambda item: item[1])
        )
        coverage_end, renewal_due_at = coverage[1:]
        for starts_at, ends_at, display_end in sorted(club_coverages):
            if starts_at <= coverage_end < ends_at:
                coverage_end, renewal_due_at = ends_at, display_end
    has_club_history = bool(club_coverages) or bool(
        membership and "club" in (membership.declared_tiers or [])
    )
    return MembershipHistoryResponse(
        periods=periods,
        club_renewal_status=club_renewal_status,
        club_renewal_due_at=renewal_due_at,
        club_action="renew" if has_club_history else "join",
    )


@router.get("/me/badges", response_model=List[ChallengeBadgeAwardResponse])
async def list_my_badges(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List challenge badges earned by the authenticated member.

    Reads from the denormalised challenge_badge_awards table (one row per
    earned badge). Hydrates badge_image_url via media_service so the
    profile page can render the badge artwork without a per-row HTTP call.
    """
    member_row = await db.execute(
        select(Member).where(Member.auth_id == current_user.user_id)
    )
    member = member_row.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found.",
        )

    rows = await db.execute(
        select(ChallengeBadgeAward)
        .where(
            ChallengeBadgeAward.member_id == member.id,
            # Hide badges revoked by HQ. The row stays in the DB for audit
            # but doesn't surface on the member's profile/public pages.
            ChallengeBadgeAward.revoked_at.is_(None),
        )
        .order_by(ChallengeBadgeAward.awarded_at.desc())
    )
    awards = list(rows.scalars().all())

    # Bulk-resolve all distinct badge image media_ids in one HTTP call
    image_ids = [a.badge_image_media_id for a in awards if a.badge_image_media_id]
    url_map = await resolve_media_urls(image_ids) if image_ids else {}

    out: List[ChallengeBadgeAwardResponse] = []
    for award in awards:
        item = ChallengeBadgeAwardResponse.model_validate(award)
        if award.badge_image_media_id is not None:
            item.badge_image_url = url_map.get(
                award.badge_image_media_id
            ) or url_map.get(str(award.badge_image_media_id))
        out.append(item)
    return out


@router.patch("/me", response_model=MemberResponse)
async def update_current_member(
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the currently authenticated member's profile.
    Handles nested updates for profile, membership, preferences, etc.
    """
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)

    logger.warning(f"PATCH /me received update_data: {update_data}")
    if "profile_photo_media_id" in update_data:
        logger.warning(
            f"profile_photo_media_id value: {update_data['profile_photo_media_id']}"
        )
    else:
        logger.warning("profile_photo_media_id NOT in update_data")

    # Extract nested updates
    profile_update = update_data.pop("profile", None)
    emergency_contact_update = update_data.pop("emergency_contact", None)
    availability_update = update_data.pop("availability", None)
    membership_update = update_data.pop("membership", None)
    preferences_update = update_data.pop("preferences", None)

    # Snapshot city BEFORE applying profile updates — used after commit to
    # reconcile the location chat channel if it changed. ``profile`` may
    # be None when the member hasn't been profiled yet; default to None.
    _old_city = member.profile.city if member.profile else None

    # Update core Member fields
    for field, value in update_data.items():
        if hasattr(member, field):
            logger.warning(f"Setting member.{field} = {value}")
            setattr(member, field, value)

    # Update profile sub-record
    if profile_update and member.profile:
        if "address" not in profile_update and "area_in_lagos" in profile_update:
            profile_update["address"] = profile_update.get("area_in_lagos")
        if "area_in_lagos" not in profile_update and "address" in profile_update:
            profile_update["area_in_lagos"] = profile_update.get("address")
        for field, value in profile_update.items():
            if value is not None and hasattr(member.profile, field):
                setattr(member.profile, field, value)

    # Update emergency contact sub-record
    if emergency_contact_update and member.emergency_contact:
        for field, value in emergency_contact_update.items():
            if value is not None and hasattr(member.emergency_contact, field):
                setattr(member.emergency_contact, field, value)

    # Update availability sub-record
    if availability_update and member.availability:
        for field, value in availability_update.items():
            if value is not None and hasattr(member.availability, field):
                setattr(member.availability, field, value)

    # Update membership sub-record (with protection for billing fields)
    if membership_update and member.membership:
        protected_fields = {
            "community_paid_until",
            "club_paid_until",
            "academy_paid_until",
            "academy_alumni",
            "primary_tier",
            "active_tiers",
        }
        for field, value in membership_update.items():
            if (
                field not in protected_fields
                and value is not None
                and hasattr(member.membership, field)
            ):
                setattr(member.membership, field, value)

        # Handle tier change requests
        requested_tiers = membership_update.get("requested_tiers")
        if requested_tiers is not None:
            current_tiers = member.membership.active_tiers or []
            if set(requested_tiers) != set(current_tiers):
                member.membership.requested_tiers = requested_tiers

    # Update preferences sub-record
    if preferences_update and member.preferences:
        for field, value in preferences_update.items():
            if value is not None and hasattr(member.preferences, field):
                setattr(member.preferences, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Reconcile location (city) chat channel if it changed. Best-effort:
    # chat downtime never blocks profile edits. Helpers are idempotent.
    _new_city = member.profile.city if member.profile else None
    if (_old_city or "").strip() != (_new_city or "").strip():
        from services.members_service.services.chat_sync import (
            ensure_location_channel,
            reconcile_location_membership,
        )

        if _old_city and _old_city.strip():
            await reconcile_location_membership(
                city=_old_city,
                member_id=member.id,
                action="remove",
            )
        if _new_city and _new_city.strip():
            await ensure_location_channel(city=_new_city)
            await reconcile_location_membership(
                city=_new_city,
                member_id=member.id,
                action="add",
            )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    updated_member = result.scalar_one()

    # Resolve media URLs
    return await _member_response_with_club_enrollment(updated_member, db)
