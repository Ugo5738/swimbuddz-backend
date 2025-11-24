import requests
import json
from datetime import datetime

base_url = "http://localhost:8000/api/v1/sessions/"

sessions = [
    {
        "title": "Early morning",
        "start_time": "2025-11-22T06:00:00Z",
        "end_time": "2025-11-22T09:00:00Z"
    },
    {
        "title": "Late morning",
        "start_time": "2025-11-22T09:00:00Z",
        "end_time": "2025-11-22T12:00:00Z"
    },
    {
        "title": "Midday",
        "start_time": "2025-11-22T12:00:00Z",
        "end_time": "2025-11-22T15:00:00Z"
    },
    {
        "title": "Late afternoon",
        "start_time": "2025-11-22T15:00:00Z",
        "end_time": "2025-11-22T18:00:00Z"
    },
    {
        "title": "Evening",
        "start_time": "2025-11-22T18:00:00Z",
        "end_time": "2025-11-22T21:00:00Z"
    }
]

for s in sessions:
    payload = {
        "title": s["title"],
        "description": "Standard session",
        "location": "main_pool",
        "pool_fee": 2000,
        "capacity": 20,
        "start_time": s["start_time"],
        "end_time": s["end_time"]
    }
    try:
        response = requests.post(base_url, json=payload)
        print(f"Created {s['title']}: {response.status_code}")
    except Exception as e:
        print(f"Failed {s['title']}: {e}")
