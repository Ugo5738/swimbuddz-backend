import uuid
from typing import List, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.transport_service.models import (
    RideArea,
    PickupLocation,
    RouteInfo,
    RidePreference,
    RideShareOption,
)
from fastapi import HTTPException, status
from pydantic import BaseModel

router = APIRouter(prefix="/transport", tags=["transport"])


class RidePreferenceIn(BaseModel):
    ride_share_option: RideShareOption = RideShareOption.NONE
    needs_ride: bool = False
    can_offer_ride: bool = False
    ride_notes: str | None = None
    pickup_location: str | None = None
    member_id: uuid.UUID


@router.get("/config")
async def get_transport_config(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get transport configuration including areas, pickup locations, and routes.
    """
    # Fetch all active areas
    areas_query = select(RideArea).where(RideArea.is_active == True).order_by(RideArea.name)
    areas_result = await db.execute(areas_query)
    areas = areas_result.scalars().all()
    
    # Fetch all active locations
    locs_query = select(PickupLocation).where(PickupLocation.is_active == True)
    locs_result = await db.execute(locs_query)
    locations = locs_result.scalars().all()
    
    # Fetch all routes
    routes_query = select(RouteInfo)
    routes_result = await db.execute(routes_query)
    routes = routes_result.scalars().all()
    
    config: List[Dict] = []
    
    for area in areas:
        # Get locations for this area
        area_locs = [loc for loc in locations if loc.area_id == area.id]
        
        # Get routes for this area (default)
        area_default_routes = {
            r.destination: r 
            for r in routes 
            if r.origin_area_id == area.id
        }
        
        # Get routes for specific locations in this area
        # Convert UUID keys to strings for easier comparison
        loc_specific_routes = {
            (str(r.origin_pickup_location_id), r.destination): r
            for r in routes
            if r.origin_pickup_location_id is not None
        }
        
        formatted_locs = []
        for loc in area_locs:
            loc_id_str = str(loc.id)
            loc_routes = {}
            
            all_destinations = set(area_default_routes.keys())
            # Add specific destinations for this loc
            for (r_loc_id, dest) in loc_specific_routes.keys():
                if r_loc_id == loc_id_str:
                    all_destinations.add(dest)
            
            for dest in all_destinations:
                route = None
                # Check specific first
                if (loc_id_str, dest) in loc_specific_routes:
                    route = loc_specific_routes[(loc_id_str, dest)]
                # Fallback to area default
                elif dest in area_default_routes:
                    route = area_default_routes[dest]
                
                if route:
                    loc_routes[dest] = {
                        "destination_name": route.destination_name,
                        "distance": route.distance_text,
                        "duration": route.duration_text,
                        "departure_offset": route.departure_offset_minutes
                    }
            
            formatted_locs.append({
                "id": str(loc.id),
                "name": loc.name,
                "description": loc.description,
                "routes": loc_routes # Granular routes attached to location
            })
        
        # We still provide area routes as a fallback/default for the UI
        area_routes_formatted = {
            dest: {
                "destination_name": r.destination_name,
                "distance": r.distance_text,
                "duration": r.duration_text,
                "departure_offset": r.departure_offset_minutes
            }
            for dest, r in area_default_routes.items()
        }
        
        config.append({
            "id": str(area.id),
            "name": area.name,
            "slug": area.slug,
            "pickup_locations": formatted_locs,
            "routes": area_routes_formatted
        })
        
    return {"areas": config}


@router.post("/sessions/{session_id}/rides", status_code=204)
async def upsert_ride_preference(
    session_id: uuid.UUID,
    payload: RidePreferenceIn,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Upsert ride-share preference for a member for a session.
    """
    query = select(RidePreference).where(
        RidePreference.session_id == session_id,
        RidePreference.member_id == payload.member_id,
    )
    result = await db.execute(query)
    pref = result.scalar_one_or_none()

    if pref:
        pref.ride_share_option = payload.ride_share_option
        pref.needs_ride = payload.needs_ride
        pref.can_offer_ride = payload.can_offer_ride
        pref.ride_notes = payload.ride_notes
        pref.pickup_location = payload.pickup_location
    else:
        pref = RidePreference(
            session_id=session_id,
            member_id=payload.member_id,
            ride_share_option=payload.ride_share_option,
            needs_ride=payload.needs_ride,
            can_offer_ride=payload.can_offer_ride,
            ride_notes=payload.ride_notes,
            pickup_location=payload.pickup_location,
        )
        db.add(pref)

    await db.commit()
    return None


@router.get("/sessions/{session_id}/ride-summary")
async def get_ride_summary(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get summary of ride groups for a session using ride preferences and locations.
    """
    # Fetch ride preferences for session
    prefs_query = select(RidePreference).where(RidePreference.session_id == session_id)
    prefs_result = await db.execute(prefs_query)
    prefs = prefs_result.scalars().all()

    # Fetch active locations and areas
    loc_query = select(PickupLocation, RideArea).join(RideArea).where(PickupLocation.is_active == True)
    loc_result = await db.execute(loc_query)
    loc_rows = loc_result.all()

    # Map area name to pickup locations
    area_to_locations: dict[str, list[str]] = {}
    for loc, area in loc_rows:
        area_to_locations.setdefault(area.name, []).append(loc.name)

    rides = []
    active_group = None
    active_location = None

    for area_name, area_loc_names in area_to_locations.items():
        group_prefs = [p for p in prefs if p.pickup_location in area_loc_names]
        group_prefs.sort(key=lambda x: x.created_at)
        total = len(group_prefs)

        if total > 0:
            chunks = [group_prefs[i:i + 4] for i in range(0, total, 4)]
            for i, chunk in enumerate(chunks):
                chunk_size = len(chunk)
                ride_location = chunk[0].pickup_location if chunk else None
                is_filling = chunk_size < 4

                rides.append({
                    "group": area_name,
                    "location": ride_location,
                    "filled_seats": chunk_size,
                    "capacity": 4,
                    "ride_number": i + 1
                })

                if is_filling:
                    active_group = area_name
                    active_location = ride_location
        else:
            rides.append({
                "group": area_name,
                "location": None,
                "filled_seats": 0,
                "capacity": 4,
                "ride_number": 1
            })

    return {
        "active_group": active_group,
        "active_location": active_location,
        "rides": rides
    }
