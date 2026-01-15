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
        subject = f"URGENT: Your swimming course starts tomorrow! 🏊‍♂️"

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
