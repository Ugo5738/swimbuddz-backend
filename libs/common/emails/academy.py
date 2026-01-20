"""
Academy-related email templates.
"""

from typing import Optional

from libs.common.emails.core import send_email


async def send_enrollment_confirmation_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
) -> bool:
    """
    Send enrollment confirmation email to a member.
    """
    subject = f"Welcome to {program_name}! Your enrollment is confirmed."

    body = f"""Hi {member_name},

Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date}

What's Next:
- Sessions will appear in your Sessions page once they're scheduled
- Make sure your profile is complete with emergency contact information
- Review the program curriculum on your Academy dashboard

If you have any questions, please reach out to our team.

See you in the water!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Welcome to {program_name}!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your enrollment has been confirmed</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.</p>
            
            <div class="details">
                <p><span class="label">Program:</span> <span class="value">{program_name}</span></p>
                <p><span class="label">Cohort:</span> <span class="value">{cohort_name}</span></p>
                <p><span class="label">Start Date:</span> <span class="value">{start_date}</span></p>
            </div>
            
            <h3>What's Next:</h3>
            <ul>
                <li>Sessions will appear in your Sessions page once they're scheduled</li>
                <li>Make sure your profile is complete with emergency contact information</li>
                <li>Review the program curriculum on your Academy dashboard</li>
            </ul>
            
            <p>If you have any questions, please reach out to our team.</p>
            
            <p>See you in the water! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

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
    subject = f"Reminder: Your swimming course starts in {days_text}! 🏊‍♂️"

    if days_until == 1:
        subject = "URGENT: Your swimming course starts tomorrow! 🏊‍♂️"

    urgency_msg = f"We're excited to see you in {days_text}!"
    if days_until == 1:
        urgency_msg = (
            "We're excited to see you tomorrow! Please double check your gear."
        )

    checklist_html = """
        <div class="checklist">
            <h4>🎒 Checklist</h4>
            <ul>
                <li>Swimwear and cap</li>
                <li>Goggles</li>
                <li>Towel and flip flops</li>
                <li>Water bottle</li>
            </ul>
        </div>
    """

    # Custom message based on timing
    tip_html = ""
    if days_until >= 7:
        tip_html = """
            <div class="session-box" style="border-left-color: #f59e0b;">
                <strong>💡 Pro Tip:</strong><br/>
                Now is a great time to try on your swimwear and make sure everything fits comfortably!
            </div>
        """
    elif days_until <= 3:
        tip_html = """
            <div class="session-box" style="border-left-color: #f59e0b;">
                <strong>💡 Preparation:</strong><br/>
                Hydrate well before your session and arrive 15 minutes early to change.
            </div>
        """

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

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .checklist {{ background: #fefce8; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .checklist h4 {{ margin: 0 0 10px 0; color: #854d0e; }}
        .checklist ul {{ margin: 0; padding-left: 20px; color: #713f12; }}
        .checklist li {{ margin: 5px 0; }}
        .session-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Getting Ready?</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your course starts in {days_text}</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>{urgency_msg}</p>
            
            <div class="details">
                <p><span class="label">Program:</span> <span class="value">{program_name}</span></p>
                <p><span class="label">Cohort:</span> <span class="value">{cohort_name}</span></p>
                <p><span class="label">When:</span> <span class="value">{start_date} at {start_time}</span></p>
                <p><span class="label">Where:</span> <span class="value">{location}</span></p>
            </div>

            {tip_html}
            
            {checklist_html}
            
            <p>See you in the water! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

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

<a href="{dashboard_url}">Go to Dashboard</a>

If you no longer wish to join, please decline the spot so we can offer it to the next person on the list.

See you in the water!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .cta-button {{ display: inline-block; background: #10b981; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🎉 You're In!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">A spot opened up for {program_name}</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Good news! A spot has opened up for you in the <strong>{program_name}</strong> ({cohort_name}) cohort.</p>
            
            <p>You have been moved off the waitlist and your status is now <strong>Pending Approval</strong>.</p>
            
            <p>Please log in to your dashboard to confirm your enrollment and complete payment within the next 24 hours to secure your spot.</p>
            
            <div style="text-align: center;">
                <a href="{dashboard_url}" class="cta-button">Secure My Spot</a>
            </div>
            
            <p style="margin-top: 30px; font-size: 14px; color: #64748b;">
                If you no longer wish to join, please decline the spot so we can offer it to the next person on the list.
            </p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

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
    achievements_html = ""
    if recent_achievements:
        achievements_text = "\n".join([f"  ✓ {a['name']}" for a in recent_achievements])
        achievements_html = "".join(
            [f"<li>✓ {a['name']}</li>" for a in recent_achievements]
        )
    else:
        achievements_text = "  No new milestones this period."
        achievements_html = "<li>No new milestones this period.</li>"

    # Build feedback section
    feedback_text = ""
    feedback_html = ""
    if coach_feedback:
        feedback_text = "\n".join(
            [f"  • {f['milestone']}: {f['notes']}" for f in coach_feedback]
        )
        feedback_html = "".join(
            [
                f"<div class='feedback-item'><strong>{f['milestone']}:</strong> {f['notes']}</div>"
                for f in coach_feedback
            ]
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

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .progress-bar {{ background: #e2e8f0; border-radius: 8px; height: 24px; overflow: hidden; margin: 15px 0; }}
        .progress-fill {{ background: linear-gradient(90deg, #10b981, #059669); height: 100%; border-radius: 8px; }}
        .stat-box {{ background: white; padding: 20px; border-radius: 8px; text-align: center; margin: 10px 0; }}
        .stat-number {{ font-size: 32px; font-weight: 700; color: #1e293b; }}
        .stat-label {{ color: #64748b; font-size: 14px; }}
        .achievements {{ background: #ecfdf5; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .achievements ul {{ margin: 10px 0 0 0; padding-left: 20px; }}
        .feedback-item {{ background: white; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #8b5cf6; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">📊 Progress Report</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">{program_name} • {cohort_name}</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Here's your progress update!</p>
            
            <div class="stat-box">
                <div class="stat-number">{completion_pct}%</div>
                <div class="stat-label">{milestones_completed} of {total_milestones} milestones completed</div>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: {completion_pct}%;"></div>
                </div>
            </div>
            
            <div class="achievements">
                <h3 style="margin: 0; color: #065f46;">🏆 Recent Achievements</h3>
                <ul>{achievements_html}</ul>
            </div>
            
            {"<h3>💬 Coach Feedback</h3>" + feedback_html if coach_feedback else ""}
            
            <p>Keep up the great work! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    # Note: PDF attachment handling would be done in send_email if extended
    # For now, we just send the HTML email
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

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .certificate-box {{ background: white; padding: 30px; border-radius: 12px; border: 2px dashed #f59e0b; text-align: center; margin: 20px 0; }}
        .cta-button {{ display: inline-block; background: #f59e0b; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; font-size: 28px;">🎓 Congratulations!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">You've earned your certificate!</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>We're thrilled to announce that you have successfully completed all requirements for <strong>{program_name}</strong>!</p>
            
            <div class="certificate-box">
                <h2 style="margin: 0 0 10px 0; color: #d97706;">🏆 Certificate Ready!</h2>
                <p style="margin: 5px 0;">Completion Date: <strong>{completion_date}</strong></p>
                <p style="margin: 5px 0; font-size: 12px; color: #64748b;">Verification: {verification_code}</p>
                <a href="{dashboard_url}" class="cta-button">Download Certificate</a>
            </div>
            
            <p>We're so proud of your achievement! This is a significant milestone in your swimming journey.</p>
            
            <p>Keep swimming! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

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
        [
            f"""<tr>
            <td style="padding: 8px; border-bottom: 1px solid #e2e8f0;">{s['name']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; text-align: center;">{s['present']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; text-align: center;">{s['absent']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; text-align: center;">{s['late']}</td>
            <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; text-align: center; font-weight: 600; color: {'#10b981' if s['rate'] >= 80 else '#f59e0b' if s['rate'] >= 60 else '#ef4444'};">{s['rate']}%</td>
        </tr>"""
            for s in student_stats
        ]
    )

    at_risk_html = ""
    if at_risk_students:
        at_risk_html = f"""
        <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; border-radius: 0 8px 8px 0; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #92400e;">⚠️ Students Needing Attention</h3>
            {"".join([f"<p style='margin: 5px 0;'><strong>{s['name']}:</strong> {s['issue']}</p>" for s in at_risk_students])}
        </div>
        """

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
        th {{ background: #0891b2; color: white; padding: 12px 8px; text-align: left; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">📊 Attendance Summary</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">{cohort_name} • {period}</p>
        </div>
        <div class="content">
            <p>Hi Coach {coach_name},</p>
            <p>Here's how your students attended this week ({total_sessions} sessions):</p>
            
            <table>
                <tr>
                    <th>Student</th>
                    <th style="text-align: center;">Present</th>
                    <th style="text-align: center;">Absent</th>
                    <th style="text-align: center;">Late</th>
                    <th style="text-align: center;">Rate</th>
                </tr>
                {stats_rows}
            </table>
            
            {at_risk_html}
            
            <p>Keep up the great work! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

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

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .alert-box {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 20px; border-radius: 0 8px 8px 0; margin: 20px 0; }}
        .suggestions {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">⚠️ Attendance Alert</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">{student_name} • {cohort_name}</p>
        </div>
        <div class="content">
            <p>Hi Coach {coach_name},</p>
            
            <div class="alert-box">
                <p style="margin: 0;"><strong>Issue:</strong> {issue}</p>
                <p style="margin: 10px 0 0 0;"><strong>Attendance Rate:</strong> {attendance_rate}%</p>
            </div>
            
            <div class="suggestions">
                <h3 style="margin: 0 0 10px 0; color: #1e293b;">💡 Suggested Actions</h3>
                <ul style="margin: 0; padding-left: 20px;">{suggestions_html}</ul>
            </div>
            
            <p>Early intervention can help keep students engaged and on track!</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)
