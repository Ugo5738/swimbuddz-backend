"""
Member-specific email templates.

These templates handle notifications for member lifecycle events:
- Welcome (post-registration)
- Tier activation (post-payment)
- Membership approval
- Membership rejection
- Password reset
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    checklist_box,
    cta_button,
    detail_box,
    info_box,
    sign_off,
    wrap_html,
)


async def send_welcome_email(
    to_email: str,
    member_name: str,
    dashboard_url: str = "https://swimbuddz.com/account",
) -> bool:
    """
    Send welcome email after a new member completes registration.

    Introduces the platform features: Bubbles wallet, referrals,
    challenges, awards, the store, and orientation basics.
    """
    subject = "Welcome to SwimBuddz — Here's everything you need to know"

    body = (
        f"Hi {member_name},\n\n"
        "Welcome to SwimBuddz! We're thrilled to have you join our swimming community.\n\n"
        "Here's a quick overview of what awaits you:\n\n"
        "BUBBLES — Your SwimBuddz wallet has been set up automatically. "
        "Bubbles are our in-app currency — earn them through activities and "
        "spend them on sessions, store items, and more.\n\n"
        "REFERRALS — Share SwimBuddz with friends and earn bonus Bubbles "
        "when they sign up using your referral code.\n\n"
        "CHALLENGES — Take on weekly and monthly swimming challenges to "
        "push your limits and earn rewards.\n\n"
        "AWARDS — Unlock badges and achievements as you hit milestones — "
        "from your first session to mastering new strokes.\n\n"
        "STORE — Browse SwimBuddz gear (goggles, caps, swimwear) in our "
        "online store. Members get exclusive discounts.\n\n"
        "ORIENTATION — New here? Check out our orientation guide on your "
        "dashboard to learn how sessions work, pool locations, and what to bring.\n\n"
        "NEXT STEPS:\n"
        "1. Complete your membership payment to unlock full access\n"
        "2. Explore available sessions and events\n"
        "3. Check out your Bubbles wallet\n\n"
        f"Visit your dashboard: {dashboard_url}\n\n"
        "See you in the water!\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Welcome to SwimBuddz! We're thrilled to have you join our swimming community.</p>"
        "<h3>Here's what awaits you:</h3>"
        + info_box(
            title="Bubbles — Your Wallet",
            content=(
                "Your SwimBuddz wallet has been set up automatically. "
                "Bubbles are our in-app currency — earn them through activities "
                "and spend them on sessions, store items, and more."
            ),
            bg_color="#f0f9ff",
            border_color="#0891b2",
        )
        + info_box(
            title="Referrals",
            content=(
                "Share SwimBuddz with friends and earn bonus Bubbles "
                "when they sign up using your referral code."
            ),
            bg_color="#f0fdf4",
            border_color="#22c55e",
        )
        + info_box(
            title="Challenges & Awards",
            content=(
                "Take on swimming challenges to push your limits and earn rewards. "
                "Unlock badges and achievements as you hit milestones — from your "
                "first session to mastering new strokes."
            ),
            bg_color="#fefce8",
            border_color="#eab308",
        )
        + info_box(
            title="Store",
            content=(
                "Browse SwimBuddz gear — goggles, caps, swimwear — in our online store. "
                "Members get exclusive discounts."
            ),
            bg_color="#faf5ff",
            border_color="#a855f7",
        )
        + info_box(
            title="Orientation",
            content=(
                "New here? Check out our orientation guide on your dashboard to learn "
                "how sessions work, pool locations, and what to bring."
            ),
            bg_color="#fff7ed",
            border_color="#f97316",
        )
        + checklist_box(
            "Next Steps",
            [
                "Complete your membership payment to unlock full access",
                "Explore available sessions and events",
                "Check out your Bubbles wallet",
            ],
        )
        + cta_button("Go to Your Dashboard", dashboard_url, color="#0891b2")
        + sign_off("See you in the water!")
    )

    html_body = wrap_html(
        title="Welcome to SwimBuddz!",
        subtitle="Everything you need to get started",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Welcome to SwimBuddz — Bubbles, referrals, challenges, and more await you",
    )

    return await send_email(to_email, subject, body, html_body)


# ── Tier activation templates ────────────────────────────────────────

# Tier-specific details for the activation email
_TIER_CONFIG = {
    "community": {
        "title": "Community Membership Activated!",
        "subtitle": "You're officially part of the SwimBuddz community",
        "gradient": GRADIENT_CYAN,
        "accent": "#0891b2",
        "highlights": [
            "Access to community swim sessions and open meets",
            "Join social events and meet fellow swimmers",
            "Earn Bubbles through participation and referrals",
            "Member discounts in the SwimBuddz store",
            "Session ride-sharing with other community members",
        ],
    },
    "club": {
        "title": "Club Membership Activated!",
        "subtitle": "Welcome to structured training at SwimBuddz Club",
        "gradient": GRADIENT_GREEN,
        "accent": "#10b981",
        "highlights": [
            "Structured training sessions with experienced coaches",
            "Performance tracking and personal progress reports",
            "Priority access to pool bookings",
            "Club challenges and competitive events",
            "Advanced gamification — badges, streaks, and leaderboards",
        ],
    },
    "academy": {
        "title": "Academy Enrollment Confirmed!",
        "subtitle": "Your swim education journey begins",
        "gradient": GRADIENT_GREEN,
        "accent": "#10b981",
        "highlights": [
            "Cohort-based learning with a structured curriculum",
            "Dedicated coach assigned to your group",
            "Progress milestones and skill assessments",
            "Certificate upon programme completion",
            "Access to all community and club sessions during your programme",
        ],
    },
}


async def send_tier_activated_email(
    to_email: str,
    member_name: str,
    tier: str,
    amount: float | int = 0,
    currency: str = "NGN",
    duration: str = "",
    dashboard_url: str = "https://swimbuddz.com/account",
) -> bool:
    """
    Send email when a member's tier payment is confirmed and their tier is activated.

    Args:
        to_email: Recipient email.
        member_name: First name for greeting.
        tier: One of 'community', 'club', 'academy'.
        amount: Amount paid.
        currency: Currency code (default NGN).
        duration: Human-readable duration e.g. '1 year', '3 months'.
        dashboard_url: Link to member dashboard.
    """
    config = _TIER_CONFIG.get(tier.lower(), _TIER_CONFIG["community"])

    subject = f"SwimBuddz — {config['title']}"

    # Build detail box
    details = {"Membership": tier.capitalize()}
    if duration:
        details["Duration"] = duration
    if amount:
        details["Amount Paid"] = f"{currency} {amount:,.0f}"

    # Plain text
    highlights_text = "\n".join(f"  - {h}" for h in config["highlights"])
    body = (
        f"Hi {member_name},\n\n"
        f"Your {tier.capitalize()} membership is now active!\n\n"
        f"Here's what's included:\n{highlights_text}\n\n"
        f"Visit your dashboard: {dashboard_url}\n\n"
        "See you in the water!\n"
        "The SwimBuddz Team"
    )

    # HTML
    highlights_html = "".join(f"<li>{h}</li>" for h in config["highlights"])
    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>Your <strong>{tier.capitalize()}</strong> membership is now active!</p>"
        + detail_box(details, accent_color=config["accent"])
        + "<h3>What's included:</h3>"
        f'<ul style="margin: 0 0 16px; padding-left: 24px;">{highlights_html}</ul>'
        + cta_button("Go to Your Dashboard", dashboard_url, color=config["accent"])
        + sign_off("See you in the water!")
    )

    html_body = wrap_html(
        title=config["title"],
        subtitle=config["subtitle"],
        body_html=body_html,
        header_gradient=config["gradient"],
        preheader=f"Your {tier.capitalize()} membership is now active",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_member_approved_email(
    to_email: str,
    member_name: str,
    login_url: str = "https://swimbuddz.com/auth/login",
) -> bool:
    """
    Send branded email when a member application is approved.
    """
    subject = "Welcome to SwimBuddz! Your account is approved"

    body = (
        f"Hi {member_name},\n\n"
        "Congratulations! Your SwimBuddz membership application has been "
        "approved.\n\n"
        "You can now log in and access all member features.\n\n"
        f"Log in: {login_url}\n\n"
        "Welcome to the community!\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Congratulations! Your SwimBuddz membership application has been approved.</p>"
        "<p>You can now log in and access all member features.</p>"
        + cta_button("Log In to SwimBuddz", login_url, color="#10b981")
        + "<p>Welcome to the community!</p>"
    )

    html_body = wrap_html(
        title="Welcome to SwimBuddz!",
        subtitle="Your membership has been approved",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader="Your SwimBuddz membership has been approved",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_member_rejected_email(
    to_email: str,
    member_name: str,
    rejection_reason: str = "Does not meet current criteria",
) -> bool:
    """
    Send branded email when a member application is rejected.
    """
    subject = "Update on your SwimBuddz application"

    body = (
        f"Hi {member_name},\n\n"
        "Thank you for your interest in SwimBuddz.\n\n"
        "After reviewing your application, we are unable to approve your "
        "membership at this time.\n\n"
        f"Reason: {rejection_reason}\n\n"
        "You are welcome to reapply in the future.\n\n"
        "Best regards,\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Thank you for your interest in SwimBuddz.</p>"
        "<p>After reviewing your application, we are unable to approve your "
        "membership at this time.</p>"
        + detail_box({"Reason": rejection_reason}, accent_color="#d97706")
        + "<p>You are welcome to reapply in the future.</p>"
    )

    html_body = wrap_html(
        title="Application Update",
        subtitle="Thank you for your interest in SwimBuddz",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader="Update on your SwimBuddz membership application",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_password_reset_email(
    to_email: str,
    reset_url: str,
) -> bool:
    """
    Send branded password reset email with a one-time reset link.

    Args:
        to_email: Recipient email address.
        reset_url: The Supabase-generated signed reset URL (redirects to /reset-password).
    """
    subject = "Reset your SwimBuddz password"

    body = (
        "Hi,\n\n"
        "We received a request to reset the password for your SwimBuddz account.\n\n"
        f"Reset your password here: {reset_url}\n\n"
        "This link expires in 1 hour. If you didn't request a password reset, "
        "you can safely ignore this email — your password won't change.\n\n"
        "The SwimBuddz Team"
    )

    body_html = (
        "<p>We received a request to reset the password for your SwimBuddz account.</p>"
        + cta_button("Reset password", reset_url, color="#0891b2")
        + info_box(
            content=(
                "⏱ This link expires in <strong>1 hour</strong>. "
                "If you didn't request a password reset, you can safely ignore "
                "this email — your password won't change."
            ),
            bg_color="#f0f9ff",
            border_color="#0891b2",
        )
        + '<hr class="divider" />'
        + '<p style="font-size: 13px; color: #94a3b8;">Button not working? Copy and paste this link into your browser:<br/>'
        + f'<a href="{reset_url}" style="color: #0891b2; word-break: break-all;">{reset_url}</a></p>'
    )

    html_body = wrap_html(
        title="Reset your password",
        subtitle="Follow the link below to choose a new password",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Reset your SwimBuddz password — link expires in 1 hour",
    )

    return await send_email(to_email, subject, body, html_body)
