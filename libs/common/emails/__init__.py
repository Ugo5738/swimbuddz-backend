"""
SwimBuddz Email Package.

Modules:
- core: Base send_email function (Brevo SMTP)
- client: EmailClient for sending emails via the Communications Service API

Note: Email templates have been moved to
services/communications_service/templates/.
Other services should use EmailClient (client.py) to send emails
through the Communications Service, not import templates directly.
"""
