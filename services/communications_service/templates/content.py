"""Article/content email templates."""

from html import escape

from libs.common.config import get_settings
from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    cta_button,
    sign_off,
    wrap_html,
)

settings = get_settings()


def _category_label(category: str | None) -> str:
    if not category:
        return "Article"
    return category.replace("_", " ").title()


async def send_content_post_published_email(
    *,
    to_email: str,
    member_name: str,
    post_id: str,
    title: str,
    summary: str | None = None,
    category: str | None = None,
) -> bool:
    """Send an email when a content post is published."""
    category_label = _category_label(category)
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    article_url = f"{frontend_url}/community/tips/{post_id}"
    subject = f"New SwimBuddz article: {title}"

    summary_text = summary.strip() if summary else "A new SwimBuddz article is ready."
    body = f"""Hi {member_name},

We published a new {category_label.lower()} article:

{title}

{summary_text}

Read it here:
{article_url}

— The SwimBuddz Team
"""

    safe_title = escape(title)
    safe_summary = escape(summary_text)
    safe_category = escape(category_label)
    body_html = (
        f"<p>Hi {escape(member_name)},</p>"
        f"<p>We published a new <strong>{safe_category}</strong> article.</p>"
        f"<h3>{safe_title}</h3>"
        f"<p>{safe_summary}</p>"
        + cta_button("Read Article", article_url)
        + sign_off("Keep building your swim confidence.")
    )

    html_body = wrap_html(
        title="New SwimBuddz Article",
        subtitle=title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=summary_text,
    )

    return await send_email(
        to_email,
        subject,
        body,
        html_body,
        raise_on_unknown=True,
    )
