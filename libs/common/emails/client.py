"""
Centralized Email Client for service-to-service email communication.

This module provides a simple HTTP client that other services can use
to send emails through the Communications Service's centralized email API.

The Communications Service is the single source of truth for all email
templates and sending. This client handles:
- Service-role JWT authentication for inter-service calls
- Fallback to direct SMTP when the Communications Service is unavailable
- Singleton pattern for reuse across a service's lifetime

Usage:
    from libs.common.emails.client import get_email_client

    email_client = get_email_client()

    # Send simple email
    await email_client.send(
        to_email="user@example.com",
        subject="Hello",
        body="Plain text body",
        html_body="<p>HTML body</p>"
    )

    # Send templated email
    await email_client.send_template(
        template_type="enrollment_confirmation",
        to_email="user@example.com",
        template_data={
            "member_name": "John",
            "program_name": "Learn to Swim",
        }
    )

    # Send bulk emails
    await email_client.send_bulk(
        to_emails=["user1@example.com", "user2@example.com"],
        subject="Announcement",
        body="Hello everyone!"
    )
"""

import uuid
from typing import Any, Optional

import httpx
from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


class EmailClient:
    """
    HTTP client for sending emails through the Communications Service.

    All email sending is routed through the centralized Communications Service
    API at port 8004. This client authenticates using a short-lived service-role
    JWT so that the email endpoints (which require service_role auth) accept
    the requests.

    Falls back to direct SMTP sending if the Communications Service is
    unreachable (connection error only — auth errors are not retried).
    """

    def __init__(self):
        settings = get_settings()
        self.base_url = getattr(
            settings, "COMMUNICATIONS_SERVICE_URL", "http://communications_service:8004"
        )
        self.timeout = 30.0

    def _get_auth_headers(self) -> dict[str, str]:
        """
        Generate service-role auth headers for inter-service calls.

        Uses a short-lived (60s) JWT signed with the Supabase JWT secret,
        matching the pattern expected by `require_service_role` in the
        Communications Service.
        """
        from libs.auth.dependencies import _service_role_jwt

        token = _service_role_jwt("email_client")
        return {"Authorization": f"Bearer {token}"}

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
    ) -> bool:
        """
        Send a single email through the Communications Service.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Plain text body
            html_body: Optional HTML body
            from_email: Optional sender email (defaults to no-reply@swimbuddz.com)
            from_name: Optional sender name (defaults to SwimBuddz)

        Returns:
            True if email was sent successfully, False otherwise
        """
        payload: dict[str, Any] = {
            "to_email": to_email,
            "subject": subject,
            "body": body,
        }
        if html_body:
            payload["html_body"] = html_body
        if from_email:
            payload["from_email"] = from_email
        if from_name:
            payload["from_name"] = from_name

        try:
            headers = self._get_auth_headers()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/email/send",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 200:
                    result = response.json()
                    return result.get("success", False)
                else:
                    logger.error(
                        f"Email API returned {response.status_code}: {response.text}"
                    )
                    return False
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Communications Service: {e}")
            # Fall back to direct send if comms service unavailable
            return await self._fallback_send(
                to_email, subject, body, html_body, from_email, from_name
            )
        except Exception as e:
            logger.error(f"Error sending email via API: {e}")
            return False

    async def send_bulk(
        self,
        to_emails: list[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
        sender_id: Optional[uuid.UUID] = None,
    ) -> dict:
        """
        Send bulk emails through the Communications Service.

        Args:
            to_emails: List of recipient email addresses
            subject: Email subject line
            body: Plain text body
            html_body: Optional HTML body
            from_email: Optional sender email
            from_name: Optional sender name
            sender_id: Optional UUID for logging (e.g., coach ID)

        Returns:
            Dict with success, sent_count, and failed_count
        """
        payload: dict[str, Any] = {
            "to_emails": to_emails,
            "subject": subject,
            "body": body,
        }
        if html_body:
            payload["html_body"] = html_body
        if from_email:
            payload["from_email"] = from_email
        if from_name:
            payload["from_name"] = from_name
        if sender_id:
            payload["sender_id"] = str(sender_id)

        try:
            headers = self._get_auth_headers()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/email/send-bulk",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        f"Bulk email API returned {response.status_code}: {response.text}"
                    )
                    return {
                        "success": False,
                        "sent_count": 0,
                        "failed_count": len(to_emails),
                    }
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Communications Service: {e}")
            # Fall back to direct send
            return await self._fallback_send_bulk(
                to_emails, subject, body, html_body, from_email, from_name
            )
        except Exception as e:
            logger.error(f"Error sending bulk email via API: {e}")
            return {
                "success": False,
                "sent_count": 0,
                "failed_count": len(to_emails),
            }

    async def send_template(
        self,
        template_type: str,
        to_email: str,
        template_data: dict[str, Any],
    ) -> bool:
        """
        Send a templated email through the Communications Service.

        All template rendering is handled by the Communications Service.
        This client just forwards the template type and data.

        Available template types:
        - enrollment_confirmation: Academy enrollment confirmed
        - enrollment_reminder: Reminder before cohort starts
        - waitlist_promotion: Student moved off waitlist
        - progress_report: Student progress report
        - certificate: Course completion certificate
        - attendance_summary: Weekly attendance summary for coach
        - low_attendance_alert: Alert about student low attendance
        - payment_approved: Payment was approved
        - session_confirmation: Session booking confirmed
        - store_order_confirmation: Store order confirmed
        - store_order_ready: Store order ready for pickup/shipped
        - coach_assignment: Coach assigned to cohort
        - coach_agreement_signed: Agreement signing confirmation
        - coach_grade_change: Grade promotion notification
        - shadow_assignment: Shadow assignment notification
        - coach_application_approved: Coach application approved
        - coach_application_rejected: Coach application rejected
        - coach_application_more_info: More info requested from applicant
        - member_approved: Member application approved
        - member_rejected: Member application rejected

        Args:
            template_type: The template identifier
            to_email: Recipient email address
            template_data: Dict of template variables

        Returns:
            True if email was sent successfully, False otherwise
        """
        payload = {
            "template_type": template_type,
            "to_email": to_email,
            "template_data": template_data,
        }

        try:
            headers = self._get_auth_headers()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/email/template",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 200:
                    result = response.json()
                    return result.get("success", False)
                else:
                    logger.error(
                        f"Template email API returned {response.status_code}: {response.text}"
                    )
                    return False
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Communications Service: {e}")
            logger.warning(
                f"Template email '{template_type}' could not be sent — "
                "Communications Service unreachable. No fallback available "
                "for templated emails (templates live in Communications Service)."
            )
            return False
        except Exception as e:
            logger.error(f"Error sending template email via API: {e}")
            return False

    # === Fallback methods when Communications Service is unavailable ===
    # NOTE: Only plain-text send/send_bulk have fallbacks (direct SMTP).
    # Templated emails have NO fallback because templates live exclusively
    # in the Communications Service.

    async def _fallback_send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
    ) -> bool:
        """Fallback to direct SMTP send if Communications Service unavailable."""
        logger.warning("Falling back to direct SMTP email send")
        from libs.common.emails.core import send_email

        return await send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            html_body=html_body,
            from_email=from_email,
            from_name=from_name,
        )

    async def _fallback_send_bulk(
        self,
        to_emails: list[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
    ) -> dict:
        """Fallback to direct SMTP send for bulk emails."""
        logger.warning("Falling back to direct SMTP bulk email send")
        from libs.common.emails.core import send_email

        sent_count = 0
        failed_count = 0

        for email in to_emails:
            success = await send_email(
                to_email=email,
                subject=subject,
                body=body,
                html_body=html_body,
                from_email=from_email,
                from_name=from_name,
            )
            if success:
                sent_count += 1
            else:
                failed_count += 1

        return {
            "success": failed_count == 0,
            "sent_count": sent_count,
            "failed_count": failed_count,
        }


# Singleton instance for convenience
_email_client: Optional[EmailClient] = None


def get_email_client() -> EmailClient:
    """Get or create the singleton EmailClient instance."""
    global _email_client
    if _email_client is None:
        _email_client = EmailClient()
    return _email_client
