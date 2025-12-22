import uuid

import requests

# Configuration
GATEWAY_URL = "http://localhost:8000"
REGISTRATION_ENDPOINT = f"{GATEWAY_URL}/api/v1/pending-registrations/"


def verify_registration():
    print(f"Testing registration endpoint: {REGISTRATION_ENDPOINT}")

    # Generate a unique email to avoid conflicts
    unique_email = f"test_user_{uuid.uuid4()}@example.com"

    payload = {
        "email": unique_email,
        "first_name": "Test",
        "last_name": "User",
        "phone": "1234567890",
        "city": "Lagos",
        "country": "Nigeria",
        "membership_tier": "community",
        "password": "TestPassword123!",
    }

    try:
        response = requests.post(REGISTRATION_ENDPOINT, json=payload)

        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")

        if response.status_code == 201:
            print("✅ Registration successful (201 Created)")
            print("Check backend logs to confirm Supabase invitation was sent.")
        else:
            print(f"❌ Registration failed with status {response.status_code}")

    except Exception as e:
        print(f"❌ Request failed: {e}")


if __name__ == "__main__":
    verify_registration()
