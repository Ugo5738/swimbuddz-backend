# Transport Service

Ride-sharing and transportation management service for SwimBuddz sessions.

**Port:** 8009
**Status:** Production

---

## Overview

The Transport Service manages ride-sharing coordination for SwimBuddz members attending sessions. It handles geographic areas, pickup locations, route information, and ride bookings to facilitate carpooling and reduce transportation costs.

### Key Features

- **Ride Areas** - Geographic zones for organizing pickups (e.g., "Lekki", "VI", "Mainland")
- **Pickup Locations** - Specific pickup points within areas with GPS coordinates
- **Route Info** - Pre-calculated routes with distance, duration, and cost estimates
- **Ride Bookings** - Member ride requests and driver seat offerings
- **Session Integration** - Links rides to specific swim sessions

---

## Domain Models

### Core Models

#### RideArea
Geographic service areas for organizing pickups.

**Fields:**
- `id` - UUID
- `name` - Area name (e.g., "Lekki")
- `slug` - URL-friendly identifier
- `is_active` - Enable/disable area
- `created_at`, `updated_at`

**Example:** "Lekki", "Victoria Island", "Ikeja"

#### PickupLocation
Specific pickup points within ride areas.

**Fields:**
- `id` - UUID
- `area_id` - Foreign key to RideArea
- `name` - Location name (e.g., "Lekki Phase 1 Roundabout")
- `description` - Additional details
- `address` - Street address
- `latitude`, `longitude` - GPS coordinates (optional)
- `is_active` - Enable/disable location
- `created_at`, `updated_at`

**Example:** "Lekki Phase 1 Roundabout", "Admiralty Way, Lekki"

#### RouteInfo
Pre-calculated route information between pickup locations and destinations.

**Fields:**
- `id` - UUID
- `origin_area_id` - Starting area
- `origin_pickup_location_id` - Starting pickup point
- `destination` - Destination identifier
- `destination_name` - Human-readable destination
- `distance_text` - Distance (e.g., "13.7 km")
- `distance_value` - Distance in meters
- `duration_text` - Estimated time (e.g., "25 mins")
- `duration_value` - Duration in seconds
- `suggested_transport_fee` - Recommended cost per rider
- `is_active` - Enable/disable route
- `created_at`, `updated_at`

**Example:** Lekki Phase 1 → Yaba (13.7 km, 25 mins, ₦1500)

#### SessionRideConfig
Ride-sharing configuration for specific sessions.

**Fields:**
- `id` - UUID
- `session_id` - Foreign key to session
- `ride_share_enabled` - Enable ride-sharing for this session
- `pickup_locations` - JSON array of enabled pickup location IDs
- `allow_new_pickups` - Allow members to suggest new pickups
- `created_at`, `updated_at`

#### RideBooking
Member ride requests and offerings.

**Fields:**
- `id` - UUID
- `session_id` - Foreign key to session
- `member_id` - Foreign key to member
- `ride_share_option` - Enum: NONE, LEAD (driver), JOIN (passenger)
- `pickup_location_id` - Selected pickup location
- `seats_offered` - Number of seats if driver (0 if passenger)
- `transport_fee` - Fee paid/charged
- `status` - Booking status
- `created_at`, `updated_at`

---

## API Endpoints

### Ride Areas

**Create Ride Area** (Admin)
```
POST /transport/areas
Body: { "name": "Lekki", "slug": "lekki" }
```

**List Ride Areas**
```
GET /transport/areas
Response: Array of RideArea objects
```

**Get Ride Area**
```
GET /transport/areas/{area_id}
```

**Update Ride Area** (Admin)
```
PATCH /transport/areas/{area_id}
Body: { "name": "...", "is_active": true }
```

**Delete Ride Area** (Admin)
```
DELETE /transport/areas/{area_id}
```

### Pickup Locations

**Create Pickup Location** (Admin)
```
POST /transport/areas/{area_id}/pickups
Body: {
  "name": "Lekki Phase 1 Roundabout",
  "description": "...",
  "address": "...",
  "latitude": 6.4474,
  "longitude": 3.4708
}
```

**List Pickup Locations**
```
GET /transport/areas/{area_id}/pickups
Response: Array of PickupLocation objects
```

**Update Pickup Location** (Admin)
```
PATCH /transport/pickups/{pickup_id}
```

**Delete Pickup Location** (Admin)
```
DELETE /transport/pickups/{pickup_id}
```

### Routes

**Create Route** (Admin)
```
POST /transport/routes
Body: {
  "origin_pickup_location_id": "uuid",
  "destination": "yaba_rowe_park",
  "destination_name": "Rowe Park, Yaba",
  "distance_text": "13.7 km",
  "distance_value": 13700,
  "duration_text": "25 mins",
  "duration_value": 1500,
  "suggested_transport_fee": 1500
}
```

**Get Routes**
```
GET /transport/routes?pickup_location_id={id}&destination={dest}
```

**Update Route** (Admin)
```
PATCH /transport/routes/{route_id}
```

### Session Ride Configuration

**Configure Session Rides** (Admin)
```
POST /transport/sessions/{session_id}/config
Body: {
  "ride_share_enabled": true,
  "pickup_locations": ["uuid1", "uuid2"],
  "allow_new_pickups": false
}
```

**Get Session Ride Config**
```
GET /transport/sessions/{session_id}/config
```

### Ride Bookings

**Create/Update Ride Booking** (Member)
```
POST /transport/sessions/{session_id}/bookings
Body: {
  "ride_share_option": "lead",  // or "join" or "none"
  "pickup_location_id": "uuid",
  "seats_offered": 3,  // if driver
  "transport_fee": 1500
}
```

**Get Member's Ride Booking**
```
GET /transport/sessions/{session_id}/bookings/me
```

**List Session Ride Bookings** (Admin)
```
GET /transport/sessions/{session_id}/bookings
Response: Array of all bookings for session
```

**Cancel Ride Booking** (Member)
```
DELETE /transport/sessions/{session_id}/bookings/me
```

---

## Database Schema

### Tables

- `ride_areas` - Geographic service areas
- `pickup_locations` - Pickup points within areas
- `route_info` - Pre-calculated routes
- `session_ride_configs` - Per-session ride configuration
- `ride_bookings` - Member ride requests/offerings

### Relationships

```
RideArea (1) → (N) PickupLocation
PickupLocation (1) → (N) RouteInfo (as origin)
Session (1) → (1) SessionRideConfig
Session (1) → (N) RideBooking
Member (1) → (N) RideBooking
```

---

## Use Cases

### 1. Admin Sets Up Ride-Sharing for an Area

1. Create ride area: "Lekki"
2. Add pickup locations: "Phase 1 Roundabout", "Admiralty Way"
3. Create routes from each pickup to session destination (e.g., Yaba)
4. Set suggested transport fees based on distance

### 2. Admin Enables Rides for Session

1. Create session (e.g., "Yaba Club Training - Saturday")
2. Configure ride-sharing:
   - Enable ride-share
   - Select available pickup locations
   - Allow/disallow new pickup suggestions

### 3. Member Offers to Drive

1. Member signs up for session
2. Selects "Lead" (driver) option
3. Chooses pickup location
4. Specifies number of seats offered (e.g., 3)
5. Sets transport fee (defaults to suggested fee)

### 4. Member Joins a Ride

1. Member signs up for session
2. Selects "Join" (passenger) option
3. Chooses pickup location matching available drivers
4. Confirms transport fee

### 5. Admin Views Ride Coordination

1. View all ride bookings for session
2. See drivers with available seats
3. See passengers looking for rides
4. Match passengers to drivers at same pickup location

---

## Integration with Other Services

### Sessions Service
- Transport configuration links to `Session` records
- Ride bookings reference `session_id`

### Attendance Service
- Ride-share option stored in attendance records
- Transport fee added to total session fee

### Payments Service
- Transport fees included in session payment breakdown
- Separate line item for ride-share cost

### Members Service
- Ride bookings reference `member_id`
- Member profile shows ride history

---

## Configuration

### Environment Variables

```env
DATABASE_URL=postgresql+psycopg://user:pass@localhost/swimbuddz
```

### Dependencies

- FastAPI
- SQLAlchemy 2.0+
- Alembic (migrations)
- Shared libs: `libs/db`, `libs/auth`, `libs/common`

---

## Development

### Running Locally

```bash
# Via Docker Compose (recommended)
docker compose up transport-service

# Direct execution
uvicorn services.transport_service.router:app --host 0.0.0.0 --port 8009
```

### Database Migrations

```bash
# Create migration
alembic revision --autogenerate -m "Add transport tables"

# Apply migrations
alembic upgrade head
```

### API Documentation

Once running, view interactive API docs at:
- Swagger UI: `http://localhost:8009/docs`
- ReDoc: `http://localhost:8009/redoc`

---

## Future Enhancements

### Planned Features
- **SMS Notifications** - Alert passengers when driver arrives
- **Driver Ratings** - Rate driver reliability and punctuality
- **Route Optimization** - Calculate optimal pickup order
- **Real-time Tracking** - Share driver location with passengers
- **Payment Integration** - Automated transport fee collection
- **Recurring Rides** - Save regular pickup preferences

### Potential Integrations
- Google Maps API for real-time distance/duration
- SMS gateway for notifications
- Payment gateway for automated fee collection

---

## Notes

- Transport fees are optional and member-managed (not enforced by platform)
- GPS coordinates are optional but recommended for future map features
- Routes are pre-calculated to avoid API costs during session sign-up
- Ride bookings are independent of attendance records for flexibility

---

*Last updated: January 2026*
