"""Email templates for the 3-step corporate outreach sequence.

Source of truth: docs/marketing/CORPORATE_WELLNESS.md (§ Outreach sequence).
Templates here intentionally mirror the playbook copy — when the doc
changes, update both. The render functions return ``(subject, plain, html)``
ready to hand to libs.common.emails.client.EmailClient.send().

We keep templates as Python (not Jinja) on purpose: tiny copy, simple
substitution, no need for the template engine surface area, and the
playbook copy stays diffable.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape as _h
from typing import Literal

from services.corporate_service.models.enums import TouchpointType

EmailNumber = Literal[1, 2, 3]


@dataclass(frozen=True)
class OutreachEmail:
    """Rendered outreach email ready to send + the touchpoint type to log."""

    subject: str
    plain: str
    html: str
    touchpoint_type: TouchpointType


def _signature_plain() -> str:
    return "\n\nThanks,\nDaniel\nSwimBuddz | swimbuddz.com"


def _signature_html() -> str:
    return (
        "<p>Thanks,<br>Daniel<br>SwimBuddz | "
        '<a href="https://swimbuddz.com">swimbuddz.com</a></p>'
    )


def _greeting(contact_name: str) -> str:
    """First-name greeting if we have a name to split."""
    first = (contact_name or "").strip().split()[0] if contact_name else ""
    return f"Hi {first}," if first else "Hello,"


def _greeting_html(contact_name: str) -> str:
    return f"<p>{_greeting(contact_name)}</p>"


def render_email_1(*, contact_name: str, company_name: str) -> OutreachEmail:
    """Day 1 — Intro email. From playbook § Email 1 — Intro."""
    subject = f"Adult swim wellness for {company_name} employees — quick intro"
    body = (
        f"{_greeting(contact_name)}\n\n"
        "I run SwimBuddz, a swimming community building structured 12-week "
        "adult swim programs. We're seeing strong demand from working "
        "professionals — most of whom never learned, or learned badly as "
        "kids and gave up.\n\n"
        f"I think this could be a useful addition to {company_name}'s "
        "wellness offering. Quick reasons why:\n\n"
        "  - Most wellness benefits go unused after Q1; structured cohorts "
        "have 75%+ completion\n"
        "  - Lower-injury alternative to gym membership\n"
        "  - Lifelong skill — measurable, photogenic outcomes\n"
        "  - Group format builds team cohesion\n\n"
        "You can read the full pitch and pricing at "
        "https://swimbuddz.com/corporate.\n\n"
        "Worth a 20-min call to discuss?"
        f"{_signature_plain()}"
    )
    html = (
        f"{_greeting_html(contact_name)}"
        "<p>I run SwimBuddz, a swimming community building structured "
        "12-week adult swim programs. We're seeing strong demand from "
        "working professionals — most of whom never learned, or learned "
        "badly as kids and gave up.</p>"
        f"<p>I think this could be a useful addition to "
        f"<strong>{_h(company_name)}</strong>'s wellness offering. Quick "
        "reasons why:</p>"
        "<ul>"
        "<li>Most wellness benefits go unused after Q1; structured cohorts "
        "have 75%+ completion</li>"
        "<li>Lower-injury alternative to gym membership</li>"
        "<li>Lifelong skill — measurable, photogenic outcomes</li>"
        "<li>Group format builds team cohesion</li>"
        "</ul>"
        "<p>You can read the full pitch and pricing at "
        '<a href="https://swimbuddz.com/corporate">swimbuddz.com/corporate</a>.</p>'
        "<p>Worth a 20-min call to discuss?</p>"
        f"{_signature_html()}"
    )
    return OutreachEmail(
        subject=subject,
        plain=body,
        html=html,
        touchpoint_type=TouchpointType.EMAIL_INTRO,
    )


def render_email_2(*, contact_name: str, company_name: str) -> OutreachEmail:
    """Day 7 — First follow-up. From playbook § Email 2 — Follow-up."""
    subject = f"Re: Adult swim wellness for {company_name}"
    body = (
        f"{_greeting(contact_name)}\n\n"
        "Bumping this up in case it got buried. Happy to send the "
        "curriculum overview if that's more useful than a call."
        f"{_signature_plain()}"
    )
    html = (
        f"{_greeting_html(contact_name)}"
        "<p>Bumping this up in case it got buried. Happy to send the "
        "curriculum overview if that's more useful than a call.</p>"
        f"{_signature_html()}"
    )
    return OutreachEmail(
        subject=subject,
        plain=body,
        html=html,
        touchpoint_type=TouchpointType.EMAIL_FOLLOWUP_1,
    )


def render_email_3(*, contact_name: str, company_name: str) -> OutreachEmail:
    """Day 14 — Final follow-up. From playbook § Email 3 — Final."""
    subject = f"Re: Adult swim wellness for {company_name}"
    body = (
        f"{_greeting(contact_name)}\n\n"
        "Last note from me — totally fine if it's not the right time. "
        "If anyone else on your team is the better contact for wellness "
        "benefits, I'd appreciate the redirect."
        f"{_signature_plain()}"
    )
    html = (
        f"{_greeting_html(contact_name)}"
        "<p>Last note from me — totally fine if it's not the right "
        "time. If anyone else on your team is the better contact for "
        "wellness benefits, I'd appreciate the redirect.</p>"
        f"{_signature_html()}"
    )
    return OutreachEmail(
        subject=subject,
        plain=body,
        html=html,
        touchpoint_type=TouchpointType.EMAIL_FOLLOWUP_2,
    )


def render_email(
    number: EmailNumber, *, contact_name: str, company_name: str
) -> OutreachEmail:
    """Pick and render the Nth email in the sequence."""
    if number == 1:
        return render_email_1(contact_name=contact_name, company_name=company_name)
    if number == 2:
        return render_email_2(contact_name=contact_name, company_name=company_name)
    if number == 3:
        return render_email_3(contact_name=contact_name, company_name=company_name)
    raise ValueError(f"No outreach email #{number}")


# ── Sequence map: position in the funnel → outreach email number ─────────
#
# Used by the scheduler to decide "they just got email N — schedule N+1 in
# the corresponding gap." The playbook gap is 7 days between each step.

OUTREACH_TYPES_IN_ORDER: tuple[TouchpointType, ...] = (
    TouchpointType.EMAIL_INTRO,
    TouchpointType.EMAIL_FOLLOWUP_1,
    TouchpointType.EMAIL_FOLLOWUP_2,
)


def next_email_number(
    last_touchpoint_type: TouchpointType | None,
) -> EmailNumber | None:
    """Given the most recent outreach touchpoint type for a contact, return
    the next email number (1, 2, or 3) — or None if the sequence is done."""
    if last_touchpoint_type is None:
        return 1
    if last_touchpoint_type == TouchpointType.EMAIL_INTRO:
        return 2
    if last_touchpoint_type == TouchpointType.EMAIL_FOLLOWUP_1:
        return 3
    return None  # EMAIL_FOLLOWUP_2 or non-outreach — sequence over
