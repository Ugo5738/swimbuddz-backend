"""Article/content email templates."""

import json
import math
import re
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
WORDS_PER_MINUTE = 225
WORD_RE = re.compile(r"\b[\w]+(?:[\u2019'-][\w]+)*\b", re.UNICODE)


def _category_label(category: str | None) -> str:
    if not category:
        return "Article"
    return category.replace("_", " ").title()


def _blocknote_text(value) -> list[str]:
    """Extract reader-visible text from a decoded BlockNote document."""
    if isinstance(value, list):
        text: list[str] = []
        for item in value:
            text.extend(_blocknote_text(item))
        return text
    if not isinstance(value, dict):
        return []

    text = []
    node_text = value.get("text")
    if isinstance(node_text, str):
        text.append(node_text)
    for key in ("content", "children"):
        text.extend(_blocknote_text(value.get(key)))
    return text


def estimate_article_reading_time(body: str | None) -> int:
    """Estimate reading minutes at 225 wpm for BlockNote JSON or plain text."""
    raw = (body or "").strip()
    if not raw:
        return 1

    visible_text = raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        decoded = None
    if decoded is not None:
        extracted = _blocknote_text(decoded)
        if extracted:
            visible_text = " ".join(extracted)

    # The fallback may be Markdown or HTML. Removing markup and URLs keeps the
    # estimate tied to words a recipient will actually read.
    visible_text = re.sub(r"<[^>]+>", " ", visible_text)
    visible_text = re.sub(r"https?://\S+", " ", visible_text)
    visible_text = re.sub(r"[#*_>`~\[\](){}|]", " ", visible_text)
    word_count = len(WORD_RE.findall(visible_text))
    return max(1, math.ceil(word_count / WORDS_PER_MINUTE))


async def send_content_post_published_email(
    *,
    to_email: str,
    member_name: str,
    post_id: str,
    title: str,
    summary: str | None = None,
    category: str | None = None,
    featured_image_url: str | None = None,
    reading_time_minutes: int = 1,
    tier_access: str = "community",
) -> bool:
    """Send an email when a content post is published."""
    category_label = _category_label(category)
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    article_path = "/tips" if tier_access == "community" else "/community/tips"
    article_url = f"{frontend_url}{article_path}/{post_id}"
    subject = f"New SwimBuddz article: {title}"
    reading_time = max(1, int(reading_time_minutes))

    summary_text = summary.strip() if summary else "A new SwimBuddz article is ready."
    body = f"""Hi {member_name},

We published a new {category_label.lower()} article:

{title}

{summary_text}

Estimated reading time: {reading_time} min

Read it here:
{article_url}

— The SwimBuddz Team
"""

    safe_title = escape(title)
    safe_summary = escape(summary_text)
    safe_category = escape(category_label)
    safe_image_alt = escape(f"{title} featured image", quote=True)
    image_html = (
        f'<img src="{escape(featured_image_url, quote=True)}" '
        f'alt="{safe_image_alt}" '
        'width="536" style="display:block;width:100%;max-width:536px;'
        'max-height:320px;object-fit:cover;border-radius:6px;margin:16px 0 20px;"/>'
        if featured_image_url
        else ""
    )
    body_html = (
        f"<p>Hi {escape(member_name)},</p>"
        f"<p>We published a new <strong>{safe_category}</strong> article.</p>"
        f"{image_html}"
        f"<h3>{safe_title}</h3>"
        f'<p style="color:#475569;font-size:14px;"><strong>{reading_time} min read</strong></p>'
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
