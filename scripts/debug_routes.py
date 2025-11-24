import asyncio
import sys
import os

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from services.attendance_service.models import RouteInfo, PickupLocation
from libs.common.config import get_settings

settings = get_settings()
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def debug_routes():
    async with AsyncSessionLocal() as db:
        # Fetch all routes
        result = await db.execute(select(RouteInfo))
        routes = result.scalars().all()
        
        print(f"Found {len(routes)} routes:")
        for r in routes:
            print(f"Route ID: {r.id}")
            print(f"  Area ID: {r.origin_area_id}")
            print(f"  Loc ID: {r.origin_pickup_location_id}")
            print(f"  Dest: {r.destination}")
            print(f"  Dist: {r.distance_text}")
            print(f"  Offset: {r.departure_offset_minutes}")
            print("-" * 20)

        # Fetch locations to compare IDs
        loc_result = await db.execute(select(PickupLocation))
        locs = loc_result.scalars().all()
        print(f"Found {len(locs)} locations:")
        for l in locs:
            print(f"Loc ID: {l.id} Name: {l.name}")

if __name__ == "__main__":
    asyncio.run(debug_routes())
