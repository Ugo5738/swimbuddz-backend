"""
Shared branded email base template for SwimBuddz.

All email templates should use `wrap_html()` to ensure consistent
branding across every outgoing email. This module provides:

- A professional branded layout with SwimBuddz logo header
- Consistent typography, spacing, and color palette
- Responsive design that works on mobile email clients
- Shared footer with company info and social links
- Helper functions for common email UI patterns (detail boxes, CTAs, etc.)

Color palette by email category:
- Academy / General:  cyan    #0891b2 → #0284c7
- Success / Coaching:  green   #10b981 → #059669
- Alerts / Warnings:   amber   #f59e0b → #d97706
- Progress / Shadow:   purple  #8b5cf6 → #7c3aed

Usage:
    from services.communications_service.templates.base import wrap_html, detail_box, cta_button

    html = wrap_html(
        title="Welcome!",
        subtitle="Your enrollment is confirmed",
        body_html="<p>Hi John, ...</p>" + detail_box({...}) + cta_button(...),
        header_gradient="linear-gradient(135deg, #0891b2 0%, #0284c7 100%)",
    )
"""

# ─── Color presets ────────────────────────────────────────────────────
GRADIENT_CYAN = "linear-gradient(135deg, #0891b2 0%, #0284c7 100%)"
GRADIENT_GREEN = "linear-gradient(135deg, #10b981 0%, #059669 100%)"
GRADIENT_AMBER = "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)"
GRADIENT_PURPLE = "linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)"


def wrap_html(
    title: str,
    body_html: str,
    subtitle: str = "",
    header_gradient: str = GRADIENT_CYAN,
    preheader: str = "",
) -> str:
    """Wrap inner content in the branded SwimBuddz email layout.

    Args:
        title: Bold heading shown in the coloured header banner.
        body_html: The main email content (already-formatted HTML).
        subtitle: Smaller text below the title in the header.
        header_gradient: CSS gradient for the header background.
        preheader: Hidden preview text shown in inbox list view.
    """
    subtitle_html = (
        f'<p style="margin: 8px 0 0 0; opacity: 0.9; font-size: 15px;">{subtitle}</p>'
        if subtitle
        else ""
    )
    preheader_html = (
        f'<span style="display:none;font-size:1px;color:#f8fafc;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</span>'
        if preheader
        else ""
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <title>{title}</title>
    <style>
        /* Reset */
        body, table, td, p, a, li {{
            -webkit-text-size-adjust: 100%;
            -ms-text-size-adjust: 100%;
        }}
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.65;
            color: #334155;
            background-color: #f1f5f9;
        }}
        img {{
            border: 0;
            outline: none;
            text-decoration: none;
        }}

        /* Layout */
        .email-wrapper {{
            width: 100%;
            background-color: #f1f5f9;
            padding: 40px 16px;
        }}
        .email-container {{
            max-width: 600px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05);
        }}

        /* Header */
        .email-header {{
            background: {header_gradient};
            padding: 36px 32px 28px;
            text-align: left;
        }}
        .email-header .logo {{
            margin-bottom: 20px;
        }}
        .email-header .logo img {{
            height: 36px;
            width: auto;
        }}
        .email-header h1 {{
            margin: 0;
            font-size: 24px;
            font-weight: 700;
            color: #ffffff;
            line-height: 1.3;
        }}

        /* Body */
        .email-body {{
            padding: 32px;
        }}
        .email-body p {{
            margin: 0 0 16px;
            font-size: 15px;
            color: #334155;
        }}
        .email-body h3 {{
            margin: 24px 0 12px;
            font-size: 16px;
            font-weight: 600;
            color: #1e293b;
        }}
        .email-body ul, .email-body ol {{
            margin: 0 0 16px;
            padding-left: 24px;
        }}
        .email-body li {{
            margin-bottom: 8px;
            font-size: 15px;
            color: #334155;
        }}

        /* Detail box */
        .detail-box {{
            background: #f8fafc;
            border-left: 4px solid #0891b2;
            border-radius: 0 8px 8px 0;
            padding: 20px 24px;
            margin: 20px 0;
        }}
        .detail-row {{
            margin: 10px 0;
            font-size: 14px;
        }}
        .detail-label {{
            color: #64748b;
            display: inline;
        }}
        .detail-value {{
            font-weight: 600;
            color: #1e293b;
            display: inline;
        }}

        /* CTA Button */
        .cta-wrapper {{
            text-align: center;
            margin: 28px 0;
        }}
        .cta-button {{
            display: inline-block;
            padding: 14px 32px;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            color: #ffffff !important;
            text-decoration: none;
            transition: opacity 0.2s;
        }}

        /* Divider */
        .divider {{
            height: 1px;
            background: #e2e8f0;
            margin: 28px 0;
            border: none;
        }}

        /* Footer */
        .email-footer {{
            background: #f8fafc;
            padding: 24px 32px;
            text-align: center;
            border-top: 1px solid #e2e8f0;
        }}
        .email-footer p {{
            margin: 4px 0;
            font-size: 13px;
            color: #94a3b8;
        }}
        .email-footer a {{
            color: #0891b2;
            text-decoration: none;
        }}

        /* Mobile */
        @media only screen and (max-width: 640px) {{
            .email-wrapper {{
                padding: 16px 8px;
            }}
            .email-header {{
                padding: 28px 20px 22px;
            }}
            .email-header h1 {{
                font-size: 20px;
            }}
            .email-body {{
                padding: 24px 20px;
            }}
            .email-footer {{
                padding: 20px;
            }}
        }}
    </style>
</head>
<body>
    {preheader_html}
    <div class="email-wrapper">
        <div class="email-container">
            <!-- Header -->
            <div class="email-header">
                <div class="logo">
                    <img src="https://swimbuddz.com/logo-white.png" alt="SwimBuddz" style="height: 36px;" />
                </div>
                <h1>{title}</h1>
                {subtitle_html}
            </div>

            <!-- Body -->
            <div class="email-body">
                {body_html}
            </div>

            <!-- Footer -->
            <div class="email-footer">
                <p style="margin-bottom: 8px;"><strong style="color: #64748b;">SwimBuddz</strong></p>
                <p>House 505, Amuwo-Odofin, Lagos State, Nigeria</p>
                <p>
                    <a href="https://swimbuddz.com">swimbuddz.com</a>
                    &nbsp;&middot;&nbsp;
                    <a href="mailto:hello@swimbuddz.com">hello@swimbuddz.com</a>
                </p>
                <p style="margin-top: 12px; font-size: 12px; color: #cbd5e1;">
                    &copy; 2026 SwimBuddz Limited. All rights reserved.
                </p>
            </div>
        </div>
    </div>
</body>
</html>"""


# ─── Helper functions ─────────────────────────────────────────────────

def detail_box(
    items: dict[str, str],
    accent_color: str = "#0891b2",
) -> str:
    """Render a key-value detail box (e.g. session info, order info).

    Args:
        items: Ordered dict of label→value pairs.
        accent_color: Left-border accent colour.
    """
    rows = "\n".join(
        f'<div class="detail-row"><span class="detail-label">{label}:</span> '
        f'<span class="detail-value">{value}</span></div>'
        for label, value in items.items()
        if value  # skip empty values
    )
    return (
        f'<div class="detail-box" style="border-left-color: {accent_color};">'
        f"{rows}</div>"
    )


def cta_button(
    label: str,
    url: str,
    color: str = "#0891b2",
) -> str:
    """Render a centered call-to-action button.

    Args:
        label: Button text.
        url: Link destination.
        color: Background colour of the button.
    """
    return (
        f'<div class="cta-wrapper">'
        f'<a href="{url}" class="cta-button" style="background-color: {color};">'
        f"{label}</a></div>"
    )


def info_box(
    content: str,
    bg_color: str = "#f0fdf4",
    border_color: str = "#22c55e",
    title: str = "",
) -> str:
    """Render a coloured info/tip box.

    Args:
        content: Inner HTML content.
        bg_color: Box background colour.
        border_color: Left border colour.
        title: Optional bold title line.
    """
    title_html = f"<strong>{title}</strong><br/>" if title else ""
    return (
        f'<div style="background: {bg_color}; border-left: 4px solid {border_color}; '
        f'padding: 16px 20px; border-radius: 0 8px 8px 0; margin: 20px 0;">'
        f"{title_html}{content}</div>"
    )


def checklist_box(
    title: str,
    items: list[str],
    bg_color: str = "#fefce8",
    title_color: str = "#854d0e",
    text_color: str = "#713f12",
) -> str:
    """Render a checklist box (e.g. what to bring).

    Args:
        title: Box heading.
        items: List of checklist items.
        bg_color: Background colour.
        title_color: Heading colour.
        text_color: Item text colour.
    """
    items_html = "".join(f"<li>{item}</li>" for item in items)
    return (
        f'<div style="background: {bg_color}; padding: 20px; border-radius: 8px; margin: 20px 0;">'
        f'<h4 style="margin: 0 0 10px 0; color: {title_color};">{title}</h4>'
        f'<ul style="margin: 0; padding-left: 20px; color: {text_color};">{items_html}</ul>'
        f"</div>"
    )


def sign_off(extra_message: str = "") -> str:
    """Render the standard sign-off line.

    Args:
        extra_message: Optional message before the sign-off (e.g. "See you in the water!").
    """
    parts = []
    if extra_message:
        parts.append(f"<p>{extra_message}</p>")
    return "\n".join(parts)
