"""
Coach-specific email templates.

These templates handle notifications for coach lifecycle events:
- Application approval/rejection/request-more-info
- Agreement signing confirmation
- Grade changes/promotions
- Shadow assignments
- Readiness assessments
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    GRADIENT_PURPLE,
    cta_button,
    detail_box,
    info_box,
    wrap_html,
)


async def send_coach_agreement_signed_email(
    to_email: str,
    coach_name: str,
    version: str,
    signed_at: str,
    dashboard_url: str = "https://swimbuddz.com/coach/dashboard",
) -> bool:
    """
    Send confirmation email after coach signs the agreement.
    """
    subject = "Agreement Signed Successfully"

    body = f"""Hi Coach {coach_name},

This confirms that you have successfully signed the SwimBuddz Coach Agreement (version {version}).

Signed on: {signed_at}

Your signed agreement is stored securely and you can view it anytime from your Coach Dashboard.

Next Steps:
- Complete your onboarding if you haven't already
- Review the Coach Handbook for operational guidelines
- Check your dashboard for any pending cohort assignments

Access your dashboard: {dashboard_url}

Welcome to the team!

— The SwimBuddz Team
"""

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        "<p>This confirms that you have successfully signed the SwimBuddz Coach Agreement.</p>"
        + detail_box(
            {
                "Agreement Version": version,
                "Signed On": signed_at,
            },
            accent_color="#10b981",
        )
        + "<h3>Next Steps:</h3>"
        "<ul>"
        "<li>Complete your onboarding if you haven't already</li>"
        "<li>Review the Coach Handbook for operational guidelines</li>"
        "<li>Check your dashboard for any pending cohort assignments</li>"
        "</ul>"
        + cta_button("Go to Coach Dashboard", dashboard_url, color="#10b981")
        + "<p>Welcome to the team!</p>"
    )

    html_body = wrap_html(
        title="✅ Agreement Signed",
        subtitle="Your coach agreement has been recorded",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader="Your SwimBuddz Coach Agreement has been signed successfully",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_grade_change_email(
    to_email: str,
    coach_name: str,
    category: str,
    old_grade: str,
    new_grade: str,
    effective_date: str = "",
    dashboard_url: str = "https://swimbuddz.com/coach/dashboard",
) -> bool:
    """
    Send notification when a coach's grade is updated.
    """
    category_display = category.replace("_", " ").title()
    old_display = old_grade.replace("_", " ").title()
    new_display = new_grade.replace("_", " ").title()

    is_promotion = new_grade > old_grade
    action = "promoted" if is_promotion else "updated"

    subject = (
        f"Coach Grade {'Promotion' if is_promotion else 'Update'}: {category_display}"
    )

    body = f"""Hi Coach {coach_name},

Your coach grade for {category_display} has been {action}.

Previous Grade: {old_display}
New Grade: {new_display}
{"Effective Date: " + effective_date if effective_date else ""}

{"Congratulations on your promotion! This reflects your dedication and growth as a coach." if is_promotion else "Your grade has been updated. Please check your dashboard for details."}

This may affect your eligible cohort assignments and pay band.

View your updated profile: {dashboard_url}

— The SwimBuddz Team
"""

    header_gradient = GRADIENT_AMBER if is_promotion else GRADIENT_CYAN

    # Grade change visual
    grade_visual = (
        '<div style="background: #f8fafc; padding: 28px; border-radius: 12px; margin: 24px 0; text-align: center;">'
        f'<span style="display: inline-block; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 18px; background: #f1f5f9; color: #64748b;">{old_display}</span>'
        '<span style="font-size: 24px; margin: 0 16px; color: #94a3b8;">&rarr;</span>'
        f'<span style="display: inline-block; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 18px; background: #ecfdf5; color: #059669;">{new_display}</span>'
        "</div>"
    )

    congrats_html = (
        "<p><strong>Congratulations!</strong> This reflects your dedication and growth as a coach.</p>"
        if is_promotion
        else "<p>Your grade has been updated. Please check your dashboard for details.</p>"
    )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        f"<p>Your coach grade for <strong>{category_display}</strong> has been {action}.</p>"
        + grade_visual
        + congrats_html
        + "<p>This may affect your eligible cohort assignments and pay band.</p>"
        + cta_button("View Updated Profile", dashboard_url, color="#10b981")
    )

    html_body = wrap_html(
        title=f"{'🎉 Grade Promotion!' if is_promotion else 'Grade Update'}",
        subtitle=category_display,
        body_html=body_html,
        header_gradient=header_gradient,
        preheader=f"Coach grade {action}: {old_display} → {new_display}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_shadow_assignment_email(
    to_email: str,
    coach_name: str,
    lead_coach_name: str,
    cohort_name: str,
    program_name: str,
    start_date: str,
    end_date: str = "",
    dashboard_url: str = "https://swimbuddz.com/coach/dashboard",
) -> bool:
    """
    Send notification when a coach is assigned as a shadow to a cohort.
    """
    subject = f"Shadow Assignment: {cohort_name}"

    body = f"""Hi Coach {coach_name},

You have been assigned as a shadow coach for {cohort_name} ({program_name}).

Lead Coach: {lead_coach_name}
Cohort: {cohort_name}
Program: {program_name}
Start Date: {start_date}
{"End Date: " + end_date if end_date else ""}

As a Shadow Coach:
- Observe the lead coach's teaching methods and session flow
- Take notes on safety protocols and student interactions
- Ask questions during debrief time (not during active instruction)
- Complete your shadow evaluation forms after each session

Your lead coach will evaluate your progress and provide feedback.

Access your dashboard: {dashboard_url}

— The SwimBuddz Team
"""

    details = {
        "Lead Coach": lead_coach_name,
        "Program": program_name,
        "Cohort": cohort_name,
        "Start Date": start_date,
        "End Date": end_date,
    }

    tips_html = info_box(
        "<h3 style='margin: 0 0 10px 0; color: #6d28d9;'>As a Shadow Coach:</h3>"
        "<ul style='margin: 0; padding-left: 20px;'>"
        "<li>Observe the lead coach's teaching methods and session flow</li>"
        "<li>Take notes on safety protocols and student interactions</li>"
        "<li>Ask questions during debrief time (not during active instruction)</li>"
        "<li>Complete your shadow evaluation forms after each session</li>"
        "</ul>",
        bg_color="#f5f3ff",
        border_color="#8b5cf6",
    )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        "<p>You have been assigned as a <strong>shadow coach</strong> for the following cohort:</p>"
        + detail_box(details, accent_color="#8b5cf6")
        + tips_html
        + "<p>Your lead coach will evaluate your progress and provide feedback.</p>"
        + cta_button("Go to Dashboard", dashboard_url, color="#8b5cf6")
    )

    html_body = wrap_html(
        title="👁️ Shadow Assignment",
        subtitle="You've been assigned to observe and learn",
        body_html=body_html,
        header_gradient=GRADIENT_PURPLE,
        preheader=f"Shadow assignment: {cohort_name} with {lead_coach_name}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_readiness_email(
    to_email: str,
    coach_name: str,
    target_grade: str,
    is_ready: bool,
    passed_checks: list[str],
    pending_checks: list[str],
    dashboard_url: str = "https://swimbuddz.com/coach/dashboard",
) -> bool:
    """
    Send coach readiness assessment result email.
    """
    grade_display = target_grade.replace("_", " ").title()

    if is_ready:
        subject = f"You're Ready for {grade_display}! 🎉"
        status_msg = (
            f"Congratulations! You have met all the requirements for {grade_display}."
        )
    else:
        subject = f"Readiness Assessment: {grade_display}"
        status_msg = f"Here's your current readiness assessment for {grade_display}."

    passed_text = (
        "\n".join([f"  ✓ {c}" for c in passed_checks])
        if passed_checks
        else "  None yet"
    )
    pending_text = (
        "\n".join([f"  ○ {c}" for c in pending_checks])
        if pending_checks
        else "  All complete!"
    )

    body = f"""Hi Coach {coach_name},

{status_msg}

Completed Requirements:
{passed_text}

{"Remaining Requirements:" if pending_checks else ""}
{pending_text if pending_checks else ""}

{"Your admin team has been notified and will review your grade progression." if is_ready else "Keep working towards these goals and you'll be there soon!"}

View your progress: {dashboard_url}

— The SwimBuddz Team
"""

    header_gradient = GRADIENT_GREEN if is_ready else GRADIENT_AMBER

    passed_html = "".join(
        [f"<li style='color: #059669;'>✓ {c}</li>" for c in passed_checks]
    )
    pending_html = "".join(
        [f"<li style='color: #d97706;'>○ {c}</li>" for c in pending_checks]
    )

    checks_html = (
        '<div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">'
        '<h3 style="margin: 0 0 10px 0; color: #059669;">✅ Completed</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{passed_html if passed_html else "<li>None yet</li>"}</ul>'
        "</div>"
    )

    if pending_checks:
        checks_html += (
            '<div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">'
            '<h3 style="margin: 0 0 10px 0; color: #d97706;">⏳ Remaining</h3>'
            f'<ul style="margin: 0; padding-left: 20px;">{pending_html}</ul>'
            "</div>"
        )

    next_steps = (
        "<p>Your admin team has been notified and will review your grade progression.</p>"
        if is_ready
        else "<p>Keep working towards these goals \u2014 you'll be there soon!</p>"
    )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        f"<p>{status_msg}</p>"
        + checks_html
        + next_steps
        + cta_button("View Your Progress", dashboard_url, color="#10b981")
    )

    html_body = wrap_html(
        title=f"{'🎉 Ready for Promotion!' if is_ready else '📋 Readiness Assessment'}",
        subtitle=grade_display,
        body_html=body_html,
        header_gradient=header_gradient,
        preheader=f"{'Ready for ' + grade_display + '!' if is_ready else 'Readiness assessment for ' + grade_display}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_application_approved_email(
    to_email: str,
    coach_name: str,
    onboarding_url: str = "https://swimbuddz.com/coach/onboarding",
) -> bool:
    """
    Send branded email when a coach application is approved.
    """
    subject = "Congratulations! Your SwimBuddz Coach Application is Approved"

    body = (
        f"Hi Coach {coach_name},\n\n"
        "We are thrilled to welcome you as an approved SwimBuddz coach!\n\n"
        "Please complete your coach onboarding to activate your profile and "
        "start coaching.\n\n"
        f"Complete onboarding: {onboarding_url}\n\n"
        "If you haven't logged in yet, you'll be prompted to sign in first.\n\n"
        "Welcome to the team!\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        "<p>We are thrilled to welcome you as an approved SwimBuddz coach!</p>"
        "<p>Please complete your coach onboarding to activate your profile "
        "and start coaching.</p>"
        + cta_button("Complete Onboarding", onboarding_url, color="#10b981")
        + "<p style='font-size: 13px; color: #64748b;'>If you haven't logged in yet, "
        "you'll be prompted to sign in first.</p>" + "<p>Welcome to the team!</p>"
    )

    html_body = wrap_html(
        title="Application Approved!",
        subtitle="Welcome to the SwimBuddz coaching team",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader="Your SwimBuddz coach application has been approved",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_application_rejected_email(
    to_email: str,
    coach_name: str,
    rejection_reason: str,
) -> bool:
    """
    Send branded email when a coach application is rejected.
    """
    subject = "Update on your SwimBuddz Coach Application"

    body = (
        f"Hi {coach_name},\n\n"
        "Thank you for your interest in becoming a SwimBuddz coach.\n\n"
        "After careful review, we are unable to approve your application "
        "at this time.\n\n"
        f"Reason: {rejection_reason}\n\n"
        "You may re-apply in the future if your qualifications change.\n\n"
        "Best regards,\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {coach_name},</p>"
        "<p>Thank you for your interest in becoming a SwimBuddz coach.</p>"
        "<p>After careful review, we are unable to approve your application "
        "at this time.</p>"
        + detail_box({"Reason": rejection_reason}, accent_color="#d97706")
        + "<p>You may re-apply in the future if your qualifications change.</p>"
    )

    html_body = wrap_html(
        title="Application Update",
        subtitle="Thank you for your interest in coaching",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader="Update on your SwimBuddz coach application",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_application_more_info_email(
    to_email: str,
    coach_name: str,
    message: str,
    dashboard_url: str = "https://swimbuddz.com/account/coach",
) -> bool:
    """
    Send branded email requesting more info from a coach applicant.
    """
    subject = "Action Required: Additional Information for SwimBuddz Coach Application"

    body = (
        f"Hi {coach_name},\n\n"
        "We are reviewing your coach application and need some additional "
        "information before we can proceed.\n\n"
        f"Request: {message}\n\n"
        "Please log in to your dashboard to update your application.\n\n"
        f"Update your application: {dashboard_url}\n\n"
        "Best regards,\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {coach_name},</p>"
        "<p>We are reviewing your coach application and need some additional "
        "information before we can proceed.</p>"
        + info_box(
            f"<p style='margin: 0;'>{message}</p>",
            bg_color="#fefce8",
            border_color="#f59e0b",
            title="Information Requested",
        )
        + "<p>Please log in to your dashboard to update your application.</p>"
        + cta_button("Update Application", dashboard_url, color="#f59e0b")
    )

    html_body = wrap_html(
        title="Action Required",
        subtitle="Additional information needed for your application",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader="We need additional information for your coach application",
    )

    return await send_email(to_email, subject, body, html_body)
