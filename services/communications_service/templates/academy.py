"""
Academy-related email templates.
"""

from typing import Optional

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    GRADIENT_PURPLE,
    checklist_box,
    cta_button,
    detail_box,
    info_box,
    sign_off,
    wrap_html,
)


async def send_enrollment_confirmation_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
    location: Optional[str] = None,
    coach_name: Optional[str] = None,
    is_installment: bool = False,
    installment_deposit: Optional[str] = None,
    installment_schedule: Optional[list[str]] = None,
) -> bool:
    """
    Send enrollment confirmation email to a member.
    Includes program details, before-you-start checklist, and installment
    schedule when the member is paying in instalments.
    """
    subject = f"Welcome to {program_name}! Your enrollment is confirmed."

    # Build plain-text installment note
    installment_note = ""
    if is_installment and installment_schedule:
        schedule_lines = "\n".join(f"  - {item}" for item in installment_schedule)
        installment_note = (
            f"\nPayment Schedule (installments):\n{schedule_lines}\n"
            "Remaining payments are auto-collected from your Bubbles wallet.\n"
        )

    location_line = f"Location: {location}\n" if location else ""
    coach_line = f"Coach: {coach_name}\n" if coach_name else ""

    body = f"""Hi {member_name},

Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date}
{location_line}{coach_line}{installment_note}
Before your first session:
- Pack your swim gear: swimsuit, goggles, towel, and swim cap
- Review the program curriculum on your Academy dashboard
- Check the session schedule and add sessions to your calendar
- Explore prep materials and learning resources in the Academy section

If you have any questions, please reach out to our team.

See you in the water!

— The SwimBuddz Team
"""

    # Build cohort detail dict
    cohort_details: dict = {
        "Program": program_name,
        "Cohort": cohort_name,
        "Start Date": start_date,
    }
    if location:
        cohort_details["Location"] = location
    if coach_name:
        cohort_details["Coach"] = coach_name

    # Build installment schedule HTML block
    installment_html = ""
    if is_installment and installment_schedule:
        schedule_items = "".join(
            f"<li style='padding:4px 0;'>{item}</li>" for item in installment_schedule
        )
        installment_html = (
            "<h3 style='margin-top:24px;margin-bottom:8px;'>💳 Payment Schedule</h3>"
            f"<ul style='margin:0;padding-left:20px;color:#475569;'>{schedule_items}</ul>"
            "<p style='font-size:13px;color:#64748b;margin-top:8px;'>"
            "Remaining payments are automatically deducted from your Bubbles wallet "
            "on schedule. You'll receive a reminder 3 days before each due date."
            "</p>"
        )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed. "
        "We're excited to have you join us.</p>"
        + detail_box(cohort_details)
        + installment_html
        + checklist_box(
            "Before Your First Session",
            [
                "Pack your swim gear: swimsuit, goggles, towel, and swim cap",
                "Review the program curriculum on your Academy dashboard",
                "Check the session schedule and add sessions to your calendar",
                "Explore prep materials and learning resources in the Academy section",
            ],
        )
        + "<p>If you have any questions, please reach out to our team.</p>"
        + sign_off("See you in the water! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title=f"🏊\u200d♂️ Welcome to {program_name}!",
        subtitle="Your enrollment has been confirmed",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Your enrollment in {program_name} is confirmed — see what to do next",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_enrollment_reminder_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
    start_time: str,
    location: str,
    days_until: int,
    prep_materials: Optional[list] = None,
) -> bool:
    """
    Send reminder email X days before cohort starts.
    Content varies based on days remaining (7, 3, 1).
    """
    days_text = f"{days_until} days" if days_until > 1 else "tomorrow"
    subject = f"Reminder: Your swimming course starts in {days_text}! 🏊\u200d♂️"

    if days_until == 1:
        subject = "URGENT: Your swimming course starts tomorrow! 🏊\u200d♂️"

    urgency_msg = f"We're excited to see you in {days_text}!"
    if days_until == 1:
        urgency_msg = (
            "We're excited to see you tomorrow! Please double check your gear."
        )

    # Tip based on timing
    tip_html = ""
    if days_until >= 7:
        tip_html = info_box(
            "<strong>💡 Pro Tip:</strong><br/>"
            "Now is a great time to try on your swimwear and make sure everything fits comfortably!",
            bg_color="#fffbeb",
            border_color="#f59e0b",
        )
    elif days_until <= 3:
        tip_html = info_box(
            "<strong>💡 Preparation:</strong><br/>"
            "Hydrate well before your session and arrive 15 minutes early to change.",
            bg_color="#fffbeb",
            border_color="#f59e0b",
        )

    body = f"""Hi {member_name},

{urgency_msg}

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date} at {start_time}
Location: {location}

Checklist:
- Swimwear and cap
- Goggles
- Towel and flip flops
- Water bottle

See you soon!

— The SwimBuddz Team
"""

    body_html = f"<p>Hi {member_name},</p>" f"<p>{urgency_msg}</p>" + detail_box(
        {
            "Program": program_name,
            "Cohort": cohort_name,
            "When": f"{start_date} at {start_time}",
            "Where": location,
        }
    ) + tip_html + checklist_box(
        "🎒 Checklist",
        [
            "Swimwear and cap",
            "Goggles",
            "Towel and flip flops",
            "Water bottle",
        ],
    ) + sign_off("See you in the water! 🏊\u200d♂️")

    html_body = wrap_html(
        title="🏊\u200d♂️ Getting Ready?",
        subtitle=f"Your course starts in {days_text}",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Your swimming course starts in {days_text}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_waitlist_promotion_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    dashboard_url: str = "https://swimbuddz.com/dashboard/academy",
) -> bool:
    """
    Send email to student when they are promoted off the waitlist.
    """
    subject = f"Good News! A Spot Opened Up for {program_name} 🎉"

    body = f"""Hi {member_name},

Good news! A spot has opened up for you in the {program_name} ({cohort_name}) cohort.

You have been moved off the waitlist and your status is now Pending Approval.

Please log in to your dashboard to confirm your enrollment and complete payment within the next 24 hours to secure your spot.

{dashboard_url}

If you no longer wish to join, please decline the spot so we can offer it to the next person on the list.

See you in the water!

— The SwimBuddz Team
"""

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>Good news! A spot has opened up for you in the <strong>{program_name}</strong> ({cohort_name}) cohort.</p>"
        "<p>You have been moved off the waitlist and your status is now <strong>Pending Approval</strong>.</p>"
        "<p>Please log in to your dashboard to confirm your enrollment and complete payment within the next 24 hours to secure your spot.</p>"
        + cta_button("Secure My Spot", dashboard_url, color="#10b981")
        + '<p style="font-size: 14px; color: #64748b;">'
        "If you no longer wish to join, please decline the spot so we can offer it to the next person on the list.</p>"
        + sign_off("See you in the water! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="🎉 You're In!",
        subtitle=f"A spot opened up for {program_name}",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"A spot opened up for {program_name}!",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_progress_report_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    milestones_completed: int,
    total_milestones: int,
    recent_achievements: list,  # [{"name": str, "achieved_at": datetime}]
    coach_feedback: list,  # [{"milestone": str, "notes": str}]
    pdf_attachment: bytes = None,
) -> bool:
    """
    Send weekly/monthly progress report email to a student.
    Optionally includes PDF attachment.
    """
    completion_pct = (
        round((milestones_completed / total_milestones) * 100)
        if total_milestones > 0
        else 0
    )
    subject = f"Your Progress Report for {program_name} 📊"

    # Build achievements list
    achievements_text = ""
    if recent_achievements:
        achievements_text = "\n".join([f"  ✓ {a['name']}" for a in recent_achievements])
    else:
        achievements_text = "  No new milestones this period."

    # Build feedback section
    feedback_text = ""
    if coach_feedback:
        feedback_text = "\n".join(
            [f"  • {f['milestone']}: {f['notes']}" for f in coach_feedback]
        )

    body = f"""Hi {member_name},

Here's your progress update for {program_name} ({cohort_name}).

📊 Progress: {milestones_completed}/{total_milestones} milestones ({completion_pct}%)

Recent Achievements:
{achievements_text}

{"Coach Feedback:" if coach_feedback else ""}
{feedback_text}

Keep up the great work! 🏊‍♂️

— The SwimBuddz Team
"""

    # Progress bar
    progress_bar_html = (
        '<div style="background: #f8fafc; border-radius: 12px; padding: 24px; margin: 20px 0; text-align: center;">'
        f'<div style="font-size: 36px; font-weight: 700; color: #1e293b;">{completion_pct}%</div>'
        f'<div style="font-size: 14px; color: #64748b; margin-bottom: 12px;">{milestones_completed} of {total_milestones} milestones completed</div>'
        '<div style="background: #e2e8f0; border-radius: 8px; height: 24px; overflow: hidden;">'
        f'<div style="background: linear-gradient(90deg, #10b981, #059669); height: 100%; border-radius: 8px; width: {completion_pct}%;"></div>'
        "</div></div>"
    )

    # Achievements
    achievements_html_items = ""
    if recent_achievements:
        achievements_html_items = "".join(
            [f"<li>✓ {a['name']}</li>" for a in recent_achievements]
        )
    else:
        achievements_html_items = "<li>No new milestones this period.</li>"

    achievements_html = (
        '<div style="background: #ecfdf5; padding: 20px; border-radius: 8px; margin: 20px 0;">'
        '<h3 style="margin: 0 0 10px; color: #065f46;">🏆 Recent Achievements</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{achievements_html_items}</ul></div>'
    )

    # Feedback
    feedback_html = ""
    if coach_feedback:
        feedback_items = "".join(
            f'<div style="background: white; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #8b5cf6;">'
            f"<strong>{f['milestone']}:</strong> {f['notes']}</div>"
            for f in coach_feedback
        )
        feedback_html = f"<h3>💬 Coach Feedback</h3>{feedback_items}"

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Here's your progress update!</p>"
        + progress_bar_html
        + achievements_html
        + feedback_html
        + sign_off("Keep up the great work! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="📊 Progress Report",
        subtitle=f"{program_name} \u2022 {cohort_name}",
        body_html=body_html,
        header_gradient=GRADIENT_PURPLE,
        preheader=f"{completion_pct}% complete - {program_name} progress report",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_certificate_email(
    to_email: str,
    member_name: str,
    program_name: str,
    completion_date: str,
    verification_code: str,
    dashboard_url: str = "https://swimbuddz.com/account/academy",
) -> bool:
    """
    Send certificate notification email when student completes all milestones.
    """
    subject = f"Congratulations! You've completed {program_name}! 🎓"

    body = f"""Hi {member_name},

Congratulations! 🎉

You have successfully completed all requirements for {program_name}!

Your Certificate of Completion is ready.

Completion Date: {completion_date}
Verification Code: {verification_code}

You can download your certificate from your Academy dashboard.

{dashboard_url}

We're so proud of your achievement! Keep swimming! 🏊‍♂️

— The SwimBuddz Team
"""

    cert_box_html = (
        '<div style="background: white; padding: 30px; border-radius: 12px; border: 2px dashed #f59e0b; text-align: center; margin: 24px 0;">'
        '<h2 style="margin: 0 0 10px 0; color: #d97706;">🏆 Certificate Ready!</h2>'
        f'<p style="margin: 5px 0;">Completion Date: <strong>{completion_date}</strong></p>'
        f'<p style="margin: 5px 0; font-size: 12px; color: #64748b;">Verification: {verification_code}</p>'
        "</div>"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>We're thrilled to announce that you have successfully completed all requirements for <strong>{program_name}</strong>!</p>"
        + cert_box_html
        + cta_button("Download Certificate", dashboard_url, color="#f59e0b")
        + "<p>We're so proud of your achievement! This is a significant milestone in your swimming journey.</p>"
        + sign_off("Keep swimming! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="🎓 Congratulations!",
        subtitle="You've earned your certificate!",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"Congratulations! You've completed {program_name}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_attendance_summary_email(
    to_email: str,
    coach_name: str,
    cohort_name: str,
    program_name: str,
    period: str,  # e.g., "Jan 8-15, 2026"
    total_sessions: int,
    student_stats: list,  # [{"name": str, "present": int, "absent": int, "late": int, "rate": int}]
    at_risk_students: list,  # [{"name": str, "issue": str}]
) -> bool:
    """
    Send weekly attendance summary to a coach.
    """
    subject = f"Attendance Summary: {cohort_name} ({period}) 📊"

    # Build student stats text
    stats_text = "\n".join(
        [
            f"  - {s['name']}: {s['present']}/{total_sessions} sessions ({s['rate']}%)"
            for s in student_stats
        ]
    )

    at_risk_text = ""
    if at_risk_students:
        at_risk_text = "\n⚠️ Students Needing Attention:\n" + "\n".join(
            [f"  - {s['name']}: {s['issue']}" for s in at_risk_students]
        )

    body = f"""Hi Coach {coach_name},

Here's the attendance summary for {cohort_name} ({program_name}) for {period}.

📊 Sessions Held: {total_sessions}

Student Attendance:
{stats_text}
{at_risk_text}

Keep up the great work!

— The SwimBuddz Team
"""

    # Build HTML stats table
    stats_rows = "".join(
        f"<tr>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0;'>{s['name']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: center;'>{s['present']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: center;'>{s['absent']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: center;'>{s['late']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: center; font-weight: 600; "
        f"color: {'#10b981' if s['rate'] >= 80 else '#f59e0b' if s['rate'] >= 60 else '#ef4444'};'>{s['rate']}%</td>"
        f"</tr>"
        for s in student_stats
    )

    table_html = (
        '<div style="margin: 20px 0; overflow-x: auto;">'
        '<table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden;">'
        "<tr>"
        '<th style="background: #0891b2; color: white; padding: 12px 10px; text-align: left;">Student</th>'
        '<th style="background: #0891b2; color: white; padding: 12px 10px; text-align: center;">Present</th>'
        '<th style="background: #0891b2; color: white; padding: 12px 10px; text-align: center;">Absent</th>'
        '<th style="background: #0891b2; color: white; padding: 12px 10px; text-align: center;">Late</th>'
        '<th style="background: #0891b2; color: white; padding: 12px 10px; text-align: center;">Rate</th>'
        "</tr>"
        f"{stats_rows}"
        "</table></div>"
    )

    at_risk_html = ""
    if at_risk_students:
        at_risk_items = "".join(
            f"<p style='margin: 5px 0;'><strong>{s['name']}:</strong> {s['issue']}</p>"
            for s in at_risk_students
        )
        at_risk_html = info_box(
            f"<h3 style='margin: 0 0 10px 0; color: #92400e;'>⚠️ Students Needing Attention</h3>{at_risk_items}",
            bg_color="#fef3c7",
            border_color="#f59e0b",
        )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        f"<p>Here's how your students attended this week ({total_sessions} sessions):</p>"
        + table_html
        + at_risk_html
        + sign_off("Keep up the great work! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="📊 Attendance Summary",
        subtitle=f"{cohort_name} \u2022 {period}",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Attendance summary for {cohort_name} - {period}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_coach_assignment_email(
    to_email: str,
    coach_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
    end_date: str,
    student_count: int,
    location: Optional[str] = None,
    dashboard_url: str = "https://swimbuddz.com/coach/dashboard",
) -> bool:
    """
    Send email to coach when they are assigned to a cohort.
    """
    subject = f"You've Been Assigned to {cohort_name} 🏊\u200d♂️"

    location_text = f"\nLocation: {location}" if location else ""

    body = f"""Hi Coach {coach_name},

You have been assigned as the coach for a new cohort!

Program: {program_name}
Cohort: {cohort_name}
Duration: {start_date} – {end_date}{location_text}
Students: {student_count} enrolled

What's Next:
- Review the program curriculum and milestones
- Check the session schedule for this cohort
- Reach out to your students to introduce yourself

Access your Coach Dashboard: {dashboard_url}

Let's make this cohort a success!

— The SwimBuddz Team
"""

    details = {
        "Program": program_name,
        "Cohort": cohort_name,
        "Duration": f"{start_date} \u2013 {end_date}",
        "Location": location or "",
        "Students": f"{student_count} enrolled",
    }

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        "<p>You have been assigned as the coach for a new cohort!</p>"
        + detail_box(details, accent_color="#10b981")
        + "<h3>What's Next:</h3>"
        "<ul>"
        "<li>Review the program curriculum and milestones</li>"
        "<li>Check the session schedule for this cohort</li>"
        "<li>Reach out to your students to introduce yourself</li>"
        "</ul>"
        + cta_button("Go to Coach Dashboard", dashboard_url, color="#10b981")
        + "<p>Let's make this cohort a success!</p>"
    )

    html_body = wrap_html(
        title="🏊\u200d♂️ New Cohort Assignment!",
        subtitle="You've been assigned as the coach",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"You've been assigned to {cohort_name}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_installment_payment_reminder_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    installment_number: int,
    total_installments: Optional[int],
    amount: int,
    currency: str,
    due_date: str,
    days_until: int,
    checkout_url: Optional[str] = None,
    insufficient_wallet: bool = False,
    dashboard_url: str = "https://swimbuddz.com/account/academy",
) -> bool:
    """
    Remind a student that an installment payment is due.

    Used for scheduled reminders (7, 3, 1 day before due date) and on due-date
    when the wallet auto-deduction failed because the balance was insufficient.

    ``amount`` is expected in kobo.
    """
    amount_ngn = amount / 100  # kobo -> NGN
    currency_symbol = "₦" if currency == "NGN" else currency
    amount_display = f"{currency_symbol}{amount_ngn:,.0f}"

    installment_label = (
        f"Installment {installment_number} of {total_installments}"
        if total_installments
        else f"Installment {installment_number}"
    )

    if days_until == 0 and insufficient_wallet:
        urgency_intro = (
            "Your installment payment is due today, but your SwimBuddz wallet doesn't have "
            "enough Bubbles. Please pay using the link below before the end of day to keep "
            "your access active."
        )
        subject = f"⚠️ Action Required: Academy Payment Due Today — {installment_label}"
    elif days_until == 1:
        urgency_intro = (
            "Your installment payment is due tomorrow. Make sure your wallet is topped up "
            "or use the payment link below to pay directly."
        )
        subject = f"Reminder: Academy Payment Due Tomorrow — {installment_label}"
    else:
        urgency_intro = (
            f"Your next installment is due in {days_until} days. You can pay early at any time — "
            "early payment is always welcome!"
        )
        subject = f"Payment Reminder: {installment_label} Due in {days_until} Days"

    details = {
        "Installment": installment_label,
        "Amount Due": amount_display,
        "Due Date": due_date,
        "Program": program_name,
        "Cohort": cohort_name,
    }

    alert_html = ""
    if days_until == 0 and insufficient_wallet:
        alert_html = info_box(
            "<strong>⚠️ Wallet balance insufficient</strong><br/>"
            "Your wallet did not have enough Bubbles to cover this installment automatically. "
            "Please pay using the button below.",
            bg_color="#fef3c7",
            border_color="#f59e0b",
        )

    cta_html = ""
    if checkout_url:
        cta_html = cta_button("Pay Now", checkout_url, color="#0891b2")
    else:
        cta_html = cta_button("Go to Academy Dashboard", dashboard_url, color="#0891b2")

    body = f"""Hi {member_name},

{urgency_intro}

{installment_label}
Amount: {amount_display}
Due: {due_date}

Program: {program_name} ({cohort_name})

Log in to your dashboard to pay: {dashboard_url}

— The SwimBuddz Team
"""

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>{urgency_intro}</p>"
        + alert_html
        + detail_box(details, accent_color="#0891b2")
        + cta_html
        + sign_off("See you in the water! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="💳 Installment Payment Due",
        subtitle=f"{program_name} — {installment_label}",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"Payment of {amount_display} due on {due_date}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_installment_payment_confirmation_email(
    to_email: str,
    member_name: str,
    installment_number: int,
    total_installments: Optional[int],
    amount: float,
    currency: str,
    payment_reference: str,
    paid_at: str,
    payment_method: str = "paystack",
    dashboard_url: str = "https://swimbuddz.com/account/academy",
) -> bool:
    """
    Confirm a successful installment payment to the student.

    Sent after every installment (except the first, which uses the enrollment
    confirmation email) — whether paid via Paystack or wallet auto-deduction.
    """
    currency_symbol = "₦" if currency == "NGN" else currency
    amount_display = f"{currency_symbol}{amount:,.0f}"

    installment_label = (
        f"Installment {installment_number} of {total_installments}"
        if total_installments
        else f"Installment {installment_number}"
    )

    method_display = (
        "SwimBuddz Wallet 🫧" if payment_method == "wallet" else "Card / Bank transfer"
    )

    subject = f"✅ Payment Received — {installment_label}"

    body = f"""Hi {member_name},

We've received your payment for {installment_label}. Your academy access remains active.

Amount: {amount_display}
Reference: {payment_reference}
Date: {paid_at}
Method: {method_display}

Log in to view your full payment schedule: {dashboard_url}

Thank you!

— The SwimBuddz Team
"""

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>We've received your payment. Your academy access remains <strong>active</strong>.</p>"
        + detail_box(
            {
                "Installment": installment_label,
                "Amount Paid": amount_display,
                "Payment Date": paid_at,
                "Reference": payment_reference,
                "Method": method_display,
            },
            accent_color="#10b981",
        )
        + cta_button("View Payment Schedule", dashboard_url, color="#10b981")
        + sign_off("Thank you for keeping up with your payments! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="✅ Payment Received",
        subtitle=f"{installment_label} — {amount_display}",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"Payment of {amount_display} confirmed for {installment_label}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_academy_access_suspended_email(
    to_email: str,
    member_name: str,
    installment_number: Optional[int],
    total_installments: Optional[int],
    amount: float,
    currency: str,
    payment_reference: str,
    enrollment_id: Optional[str],
    dashboard_url: str = "https://swimbuddz.com/account/academy",
) -> bool:
    """
    Notify the student that their academy access has been suspended due to a
    failed installment payment.

    The student can restore access by completing the overdue payment from their
    academy dashboard. A 24-hour grace window applies after each due date.
    """
    currency_symbol = "₦" if currency == "NGN" else currency
    amount_display = f"{currency_symbol}{amount:,.0f}"

    installment_label = (
        f"Installment {installment_number} of {total_installments}"
        if installment_number and total_installments
        else (
            f"Installment {installment_number}"
            if installment_number
            else "your installment"
        )
    )

    subject = "⚠️ Academy Access Suspended — Installment Payment Failed"

    body = f"""Hi {member_name},

Your academy access has been temporarily suspended because a payment could not be processed.

Installment: {installment_label}
Amount: {amount_display}
Reference: {payment_reference}

Your access will be restored as soon as you complete this payment. Please log in to your dashboard to pay.

{dashboard_url}

If you believe this is an error, please contact our support team.

— The SwimBuddz Team
"""

    alert_html = info_box(
        "<strong>Your access to academy sessions has been suspended.</strong><br/>"
        "Please complete your payment as soon as possible to restore access. "
        "You have a 24-hour grace window after each installment due date.",
        bg_color="#fef2f2",
        border_color="#ef4444",
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Unfortunately, we couldn't process your installment payment and your academy access has been <strong>temporarily suspended</strong>.</p>"
        + alert_html
        + detail_box(
            {
                "Installment": installment_label,
                "Amount Due": amount_display,
                "Failed Reference": payment_reference,
            },
            accent_color="#ef4444",
        )
        + cta_button("Pay Now to Restore Access", dashboard_url, color="#ef4444")
        + "<p>If you believe this is an error or need help, please reach out to our support team.</p>"
        + sign_off("We look forward to seeing you back in the water soon! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="⚠️ Access Suspended",
        subtitle="Installment payment failed",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader="Action required: Your academy access has been suspended",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_admin_dropout_pending_email(
    to_email: str,
    member_name: str,
    member_id: str,
    enrollment_id: str,
    program_name: str,
    cohort_name: str,
    missed_count: int,
    admin_dashboard_url: str = "https://swimbuddz.com/admin/academy/enrollments",
) -> bool:
    """
    Notify the admin team that a student has reached the dropout threshold
    (2 missed installments) and requires admin approval to be formally dropped.

    Only sent when cohort.admin_dropout_approval = True.
    """
    subject = f"⚠️ Dropout Approval Required — {member_name} ({cohort_name})"

    body = f"""Admin Action Required,

A student has reached the dropout threshold and requires your approval.

Student: {member_name}
Member ID: {member_id}
Enrollment ID: {enrollment_id}
Program: {program_name}
Cohort: {cohort_name}
Missed Installments: {missed_count}

The student's status is now DROPOUT_PENDING. Their academy access has been suspended automatically.

Please review this enrollment in the admin dashboard and either:
  - Approve Dropout: Formally drop the student.
  - Reverse Dropout: Reinstate access and allow the student to catch up.

Admin Dashboard: {admin_dashboard_url}

— The SwimBuddz System
"""

    alert_html = info_box(
        f"<strong>Student:</strong> {member_name}<br/>"
        f"<strong>Enrollment:</strong> {enrollment_id}<br/>"
        f"<strong>Missed Installments:</strong> {missed_count}<br/>"
        f"<strong>Status:</strong> DROPOUT_PENDING — awaiting your action",
        bg_color="#fef3c7",
        border_color="#f59e0b",
    )

    body_html = (
        "<p>An enrollment has reached the dropout threshold and needs your review.</p>"
        + detail_box(
            {
                "Student": member_name,
                "Member ID": member_id,
                "Program": program_name,
                "Cohort": cohort_name,
                "Missed Installments": str(missed_count),
                "Status": "DROPOUT_PENDING",
            },
            accent_color="#f59e0b",
        )
        + alert_html
        + "<p>Please review and take action in the admin dashboard:</p>"
        + cta_button("Review Enrollment", admin_dashboard_url, color="#f59e0b")
    )

    html_body = wrap_html(
        title="⚠️ Dropout Approval Required",
        subtitle=f"{member_name} — {cohort_name}",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"Action required: {member_name} has reached dropout threshold",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_low_attendance_alert_email(
    to_email: str,
    coach_name: str,
    student_name: str,
    cohort_name: str,
    issue: str,  # e.g., "Missed 3 consecutive sessions"
    attendance_rate: int,
    suggestions: list,  # ["Schedule a check-in call", "Offer makeup session"]
) -> bool:
    """
    Send alert to coach about a student with low attendance.
    """
    subject = f"⚠️ Attendance Alert: {student_name} - {cohort_name}"

    suggestions_text = "\n".join([f"  • {s}" for s in suggestions])

    body = f"""Hi Coach {coach_name},

This is an attendance alert for {student_name} in {cohort_name}.

Issue: {issue}
Current Attendance Rate: {attendance_rate}%

Suggested Actions:
{suggestions_text}

Early intervention can help keep students engaged and on track!

— The SwimBuddz Team
"""

    suggestions_html = "".join([f"<li>{s}</li>" for s in suggestions])

    alert_html = info_box(
        f"<p style='margin: 0;'><strong>Issue:</strong> {issue}</p>"
        f"<p style='margin: 10px 0 0 0;'><strong>Attendance Rate:</strong> {attendance_rate}%</p>",
        bg_color="#fef3c7",
        border_color="#f59e0b",
    )

    body_html = (
        f"<p>Hi Coach {coach_name},</p>"
        + alert_html
        + '<div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">'
        '<h3 style="margin: 0 0 10px 0; color: #1e293b;">💡 Suggested Actions</h3>'
        f'<ul style="margin: 0; padding-left: 20px;">{suggestions_html}</ul></div>'
        + "<p>Early intervention can help keep students engaged and on track!</p>"
    )

    html_body = wrap_html(
        title="⚠️ Attendance Alert",
        subtitle=f"{student_name} \u2022 {cohort_name}",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"Attendance alert for {student_name} in {cohort_name}",
    )

    return await send_email(to_email, subject, body, html_body)
