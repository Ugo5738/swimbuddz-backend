"""
Centralized Email Client for service-to-service email communication.

This module provides a simple HTTP client that other services can use
to send emails through the Communications Service's centralized email API.

Usage:
    from libs.common.emails.client import EmailClient

    email_client = EmailClient()

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
            ...
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

    This replaces direct imports from libs.common.emails.* modules,
    routing all email sending through the centralized API.
    """

    def __init__(self):
        settings = get_settings()
        # Communications service runs on port 8004
        self.base_url = getattr(
            settings, "COMMUNICATIONS_SERVICE_URL", "http://communications_service:8004"
        )
        self.timeout = 30.0

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
        payload = {
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/email/send",
                    json=payload,
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
        payload = {
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/email/send-bulk",
                    json=payload,
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/email/template",
                    json=payload,
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
            # Fall back to direct template send
            return await self._fallback_send_template(
                template_type, to_email, template_data
            )
        except Exception as e:
            logger.error(f"Error sending template email via API: {e}")
            return False

    # === Fallback methods when Communications Service is unavailable ===

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
        logger.warning("Falling back to direct email send")
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
        logger.warning("Falling back to direct bulk email send")
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

    async def _fallback_send_template(
        self,
        template_type: str,
        to_email: str,
        template_data: dict[str, Any],
    ) -> bool:
        """Fallback to direct template send if Communications Service unavailable."""
        logger.warning(f"Falling back to direct template send: {template_type}")

        # Import the appropriate template function
        if template_type in [
            "enrollment_confirmation",
            "enrollment_reminder",
            "waitlist_promotion",
            "progress_report",
            "certificate",
            "attendance_summary",
            "low_attendance_alert",
        ]:
            from libs.common.emails import academy

            handlers = {
                "enrollment_confirmation": lambda: academy.send_enrollment_confirmation_email(
                    to_email=to_email,
                    member_name=template_data.get("member_name", ""),
                    program_name=template_data.get("program_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                    start_date=template_data.get("start_date", ""),
                ),
                "enrollment_reminder": lambda: academy.send_enrollment_reminder_email(
                    to_email=to_email,
                    member_name=template_data.get("member_name", ""),
                    program_name=template_data.get("program_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                    start_date=template_data.get("start_date", ""),
                    start_time=template_data.get("start_time", ""),
                    location=template_data.get("location", ""),
                    days_until=template_data.get("days_until", 7),
                ),
                "waitlist_promotion": lambda: academy.send_waitlist_promotion_email(
                    to_email=to_email,
                    member_name=template_data.get("member_name", ""),
                    program_name=template_data.get("program_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                ),
                "progress_report": lambda: academy.send_progress_report_email(
                    to_email=to_email,
                    member_name=template_data.get("member_name", ""),
                    program_name=template_data.get("program_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                    milestones_completed=template_data.get("milestones_completed", 0),
                    total_milestones=template_data.get("total_milestones", 0),
                    recent_achievements=template_data.get("recent_achievements", []),
                    coach_feedback=template_data.get("coach_feedback", []),
                ),
                "certificate": lambda: academy.send_certificate_email(
                    to_email=to_email,
                    member_name=template_data.get("member_name", ""),
                    program_name=template_data.get("program_name", ""),
                    completion_date=template_data.get("completion_date", ""),
                    verification_code=template_data.get("verification_code", ""),
                ),
                "attendance_summary": lambda: academy.send_attendance_summary_email(
                    to_email=to_email,
                    coach_name=template_data.get("coach_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                    program_name=template_data.get("program_name", ""),
                    period=template_data.get("period", ""),
                    total_sessions=template_data.get("total_sessions", 0),
                    student_stats=template_data.get("student_stats", []),
                    at_risk_students=template_data.get("at_risk_students", []),
                ),
                "low_attendance_alert": lambda: academy.send_low_attendance_alert_email(
                    to_email=to_email,
                    coach_name=template_data.get("coach_name", ""),
                    student_name=template_data.get("student_name", ""),
                    cohort_name=template_data.get("cohort_name", ""),
                    issue=template_data.get("issue", ""),
                    attendance_rate=template_data.get("attendance_rate", 0),
                    suggestions=template_data.get("suggestions", []),
                ),
            }
            return await handlers[template_type]()

        elif template_type == "payment_approved":
            from libs.common.emails import payments

            return await payments.send_payment_approved_email(
                to_email=to_email,
                payment_reference=template_data.get("payment_reference", ""),
                purpose=template_data.get("purpose", ""),
                amount=template_data.get("amount", 0),
                currency=template_data.get("currency", "NGN"),
            )

        elif template_type == "session_confirmation":
            from libs.common.emails import sessions

            return await sessions.send_session_confirmation_email(
                to_email=to_email,
                member_name=template_data.get("member_name", ""),
                member_id=template_data.get("member_id", ""),
                session_title=template_data.get("session_title", ""),
                session_date=template_data.get("session_date", ""),
                session_time=template_data.get("session_time", ""),
                session_location=template_data.get("session_location", ""),
                session_address=template_data.get("session_address", ""),
                amount_paid=template_data.get("amount_paid", 0),
                ride_share_area=template_data.get("ride_share_area"),
                pickup_location=template_data.get("pickup_location"),
                pickup_description=template_data.get("pickup_description"),
                departure_time=template_data.get("departure_time"),
                ride_distance=template_data.get("ride_distance"),
                ride_duration=template_data.get("ride_duration"),
                currency=template_data.get("currency", "NGN"),
            )

        elif template_type == "store_order_confirmation":
            from libs.common.emails import store

            return await store.send_store_order_confirmation_email(
                to_email=to_email,
                customer_name=template_data.get("customer_name", ""),
                order_number=template_data.get("order_number", ""),
                items=template_data.get("items", []),
                subtotal=template_data.get("subtotal", 0),
                discount=template_data.get("discount", 0),
                delivery_fee=template_data.get("delivery_fee", 0),
                total=template_data.get("total", 0),
                fulfillment_type=template_data.get("fulfillment_type", "pickup"),
                pickup_location=template_data.get("pickup_location"),
                delivery_address=template_data.get("delivery_address"),
            )

        elif template_type == "store_order_ready":
            from libs.common.emails import store

            return await store.send_store_order_ready_email(
                to_email=to_email,
                customer_name=template_data.get("customer_name", ""),
                order_number=template_data.get("order_number", ""),
                fulfillment_type=template_data.get("fulfillment_type", "pickup"),
                pickup_location=template_data.get("pickup_location"),
                tracking_number=template_data.get("tracking_number"),
            )

        logger.error(f"Unknown template type for fallback: {template_type}")
        return False


# Singleton instance for convenience
_email_client: Optional[EmailClient] = None


def get_email_client() -> EmailClient:
    """Get or create the singleton EmailClient instance."""
    global _email_client
    if _email_client is None:
        _email_client = EmailClient()
    return _email_client
