# Attendance Service

SwimBuddz Attendance Service manages session attendance, check-ins, and pool lists.

## Features

- Attendance tracking for sessions
- Check-in/check-out management
- Pool list generation
- Attendance statistics and reporting
- Lane assignment support

## API Endpoints

### Attendance
- `GET /attendance/sessions/{session_id}/attendance` - Get session attendance list
- `GET /attendance/sessions/{session_id}/pool-list` - Generate pool list
- `POST /attendance/check-in` - Check-in to session
- `POST /attendance/check-out` - Check-out from session
- `GET /attendance/member/{member_id}` - Get member attendance history
- `GET /attendance/stats` - Get attendance statistics

### Pool List Management
- `POST /attendance/pool-list/assign-lanes` - Assign swimmers to lanes
- `GET /attendance/pool-list/{session_id}` - Get formatted pool list

## Database Tables

- `attendance_records` - Individual attendance records
- `pool_assignments` - Lane assignments for pool lists

## Key Features

### Check-in Process
1. Member scans QR code or checks in manually
2. System validates membership status
3. Records timestamp and session details
4. Updates real-time attendance count

### Pool List Generation
- Groups swimmers by skill level
- Assigns to lanes based on capacity
- Exports to printable format
- Updates in real-time

### Attendance Tracking
- Per-session attendance rates
- Member attendance history
- Punctuality tracking
- Attendance trends and analytics

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string

## Running

```bash
# Via Docker
docker-compose up attendance-service

# Standalone (dev)
cd services/attendance_service
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

## Port

Default: `8003`
