"""Expected Club attendees, using the same dated rules as session booking."""

from types import SimpleNamespace

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    Club,
    ClubEnrollment,
    Member,
    MemberMembership,
    PodAssignment,
)
from services.members_service.schemas.club_roster import (
    ClubSessionRosterContext,
    ClubSessionRosterMember,
)
from services.members_service.services.club_access import resolve_club_access_checks


async def club_session_roster(
    db: AsyncSession, context: ClubSessionRosterContext
) -> list[ClubSessionRosterMember]:
    """Do not infer Club eligibility from cached tier labels or Academy payment.

    Candidates include dated enrollments and the two supported compatibility
    paths. The existing access resolver owns location, inclusion and visitor
    decisions. Bookings/actual attendance remain separate from this read model.
    """
    enrolled_ids = (
        select(ClubEnrollment.member_id)
        .join(Club, Club.id == ClubEnrollment.club_id)
        .where(
            ClubEnrollment.status == "active",
            ClubEnrollment.starts_at <= context.at,
            ClubEnrollment.ends_at > context.at,
            Club.is_active.is_(True),
        )
    )
    legacy_ids = select(MemberMembership.member_id).where(
        or_(
            MemberMembership.club_paid_until > context.at,
            MemberMembership.post_academy_club_until > context.at,
        )
    )
    query = select(Member.id, Member.first_name, Member.last_name, Member.email).where(
        Member.is_active.is_(True),
        Member.approval_status == "approved",
        or_(Member.id.in_(enrolled_ids), Member.id.in_(legacy_ids)),
    )
    if context.pod_id is not None:
        # A historical sheet must use the assignment covering that swim, not
        # today's assignment after a later transfer.
        assigned_ids = select(PodAssignment.member_id).where(
            PodAssignment.pod_id == context.pod_id,
            PodAssignment.joined_at <= context.at,
            or_(PodAssignment.left_at.is_(None), PodAssignment.left_at > context.at),
        )
        query = query.where(Member.id.in_(assigned_ids))
    candidates = list(
        (
            await db.execute(
                query.order_by(Member.first_name, Member.last_name, Member.id)
            )
        )
        .mappings()
        .all()
    )
    members = []
    for offset in range(0, len(candidates), 500):
        batch = candidates[offset : offset + 500]
        decisions = await resolve_club_access_checks(
            db,
            [
                SimpleNamespace(
                    **context.model_dump(),
                    member_id=row["id"],
                    context_key=str(row["id"]),
                )
                for row in batch
            ],
        )
        allowed = {row["context_key"]: row for row in decisions if row["allowed"]}
        members.extend(
            ClubSessionRosterMember(
                member_id=row["id"],
                name=f"{row['first_name']} {row['last_name']}".strip(),
                email=row["email"],
                access_source=allowed[str(row["id"])]["source"],
            )
            for row in batch
            if str(row["id"]) in allowed
        )
    return members
