"""Public Stroke Lab analyzer email templates (guest ready / failed notices).

Guests have no member profile, so these take just an email + a link. They use
the shared branded layout (wrap_html) like every other transactional template.
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    cta_button,
    sign_off,
    wrap_html,
)


def _provider_usage_text(provider_usage: dict | None) -> str:
    if not provider_usage:
        return ""
    run = provider_usage.get("run") or {}
    quota = provider_usage.get("gemini_quota") or {}
    lines = [
        "AI usage visibility:",
        f"- Calls: {run.get('calls', 0)}",
        f"- Tokens: {run.get('total_tokens', 0)} total "
        f"({run.get('input_tokens', 0)} in, {run.get('output_tokens', 0)} out)",
        f"- Estimated cost: ${float(run.get('cost_usd') or 0):.6f}",
    ]
    models = ", ".join(run.get("models") or [])
    if models:
        lines.append(f"- Models: {models}")
    if run.get("retry_count"):
        lines.append(
            f"- Provider retries: {run.get('retry_count')} "
            f"({', '.join(run.get('retry_reasons') or ['provider retry'])})"
        )
    remaining = []
    for key, label in (
        ("rpm_remaining", "requests/min"),
        ("tpm_remaining", "tokens/min"),
        ("rpd_remaining", "requests/day"),
    ):
        if quota.get(key) is not None:
            remaining.append(f"{quota[key]} {label}")
    if remaining:
        lines.append("- Estimated remaining: " + ", ".join(remaining))
    else:
        lines.append("- Estimated remaining: quota ceilings not configured")
    return "\n" + "\n".join(lines) + "\n"


def _provider_usage_html(provider_usage: dict | None) -> str:
    if not provider_usage:
        return ""
    run = provider_usage.get("run") or {}
    quota = provider_usage.get("gemini_quota") or {}
    models = ", ".join(run.get("models") or []) or "n/a"
    remaining = []
    for key, label in (
        ("rpm_remaining", "requests/min"),
        ("tpm_remaining", "tokens/min"),
        ("rpd_remaining", "requests/day"),
    ):
        if quota.get(key) is not None:
            remaining.append(f"{quota[key]} {label}")
    remaining_text = (
        ", ".join(remaining) if remaining else "quota ceilings not configured"
    )
    retry_text = (
        f"{run.get('retry_count')} ({', '.join(run.get('retry_reasons') or ['provider retry'])})"
        if run.get("retry_count")
        else "0"
    )
    return (
        "<div style='margin:18px 0;padding:14px;border:1px solid #e2e8f0;"
        "border-radius:12px;background:#f8fafc;color:#334155;font-size:13px'>"
        "<p style='margin:0 0 8px;font-weight:700;color:#0f172a'>AI usage visibility</p>"
        f"<p style='margin:4px 0'>Calls: <strong>{run.get('calls', 0)}</strong></p>"
        f"<p style='margin:4px 0'>Tokens: <strong>{run.get('total_tokens', 0)}</strong> "
        f"({run.get('input_tokens', 0)} in, {run.get('output_tokens', 0)} out)</p>"
        f"<p style='margin:4px 0'>Estimated cost: <strong>${float(run.get('cost_usd') or 0):.6f}</strong></p>"
        f"<p style='margin:4px 0'>Models: <strong>{models}</strong></p>"
        f"<p style='margin:4px 0'>Provider retries: <strong>{retry_text}</strong></p>"
        f"<p style='margin:4px 0'>Estimated remaining: <strong>{remaining_text}</strong></p>"
        "<p style='margin:8px 0 0;color:#64748b'>Remaining quota is estimated from "
        "SwimBuddz traffic only; Google does not return an exact remaining counter "
        "with each Gemini response.</p>"
        "</div>"
    )


async def send_analyzer_ready_email(
    to_email: str,
    result_url: str,
    member_name: str = "",
) -> bool:
    """Notify a guest that their freestyle analysis is ready to view."""
    name_line = f"Hi {member_name}," if member_name else "Hi there,"
    greeting_html = (
        f"<p>Hi <strong>{member_name}</strong>,</p>"
        if member_name
        else "<p>Hi there,</p>"
    )
    subject = "Your SwimBuddz freestyle analysis is ready"

    body = (
        f"{name_line}\n\n"
        "Your freestyle stroke analysis is ready.\n\n"
        f"View it here: {result_url}\n\n"
        "— SwimBuddz Stroke Lab"
    )

    inner_html = (
        greeting_html
        + "<p>Your <strong>freestyle stroke analysis</strong> is ready — we measured "
        "your stroke rate, body roll, and breathing balance, flagged what to work "
        "on, and suggested drills.</p>"
        + cta_button("View My Analysis", result_url)
        + "<p style='color:#64748b;font-size:13px;margin-top:18px'>SwimBuddz Stroke "
        "Lab is an automated freestyle measurement tool — honest numbers, not a "
        "human coach.</p>" + sign_off()
    )

    html_body = wrap_html(
        title="Your analysis is ready",
        subtitle="Freestyle stroke breakdown",
        body_html=inner_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Your freestyle stroke analysis is ready to view.",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_analyzer_failed_email(
    to_email: str,
    retry_url: str = "https://analyzer.swimbuddz.com",
    member_name: str = "",
) -> bool:
    """Notify a guest that their clip couldn't be analyzed (credit refunded)."""
    name_line = f"Hi {member_name}," if member_name else "Hi there,"
    greeting_html = (
        f"<p>Hi <strong>{member_name}</strong>,</p>"
        if member_name
        else "<p>Hi there,</p>"
    )
    subject = "We couldn't analyze your freestyle clip"

    body = (
        f"{name_line}\n\n"
        "We couldn't analyze your clip this time, and your credit has been "
        "refunded.\n\n"
        "Tips: film side-on with the swimmer clearly in frame, 10–90 seconds, "
        "exported as MP4.\n\n"
        f"Try again: {retry_url}\n\n"
        "— SwimBuddz Stroke Lab"
    )

    inner_html = (
        greeting_html
        + "<p>We couldn't analyze your clip this time — and your credit has been "
        "<strong>refunded</strong>.</p>"
        + "<p>For the best results, film <strong>side-on</strong> with the swimmer "
        "clearly in frame, 10–90 seconds long, and exported as MP4.</p>"
        + cta_button("Try Another Clip", retry_url)
        + sign_off()
    )

    html_body = wrap_html(
        title="We couldn't analyze your clip",
        subtitle="Your credit has been refunded",
        body_html=inner_html,
        header_gradient=GRADIENT_AMBER,
        preheader="We couldn't analyze your clip — your credit was refunded.",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_analyzer_usage_email(
    to_email: str,
    job_id: str,
    guest_email: str,
    outcome: str,
    provider_usage: dict | None = None,
) -> bool:
    """Internal usage/quota visibility after a Stroke Lab provider run."""
    subject = f"Stroke Lab AI usage: {outcome}"
    body = (
        "Stroke Lab AI usage report\n\n"
        f"Job: {job_id}\n"
        f"Guest: {guest_email}\n"
        f"Outcome: {outcome}\n"
        f"{_provider_usage_text(provider_usage)}"
    )
    inner_html = (
        "<p>A Stroke Lab provider run finished.</p>"
        f"<p><strong>Job:</strong> {job_id}<br />"
        f"<strong>Guest:</strong> {guest_email}<br />"
        f"<strong>Outcome:</strong> {outcome}</p>"
        + _provider_usage_html(provider_usage)
        + sign_off()
    )
    html_body = wrap_html(
        title="Stroke Lab AI usage",
        subtitle=outcome,
        body_html=inner_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Stroke Lab provider usage and Gemini quota estimate.",
    )
    return await send_email(to_email, subject, body, html_body)
