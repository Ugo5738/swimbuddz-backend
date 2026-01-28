#!/usr/bin/env python3
"""
Test script to verify Brevo SMTP connection.
Run from backend root: python3 scripts/test_brevo_smtp.py
"""

import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from dotenv import load_dotenv

# Load environment
project_root = Path(__file__).resolve().parents[1]
env_file = os.environ.get("ENV_FILE", ".env.dev")
load_dotenv(project_root / env_file, override=True)

# Brevo SMTP settings (these should match what you have in Supabase)
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_USERNAME = "9e85f2001@smtp-brevo.com"  # From your Brevo screenshot
SMTP_PASSWORD = os.environ.get("BREVO_KEY")

# Sender and recipient for test
SENDER_EMAIL = "no-reply@swimbuddz.com"
SENDER_NAME = "SwimBuddz"
TEST_RECIPIENT = input("Enter your email address to receive the test email: ").strip()

if not SMTP_PASSWORD:
    print("❌ BREVO_KEY not found in environment")
    sys.exit(1)

print("\n📧 Testing Brevo SMTP Connection")
print("=" * 50)
print(f"Host: {SMTP_HOST}")
print(f"Port: {SMTP_PORT}")
print(f"Username: {SMTP_USERNAME}")
print(f"Password: {'*' * 20}...{SMTP_PASSWORD[-6:]}")
print(f"Sender: {SENDER_NAME} <{SENDER_EMAIL}>")
print(f"Recipient: {TEST_RECIPIENT}")
print("=" * 50)

try:
    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "SwimBuddz SMTP Test"
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = TEST_RECIPIENT

    text = (
        "This is a test email from SwimBuddz to verify Brevo SMTP is working correctly."
    )
    html = """
    <html>
    <body>
        <h2>🏊 SwimBuddz SMTP Test</h2>
        <p>This is a test email to verify that the Brevo SMTP connection is working correctly.</p>
        <p>If you're seeing this, the SMTP configuration is correct!</p>
        <br>
        <p>Best regards,<br>SwimBuddz Team</p>
    </body>
    </html>
    """

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    print("\n🔌 Connecting to SMTP server...")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        print("✓ Connected")

        print("🔐 Starting TLS...")
        server.starttls()
        print("✓ TLS started")

        print("🔑 Authenticating...")
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        print("✓ Authenticated successfully!")

        print("📤 Sending test email...")
        server.sendmail(SENDER_EMAIL, TEST_RECIPIENT, msg.as_string())
        print("✓ Email sent successfully!")

    print(f"\n✅ SUCCESS! Check your inbox at {TEST_RECIPIENT}")
    print("\nIf you received the email, the BREVO_KEY credentials are correct.")
    print("The issue is likely in the Supabase SMTP configuration.")
    print("\nMake sure in Supabase SMTP Settings:")
    print(f"  - Host: {SMTP_HOST}")
    print(f"  - Port: {SMTP_PORT}")
    print(f"  - Username: {SMTP_USERNAME}")
    print(f"  - Password: {SMTP_PASSWORD}")

except smtplib.SMTPAuthenticationError as e:
    print(f"\n❌ AUTHENTICATION FAILED: {e}")
    print("\nThe username or password is incorrect.")
    print("Double-check your BREVO_KEY matches the SMTP key in Brevo.")

except smtplib.SMTPException as e:
    print(f"\n❌ SMTP ERROR: {e}")

except Exception as e:
    print(f"\n❌ ERROR: {type(e).__name__}: {e}")
