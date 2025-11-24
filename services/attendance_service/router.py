import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.attendance_service.models import SessionAttendance, PaymentStatus, RideShareOption, PickupLocation, RideArea, RouteInfo
from services.attendance_service.schemas import AttendanceResponse, AttendanceCreate, PublicAttendanceCreate
from services.members_service.models import Member
from services.sessions_service.models import Session

router = APIRouter(tags=["attendance"])


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Member:
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )
    return member


@router.post("/sessions/{session_id}/sign-in", response_model=AttendanceResponse)
async def sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: AttendanceCreate,
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Sign in to a session. Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check for existing attendance
    query = select(SessionAttendance).where(
        SessionAttendance.session_id == session_id,
        SessionAttendance.member_id == current_member.id
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.ride_share_option = attendance_in.ride_share_option
        attendance.needs_ride = attendance_in.needs_ride
        attendance.can_offer_ride = attendance_in.can_offer_ride
        attendance.ride_notes = attendance_in.ride_notes
        attendance.pickup_location = attendance_in.pickup_location
    else:
        # Create new
        attendance = SessionAttendance(
            session_id=session_id,
            member_id=current_member.id,
            ride_share_option=attendance_in.ride_share_option,
            needs_ride=attendance_in.needs_ride,
            can_offer_ride=attendance_in.can_offer_ride,
            ride_notes=attendance_in.ride_notes,
            pickup_location=attendance_in.pickup_location,
            payment_status=PaymentStatus.PENDING,
            total_fee=session.pool_fee
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance


@router.post("/sessions/{session_id}/attendance/public", response_model=AttendanceResponse)
async def public_sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: PublicAttendanceCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Public sign in to a session (no auth required). Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify member exists
    query = select(Member).where(Member.id == attendance_in.member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check for existing attendance
    query = select(SessionAttendance).where(
        SessionAttendance.session_id == session_id,
        SessionAttendance.member_id == attendance_in.member_id
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.ride_share_option = attendance_in.ride_share_option
        attendance.needs_ride = attendance_in.needs_ride
        attendance.can_offer_ride = attendance_in.can_offer_ride
        attendance.ride_notes = attendance_in.ride_notes
        attendance.pickup_location = attendance_in.pickup_location
        # Don't update payment status blindly if it's already paid, but for now we trust the input or keep existing
        # attendance.payment_status = attendance_in.payment_status 
    else:
        # Create new
        attendance = SessionAttendance(
            session_id=session_id,
            member_id=attendance_in.member_id,
            ride_share_option=attendance_in.ride_share_option,
            needs_ride=attendance_in.needs_ride,
            can_offer_ride=attendance_in.can_offer_ride,
            ride_notes=attendance_in.ride_notes,
            pickup_location=attendance_in.pickup_location,
            payment_status=attendance_in.payment_status,
            total_fee=session.pool_fee
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance


    return attendance


@router.get("/config")
async def get_attendance_config(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance configuration including areas, pickup locations, and routes.
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
    
    config = []
    
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


@router.get("/sessions/{session_id}/ride-summary")
async def get_ride_summary(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get summary of active rides and capacity for a session.
    """
    # Get all attendees who need a ride or are offering a ride
    query = select(SessionAttendance).where(
        SessionAttendance.session_id == session_id,
        SessionAttendance.ride_share_option.in_([RideShareOption.JOIN, RideShareOption.LEAD])
    )
    result = await db.execute(query)
    attendees = result.scalars().all()

    # Fetch active locations and areas
    # We need to map pickup_location (name) to area (name)
    # Note: SessionAttendance stores pickup_location NAME (string). 
    # Ideally it should store ID, but for now we map by name.
    
    loc_query = select(PickupLocation, RideArea).join(RideArea).where(PickupLocation.is_active == True)
    loc_result = await db.execute(loc_query)
    loc_rows = loc_result.all()
    
    # Map location name to area name
    location_to_area = {loc.name: area.name for loc, area in loc_rows}
    
    # Get unique areas from the map
    areas = list(set(area.name for _, area in loc_rows))
    
    active_group = None
    active_location = None
    rides = []
    
    for area_name in areas:
        # Get locations for this area
        area_loc_names = [loc.name for loc, area in loc_rows if area.name == area_name]
        
        # Filter attendees for this area
        # Attendees store pickup_location name
        group_attendees = [a for a in attendees if a.pickup_location in area_loc_names]
        group_attendees.sort(key=lambda x: x.created_at)
        
        total_attendees = len(group_attendees)
        
        if total_attendees > 0:
            # Chunk attendees into groups of 4
            chunks = [group_attendees[i:i + 4] for i in range(0, total_attendees, 4)]
            
            for i, chunk in enumerate(chunks):
                chunk_size = len(chunk)
                # The location of the first person in the chunk determines the ride's location
                ride_location = chunk[0].pickup_location if chunk else None
                
                is_filling = chunk_size < 4
                
                rides.append({
                    "group": area_name,
                    "location": ride_location,
                    "filled_seats": chunk_size,
                    "capacity": 4,
                    "ride_number": i + 1
                })
                
                # If this is the last chunk and it's filling, set active group/location
                if is_filling:
                    active_group = area_name
                    active_location = ride_location
        else:
             # No rides yet, but we want to show 0 passengers for this group
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


@router.get("/sessions/{session_id}/attendance", response_model=List[AttendanceResponse])
async def list_session_attendance(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all attendees for a session (Admin only).
    """
    query = select(SessionAttendance, Member).join(Member).where(SessionAttendance.session_id == session_id)
    result = await db.execute(query)
    rows = result.all()
    
    responses = []
    for attendance, member in rows:
        # Convert SQLAlchemy model to Pydantic model
        resp = AttendanceResponse.model_validate(attendance)
        # Manually populate extra fields
        resp.member_name = f"{member.first_name} {member.last_name}"
        resp.member_email = member.email
        responses.append(resp)
        
    return responses


@router.get("/me/attendance", response_model=List[AttendanceResponse])
async def get_my_attendance_history(
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance history for the current member.
    """
    query = select(SessionAttendance).where(
        SessionAttendance.member_id == current_member.id
    ).order_by(SessionAttendance.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}/pool-list")
async def get_pool_list_csv(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Export pool list as CSV (Admin only).
    """
    # Join with Member to get names
    query = select(SessionAttendance, Member).join(Member).where(SessionAttendance.session_id == session_id)
    result = await db.execute(query)
    rows = result.all()

    # Simple CSV generation
    csv_content = "First Name,Last Name,Email,Payment Status,Ride Notes\n"
    for attendance, member in rows:
        csv_content += f"{member.first_name},{member.last_name},{member.email},{attendance.payment_status},{attendance.ride_notes or ''}\n"

    return Response(content=csv_content, media_type="text/csv")
