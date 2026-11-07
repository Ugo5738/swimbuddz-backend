"""Transactional member acknowledgments and reviewer queue alerts."""

from html import escape
import hashlib
import json

from libs.common.config import get_settings
from libs.common.operational_email import enqueue_operational_email
from services.communications_service.templates.base import (
    cta_button,
    detail_box,
    wrap_html,
)
from services.members_service.models import Club
from services.members_service.services.membership_notifications import (
    notify_membership_change,
)


async def notify_assessment_submission(db, *, member, application, stage, submission):
    fingerprint = hashlib.sha256(
        json.dumps(submission, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]
    key = f"club-assessment:{application.id}:{stage}:{fingerprint}"
    club = await db.get(Club, application.club_id)
    club_name = club.name if club else "your Club"
    labels = {
        "readiness": "readiness form",
        "in_person": "in-pool assessment request",
        "video": "video assessment",
    }
    label = labels[stage]
    member_full_name = f"{member.first_name} {member.last_name}".strip()
    await notify_membership_change(
        db,
        member_id=member.id,
        title="We received your Club assessment submission",
        body=f"We received your {label} for {club_name}. The SwimBuddz team will review it and email you with the next step.",
        event_key=f"{key}:member",
        action_url=f"/upgrade/club/assessment/{application.id}",
    )
    admin_path = "/admin/club-applications"
    link = f"{get_settings().FRONTEND_URL.rstrip('/')}{admin_path}"
    subject = f"Club assessment ready for review: {member_full_name}"
    body = (
        f"{member_full_name} submitted a {label} for {club_name}.\n\n"
        f"Review the application in the admin portal: {link}\n\n"
        f"SwimBuddz"
    )
    html_body = wrap_html(
        title="Club Assessment Ready for Review",
        subtitle=f"{member_full_name} · {club_name}",
        preheader=f"{member_full_name} submitted a {label} for {club_name}.",
        body_html=(
            "<p>A member has submitted their Club assessment materials and is awaiting review in the admin portal.</p>"
            + detail_box(
                {
                    "Applicant": escape(member_full_name),
                    "Club Location": escape(club_name),
                    "Submission Type": escape(label.title()),
                }
            )
            + cta_button("Review Club Applications", escape(link, quote=True))
        ),
    )
    for email in sorted(
        {
            email.strip().lower()
            for email in get_settings().CLUB_REVIEW_NOTIFICATION_EMAILS
            if email.strip()
        }
    ):
        await enqueue_operational_email(
            db,
            event_key=f"{key}:reviewer:{email}",
            recipient_email=email,
            subject=subject,
            body=body,
            html_body=html_body,
            action_url=admin_path,
        )
