import asyncio
import sys
import os

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, date
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from libs.common.config import get_settings
from services.sessions_service.models import Session, SessionLocation
from services.attendance_service.models import SessionAttendance

settings = get_settings()
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

def get_next_saturday():
    today = date.today()
    weekday = today.weekday()
    if weekday == 5:
        days_ahead = 0
    else:
        days_ahead = (5 - weekday) % 7
    return today + timedelta(days=days_ahead)

async def create_sessions():
    async with AsyncSessionLocal() as db:
        print("Deleting existing sessions...")
        # Delete attendance first to avoid FK violation
        await db.execute(text("DELETE FROM session_attendance"))
        await db.execute(text("DELETE FROM sessions"))
        await db.commit()

        print("Creating new sessions...")
        target_date = get_next_saturday()
        date_str = target_date.strftime("%Y-%m-%d")
        
        # Times adjusted for UTC+1 (Nigeria)
        # 6 AM WAT = 5 AM UTC
        sessions_data = [
            {
                "title": "Early morning",
                "start_time": f"{date_str}T05:00:00Z",
                "end_time": f"{date_str}T08:00:00Z"
            },
            {
                "title": "Late morning",
                "start_time": f"{date_str}T08:00:00Z",
                "end_time": f"{date_str}T11:00:00Z"
            },
            {
                "title": "Midday",
                "start_time": f"{date_str}T11:00:00Z",
                "end_time": f"{date_str}T14:00:00Z"
            },
            {
                "title": "Late afternoon",
                "start_time": f"{date_str}T14:00:00Z",
                "end_time": f"{date_str}T17:00:00Z"
            },
            {
                "title": "Evening",
                "start_time": f"{date_str}T17:00:00Z",
                "end_time": f"{date_str}T20:00:00Z"
            }
        ]

        new_sessions = []
        for s in sessions_data:
            new_sessions.append(Session(
                title=s["title"],
                description="Standard session",
                location=SessionLocation.MAIN_POOL,
                pool_fee=2000,
                capacity=20,
                start_time=datetime.fromisoformat(s["start_time"].replace("Z", "+00:00")),
                end_time=datetime.fromisoformat(s["end_time"].replace("Z", "+00:00"))
            ))
        
        db.add_all(new_sessions)
        await db.commit()
        print("Sessions created.")

if __name__ == "__main__":
    asyncio.run(create_sessions())
