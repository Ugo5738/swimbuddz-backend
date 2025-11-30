import asyncio
import uuid
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from libs.db.base import Base
from services.transport_service.models import PickupLocation, RideArea, RouteInfo
from services.attendance_service.models import AttendanceRecord
from services.members_service.models import Member
from services.sessions_service.models import Session
from libs.common.config import get_settings

settings = get_settings()

# Use the same DB URL as the app
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def seed_locations():
    async with engine.begin() as conn:
        # Drop specific tables to avoid wiping members/sessions
        # We need to drop route_info and pickup_locations first due to FKs to ride_areas
        await conn.execute(text("DROP TABLE IF EXISTS route_info CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS pickup_locations CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ride_areas CASCADE"))
        
        # Create tables
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        print("Seeding data...")
        
        # 1. Create Areas
        agor = RideArea(name="Agor", slug="agor")
        lekki = RideArea(name="Lekki", slug="lekki")
        db.add_all([agor, lekki])
        await db.flush() # Get IDs
        
        # 2. Create Pickup Locations
        locations = [
            PickupLocation(name="Mega Chicken", description="Apple Junction", area_id=agor.id),
            PickupLocation(name="First Bank", description="Ago Round About", area_id=agor.id),
            PickupLocation(name="Admiralty Way", description="Lekki Phase 1 Gate", area_id=lekki.id),
        ]
        db.add_all(locations)
        await db.flush() # Get IDs for locations
        
        # 3. Create Route Info
        
        # Agor Area Default (e.g. center of Agor) -> Main Pool
        agor_default = RouteInfo(
            origin_area_id=agor.id,
            destination="main_pool",
            destination_name="Rowe Park, Yaba",
            distance_text="14.0 km",
            duration_text="45 mins",
            departure_offset_minutes=120 # 2 hours
        )
        
        # Mega Chicken Override (Closer, less traffic?)
        mega_chicken_route = RouteInfo(
            origin_pickup_location_id=locations[0].id, # Mega Chicken
            destination="main_pool",
            destination_name="Rowe Park, Yaba",
            distance_text="13.7 km",
            duration_text="44 mins",
            departure_offset_minutes=120 # 2 hours
        )
        
        # First Bank Override (Further, more traffic?)
        first_bank_route = RouteInfo(
            origin_pickup_location_id=locations[1].id, # First Bank
            destination="main_pool",
            destination_name="Rowe Park, Yaba",
            distance_text="14.2 km",
            duration_text="46 mins",
            departure_offset_minutes=120 # 2 hours
        )
        
        # Lekki -> Main Pool (Further away)
        lekki_main = RouteInfo(
            origin_area_id=lekki.id,
            destination="main_pool",
            destination_name="Rowe Park, Yaba",
            distance_text="18.5 km",
            duration_text="55 mins",
            departure_offset_minutes=120 # 2 hours
        )
        
        db.add_all([agor_default, mega_chicken_route, first_bank_route, lekki_main])
        
        await db.commit()
        print("Data seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed_locations())
